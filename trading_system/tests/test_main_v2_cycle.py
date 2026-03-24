import json
from dataclasses import replace
from pathlib import Path

import pytest

from trading_system.app.config import AppConfig, DEFAULT_CONFIG
from trading_system.app import config as config_module
from trading_system.app import main as main_module
from trading_system.app.storage.state_store import build_state_store
from trading_system.app.types import RegimeSnapshot, EngineCandidate, AllocationDecision, LifecycleState
from trading_system.app.risk.validator import ValidationResult


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


def test_v2_config_exposes_execution_mode_controls():
    assert hasattr(DEFAULT_CONFIG, "execution")
    assert DEFAULT_CONFIG.execution.mode in {"paper", "dry-run", "live"}
    assert hasattr(DEFAULT_CONFIG.execution, "allow_live_execution")


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


def test_v2_build_config_reads_execution_mode_overrides(monkeypatch):
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setenv("TRADING_ALLOW_LIVE_EXECUTION", "1")

    config = config_module.build_config()

    assert config.execution.mode == "dry-run"
    assert config.execution.allow_live_execution is True


def test_v2_main_uses_runtime_config_loader(monkeypatch):
    sentinel_config = replace(DEFAULT_CONFIG, execution=replace(DEFAULT_CONFIG.execution, mode="paper"))
    seen: dict[str, object] = {}

    monkeypatch.setattr(main_module, "load_config", lambda: sentinel_config, raising=False)

    def fake_build_state_store(config):
        seen["config"] = config
        raise RuntimeError("stop-after-config-load")

    monkeypatch.setattr(main_module, "build_state_store", fake_build_state_store)

    with pytest.raises(RuntimeError, match="stop-after-config-load"):
        main_module.main()

    assert seen["config"] is sentinel_config


def test_v2_main_rejects_live_execution_without_explicit_allow(monkeypatch):
    config = replace(DEFAULT_CONFIG, execution=replace(DEFAULT_CONFIG.execution, mode="live", allow_live_execution=False))
    monkeypatch.setattr(main_module, "load_config", lambda: config, raising=False)

    with pytest.raises(RuntimeError, match="live execution is disabled"):
        main_module.main()


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
    state.lifecycle_summary = {
        "tracked_count": 1,
        "state_counts": {"INIT": 0, "CONFIRM": 0, "PAYLOAD": 0, "PROTECT": 1, "EXIT": 0},
        "pending_confirmation_symbols": [],
        "protected_symbols": ["BTCUSDT"],
        "exit_symbols": [],
        "attention_symbols": [],
        "leaders": [{"symbol": "BTCUSDT", "state": "PROTECT", "r_multiple": 1.7, "reason_codes": ["payload_to_protect_trend_mature"]}],
    }
    state.rotation_summary = {
        "universe_count": 5,
        "candidate_count": 2,
        "accepted_symbols": ["SOLUSDT"],
        "executed_symbols": ["SOLUSDT"],
        "leaders": [{"symbol": "SOLUSDT", "score": 0.81}],
    }
    state.short_summary = {
        "universe_count": 2,
        "candidate_count": 1,
        "accepted_symbols": ["BTCUSDT"],
        "deferred_execution_symbols": ["BTCUSDT"],
        "leaders": [{"symbol": "BTCUSDT", "score": 0.79}],
    }
    store.save(state)
    reloaded = store.load()
    assert reloaded.latest_regime["label"] == "RISK_ON_TREND"
    assert reloaded.latest_universes["rotation_count"] == 6
    assert reloaded.latest_candidates[0]["symbol"] == "BTCUSDT"
    assert reloaded.latest_allocations[0]["symbol"] == "BTCUSDT"
    assert reloaded.latest_lifecycle["BTCUSDT"]["state"] == "PROTECT"
    assert reloaded.lifecycle_summary["protected_symbols"] == ["BTCUSDT"]
    assert reloaded.rotation_summary["candidate_count"] == 2
    assert reloaded.rotation_summary["leaders"][0]["symbol"] == "SOLUSDT"
    assert reloaded.short_summary["candidate_count"] == 1
    assert reloaded.short_summary["deferred_execution_symbols"] == ["BTCUSDT"]


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
    assert state.get("lifecycle_summary") == {
        "tracked_count": 4,
        "state_counts": {
            "INIT": 4,
            "CONFIRM": 0,
            "PAYLOAD": 0,
            "PROTECT": 0,
            "EXIT": 0,
        },
        "pending_confirmation_symbols": ["BTCUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT"],
        "protected_symbols": [],
        "exit_symbols": [],
        "attention_symbols": ["ETHUSDT"],
        "leaders": [
            {
                "symbol": "ETHUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.02648, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "INIT",
                "r_multiple": 0.0,
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "LINKUSDT",
                "state": "INIT",
                "r_multiple": 0.0,
                "reason_codes": ["init_waiting_confirmation"],
            },
        ],
    }
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
    assert payload["portfolio"]["lifecycle_summary"] == {
        "tracked_count": 4,
        "state_counts": {
            "INIT": 4,
            "CONFIRM": 0,
            "PAYLOAD": 0,
            "PROTECT": 0,
            "EXIT": 0,
        },
        "pending_confirmation_symbols": ["BTCUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT"],
        "protected_symbols": [],
        "exit_symbols": [],
        "attention_symbols": ["ETHUSDT"],
        "leaders": [
            {
                "symbol": "ETHUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.02648, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "INIT",
                "r_multiple": 0.0,
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "LINKUSDT",
                "state": "INIT",
                "r_multiple": 0.0,
                "reason_codes": ["init_waiting_confirmation"],
            },
        ],
    }


