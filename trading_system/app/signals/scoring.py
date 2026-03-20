from __future__ import annotations

from typing import Any, Mapping

TREND_SCORE_WEIGHTS: dict[str, float] = {
    "timeframe_alignment": 0.50,
    "h4_structure": 0.20,
    "h1_trigger": 0.15,
    "volume_quality": 0.15,
}

ROTATION_SCORE_WEIGHTS: dict[str, float] = {
    "relative_strength_rank": 0.35,
    "persistence": 0.25,
    "pullback_quality": 0.20,
    "liquidity_quality": 0.10,
    "volatility_quality": 0.10,
}

SHORT_SCORE_WEIGHTS: dict[str, float] = {
    "timeframe_alignment": 0.40,
    "h4_structure": 0.20,
    "h1_trigger": 0.15,
    "momentum_quality": 0.15,
    "liquidity_quality": 0.10,
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


def score_rotation_candidate(features: Mapping[str, Any]) -> dict[str, Any]:
    components = {
        "relative_strength_rank": _bounded_float(features.get("relative_strength_rank"))
        * ROTATION_SCORE_WEIGHTS["relative_strength_rank"],
        "persistence": _bounded_float(features.get("persistence")) * ROTATION_SCORE_WEIGHTS["persistence"],
        "pullback_quality": _bounded_float(features.get("pullback_quality"))
        * ROTATION_SCORE_WEIGHTS["pullback_quality"],
        "liquidity_quality": _bounded_float(features.get("liquidity_quality"))
        * ROTATION_SCORE_WEIGHTS["liquidity_quality"],
        "volatility_quality": _bounded_float(features.get("volatility_quality"))
        * ROTATION_SCORE_WEIGHTS["volatility_quality"],
    }
    total = sum(components.values())
    return {"total": total, "components": components}


def score_short_candidate(features: Mapping[str, Any]) -> dict[str, Any]:
    alignment_raw = (
        _normalized_flag(features.get("daily_bias"), {"down"})
        * _normalized_flag(features.get("h4_structure"), {"breakdown"})
        * _normalized_flag(features.get("h1_trigger"), {"confirmed"})
    )
    h4_structure_raw = _normalized_flag(features.get("h4_structure"), {"breakdown"})
    h1_trigger_raw = _normalized_flag(features.get("h1_trigger"), {"confirmed"})
    momentum_quality_raw = _bounded_float(features.get("momentum_quality"))
    liquidity_quality_raw = _bounded_float(features.get("liquidity_quality"))

    components = {
        "timeframe_alignment": alignment_raw * SHORT_SCORE_WEIGHTS["timeframe_alignment"],
        "h4_structure": h4_structure_raw * SHORT_SCORE_WEIGHTS["h4_structure"],
        "h1_trigger": h1_trigger_raw * SHORT_SCORE_WEIGHTS["h1_trigger"],
        "momentum_quality": momentum_quality_raw * SHORT_SCORE_WEIGHTS["momentum_quality"],
        "liquidity_quality": liquidity_quality_raw * SHORT_SCORE_WEIGHTS["liquidity_quality"],
    }
    total = sum(components.values())
    return {"total": total, "components": components}
