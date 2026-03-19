import json
from dataclasses import replace
from pathlib import Path

import pytest

from trading_system.app.config import AppConfig, DEFAULT_CONFIG
from trading_system.app import config as config_module
from trading_system.app import main as main_module
from trading_system.app.storage.state_store import build_state_store
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


def test_state_store_persists_regime_candidates_and_allocations(tmp_path):
    config = replace(DEFAULT_CONFIG, state_file=tmp_path / "runtime_state.json")
    store = build_state_store(config)
    state = store.load()
    state.latest_regime = {"label": "RISK_ON_TREND", "confidence": 0.8}
    state.latest_universes = {"major_count": 4, "rotation_count": 6, "short_count": 2}
    state.latest_candidates = [{"symbol": "BTCUSDT", "engine": "trend", "score": 0.91}]
    state.latest_allocations = [{"symbol": "BTCUSDT", "status": "ACCEPTED"}]
    state.latest_lifecycle = {"BTCUSDT": {"state": "PROTECT", "reason_codes": ["payload_to_protect_trend_mature"]}}
    state.rotation_summary = {
        "universe_count": 5,
        "candidate_count": 2,
        "accepted_symbols": ["SOLUSDT"],
        "executed_symbols": ["SOLUSDT"],
        "leaders": [{"symbol": "SOLUSDT", "score": 0.81}],
    }
    store.save(state)
    reloaded = store.load()
    assert reloaded.latest_regime["label"] == "RISK_ON_TREND"
    assert reloaded.latest_universes["rotation_count"] == 6
    assert reloaded.latest_candidates[0]["symbol"] == "BTCUSDT"
    assert reloaded.latest_allocations[0]["symbol"] == "BTCUSDT"
    assert reloaded.latest_lifecycle["BTCUSDT"]["state"] == "PROTECT"
    assert reloaded.rotation_summary["candidate_count"] == 2
    assert reloaded.rotation_summary["leaders"][0]["symbol"] == "SOLUSDT"


def test_main_v2_cycle_writes_regime_and_allocation_sections(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    assert state["latest_regime"]["label"]
    assert "latest_universes" in state
    assert "latest_candidates" in state
    assert "latest_allocations" in state
    assert state.get("partial_v2_coverage") is True
    assert state.get("rotation_candidates")
    assert {row["engine"] for row in state["rotation_candidates"]} == {"rotation"}
    assert state.get("rotation_summary") == {
        "universe_count": 5,
        "candidate_count": 3,
        "accepted_symbols": ["LINKUSDT", "SOLUSDT"],
        "executed_symbols": ["LINKUSDT", "SOLUSDT"],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "score": pytest.approx(0.829508, abs=1e-6),
                "daily_spread": pytest.approx(0.0175, abs=1e-6),
                "h4_spread": pytest.approx(0.006, abs=1e-6),
                "h1_spread": pytest.approx(0.0015, abs=1e-6),
                "volume_usdt_24h": 3900000000.0,
                "slippage_bps": 8.0,
            },
            {
                "symbol": "LINKUSDT",
                "score": pytest.approx(0.76898, abs=1e-6),
                "daily_spread": pytest.approx(0.0055, abs=1e-6),
                "h4_spread": pytest.approx(-0.001, abs=1e-6),
                "h1_spread": pytest.approx(-0.0015, abs=1e-6),
                "volume_usdt_24h": 1010000000.0,
                "slippage_bps": 8.0,
            },
            {
                "symbol": "ADAUSDT",
                "score": pytest.approx(0.707739, abs=1e-6),
                "daily_spread": pytest.approx(-0.0095, abs=1e-6),
                "h4_spread": pytest.approx(-0.006, abs=1e-6),
                "h1_spread": pytest.approx(-0.0025, abs=1e-6),
                "volume_usdt_24h": 920000000.0,
                "slippage_bps": 8.0,
            },
        ],
    }
    assert state.get("short_candidates", []) == []


def test_main_v2_stdout_surfaces_rotation_reporting(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()
    printed = capsys.readouterr().out
    payload = json.loads(printed)

    assert payload["regime"]["rotation"]["universe_count"] == 5
    assert payload["regime"]["rotation"]["candidate_count"] == 3
    assert payload["regime"]["rotation"]["accepted_symbols"] == ["LINKUSDT", "SOLUSDT"]
    assert payload["regime"]["rotation"]["executed_symbols"] == ["LINKUSDT", "SOLUSDT"]
    assert [row["symbol"] for row in payload["regime"]["rotation"]["leaders"]] == ["SOLUSDT", "LINKUSDT", "ADAUSDT"]


def test_main_v2_cycle_is_idempotent_for_same_inputs(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()
    first = json.loads(Path(output_path).read_text())
    first_output = json.loads(capsys.readouterr().out)
    main_module.main()
    second = json.loads(Path(output_path).read_text())
    second_output = json.loads(capsys.readouterr().out)
    assert first.get("last_signal_ids") == second.get("last_signal_ids")
    assert first.get("latest_allocations") == second.get("latest_allocations")
    assert first.get("rotation_summary") == second.get("rotation_summary")
    assert first_output["regime"]["rotation"] == second_output["regime"]["rotation"]
