from __future__ import annotations

import math
from typing import Any, Mapping

from trading_system.app.market_regime.derivatives import is_late_stage_long_blowoff, symbol_derivatives_features
from trading_system.app.signals.entry_profile import EntryProfile, resolve_entry_profile
from trading_system.app.signals.scoring import score_trend_candidate
from trading_system.app.types import EngineCandidate

_MAJOR_SECTOR = "majors"
_HIGH_LIQUIDITY_TIERS = {"high", "top"}
_CROWDED_LONG_BASIS_BPS = 20.0
_TREND_ABSOLUTE_STRENGTH_DAILY_FLOOR = 0.03
_TREND_ABSOLUTE_STRENGTH_H4_FLOOR = 0.01
_TREND_ABSOLUTE_STRENGTH_H1_FLOOR = 0.003
_TREND_H4_EXTENSION_OVERHEAT_PCT = 0.03
_TREND_H1_EXTENSION_OVERHEAT_PCT = 0.01
_SUPPORTIVE_NON_MAJOR_SOFT_PRETREND_REGIMES = {"MIXED", "RISK_ON_ROTATION"}
_SUPPORTIVE_NON_MAJOR_SOFT_PRETREND_MAX_RECLAIM_GAP_PCT = 0.02
_ACTIVE_PAPER_H1_PULLBACK_EMA50_TOLERANCE_PCT = 0.005
_REQUIRED_TREND_NUMERIC_FIELDS = {
    "daily": ("close", "ema_20", "ema_50", "return_pct_7d", "volume_usdt_24h"),
    "4h": ("close", "ema_20", "ema_50", "return_pct_3d"),
    "1h": ("close", "ema_20", "ema_50", "return_pct_24h"),
}


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(converted):
        return 0.0
    return converted


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value)


def _validate_required_trend_numerics(symbol: str, payload: Mapping[str, Any]) -> bool:
    complete = True
    for timeframe, fields in _REQUIRED_TREND_NUMERIC_FIELDS.items():
        row = _tf_row(payload, timeframe)
        for field in fields:
            if field not in row:
                complete = False
                continue
            if not _is_finite_number(row[field]):
                raise ValueError(f"{symbol}.{timeframe}.{field} must be a finite non-bool number")
    return complete


def _optional_string_field(symbol: str, payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{symbol}.{field} must be a string")
    return value


def _payload_categories(symbol: str, payload: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _optional_string_field(symbol, payload, "sector"),
        _optional_string_field(symbol, payload, "liquidity_tier"),
    )


def _has_required_trend_numerics(payload: Mapping[str, Any]) -> bool:
    for timeframe, fields in _REQUIRED_TREND_NUMERIC_FIELDS.items():
        row = _tf_row(payload, timeframe)
        if not all(_is_finite_number(row.get(field)) for field in fields):
            return False
    return True


def _tf_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


def _regime_value(regime: Any, key: str, default: Any = None) -> Any:
    if regime is None:
        return default
    if isinstance(regime, Mapping):
        return regime.get(key, default)
    return getattr(regime, key, default)


def _regime_label(regime: Any) -> str:
    return str(_regime_value(regime, "label", "")).upper()


def _has_suppression_rule(regime: Any, rule_name: str) -> bool:
    rules = _regime_value(regime, "suppression_rules", [])
    if not isinstance(rules, list):
        return False
    normalized_rules: set[str] = set()
    for rule in rules:
        if not isinstance(rule, str):
            raise ValueError("regime.suppression_rules entries must be strings")
        normalized_rules.add(rule.lower().strip())
    return rule_name.lower() in normalized_rules


def _is_uptrend(daily: Mapping[str, Any], h4: Mapping[str, Any], h1: Mapping[str, Any]) -> bool:
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


def _is_intraday_multi_profile(profile: EntryProfile) -> bool:
    return profile.name == "intraday_multi"


def _uses_intraday_long_trigger(profile: EntryProfile) -> bool:
    return _is_short_term_profile(profile) or _is_scout_profile(profile) or _is_intraday_multi_profile(profile)


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


def _is_active_paper_major_shallow_h1_pullback(
    payload: Mapping[str, Any],
    profile: EntryProfile,
    sector: str,
    liquidity_tier: str,
) -> bool:
    if not _is_active_paper_profile(profile):
        return False
    if sector != _MAJOR_SECTOR:
        return False
    if liquidity_tier.lower() not in _HIGH_LIQUIDITY_TIERS:
        return False

    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_constructive = _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
    h4_constructive = _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
    h1_close = _to_float(h1.get("close"))
    h1_ema20 = _to_float(h1.get("ema_20"))
    h1_ema50 = _to_float(h1.get("ema_50"))
    h1_shallow_pullback = (
        h1_close > 0.0
        and h1_ema20 > 0.0
        and h1_ema50 > 0.0
        and h1_ema20 >= h1_ema50
        and h1_close < h1_ema20
        and h1_close >= h1_ema50 * (1.0 - _ACTIVE_PAPER_H1_PULLBACK_EMA50_TOLERANCE_PCT)
    )
    return daily_constructive and h4_constructive and h1_shallow_pullback


def _is_scout_major_intraday_recovery(
    payload: Mapping[str, Any],
    profile: EntryProfile,
    sector: str,
    liquidity_tier: str,
) -> bool:
    if not _is_scout_profile(profile):
        return False
    if sector != _MAJOR_SECTOR:
        return False
    if liquidity_tier.lower() not in _HIGH_LIQUIDITY_TIERS:
        return False

    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_constructive = _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
    h4_reclaimed_ema20 = _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) > 0.0
    h1_reclaimed_ema20 = _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) > 0.0
    return daily_constructive and h4_reclaimed_ema20 and h1_reclaimed_ema20 and _short_term_long_trigger(payload, profile)


