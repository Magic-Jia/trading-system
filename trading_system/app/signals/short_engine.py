from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from trading_system.app.market_regime.derivatives import symbol_derivatives_features
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


def _tf_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


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
    return "short" in {str(rule).lower().strip() for rule in rules}


def _short_enabled(regime: RegimeSnapshot | Mapping[str, Any] | None) -> bool:
    if regime is None:
        return False
    label = str(_regime_value(regime, "label", "")).upper().strip()
    if label in _DEFENSIVE_REGIMES:
        return True
    bucket_targets = _regime_value(regime, "bucket_targets", {})
    if isinstance(bucket_targets, Mapping):
        return _to_float(bucket_targets.get("short")) >= 0.2
    return False


def _short_symbols(short_universe: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    if short_universe is None:
        return rows
    for row in short_universe:
        symbol = str(row.get("symbol", "")).upper().strip()
        if symbol:
            rows[symbol] = row
    return rows


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


def _liquidity_quality(payload: Mapping[str, Any], universe_row: Mapping[str, Any]) -> float:
    daily = _tf_row(payload, "daily")
    volume = _to_float(daily.get("volume_usdt_24h"))
    rolling_notional = _to_float(dict(universe_row.get("liquidity_meta", {})).get("rolling_notional"))
    return min(max(volume, rolling_notional) / 10_000_000_000.0, 1.0)


def _setup_type(payload: Mapping[str, Any]) -> str:
    h4 = _tf_row(payload, "4h")
    h1 = _tf_row(payload, "1h")
    if _to_float(h4.get("return_pct_3d")) <= -0.02 and _to_float(h1.get("return_pct_24h")) <= -0.008:
        return "BREAKDOWN_SHORT"
    return "FAILED_BOUNCE_SHORT"


def _short_stop_loss(payload: Mapping[str, Any]) -> float:
    h4 = _tf_row(payload, "4h")
    daily = _tf_row(payload, "daily")
    entry_reference = _to_float(daily.get("close")) or _to_float(h4.get("close"))
    stop_loss = _to_float(h4.get("ema_50"))
    if entry_reference <= 0 or stop_loss <= 0 or stop_loss <= entry_reference:
        return 0.0
    return stop_loss


def _reject_crowded_short_squeeze_risk(features: Mapping[str, Any]) -> bool:
    return (
        str(features.get("crowding_bias", "balanced")) == "crowded_short"
        and _to_float(features.get("basis_bps")) <= _CROWDED_SHORT_BASIS_BPS
    )


def generate_short_candidates(
    market_context: Mapping[str, Any],
    *,
    short_universe: Sequence[Mapping[str, Any]] | None = None,
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None = None,
    regime: RegimeSnapshot | Mapping[str, Any] | None = None,
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
    for symbol, universe_row in eligible.items():
        payload_value = symbols.get(symbol)
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        if str(payload.get("sector", "")).lower() != "majors":
            continue
        if not _trend_broken(payload):
            continue

        derivatives_features = symbol_derivatives_features(derivatives, str(symbol))
        if _reject_crowded_short_squeeze_risk(derivatives_features):
            continue

        scored = score_short_candidate(
            {
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "momentum_quality": _momentum_quality(payload),
                "liquidity_quality": _liquidity_quality(payload, universe_row),
            }
        )
        total_score = _to_float(scored.get("total"))
        if total_score < _SHORT_SCORE_FLOOR:
            continue

        stop_loss = _short_stop_loss(payload)
        if stop_loss <= 0.0:
            continue

        daily = _tf_row(payload, "daily")
        liquidity_meta = dict(universe_row.get("liquidity_meta", {})) if isinstance(universe_row, Mapping) else {}
        liquidity_meta.setdefault("liquidity_tier", payload.get("liquidity_tier"))
        liquidity_meta["volume_usdt_24h"] = _to_float(daily.get("volume_usdt_24h"))

        candidates.append(
            EngineCandidate(
                engine="short",
                setup_type=_setup_type(payload),
                symbol=symbol,
                side="SHORT",
                score=total_score,
                stop_loss=stop_loss,
                invalidation_source="short_structure_reclaim_above_4h_ema50",
                timeframe_meta={
                    "daily_bias": "down",
                    "h4_structure": "breakdown",
                    "h1_trigger": "confirmed",
                    "score_components": scored.get("components", {}),
                },
                sector=str(payload.get("sector") or universe_row.get("sector") or ""),
                liquidity_meta=liquidity_meta,
            )
        )

    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.symbol))
