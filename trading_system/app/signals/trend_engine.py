from __future__ import annotations

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
    return rule_name.lower() in {str(rule).lower().strip() for rule in rules}


def _is_uptrend(daily: Mapping[str, Any], h4: Mapping[str, Any], h1: Mapping[str, Any]) -> bool:
    return (
        _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
        and _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


def _is_active_paper_profile(profile: EntryProfile) -> bool:
    return profile.name == "active_paper"


def _is_active_paper_major_shallow_h1_pullback(payload: Mapping[str, Any], profile: EntryProfile) -> bool:
    if not _is_active_paper_profile(profile):
        return False
    if str(payload.get("sector", "")) != _MAJOR_SECTOR:
        return False
    if str(payload.get("liquidity_tier", "")).lower() not in _HIGH_LIQUIDITY_TIERS:
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


def _is_high_liquidity_strong_name(payload: Mapping[str, Any]) -> bool:
    tier = str(payload.get("liquidity_tier", "")).lower()
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


def _is_supportive_non_major_soft_pretrend(payload: Mapping[str, Any], regime: Any) -> bool:
    if _regime_label(regime) not in _SUPPORTIVE_NON_MAJOR_SOFT_PRETREND_REGIMES:
        return False
    if _has_suppression_rule(regime, "trend"):
        return False

    tier = str(payload.get("liquidity_tier", "")).lower()
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


def _trend_stop_loss(payload: Mapping[str, Any]) -> float:
    h4 = _tf_row(payload, "4h")
    daily = _tf_row(payload, "daily")
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
    return (
        _to_float(daily.get("return_pct_7d")) >= profile.trend_daily_floor
        and _to_float(h4.get("return_pct_3d")) >= profile.trend_h4_floor
        and _to_float(h1.get("return_pct_24h")) >= profile.trend_h1_floor
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
        sector = str(payload.get("sector", ""))
        is_major = sector == _MAJOR_SECTOR
        soft_non_major_pretrend = False
        active_paper_shallow_pullback = _is_active_paper_major_shallow_h1_pullback(payload, profile)
        if not is_major:
            soft_non_major_pretrend = _is_supportive_non_major_soft_pretrend(payload, regime)
            if not include_high_liquidity_strong_names and not soft_non_major_pretrend:
                continue
            if include_high_liquidity_strong_names and not _is_high_liquidity_strong_name(payload) and not soft_non_major_pretrend:
                continue

        daily = _tf_row(payload, "daily")
        h4 = _tf_row(payload, "4h")
        h1 = _tf_row(payload, "1h")
        if not _is_uptrend(daily, h4, h1) and not soft_non_major_pretrend and not active_paper_shallow_pullback:
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

        stop_loss = _trend_stop_loss(payload)
        if stop_loss <= 0.0:
            continue

        timeframe_meta = {
            "daily_bias": "supportive_soft_pretrend" if soft_non_major_pretrend else "up",
            "h4_structure": "intact",
            "h1_trigger": "active_paper_shallow_pullback" if active_paper_shallow_pullback else "confirmed",
        }
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
                invalidation_source="trend_structure_loss_below_4h_ema50",
                timeframe_meta=timeframe_meta,
                sector=sector or None,
                liquidity_meta={
                    "liquidity_tier": payload.get("liquidity_tier"),
                    "volume_usdt_24h": _to_float(daily.get("volume_usdt_24h")),
                },
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
