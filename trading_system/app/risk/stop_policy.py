from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class StopPolicy:
    stop_loss: float
    stop_family: str
    stop_reference: str
    invalidation_source: str
    invalidation_reason: str


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


def _regime_label(regime: Mapping[str, Any] | None) -> str:
    if not isinstance(regime, Mapping):
        return ""
    return str(regime.get("label", "")).strip().upper()


def _long_policy(
    payload: Mapping[str, Any],
    *,
    anchor: float,
    stop_family: str,
    stop_reference: str,
    invalidation_source: str,
    invalidation_reason: str,
) -> StopPolicy | None:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    entry_reference = _to_float(daily.get("close")) or _to_float(h4.get("close"))
    if entry_reference <= 0.0 or anchor <= 0.0 or anchor >= entry_reference:
        return None
    return StopPolicy(
        stop_loss=anchor,
        stop_family=stop_family,
        stop_reference=stop_reference,
        invalidation_source=invalidation_source,
        invalidation_reason=invalidation_reason,
    )


def _short_policy(
    payload: Mapping[str, Any],
    *,
    anchor: float,
    stop_family: str,
    stop_reference: str,
    invalidation_source: str,
    invalidation_reason: str,
) -> StopPolicy | None:
    daily = _tf_row(payload, "daily")
    h4 = _tf_row(payload, "4h")
    entry_reference = _to_float(daily.get("close")) or _to_float(h4.get("close"))
    if entry_reference <= 0.0 or anchor <= 0.0 or anchor <= entry_reference:
        return None
    return StopPolicy(
        stop_loss=anchor,
        stop_family=stop_family,
        stop_reference=stop_reference,
        invalidation_source=invalidation_source,
        invalidation_reason=invalidation_reason,
    )


def _crash_defensive_long_policy(payload: Mapping[str, Any]) -> StopPolicy | None:
    daily = _tf_row(payload, "daily")
    h1 = _tf_row(payload, "1h")
    close = _to_float(daily.get("close")) or _to_float(h1.get("close"))
    atr_pct = _to_float(daily.get("atr_pct"))
    atr_band = close * max(1.0 - atr_pct, 0.0) if close > 0.0 and atr_pct > 0.0 else 0.0
    anchor = max(_to_float(h1.get("ema_20")), atr_band)
    return _long_policy(
        payload,
        anchor=anchor,
        stop_family="squeeze_stop",
        stop_reference="1h_ema20_or_1d_atr_band",
        invalidation_source="crash_defensive_squeeze_loss_below_1h_ema20_or_1d_atr_band",
        invalidation_reason="crash-defensive regime keeps long exposure on a tight squeeze stop",
    )


def build_stop_policy(
    payload: Mapping[str, Any],
    *,
    engine: str,
    setup_type: str,
    side: str,
    regime: Mapping[str, Any] | None = None,
) -> StopPolicy | None:
    engine_key = str(engine).strip().lower()
    setup_key = str(setup_type).strip().upper()
    side_key = str(side).strip().upper()

    if side_key == "LONG" and _regime_label(regime) == "CRASH_DEFENSIVE":
        return _crash_defensive_long_policy(payload)

    if engine_key == "trend" and side_key == "LONG":
        h4 = _tf_row(payload, "4h")
        if setup_key == "BREAKOUT_CONTINUATION":
            return _long_policy(
                payload,
                anchor=_to_float(h4.get("ema_20")),
                stop_family="structure_stop",
                stop_reference="4h_ema20",
                invalidation_source="trend_breakout_failure_below_4h_ema20",
                invalidation_reason="breakout continuation lost 4h breakout support",
            )
        return _long_policy(
            payload,
            anchor=_to_float(h4.get("ema_50")),
            stop_family="structure_stop",
            stop_reference="4h_ema50",
            invalidation_source="trend_structure_loss_below_4h_ema50",
            invalidation_reason="trend continuation lost the 4h structure floor",
        )

    if engine_key == "rotation" and side_key == "LONG":
        h1 = _tf_row(payload, "1h")
        return _long_policy(
            payload,
            anchor=_to_float(h1.get("ema_50")),
            stop_family="failure_stop",
            stop_reference="1h_ema50",
            invalidation_source="rotation_pullback_failure_below_1h_ema50",
            invalidation_reason="rotation leadership failed on the 1h pullback structure",
        )

    if engine_key == "short" and side_key == "SHORT":
        h4 = _tf_row(payload, "4h")
        return _short_policy(
            payload,
            anchor=_to_float(h4.get("ema_50")),
            stop_family="structure_stop",
            stop_reference="4h_ema50",
            invalidation_source="short_structure_reclaim_above_4h_ema50",
            invalidation_reason="short thesis is invalid once price reclaims the 4h structure floor",
        )

    return None
