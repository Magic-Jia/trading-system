import pytest

from trading_system.app.config import AppConfig, DEFAULT_CONFIG
from trading_system.app import config as config_module
from trading_system.app import main as main_module
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


def test_v2_app_config_reads_env_overrides_at_instantiation(monkeypatch):
    monkeypatch.setenv("TRADING_DEFAULT_RISK_PCT", "0.02")
    monkeypatch.setenv("TRADING_MAX_OPEN_POSITIONS", "11")

    config = AppConfig()

    assert config.risk.default_risk_pct == 0.02
    assert config.risk.max_open_positions == 11


def test_v2_default_config_keeps_import_time_values_even_if_env_changes(monkeypatch):
    baseline_default_risk_pct = DEFAULT_CONFIG.risk.default_risk_pct

    monkeypatch.setenv("TRADING_DEFAULT_RISK_PCT", "0.07")

    assert DEFAULT_CONFIG.risk.default_risk_pct == baseline_default_risk_pct


def test_v2_build_config_reads_env_overrides_at_call_time(monkeypatch):
    monkeypatch.setenv("TRADING_DEFAULT_RISK_PCT", "0.09")
    monkeypatch.setenv("TRADING_MAX_OPEN_POSITIONS", "13")

    config = config_module.build_config()

    assert config.risk.default_risk_pct == 0.09
    assert config.risk.max_open_positions == 13


def test_v2_main_uses_runtime_config_loader(monkeypatch):
    sentinel_config = object()
    seen: dict[str, object] = {}

    monkeypatch.setattr(main_module, "load_config", lambda: sentinel_config, raising=False)

    def fake_build_state_store(config):
        seen["config"] = config
        raise RuntimeError("stop-after-config-load")

    monkeypatch.setattr(main_module, "build_state_store", fake_build_state_store)

    with pytest.raises(RuntimeError, match="stop-after-config-load"):
        main_module.main()

    assert seen["config"] is sentinel_config


def test_v2_allocation_decision_normalizes_status_to_uppercase():
    decision = AllocationDecision(status="accepted")
    assert decision.status == "ACCEPTED"


def test_v2_allocation_decision_rejects_unknown_status():
    with pytest.raises(ValueError, match="status"):
        AllocationDecision(status="PENDING")
