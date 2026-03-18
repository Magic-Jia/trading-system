from __future__ import annotations

from typing import Any, Mapping

TREND_SCORE_WEIGHTS: dict[str, float] = {
    "timeframe_alignment": 0.50,
    "h4_structure": 0.20,
    "h1_trigger": 0.15,
    "volume_quality": 0.15,
}


def _normalized_flag(value: Any, positive_values: set[str]) -> float:
    return 1.0 if str(value).lower() in positive_values else 0.0


def _bounded_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def score_trend_candidate(features: Mapping[str, Any]) -> dict[str, Any]:
    alignment_raw = (
        _normalized_flag(features.get("daily_bias"), {"up"})
        * _normalized_flag(features.get("h4_structure"), {"intact"})
        * _normalized_flag(features.get("h1_trigger"), {"confirmed"})
    )
    h4_structure_raw = _normalized_flag(features.get("h4_structure"), {"intact"})
    h1_trigger_raw = _normalized_flag(features.get("h1_trigger"), {"confirmed"})
    volume_quality_raw = _bounded_float(features.get("volume_quality"))

    components = {
        "timeframe_alignment": alignment_raw * TREND_SCORE_WEIGHTS["timeframe_alignment"],
        "h4_structure": h4_structure_raw * TREND_SCORE_WEIGHTS["h4_structure"],
        "h1_trigger": h1_trigger_raw * TREND_SCORE_WEIGHTS["h1_trigger"],
        "volume_quality": volume_quality_raw * TREND_SCORE_WEIGHTS["volume_quality"],
    }
    total = sum(components.values())
    return {"total": total, "components": components}
