from __future__ import annotations


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def scaled_risk_budget(
    *,
    base_risk_pct: float,
    regime_multiplier: float = 1.0,
    confidence: float = 1.0,
    engine_tier_multiplier: float = 1.0,
) -> float:
    if base_risk_pct <= 0:
        return 0.0

    safe_regime_multiplier = max(regime_multiplier, 0.0)
    safe_engine_tier_multiplier = max(engine_tier_multiplier, 0.0)
    confidence_scale = 0.5 + (0.5 * _clamp(confidence, 0.0, 1.0))

    return base_risk_pct * safe_regime_multiplier * safe_engine_tier_multiplier * confidence_scale
