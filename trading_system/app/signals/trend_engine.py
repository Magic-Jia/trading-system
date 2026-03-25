from __future__ import annotations

from typing import Any, Mapping

from trading_system.app.market_regime.derivatives import symbol_derivatives_features
from trading_system.app.signals.scoring import score_trend_candidate
from trading_system.app.types import EngineCandidate

_MAJOR_SECTOR = "majors"
_HIGH_LIQUIDITY_TIERS = {"high", "top"}
_CROWDED_LONG_BASIS_BPS = 20.0


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


def _is_uptrend(daily: Mapping[str, Any], h4: Mapping[str, Any], h1: Mapping[str, Any]) -> bool:
    return (
        _to_float(daily.get("close")) > _to_float(daily.get("ema_20")) > _to_float(daily.get("ema_50"))
        and _to_float(h4.get("close")) >= _to_float(h4.get("ema_20")) >= _to_float(h4.get("ema_50"))
        and _to_float(h1.get("close")) >= _to_float(h1.get("ema_20")) >= _to_float(h1.get("ema_50"))
    )


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


def _reject_crowded_long(features: Mapping[str, Any]) -> bool:
    return (
        str(features.get("crowding_bias", "balanced")) == "crowded_long"
        and _to_float(features.get("basis_bps")) >= _CROWDED_LONG_BASIS_BPS
    )


def generate_trend_candidates(
    market_context: Mapping[str, Any],
    *,
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None = None,
    include_high_liquidity_strong_names: bool = True,
) -> list[EngineCandidate]:
    symbols = market_context.get("symbols")
    if not isinstance(symbols, Mapping):
        return []

    candidates: list[EngineCandidate] = []
    for symbol, payload_value in symbols.items():
        if not isinstance(payload_value, Mapping):
            continue
        payload = payload_value
        sector = str(payload.get("sector", ""))
        is_major = sector == _MAJOR_SECTOR
        if not is_major and not include_high_liquidity_strong_names:
            continue
        if not is_major and not _is_high_liquidity_strong_name(payload):
            continue

        daily = _tf_row(payload, "daily")
        h4 = _tf_row(payload, "4h")
        h1 = _tf_row(payload, "1h")
        if not _is_uptrend(daily, h4, h1):
            continue

        derivatives_features = symbol_derivatives_features(derivatives, str(symbol))
        if _reject_crowded_long(derivatives_features):
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
            "daily_bias": "up",
            "h4_structure": "intact",
            "h1_trigger": "confirmed",
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
