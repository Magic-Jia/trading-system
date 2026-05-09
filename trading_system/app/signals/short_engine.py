from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from trading_system.app.market_regime.derivatives import symbol_derivatives_features
from trading_system.app.signals.entry_profile import EntryProfile, resolve_entry_profile
from trading_system.app.signals.scoring import score_short_candidate
from trading_system.app.types import EngineCandidate, RegimeSnapshot

_SHORT_SCORE_FLOOR = 0.58
_CROWDED_SHORT_BASIS_BPS = -20.0
_DEFENSIVE_REGIMES = {"RISK_OFF", "HIGH_VOL_DEFENSIVE"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _score_total(scored: Mapping[str, Any]) -> float:
    if "total" not in scored:
        return 0.0
    value = scored.get("total")
    if not _is_finite_number(value):
        raise ValueError("short score.total must be a finite non-bool number")
    return float(value)


def _score_components(scored: Mapping[str, Any]) -> dict[str, float]:
    if "components" not in scored or scored.get("components") is None:
        return {}
    components = scored.get("components")
    if not isinstance(components, Mapping):
        raise ValueError("short score.components must be an object")

    valid_components: dict[str, float] = {}
    for key, value in components.items():
        if not isinstance(key, str):
            raise ValueError("short score.components key must be a string")
        if not _is_finite_number(value):
            raise ValueError(f"short score.components.{key} must be a finite non-bool number")
        valid_components[key] = float(value)
    return valid_components


def _tf_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


def _liquidity_meta(universe_row: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    if "liquidity_meta" not in universe_row:
        return {}
    value = universe_row.get("liquidity_meta")
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise ValueError(f"{symbol}.liquidity_meta key must be a string")
        return value
    raise ValueError(f"{symbol}.liquidity_meta must be an object")


def _payload_sector(payload: Mapping[str, Any], symbol: str) -> str:
    if "sector" not in payload:
        return ""
    value = payload.get("sector")
    if not isinstance(value, str):
        raise ValueError(f"{symbol}.sector must be a string when present")
    return value


def _short_universe_sector(universe_row: Mapping[str, Any], symbol: str) -> str:
    if "sector" not in universe_row:
        return ""
    value = universe_row.get("sector")
    if not isinstance(value, str):
        raise ValueError(f"{symbol}.short_universe.sector must be a string when present")
    return value


def _optional_payload_string(payload: Mapping[str, Any], symbol: str, field: str) -> str | None:
    if field not in payload:
        return None
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{symbol}.{field} must be a string when present")
    return value


_REQUIRED_SHORT_TIMEFRAME_NUMERIC_FIELDS = {
    "daily": ("close", "ema_20", "ema_50", "return_pct_7d", "volume_usdt_24h"),
    "4h": ("close", "ema_20", "ema_50", "return_pct_3d"),
    "1h": ("close", "ema_20", "ema_50", "return_pct_24h"),
    "30m": ("close", "ema_20", "ema_50"),
    "15m": ("close", "ema_20", "ema_50"),
}


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value)


def _optional_strict_feature_number(features: Mapping[str, Any], field: str, *, default: float) -> float:
    if field not in features:
        return default
    value = features[field]
    if not _is_finite_number(value):
        raise ValueError(f"derivatives.{field} must be a finite non-bool number when present")
    return float(value)


def _optional_strict_feature_string(features: Mapping[str, Any], field: str, *, default: str) -> str:
    if field not in features:
        return default
    value = features[field]
    if not isinstance(value, str):
        raise ValueError(f"derivatives.{field} must be a string when present")
    return value


def _strict_derivatives_short_features(features: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "crowding_bias": _optional_strict_feature_string(features, "crowding_bias", default="balanced"),
        "basis_bps": _optional_strict_feature_number(features, "basis_bps", default=0.0),
    }


def _validate_required_short_timeframe_numerics(symbol: str, payload: Mapping[str, Any]) -> bool:
    for timeframe, fields in _REQUIRED_SHORT_TIMEFRAME_NUMERIC_FIELDS.items():
        row = _tf_row(payload, timeframe)
        for field in fields:
            if field not in row:
                continue
            if not _is_finite_number(row[field]):
                raise ValueError(f"{symbol}.{timeframe}.{field} must be a finite non-bool number")
    return True


def _market_symbol_key(symbol: Any) -> str:
    if not isinstance(symbol, str):
        raise ValueError("market.symbols key must be a string")
    return symbol


def _regime_value(regime: RegimeSnapshot | Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if regime is None:
        return default
    if isinstance(regime, Mapping):
        return regime.get(key, default)
    return getattr(regime, key, default)


def _short_suppressed(regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    rules = _regime_value(regime, "suppression_rules", [])
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, str):
            raise ValueError("regime.suppression_rules entries must be strings")
    return "short" in {rule.lower().strip() for rule in rules}


def _short_enabled(regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    if regime is None:
        return False
    label_value = _regime_value(regime, "label", None)
    if label_value is not None and not isinstance(label_value, str):
        raise ValueError("regime.label must be a string when present")
    label = (label_value or "").upper().strip()
    if label in _DEFENSIVE_REGIMES:
        return True
    bucket_targets = _regime_value(regime, "bucket_targets", {})
    if isinstance(bucket_targets, Mapping):
        if "short" not in bucket_targets:
            return False
        short_target = bucket_targets.get("short")
        if not _is_finite_number(short_target):
            raise ValueError("regime.bucket_targets.short must be a finite non-bool number")
        return short_target >= 0.2
    return False


def _short_symbols(short_universe: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    if short_universe is None:
        return rows
    for row in short_universe:
        if "symbol" not in row:
            continue
        symbol_value = row.get("symbol")
        if not isinstance(symbol_value, str):
            raise ValueError("short_universe.symbol must be a string when present")
        symbol = symbol_value.upper().strip()
        if symbol:
            rows[symbol] = row
    return rows


def _is_short_term_profile(profile: EntryProfile) -> bool:
    return profile.name == "short_term"


def _short_term_breakdown_trigger(payload: Mapping[str, Any], profile: EntryProfile) -> bool:
    if not _is_short_term_profile(profile):
        return True
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    m30 = _tf_row(payload, "30m")
    m15 = _tf_row(payload, "15m")
    return (
        _to_float(h4.get("close")) <= _to_float(h4.get("ema_20")) <= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) <= _to_float(h1.get("ema_20")) <= _to_float(h1.get("ema_50"))
        and _to_float(m30.get("close")) <= _to_float(m30.get("ema_20")) <= _to_float(m30.get("ema_50"))
        and _to_float(m15.get("close")) <= _to_float(m15.get("ema_20")) <= _to_float(m15.get("ema_50"))
    )


def _trend_broken(payload: Mapping[str, Any]) -> bool:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    return (
        _to_float(daily.get("close")) < _to_float(daily.get("ema_20")) < _to_float(daily.get("ema_50"))
        and _to_float(h4.get("close")) <= _to_float(h4.get("ema_20")) <= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) <= _to_float(h1.get("ema_20")) <= _to_float(h1.get("ema_50"))
    )


def _momentum_quality(payload: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    weakness = max(-_to_float(daily.get("return_pct_7d")), 0.0)
    weakness += max(-_to_float(h4.get("return_pct_3d")), 0.0)
    weakness += max(-_to_float(h1.get("return_pct_24h")), 0.0)
    return max(min(weakness / 0.09, 1.0), 0.0)


def _liquidity_quality(payload: Mapping[str, Any], universe_row: Mapping[str, Any], symbol: str) -> float:
    daily = _tf_row(payload, "daily")
    volume = _to_float(daily.get("volume_usdt_24h"))
    liquidity_meta = _liquidity_meta(universe_row, symbol)
    if "rolling_notional" in liquidity_meta and not _is_finite_number(liquidity_meta["rolling_notional"]):
        raise ValueError(f"{symbol}.liquidity_meta.rolling_notional must be a finite non-bool number")
    rolling_notional = _to_float(liquidity_meta.get("rolling_notional"))
    return min(max(volume, rolling_notional) / 10_000_000_000.0, 1.0)


def _setup_type(payload: Mapping[str, Any]) -> str | None:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    daily_weakness = max(-_to_float(daily.get("return_pct_7d")), 0.0)
    h4_weakness = max(-_to_float(h4.get("return_pct_3d")), 0.0)
    h1_weakness = max(-_to_float(h1.get("return_pct_24h")), 0.0)
    if daily_weakness >= 0.03 and h4_weakness >= 0.02 and h1_weakness >= 0.008:
        return "BREAKDOWN_SHORT"
    if daily_weakness >= 0.025 and h4_weakness >= 0.012 and h1_weakness >= 0.003:
        return "FAILED_BOUNCE_SHORT"
    return None


def _short_stop_loss(payload: Mapping[str, Any], entry_profile: EntryProfile | None = None) -> float:
    h4 = _tf_row(payload, "4h")
    daily = _tf_row(payload, "daily")
    m15 = _tf_row(payload, "15m")
    if entry_profile is not None and _is_short_term_profile(entry_profile):
        entry_reference = _to_float(m15.get("close")) or _to_float(h4.get("close"))
        stop_loss = _to_float(m15.get("ema_50"))
    else:
        entry_reference = _to_float(daily.get("close")) or _to_float(h4.get("close"))
        stop_loss = _to_float(h4.get("ema_50"))
    if entry_reference <= 0 or stop_loss <= 0 or stop_loss <= entry_reference:
        return 0.0
    return stop_loss


def _reject_crowded_short_squeeze_risk(features: Mapping[str, Any]) -> bool:
    return (
        features["crowding_bias"] == "crowded_short"
        and features["basis_bps"] <= _CROWDED_SHORT_BASIS_BPS
    )


def generate_short_candidates(
    market_context: Mapping[str, Any],
    *,
    short_universe: Sequence[Mapping[str, Any]] | None = None,
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None = None,
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
    entry_profile: EntryProfile | str | None = None,
) -> list[EngineCandidate]:
    if _short_suppressed(regime) or not _short_enabled(regime):
        return []

    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return []

    eligible = _short_symbols(short_universe)
    if not eligible:
        return []

    candidates: list[EngineCandidate] = []
    profile = resolve_entry_profile(entry_profile)
    for market_symbol in symbols:
        _market_symbol_key(market_symbol)
    for canonical_symbol, universe_row in eligible.items():
        payload_value = symbols.get(canonical_symbol)
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        payload_sector = _payload_sector(payload, canonical_symbol)
        sector = payload_sector or _short_universe_sector(universe_row, canonical_symbol)
        if payload_sector.lower() != "majors":
            continue
        if not _validate_required_short_timeframe_numerics(canonical_symbol, payload):
            continue
        if not _trend_broken(payload):
            continue
        if not _short_term_breakdown_trigger(payload, profile):
            continue

        setup_type = _setup_type(payload)
        if setup_type is None:
            continue

        derivatives_features = _strict_derivatives_short_features(symbol_derivatives_features(derivatives, canonical_symbol))
        if _reject_crowded_short_squeeze_risk(derivatives_features):
            continue

        scored = score_short_candidate(
            {
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "momentum_quality": _momentum_quality(payload),
                "liquidity_quality": _liquidity_quality(payload, universe_row, canonical_symbol),
            }
        )
        total_score = _score_total(scored)
        if total_score < _SHORT_SCORE_FLOOR:
            continue

        stop_loss = _short_stop_loss(payload, profile)
        if stop_loss <= 0.0:
            continue

        daily = _tf_row(payload, "daily")
        timeframe_meta = {
            "daily_bias": "down",
            "h4_structure": "breakdown",
            "h1_trigger": "confirmed",
            "gate_timeframes": ["daily", "4h", "1h"],
            "score_components": _score_components(scored),
        }
        invalidation_source = "short_structure_reclaim_above_4h_ema50"
        if _is_short_term_profile(profile):
            timeframe_meta["trigger_timeframes"] = ["30m", "15m"]
            timeframe_meta["entry_reference_timeframes"] = ["15m", "30m", "1h", "4h", "daily"]
            timeframe_meta["stop_reference_timeframe"] = "15m"
            invalidation_source = "short_term_short_reclaim_above_15m_ema50"
        if derivatives is not None:
            timeframe_meta["derivatives"] = {
                "crowding_bias": derivatives_features["crowding_bias"],
                "basis_bps": derivatives_features["basis_bps"],
            }

        liquidity_meta = dict(_liquidity_meta(universe_row, canonical_symbol))
        liquidity_meta.setdefault(
            "liquidity_tier",
            _optional_payload_string(payload, canonical_symbol, "liquidity_tier"),
        )
        liquidity_meta["volume_usdt_24h"] = _to_float(daily.get("volume_usdt_24h"))

        candidates.append(
            EngineCandidate(
                engine="short",
                setup_type=setup_type,
                symbol=canonical_symbol,
                side="SHORT",
                score=total_score,
                stop_loss=stop_loss,
                invalidation_source=invalidation_source,
                timeframe_meta=timeframe_meta,
                sector=sector,
                liquidity_meta=liquidity_meta,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
