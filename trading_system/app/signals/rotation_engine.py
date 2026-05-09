from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from trading_system.app.market_regime.derivatives import is_late_stage_long_blowoff, symbol_derivatives_features
from trading_system.app.signals.entry_profile import EntryProfile, resolve_entry_profile
from trading_system.app.signals.scoring import score_rotation_candidate
from trading_system.app.types import EngineCandidate, RegimeSnapshot

_ROTATION_SCORE_FLOOR = 0.60
_SCOUT_ROTATION_SCORE_FLOOR = 0.55
_CROWDED_LONG_BASIS_BPS = 20.0
_ROTATION_ABSOLUTE_STRENGTH_DAILY_FLOOR = 0.03
_ROTATION_ABSOLUTE_STRENGTH_H4_FLOOR = 0.01
_ROTATION_ABSOLUTE_STRENGTH_H1_FLOOR = 0.003
_ROTATION_H4_EXTENSION_OVERHEAT_PCT = 0.03
_ROTATION_H1_EXTENSION_OVERHEAT_PCT = 0.01
_ROTATION_REACCELERATION_H1_EXTENSION_FLOOR_PCT = 0.007
_SOFT_RECLAIM_ROTATION_REGIMES = {"RISK_ON_ROTATION"}
_ACTIVE_PAPER_SOFT_RECLAIM_EMA50_TOLERANCE_PCT = 0.02
_ACTIVE_PAPER_SOFT_RECLAIM_REGIMES = {"MIXED", "RISK_ON_ROTATION"}
_ACTIVE_PAPER_RELATIVE_H4_PULLBACK_FLOOR = -0.01
_HIGH_LIQUIDITY_TIERS = {"high", "top"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _tf_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


def _strict_present_numeric(row: Mapping[str, Any], field: str, field_path: str) -> float:
    if field not in row:
        return 0.0
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field_path} must be a finite non-bool number")
    return float(value)


def _strict_optional_sector(row: Mapping[str, Any], field_path: str) -> str:
    if "sector" not in row:
        return ""
    value = row.get("sector")
    if not isinstance(value, str):
        raise ValueError(f"{field_path} must be a string when present")
    return value.strip()


def _payload_sector(symbol: str, payload: Mapping[str, Any]) -> str:
    return _strict_optional_sector(payload, f"{symbol}.sector")


def _candidate_sector(symbol: str, payload: Mapping[str, Any], universe_row: Mapping[str, Any]) -> str:
    payload_sector = _payload_sector(symbol, payload)
    if payload_sector:
        return payload_sector
    return _strict_optional_sector(universe_row, f"rotation_universe[{symbol}].sector")


def _validate_required_rotation_timeframe_numerics(symbol: str, payload: Mapping[str, Any]) -> None:
    required_fields = {
        "daily": ("close", "ema_20", "ema_50", "atr_pct", "return_pct_7d", "volume_usdt_24h"),
        "4h": ("close", "ema_20", "ema_50", "return_pct_3d"),
        "1h": ("close", "ema_20", "ema_50", "return_pct_24h"),
    }
    for timeframe, fields in required_fields.items():
        row = _tf_row(payload, timeframe)
        for field in fields:
            _strict_present_numeric(row, field, f"{symbol}.{timeframe}.{field}")