def _is_high_liquidity_strong_name(payload: Mapping[str, Any], liquidity_tier: str) -> bool:
    tier = liquidity_tier.lower()
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        tier in _HIGH_LIQUIDITY_TIERS
        and _is_uptrend(daily, h4, h1)
        and _to_float(daily.get("return_pct_7d")) > 0.0
        and _to_float(h4.get("return_pct_3d")) > 0.0
    )


def _lower_timeframes_intact(payload: Mapping[str, Any]) -> bool:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _is_supportive_non_major_soft_pretrend(payload: Mapping[str, Any], regime: Any, liquidity_tier: str) -> bool:
    if _regime_label(regime) not in _SUPPORTIVE_NON_MAJOR_SOFT_PRETREND_REGIMES:
        return False
    if _has_suppression_rule(regime, "trend"):
        return False

    tier = liquidity_tier.lower()
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_close = _to_float(daily.get("close"))
    daily_ema20 = _to_float(daily.get("ema_20"))
    daily_ema50 = _to_float(daily.get("ema_50"))
    daily_reclaim_nearby = (
        daily_close > daily_ema20
        and daily_close >= (daily_ema50 * 0.99)
        and daily_close <= (daily_ema50 * (1.0 + _SUPPORTIVE_NON_MAJOR_SOFT_PRETREND_MAX_RECLAIM_GAP_PCT))
    )
    momentum_non_negative = (
        _to_float(daily.get("return_pct_7d")) >= 0.0
        and _to_float(h4.get("return_pct_3d")) >= 0.0
        and _to_float(h1.get("return_pct_24h")) >= 0.0
    )
    return tier in _HIGH_LIQUIDITY_TIERS and daily_reclaim_nearby and _lower_timeframes_intact(payload) and momentum_non_negative


def _volume_quality(payload: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    volume = _to_float(daily.get("volume_usdt_24h"))
    if volume <= 0:
        return 0.0
    return min(volume / 1_000_000_000.0, 1.0)


def _setup_type(payload: Mapping[str, Any]) -> str:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    if _to_float(h4.get("return_pct_3d")) >= 0.02 and _to_float(h1.get("return_pct_24h")) >= 0.006:
        return "BREAKOUT_CONTINUATION"
    return "PULLBACK_CONTINUATION"


def _trend_stop_loss(payload: Mapping[str, Any], entry_profile: EntryProfile | None = None) -> float:
    h4 = _tf_row(payload, "4h")
    daily = _tf_row(payload, "daily")
    m15 = _tf_row(payload, "15m")
    if entry_profile is not None and (_is_short_term_profile(entry_profile) or _is_scout_profile(entry_profile)):
        entry_reference = _to_float(m15.get("close")) or _to_float(h4.get("close"))
        stop_loss = _to_float(m15.get("ema_50"))
    else:
        entry_reference = _to_float(daily.get("close")) or _to_float(h4.get("close"))
        stop_loss = _to_float(h4.get("ema_50"))
    if entry_reference <= 0 or stop_loss <= 0 or stop_loss >= entry_reference:
        return 0.0
    return stop_loss


def _passes_absolute_strength_gate(payload: Mapping[str, Any], entry_profile: EntryProfile | str | None = None) -> bool:
    profile = resolve_entry_profile(entry_profile)
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    m30 = _tf_row(payload, "30m")
    m15 = _tf_row(payload, "15m")
    base_gate = (
        _to_float(daily.get("return_pct_7d")) >= profile.trend_daily_floor
        and _to_float(h4.get("return_pct_3d")) >= profile.trend_h4_floor
        and _to_float(h1.get("return_pct_24h")) >= profile.trend_h1_floor
    )
    if not base_gate or not _uses_intraday_long_trigger(profile):
        return base_gate
    return (
        _to_float(m30.get("return_pct_8h")) >= profile.trend_m30_floor
        and _to_float(m15.get("return_pct_4h")) >= profile.trend_m15_floor
    )


def _extension_pct(row: Mapping[str, Any]) -> float:
    close = _to_float(row.get("close"))
    ema20 = _to_float(row.get("ema_20"))
    if close <= 0.0 or ema20 <= 0.0:
        return 0.0
    return max((close / ema20) - 1.0, 0.0)


def _reject_price_extension_overheat(payload: Mapping[str, Any]) -> bool:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _extension_pct(h4) >= _TREND_H4_EXTENSION_OVERHEAT_PCT
        and _extension_pct(h1) >= _TREND_H1_EXTENSION_OVERHEAT_PCT
    )