def test_main_v2_cycle_persists_short_candidates_without_enabling_short_execution(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setattr(
        main_module,
        "generate_short_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDT",
                side="SHORT",
                score=0.81,
                timeframe_meta={"daily_bias": "down", "h4_structure": "breakdown", "h1_trigger": "confirmed"},
                sector="majors",
                liquidity_meta={"volume_usdt_24h": 12_500_000_000.0},
            )
        ],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    assert len(state["short_candidates"]) == 1
    assert state["short_candidates"][0]["engine"] == "short"
    assert state["short_candidates"][0]["symbol"] == "BTCUSDT"
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 1,
        "accepted_symbols": ["BTCUSDT"],
        "deferred_execution_symbols": ["BTCUSDT"],
        "leaders": [
            {
                "symbol": "BTCUSDT",
                "setup_type": "BREAKDOWN_SHORT",
                "score": 0.81,
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "volume_usdt_24h": 12500000000.0,
                "liquidity_tier": "",
            }
        ],
    }

    short_allocations = [row for row in state["latest_allocations"] if row["engine"] == "short"]
    assert len(short_allocations) == 1
    assert short_allocations[0]["execution"] == {"status": "SKIPPED", "reason": "short_execution_not_enabled"}


def test_main_v2_stdout_surfaces_short_reporting(monkeypatch, tmp_path, load_fixture, capsys):
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
    monkeypatch.setattr(
        main_module,
        "generate_short_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDT",
                side="SHORT",
                score=0.81,
                timeframe_meta={"daily_bias": "down", "h4_structure": "breakdown", "h1_trigger": "confirmed"},
                sector="majors",
                liquidity_meta={"volume_usdt_24h": 12_500_000_000.0},
            )
        ],
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 1,
        "accepted_symbols": ["BTCUSDT"],
        "deferred_execution_symbols": ["BTCUSDT"],
        "leaders": [
            {
                "symbol": "BTCUSDT",
                "setup_type": "BREAKDOWN_SHORT",
                "score": 0.81,
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "volume_usdt_24h": 12500000000.0,
                "liquidity_tier": "",
            }
        ],
    }


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
    assert first.get("lifecycle_summary") == second.get("lifecycle_summary")
    assert first.get("rotation_summary") == second.get("rotation_summary")
    assert first_output["portfolio"]["lifecycle_summary"] == second_output["portfolio"]["lifecycle_summary"]
    assert first_output["regime"]["rotation"] == second_output["regime"]["rotation"]


def test_main_v2_dry_run_does_not_leave_execution_traces(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    assert state.get("last_signal_ids") == {}
    assert state.get("cooldowns") == {}
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    assert all(row.get("execution", {}).get("status") == "SENT" for row in accepted_allocations if row.get("engine") != "short")


def test_main_v2_live_not_yet_enabled_leaves_no_partial_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    exec_log_path = Path(main_module.__file__).resolve().parents[1] / "data" / "execution_log.jsonl"
    original_exec_log = exec_log_path.read_text(encoding="utf-8") if exec_log_path.exists() else None
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "live")
    monkeypatch.setenv("TRADING_ALLOW_LIVE_EXECUTION", "1")

    with pytest.raises(Exception, match="live 模式尚未启用"):
        main_module.main()

    assert not output_path.exists()
    current_exec_log = exec_log_path.read_text(encoding="utf-8") if exec_log_path.exists() else None
    assert current_exec_log == original_exec_log


def test_main_v2_blocks_invalid_signal_before_execution(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    def bad_signal(*args, **kwargs):
        return main_module.TradeSignal(
            signal_id="bad-stop",
            symbol="BTCUSDT",
            side="LONG",
            entry_price=100.0,
            stop_loss=99.9,
            take_profit=104.0,
            source="strategy",
            timeframe="4h",
            tags=["v2", "trend"],
            meta={"setup_type": "BREAKOUT", "score": 0.9},
        )

    monkeypatch.setattr(main_module, "_candidate_signal", bad_signal)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    blocked = [row for row in accepted_allocations if row.get("execution", {}).get("status") == "BLOCKED"]
    assert blocked
    assert all("止损" in row.get("execution", {}).get("reason", "") for row in blocked)
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())


def test_main_v2_blocks_too_wide_stop_before_execution(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    def wide_stop_signal(*args, **kwargs):
        return main_module.TradeSignal(
            signal_id="wide-stop",
            symbol="BTCUSDT",
            side="LONG",
            entry_price=100.0,
            stop_loss=80.0,
            take_profit=130.0,
            source="strategy",
            timeframe="4h",
            tags=["v2", "trend"],
            meta={"setup_type": "BREAKOUT", "score": 0.9},
        )

    monkeypatch.setattr(main_module, "_candidate_signal", wide_stop_signal)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    blocked = [row for row in accepted_allocations if row.get("execution", {}).get("status") == "BLOCKED"]
    assert blocked
    assert all("止损太宽" in row.get("execution", {}).get("reason", "") for row in blocked)
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())