def _regime_value(regime: RegimeSnapshot | Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if regime is None:
        return default
    if isinstance(regime, Mapping):
        return regime.get(key, default)
    return getattr(regime, key, default)


def _rotation_suppressed(regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    rules = _regime_value(regime, "suppression_rules", [])
    if not isinstance(rules, list):
        return False
    normalized_rules: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, str):
            raise ValueError(f"regime.suppression_rules[{index}] must be a string")
        normalized_rules.add(rule.lower().strip())
    return "rotation" in normalized_rules


def _rotation_symbols(rotation_universe: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    if rotation_universe is None:
        return rows
    for row in rotation_universe:
        symbol_value = row.get("symbol", "")
        if not isinstance(symbol_value, str):
            raise ValueError("rotation_universe.symbol must be a string when present")
        symbol = symbol_value.upper().strip()
        if symbol:
            rows[symbol] = row
    return rows


def _liquidity_meta(universe_row: Mapping[str, Any]) -> dict[str, Any]:
    if "liquidity_meta" not in universe_row:
        return {}
    value = universe_row.get("liquidity_meta")
    if not isinstance(value, Mapping):
        raise ValueError("rotation_universe.liquidity_meta must be an object when present")
    return dict(value)


def _major_proxy_returns(market_context: Mapping[str, Any]) -> dict[str, float]:
    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return {"daily": 0.0, "4h": 0.0, "1h": 0.0}

    majors = [
        (str(symbol).upper(), payload)
        for symbol, payload in symbols.items()
        if str(symbol).upper() in {"BTCUSDT", "ETHUSDT"}
    ]
    if not majors:
        return {"daily": 0.0, "4h": 0.0, "1h": 0.0}

    total_daily = 0.0
    total_h4 = 0.0
    total_h1 = 0.0
    for symbol, payload in majors:
        total_daily += _strict_present_numeric(
            _tf_row(payload, "daily"), "return_pct_7d", f"{symbol}.daily.return_pct_7d"
        )
        total_h4 += _strict_present_numeric(_tf_row(payload, "4h"), "return_pct_3d", f"{symbol}.4h.return_pct_3d")
        total_h1 += _strict_present_numeric(
            _tf_row(payload, "1h"), "return_pct_24h", f"{symbol}.1h.return_pct_24h"
        )

    count = float(len(majors))
    return {"daily": total_daily / count, "4h": total_h4 / count, "1h": total_h1 / count}


def _trend_intact(payload: Mapping[str, Any]) -> bool:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
        and _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _is_active_paper_profile(profile: EntryProfile) -> bool:
    return profile.name == "active_paper"


def _is_short_term_profile(profile: EntryProfile) -> bool:
    return profile.name == "short_term"


def _is_scout_profile(profile: EntryProfile) -> bool:
    return profile.name == "scout"


def _uses_intraday_long_trigger(profile: EntryProfile) -> bool:
    return _is_short_term_profile(profile) or _is_scout_profile(profile)


def _short_term_long_trigger(payload: Mapping[str, Any], profile: EntryProfile) -> bool:
    if not _uses_intraday_long_trigger(profile):
        return True
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    m30 = _tf_row(payload, "30m")
    m15 = _tf_row(payload, "15m")
    intraday_trigger = (
        _to_float(m30.get("close")) >= _to_float(m30.get("ema_20")) >= _to_float(m30.get("ema_50"))
        and _to_float(m15.get("close")) >= _to_float(m15.get("ema_20")) >= _to_float(m15.get("ema_50"))
    )
    if _is_scout_profile(profile):
        return intraday_trigger
    return intraday_trigger and (
        _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _soft_reclaim_trend_intact(payload: Mapping[str, Any], regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    if str(_regime_value(regime, "label", "")).upper() not in _SOFT_RECLAIM_ROTATION_REGIMES:
        return False
    if _rotation_suppressed(regime):
        return False

    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_close = _to_float(daily.get("close"))
    daily_ema20 = _to_float(daily.get("ema_20"))
    daily_ema50 = _to_float(daily.get("ema_50"))
    return (
        daily_close > daily_ema20
        and daily_close >= daily_ema50
        and _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _active_paper_soft_reclaim_trend_intact(
    payload: Mapping[str, Any],
    regime: RegimeSnapshot | Mapping[str, Any] | None,
    profile: EntryProfile,
) -> bool:
    if not _is_active_paper_profile(profile):
        return False
    if str(_regime_value(regime, "label", "")).upper() not in _ACTIVE_PAPER_SOFT_RECLAIM_REGIMES:
        return False
    if _rotation_suppressed(regime):
        return False
    if _payload_sector("", payload).lower() == "majors":
        return False
    if str(payload.get("liquidity_tier", "")).lower() not in _HIGH_LIQUIDITY_TIERS:
        return False

    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_close = _to_float(daily.get("close"))
    daily_ema20 = _to_float(daily.get("ema_20"))
    daily_ema50 = _to_float(daily.get("ema_50"))
    daily_soft_reclaim = (
        daily_close > 0.0
        and daily_ema20 > 0.0
        and daily_ema50 > 0.0
        and daily_close > daily_ema20
        and daily_close >= daily_ema50 * (1.0 - _ACTIVE_PAPER_SOFT_RECLAIM_EMA50_TOLERANCE_PCT)
        and daily_close <= daily_ema50 * (1.0 + _ACTIVE_PAPER_SOFT_RECLAIM_EMA50_TOLERANCE_PCT)
    )

    def lower_timeframe_near_ema50(row: Mapping[str, Any]) -> bool:
        close = _to_float(row.get("close"))
        ema50 = _to_float(row.get("ema_50"))
        return close > 0.0 and ema50 > 0.0 and close >= ema50 * (1.0 - _ACTIVE_PAPER_SOFT_RECLAIM_EMA50_TOLERANCE_PCT)

    return daily_soft_reclaim and lower_timeframe_near_ema50(h4) and lower_timeframe_near_ema50(h1)


def _scout_intraday_recovery_trend_intact(payload: Mapping[str, Any], profile: EntryProfile) -> bool:
    if not _is_scout_profile(profile):
        return False
    if _payload_sector("", payload).lower() == "majors":
        return False
    if str(payload.get("liquidity_tier", "")).lower() not in _HIGH_LIQUIDITY_TIERS:
        return False

    daily = _tf_row(payload, "daily")
    h1 = _tf_row(payload, "1h")
    daily_constructive = _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
    h1_reclaimed_ema20 = _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) > 0.0
    return daily_constructive and h1_reclaimed_ema20 and _short_term_long_trigger(payload, profile)


def _trend_accepted(
    payload: Mapping[str, Any],
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
    profile: EntryProfile | None = None,
) -> bool:
    return (
        _trend_intact(payload)
        or _soft_reclaim_trend_intact(payload, regime)
        or (profile is not None and _active_paper_soft_reclaim_trend_intact(payload, regime, profile))
        or (profile is not None and _scout_intraday_recovery_trend_intact(payload, profile))
    )


def _passes_active_paper_relative_pullback_strength_gate(
    payload: Mapping[str, Any],
    profile: EntryProfile,
    rs_features: Mapping[str, float],
) -> bool:
    if not _is_active_paper_profile(profile):
        return False

    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _to_float(daily.get("return_pct_7d")) >= profile.rotation_daily_floor
        and _to_float(h1.get("return_pct_24h")) >= profile.rotation_h1_floor
        and _to_float(h4.get("return_pct_3d")) >= _ACTIVE_PAPER_RELATIVE_H4_PULLBACK_FLOOR
        and _to_float(rs_features.get("h4_spread")) >= profile.rotation_h4_floor
    )


def _relative_strength_features(payload: Mapping[str, Any], proxy: Mapping[str, float]) -> dict[str, float]:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")

    daily_spread = _to_float(daily.get("return_pct_7d")) - _to_float(proxy.get("daily"))
    h4_spread = _to_float(h4.get("return_pct_3d")) - _to_float(proxy.get("4h"))
    h1_spread = _to_float(h1.get("return_pct_24h")) - _to_float(proxy.get("1h"))

    average_spread = (daily_spread + h4_spread + h1_spread) / 3.0
    relative_strength_rank = max(min((average_spread + 0.03) / 0.06, 1.0), 0.0)
    persistence = sum(1 for spread in (daily_spread, h4_spread, h1_spread) if spread >= -0.01) / 3.0

    return {
        "daily_spread": daily_spread,
        "h4_spread": h4_spread,
        "h1_spread": h1_spread,
        "relative_strength_rank": relative_strength_rank,
        "persistence": persistence,
    }


def _pullback_quality(payload: Mapping[str, Any]) -> float:
    h1 = _tf_row(payload, "1h")
    close = _to_float(h1.get("close"))
    ema20 = _to_float(h1.get("ema_20"))
    ema50 = _to_float(h1.get("ema_50"))
    if close <= 0 or ema20 <= 0 or ema50 <= 0 or close < ema20 or ema20 < ema50:
        return 0.0

    extension = max(close - ema20, 0.0)
    extension_band = max(close * 0.03, 1e-9)
    quality = 1.0 - min(extension / extension_band, 1.0)
    return max(min(quality, 1.0), 0.0)


def _liquidity_quality(payload: Mapping[str, Any], universe_row: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    volume = _to_float(daily.get("volume_usdt_24h"))
    liquidity_meta = _liquidity_meta(universe_row)
    rolling_notional = _to_float(liquidity_meta.get("rolling_notional"))
    normalized_volume = min(max(volume, rolling_notional) / 1_200_000_000.0, 1.0)
    slippage_bps = _to_float(liquidity_meta.get("slippage_bps"),)
    slippage_quality = 1.0 - min(slippage_bps / 25.0, 1.0)
    return max(min((normalized_volume * 0.8) + (slippage_quality * 0.2), 1.0), 0.0)


def _volatility_quality(payload: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    atr_pct = _to_float(daily.get("atr_pct"))
    if atr_pct <= 0.0:
        return 0.0
    return max(0.0, min(1.0 - (abs(atr_pct - 0.055) / 0.04), 1.0))


def _extension_pct(row: Mapping[str, Any]) -> float:
    close = _to_float(row.get("close"))
    ema20 = _to_float(row.get("ema_20"))
    if close <= 0.0 or ema20 <= 0.0:
        return 0.0
    return max((close / ema20) - 1.0, 0.0)


def _passes_absolute_strength_gate(payload: Mapping[str, Any], entry_profile: EntryProfile | str | None = None) -> bool:
    profile = resolve_entry_profile(entry_profile)
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    m30 = _tf_row(payload, "30m")
    m15 = _tf_row(payload, "15m")
    base_gate = (
        _to_float(daily.get("return_pct_7d")) >= profile.rotation_daily_floor
        and _to_float(h4.get("return_pct_3d")) >= profile.rotation_h4_floor
        and _to_float(h1.get("return_pct_24h")) >= profile.rotation_h1_floor
    )
    if not base_gate or not _uses_intraday_long_trigger(profile):
        return base_gate
    return (
        _to_float(m30.get("return_pct_8h")) >= profile.rotation_m30_floor
        and _to_float(m15.get("return_pct_4h")) >= profile.rotation_m15_floor
    )


def _reject_price_extension_overheat(payload: Mapping[str, Any]) -> bool:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _extension_pct(h4) >= _ROTATION_H4_EXTENSION_OVERHEAT_PCT
        and _extension_pct(h1) >= _ROTATION_H1_EXTENSION_OVERHEAT_PCT
    )


def _reject_overheated_crowded_leader(features: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        is_late_stage_long_blowoff(
            features,
            h4_extension_pct=_extension_pct(h4),
            h1_extension_pct=_extension_pct(h1),
        )
        or (
            str(features.get("crowding_bias", "balanced")) == "crowded_long"
            and _to_float(features.get("basis_bps")) >= _CROWDED_LONG_BASIS_BPS
        )
    )


def _setup_type(payload: Mapping[str, Any]) -> str:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    if _to_float(h4.get("return_pct_3d")) >= 0.024 and _to_float(h1.get("return_pct_24h")) >= 0.007:
        return "RS_REACCELERATION"
    return "RS_PULLBACK"


def _passes_reacceleration_h1_extension_gate(payload: Mapping[str, Any], setup_type: str) -> bool:
    if setup_type != "RS_REACCELERATION":
        return True
    return _extension_pct(_tf_row(payload, "1h")) >= _ROTATION_REACCELERATION_H1_EXTENSION_FLOOR_PCT


def _rotation_stop_loss(payload: Mapping[str, Any], entry_profile: EntryProfile | None = None) -> float:
    h1 = _tf_row(payload, "1h")
    daily = _tf_row(payload, "daily")
    m15 = _tf_row(payload, "15m")
    if entry_profile is not None and (_is_short_term_profile(entry_profile) or _is_scout_profile(entry_profile)):
        entry_reference = _to_float(m15.get("close")) or _to_float(h1.get("close"))
        stop_loss = _to_float(m15.get("ema_50"))
    else:
        entry_reference = _to_float(h1.get("close")) or _to_float(daily.get("close"))
        stop_loss = _to_float(h1.get("ema_50"))
    if entry_reference <= 0 or stop_loss <= 0 or stop_loss >= entry_reference:
        return 0.0
    return stop_loss


def generate_rotation_candidates(
    market_context: Mapping[str, Any],
    *,
    rotation_universe: Sequence[Mapping[str, Any]] | None = None,
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None = None,
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
    entry_profile: EntryProfile | str | None = None,
) -> list[EngineCandidate]:
    if _rotation_suppressed(regime):
        return []

    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return []

    eligible = _rotation_symbols(rotation_universe)
    if not eligible:
        return []

    profile = resolve_entry_profile(entry_profile)
    proxy = _major_proxy_returns(market_context)
    candidates: list[EngineCandidate] = []
    for symbol, universe_row in eligible.items():
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        if _payload_sector(symbol, payload).lower() == "majors":
            continue
        _validate_required_rotation_timeframe_numerics(symbol, payload)
        active_paper_soft_reclaim = _active_paper_soft_reclaim_trend_intact(payload, regime, profile)
        scout_intraday_recovery = _scout_intraday_recovery_trend_intact(payload, profile)
        if not _trend_accepted(payload, regime, profile):
            continue
        if not _short_term_long_trigger(payload, profile):
            continue
        rs_features = _relative_strength_features(payload, proxy)
        if not _passes_absolute_strength_gate(
            payload, profile
        ) and not _passes_active_paper_relative_pullback_strength_gate(payload, profile, rs_features):
            continue
        if _reject_price_extension_overheat(payload):
            continue
        setup_type = _setup_type(payload)
        if not _passes_reacceleration_h1_extension_gate(payload, setup_type):
            continue

        derivatives_features = symbol_derivatives_features(derivatives, str(symbol))
        if _reject_overheated_crowded_leader(derivatives_features, payload):
            continue

        if rs_features["relative_strength_rank"] < 0.38 or rs_features["persistence"] < (2.0 / 3.0):
            continue

        scored = score_rotation_candidate(
            {
                "relative_strength_rank": rs_features["relative_strength_rank"],
                "persistence": rs_features["persistence"],
                "pullback_quality": _pullback_quality(payload),
                "liquidity_quality": _liquidity_quality(payload, universe_row),
                "volatility_quality": _volatility_quality(payload),
            }
        )
        total_score = _to_float(scored.get("total"))
        score_floor = _SCOUT_ROTATION_SCORE_FLOOR if _is_scout_profile(profile) else _ROTATION_SCORE_FLOOR
        if total_score < score_floor:
            continue

        stop_loss = _rotation_stop_loss(payload, profile)
        if stop_loss <= 0.0:
            continue

        daily = _tf_row(payload, "daily")
        liquidity_meta = _liquidity_meta(universe_row)
        liquidity_meta.setdefault("liquidity_tier", payload.get("liquidity_tier"))
        liquidity_meta["volume_usdt_24h"] = _to_float(daily.get("volume_usdt_24h"))
        invalidation_source = "rotation_pullback_failure_below_1h_ema50"
        trigger_timeframes = None
        if _is_short_term_profile(profile) or _is_scout_profile(profile):
            invalidation_source = (
                "scout_rotation_loss_below_15m_ema50"
                if _is_scout_profile(profile)
                else "short_term_rotation_loss_below_15m_ema50"
            )
            trigger_timeframes = ["30m", "15m"]

        candidates.append(
            EngineCandidate(
                engine="rotation",
                setup_type=setup_type,
                symbol=symbol,
                side="LONG",
                score=total_score,
                stop_loss=stop_loss,
                invalidation_source=invalidation_source,
                timeframe_meta={
                    "daily_bias": "relative_strength_leader",
                    "h4_structure": (
                        "leader_persistence"
                        if _trend_intact(payload)
                        else "scout_intraday_recovery"
                        if scout_intraday_recovery
                        else "active_paper_soft_reclaim"
                        if active_paper_soft_reclaim
                        else "soft_daily_reclaim"
                    ),
                    "h1_trigger": "pullback_hold_or_reacceleration",
                    "relative_strength": {
                        "daily_spread": round(rs_features["daily_spread"], 6),
                        "h4_spread": round(rs_features["h4_spread"], 6),
                        "h1_spread": round(rs_features["h1_spread"], 6),
                    },
                    "score_components": scored.get("components", {}),
                    **({"trigger_timeframes": trigger_timeframes} if trigger_timeframes else {}),
                },
                sector=_candidate_sector(symbol, payload, universe_row),
                liquidity_meta=liquidity_meta,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
