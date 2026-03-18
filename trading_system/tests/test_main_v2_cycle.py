from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.types import RegimeSnapshot, EngineCandidate, AllocationDecision, LifecycleState


def test_v2_config_exposes_regime_universe_allocator_sections():
    assert hasattr(DEFAULT_CONFIG, "regime")
    assert hasattr(DEFAULT_CONFIG, "universe")
    assert hasattr(DEFAULT_CONFIG, "allocator")
    assert hasattr(DEFAULT_CONFIG, "lifecycle")


def test_v2_allocator_config_exposes_short_bucket_placeholder():
    allocator = DEFAULT_CONFIG.allocator
    assert hasattr(allocator, "trend_bucket_weight")
    assert hasattr(allocator, "rotation_bucket_weight")
    assert hasattr(allocator, "short_bucket_weight")


def test_v2_types_are_importable():
    assert RegimeSnapshot
    assert EngineCandidate
    assert AllocationDecision
    assert LifecycleState


def test_v2_allocation_decision_supports_uppercase_status_and_allocator_task_fields():
    decision = AllocationDecision(
        status="ACCEPTED",
        engine="trend",
        reasons=["score_ok", "risk_ok"],
        meta={"source": "test"},
    )
    assert decision.status == "ACCEPTED"
    assert decision.engine == "trend"
    assert decision.reasons == ["score_ok", "risk_ok"]
    assert decision.meta == {"source": "test"}