def _reject_crowded_long(features: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
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


def generate_trend_candidates(
    market_context: Mapping[str, Any],
    *,
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None = None,
    include_high_liquidity_strong_names: bool = True,
    regime: Any = None,
    entry_profile: EntryProfile | str | None = None,
) -> list[EngineCandidate]:
    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return []

    profile = resolve_entry_profile(entry_profile)
    candidates: list[EngineCandidate] = []
    for symbol, payload_value in symbols.items():
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        if not _validate_required_trend_numerics(str(symbol), payload):
            continue
        sector, liquidity_tier = _payload_categories(str(symbol), payload)
        is_major = sector == _MAJOR_SECTOR
        soft_non_major_pretrend = False
        active_paper_shallow_pullback = _is_active_paper_major_shallow_h1_pullback(
            payload,
            profile,
            sector,
            liquidity_tier,
        )
        scout_intraday_recovery = _is_scout_major_intraday_recovery(payload, profile, sector, liquidity_tier)
        if not is_major:
            soft_non_major_pretrend = _is_supportive_non_major_soft_pretrend(payload, regime, liquidity_tier)
            if not include_high_liquidity_strong_names and not soft_non_major_pretrend:
                continue
            if (
                include_high_liquidity_strong_names
                and not _is_high_liquidity_strong_name(payload, liquidity_tier)
                and not soft_non_major_pretrend
            ):
                continue

        daily = _tf_row(payload, "daily")
        h4 = _tf_row(payload, "4h")
        h1 = _tf_row(payload, "1h")
        if (
            not _is_uptrend(daily, h4, h1)
            and not soft_non_major_pretrend
            and not active_paper_shallow_pullback
            and not scout_intraday_recovery
        ):
            continue
        if not _short_term_long_trigger(payload, profile):
            continue
        if not _passes_absolute_strength_gate(payload, profile):
            continue
        if _reject_price_extension_overheat(payload):
            continue

        derivatives_features = symbol_derivatives_features(derivatives, str(symbol))
        if _reject_crowded_long(derivatives_features, payload):
            continue

        scored = score_trend_candidate(
            {
                "daily_bias": "up",
                "h4_structure": "intact",
                "h1_trigger": "confirmed",
                "volume_quality": _volume_quality(payload),
            }
        )
        total_score = _to_float(scored.get("total"))
        if total_score <= 0.0:
            continue

        stop_loss = _trend_stop_loss(payload, profile)
        if stop_loss <= 0.0:
            continue

        timeframe_meta = {
            "daily_bias": "supportive_soft_pretrend" if soft_non_major_pretrend else "up",
            "h4_structure": "intact",
            "h1_trigger": (
                "scout_intraday_recovery"
                if scout_intraday_recovery
                else "active_paper_shallow_pullback"
                if active_paper_shallow_pullback
                else "confirmed"
            ),
        }
        invalidation_source = "trend_structure_loss_below_4h_ema50"
        if _is_short_term_profile(profile) or _is_scout_profile(profile) or _is_intraday_multi_profile(profile):
            timeframe_meta["trigger_timeframes"] = ["30m", "15m"]
            invalidation_source = (
                "scout_structure_loss_below_15m_ema50"
                if _is_scout_profile(profile)
                else "intraday_multi_structure_loss_below_15m_ema50"
                if _is_intraday_multi_profile(profile)
                else "short_term_structure_loss_below_15m_ema50"
            )
        if derivatives is not None:
            timeframe_meta["derivatives"] = {
                "crowding_bias": str(derivatives_features.get("crowding_bias", "balanced")),
                "basis_bps": _to_float(derivatives_features.get("basis_bps")),
            }

        candidates.append(
            EngineCandidate(
                engine="trend",
                setup_type=_setup_type(payload),
                symbol=str(symbol),
                side="LONG",
                score=total_score,
                stop_loss=stop_loss,
                invalidation_source=invalidation_source,
                timeframe_meta=timeframe_meta,
                sector=sector or None,
                liquidity_meta={
                    "liquidity_tier": payload.get("liquidity_tier"),
                    "volume_usdt_24h": _to_float(daily.get("volume_usdt_24h")),
                },
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
