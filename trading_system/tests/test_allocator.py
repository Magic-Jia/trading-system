from trading_system.app.risk.regime_risk import scaled_risk_budget
from trading_system.app.portfolio.exposure import exposure_snapshot


def test_scaled_risk_budget_respects_engine_tier_and_regime_confidence():
    budget = scaled_risk_budget(base_risk_pct=0.008, regime_multiplier=0.5, confidence=0.4)
    assert budget < 0.008


def test_exposure_snapshot_summarizes_sector_and_direction(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    snapshot = exposure_snapshot(account)
    assert "net_long_notional" in snapshot
    assert "sector_risk" in snapshot
