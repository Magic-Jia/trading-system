from trading_system.app.risk.regime_risk import scaled_risk_budget
from trading_system.app.portfolio.exposure import exposure_snapshot
from trading_system.app.portfolio.allocator import allocate_candidates


def test_scaled_risk_budget_respects_engine_tier_and_regime_confidence():
    budget = scaled_risk_budget(base_risk_pct=0.008, regime_multiplier=0.5, confidence=0.4)
    assert budget < 0.008


def test_exposure_snapshot_summarizes_sector_and_direction(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    snapshot = exposure_snapshot(account)
    assert "net_long_notional" in snapshot
    assert "sector_risk" in snapshot


def test_allocator_rejects_candidates_from_suppressed_bucket(load_fixture, sample_rotation_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"suppressed_engines": ["rotation"], "bucket_targets": {"trend": 1.0, "rotation": 0.0, "short": 0.0}}
    decisions = allocate_candidates(account=account, candidates=sample_rotation_candidates, regime=regime)
    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("suppressed" in " ".join(d.reasons).lower() for d in decisions)


def test_allocator_downweights_duplicate_trend_breakouts(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.6, "rotation": 0.2, "short": 0.2}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    assert any(d.status in {"ACCEPTED", "DOWNSIZED"} for d in decisions)
    assert any(d.status == "DOWNSIZED" for d in decisions[1:])


def test_allocator_respects_total_active_risk_cap(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.9, "rotation": 0.1, "short": 0.0}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted_risk = sum(d.final_risk_budget for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"})
    assert accepted_risk <= decisions[0].meta["portfolio_total_risk_cap"]


def test_allocator_respects_net_exposure_and_major_alt_balance(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.5, "rotation": 0.0, "short": 0.0}, "suppressed_engines": ["rotation", "short"]}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted = [d for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"}]
    assert all(d.engine == "trend" for d in accepted)
    assert all(d.meta["net_exposure_after"] <= d.meta["net_exposure_cap"] for d in accepted)
    assert all(d.meta["major_alt_balance_ok"] is True for d in accepted)


def test_allocator_enforces_symbol_and_sector_caps(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any(d.meta.get("symbol_cap_checked") for d in decisions)
    assert any(d.meta.get("sector_cap_checked") for d in decisions)
    assert any(d.status == "REJECTED" and (d.meta.get("symbol_cap_hit") or d.meta.get("sector_cap_hit")) for d in decisions)


def test_allocator_checks_conflict_against_existing_exposure(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any("existing exposure" in " ".join(d.reasons).lower() or d.meta.get("conflict_checked") for d in decisions)
