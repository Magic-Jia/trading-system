from trading_system.app.risk.regime_risk import scaled_risk_budget


def test_scaled_risk_budget_respects_engine_tier_and_regime_confidence():
    budget = scaled_risk_budget(base_risk_pct=0.008, regime_multiplier=0.5, confidence=0.4)
    assert budget < 0.008
