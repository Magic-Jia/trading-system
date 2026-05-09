import importlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from trading_system.app.config import AppConfig, DEFAULT_CONFIG
from trading_system.app import config as config_module
from trading_system.app import main as main_module
from trading_system.app.signals.entry_profile import ACTIVE_PAPER_ENTRY_PROFILE, CONSERVATIVE_ENTRY_PROFILE
from trading_system.app.storage.state_store import RuntimeStateV2, StateStore, build_state_store
from trading_system.app.types import (
    AccountSnapshot,
    RegimeSnapshot,
    EngineCandidate,
    AllocationDecision,
    LifecycleState,
    PositionSnapshot,
    OrderIntent,
)
from trading_system.app.risk.validator import ValidationResult
from trading_system.app.signals.short_engine import generate_short_candidates as generate_real_short_candidates
from trading_system.app.universe.builder import UniverseBuildResult


def _expected_paper_lifecycle_summary_after_break_even_writeback() -> dict:
    return {
        "tracked_count": 3,
        "state_counts": {
            "INIT": 0,
            "CONFIRM": 3,
            "PAYLOAD": 0,
            "PROTECT": 0,
            "EXIT": 0,
        },
        "pending_confirmation_symbols": [],
        "protected_symbols": [],
        "exit_symbols": [],
        "attention_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "management_action_counts": {"BREAK_EVEN": 3},
        "review_actions": [],
        "audit_target_states": [],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "state": "CONFIRM",
                "r_multiple": pytest.approx(1.883562, abs=1e-6),
                "reason_codes": ["init_to_confirm_confirmed"],
            },
            {
                "symbol": "ETHUSDT",
                "state": "CONFIRM",
                "r_multiple": pytest.approx(1.323988, abs=1e-6),
                "reason_codes": ["init_to_confirm_confirmed"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "CONFIRM",
                "r_multiple": pytest.approx(1.010342, abs=1e-6),
                "reason_codes": ["init_to_confirm_confirmed"],
            },
        ],
    }


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


def test_v2_config_defaults_to_conservative_entry_profile():
    assert DEFAULT_CONFIG.entry_profile == CONSERVATIVE_ENTRY_PROFILE


def test_v2_build_config_reads_entry_profile_override(monkeypatch):
    monkeypatch.setenv("TRADING_ENTRY_PROFILE", "active_paper")

    config = config_module.build_config()

    assert config.entry_profile == ACTIVE_PAPER_ENTRY_PROFILE


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


def test_v2_build_config_reads_disabled_setup_types_overrides(monkeypatch):
    monkeypatch.setenv("TRADING_DISABLED_SETUP_TYPES", " rs_pullback ,RS_PULLBACK, breakdown_short ")

    config = config_module.build_config()

    assert config.execution.disabled_setup_types == ("RS_PULLBACK", "BREAKDOWN_SHORT")


def test_v2_build_config_routes_default_state_file_to_runtime_bucket(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")

    config = config_module.build_config()

    assert config.data_dir == tmp_path / "data"
    assert config.state_file == tmp_path / "data" / "runtime" / "paper" / "testnet" / "runtime_state.json"


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


def test_v2_main_defaults_runtime_paths_to_env_bucket(monkeypatch, tmp_path, load_fixture):
    bucket_dir = tmp_path / "data" / "runtime" / "paper" / "testnet"
    output_path = bucket_dir / "runtime_state.json"
    account_path = bucket_dir / "account_snapshot.json"
    market_path = bucket_dir / "market_context.json"
    deriv_path = bucket_dir / "derivatives_snapshot.json"
    bucket_dir.mkdir(parents=True)
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    seen: dict[str, Path | None] = {}
    real_market_loader = main_module.load_market_context
    real_derivatives_loader = main_module.load_derivatives_snapshot

    def traced_market_loader(path=None):
        seen["market_path"] = Path(path) if path is not None else None
        return real_market_loader(path)

    def traced_derivatives_loader(path=None):
        seen["derivatives_path"] = Path(path) if path is not None else None
        return real_derivatives_loader(path)

    monkeypatch.setattr(main_module, "load_market_context", traced_market_loader)
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", traced_derivatives_loader)
    monkeypatch.setattr(main_module, "ACCOUNT_SNAPSHOT", tmp_path / "should-not-be-used" / "account_snapshot.json")
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")
    monkeypatch.delenv("TRADING_STATE_FILE", raising=False)
    monkeypatch.delenv("TRADING_ACCOUNT_SNAPSHOT_FILE", raising=False)
    monkeypatch.delenv("TRADING_MARKET_CONTEXT_FILE", raising=False)
    monkeypatch.delenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", raising=False)

    main_module.main()

    assert output_path.exists()
    assert seen["market_path"] == market_path
    assert seen["derivatives_path"] == deriv_path


def test_main_v2_cycle_writes_recommendations_and_promotion_artifacts(monkeypatch, tmp_path, load_fixture):
    bucket_dir = tmp_path / "data" / "runtime" / "testnet" / "prod"
    output_path = bucket_dir / "runtime_state.json"
    account_path = bucket_dir / "account_snapshot.json"
    market_path = bucket_dir / "market_context.json"
    deriv_path = bucket_dir / "derivatives_snapshot.json"
    bucket_dir.mkdir(parents=True)
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    monkeypatch.setattr(main_module, "ACCOUNT_SNAPSHOT", tmp_path / "should-not-be-used" / "account_snapshot.json")
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "prod")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT,LINKUSDT")
    monkeypatch.setenv("TRADING_FEISHU_NOTIFICATIONS_ENABLED", "0")
    monkeypatch.delenv("TRADING_STATE_FILE", raising=False)
    monkeypatch.delenv("TRADING_ACCOUNT_SNAPSHOT_FILE", raising=False)
    monkeypatch.delenv("TRADING_MARKET_CONTEXT_FILE", raising=False)
    monkeypatch.delenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", raising=False)

    main_module.main()

    optimization_dir = bucket_dir / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_path = optimization_dir / "promotion_decision.json"
    assert output_path.exists()
    assert recommendations_path.exists()
    assert promotion_path.exists()
    state_payload = json.loads(output_path.read_text())
    summary = state_payload.get("optimization_summary") or {}
    assert summary.get("promotion_decision") == "observe"
    assert "recommendation_count" in summary


def test_v2_main_explicit_file_envs_override_runtime_bucket_defaults(monkeypatch, tmp_path, load_fixture):
    bucket_dir = tmp_path / "data" / "runtime" / "paper" / "testnet"
    override_dir = tmp_path / "override"
    output_path = override_dir / "runtime_state.json"
    account_path = override_dir / "account_snapshot.json"
    market_path = override_dir / "market_context.json"
    deriv_path = override_dir / "derivatives_snapshot.json"
    bucket_dir.mkdir(parents=True)
    override_dir.mkdir(parents=True)
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    seen: dict[str, Path | None] = {}
    real_market_loader = main_module.load_market_context
    real_derivatives_loader = main_module.load_derivatives_snapshot

    def traced_market_loader(path=None):
        seen["market_path"] = Path(path) if path is not None else None
        return real_market_loader(path)

    def traced_derivatives_loader(path=None):
        seen["derivatives_path"] = Path(path) if path is not None else None
        return real_derivatives_loader(path)

    monkeypatch.setattr(main_module, "load_market_context", traced_market_loader)
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", traced_derivatives_loader)
    monkeypatch.setattr(main_module, "ACCOUNT_SNAPSHOT", tmp_path / "should-not-be-used" / "account_snapshot.json")
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    assert output_path.exists()
    assert seen["market_path"] == market_path
    assert seen["derivatives_path"] == deriv_path
    assert not (bucket_dir / "runtime_state.json").exists()


def test_v2_main_passes_selected_entry_profile_to_long_engines_and_records_it(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    captured: dict[str, object] = {}

    def fake_trend_candidates(*args, entry_profile=None, **kwargs):
        captured["trend_entry_profile"] = entry_profile
        return []

    def fake_rotation_candidates(*args, entry_profile=None, **kwargs):
        captured["rotation_entry_profile"] = entry_profile
        return []

    monkeypatch.setattr(main_module, "generate_trend_candidates", fake_trend_candidates)
    monkeypatch.setattr(main_module, "generate_rotation_candidates", fake_rotation_candidates)
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])
    monkeypatch.setenv("TRADING_ENTRY_PROFILE", "active_paper")
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    state = json.loads(output_path.read_text(encoding="utf-8"))
    assert captured == {
        "trend_entry_profile": ACTIVE_PAPER_ENTRY_PROFILE,
        "rotation_entry_profile": ACTIVE_PAPER_ENTRY_PROFILE,
    }
    assert state["latest_entry_profile"]["name"] == "active_paper"


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
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
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
    assert state.get("rotation_candidates") == []
    assert state.get("lifecycle_summary") == _expected_paper_lifecycle_summary_after_break_even_writeback()
    assert state.get("rotation_summary") == {
        "universe_count": 0,
        "candidate_count": 0,
        "accepted_symbols": [],
        "executed_symbols": [],
        "leaders": [],
    }
    assert [row["symbol"] for row in state.get("short_candidates", [])] == ["BTCUSDT", "ETHUSDT"]
    assert {row["engine"] for row in state["short_candidates"]} == {"short"}


def test_main_v2_cycle_filters_crowded_long_trend_candidates_from_runtime_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    trend_candidates = [row for row in state["latest_candidates"] if row.get("engine") == "trend"]

    assert trend_candidates == []
    assert [row["symbol"] for row in state["latest_candidates"]] == ["BTCUSDT", "ETHUSDT"]
    assert {row["engine"] for row in state["latest_candidates"]} == {"short"}


def test_main_v2_cycle_filters_crowded_long_rotation_candidates_from_runtime_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    rotation_universe = [row["symbol"] for row in state["latest_universes"]["rotation_universe"]]
    rotation_candidates = [row for row in state["latest_candidates"] if row.get("engine") == "rotation"]

    assert rotation_universe == []
    assert rotation_candidates == []
    assert state["rotation_summary"]["candidate_count"] == 0
    assert state["rotation_summary"]["leaders"] == []


def test_main_v2_cycle_surfaces_crash_protection_and_compresses_execution(
    monkeypatch, tmp_path, load_fixture, capsys
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00005,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.12,
                        "mark_price_change_24h_pct": -0.08,
                        "taker_buy_sell_ratio": 0.84,
                        "basis_bps": -18,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00005,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": -0.12,
                        "mark_price_change_24h_pct": -0.08,
                        "taker_buy_sell_ratio": 0.84,
                        "basis_bps": -18,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())
    accepted = [row for row in state["latest_allocations"] if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]

    assert state["latest_regime"]["label"] == "CRASH_DEFENSIVE"
    assert state["latest_regime"]["execution_policy"] == "suppress"
    assert state["latest_regime"]["late_stage_heat"] == "cascade"
    assert state["latest_regime"]["execution_hazard"] == "compress_risk"
    assert payload["regime"]["regime"]["label"] == "CRASH_DEFENSIVE"
    assert payload["regime"]["regime"]["execution_policy"] == "suppress"
    assert payload["regime"]["regime"]["late_stage_heat"] == "cascade"
    assert payload["regime"]["regime"]["execution_hazard"] == "compress_risk"
    assert payload["regime"]["executions"]["count"] == 0
    assert all(row["engine"] == "short" for row in accepted)


def test_main_v2_stdout_surfaces_rotation_reporting(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    main_module.main()
    printed = capsys.readouterr().out
    payload = json.loads(printed)

    assert payload["regime"]["rotation"]["universe_count"] == 0
    assert payload["regime"]["rotation"]["candidate_count"] == 0
    assert payload["regime"]["rotation"]["accepted_symbols"] == []
    assert payload["regime"]["rotation"]["executed_symbols"] == []
    assert payload["regime"]["rotation"]["leaders"] == []
    assert payload["portfolio"]["lifecycle_summary"] == _expected_paper_lifecycle_summary_after_break_even_writeback()


def test_main_v2_stdout_surfaces_trend_b2_absolute_strength_review_notes(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    market = load_fixture("market_context_v2.json")
    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.02
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = 0.008
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = 0.002

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(market))
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": 0.00004,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": 0.01,
                        "mark_price_change_24h_pct": 0.012,
                        "taker_buy_sell_ratio": 1.01,
                        "basis_bps": 12,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": 0.00003,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.009,
                        "mark_price_change_24h_pct": 0.008,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 10,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    trend_report = payload["regime"]["trend"]
    assert trend_report["candidate_count"] == 1
    assert [row["symbol"] for row in trend_report["leaders"]] == ["ETHUSDT"]
    assert [note["symbol"] for note in trend_report["review_notes"]] == ["BTCUSDT"]
    note = trend_report["review_notes"][0]
    assert note["reason"] == "absolute_strength_floor"
    assert note["setup_type"] == "PULLBACK_CONTINUATION"
    assert note["daily_return_pct_7d"] == 0.02
    assert "absolute strength" in note["message"]


def test_main_v2_cycle_persists_trend_b2_review_notes_in_runtime_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    market = load_fixture("market_context_v2.json")
    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.02
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = 0.008
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = 0.002

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(market))
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": 0.00004,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": 0.01,
                        "mark_price_change_24h_pct": 0.012,
                        "taker_buy_sell_ratio": 1.01,
                        "basis_bps": 12,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": 0.00003,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.009,
                        "mark_price_change_24h_pct": 0.008,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 10,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])

    main_module.main()
    state = json.loads(output_path.read_text())

    trend_summary = state["trend_summary"]
    assert trend_summary["candidate_count"] == 1
    assert [row["symbol"] for row in trend_summary["leaders"]] == ["ETHUSDT"]
    assert [note["symbol"] for note in trend_summary["review_notes"]] == ["BTCUSDT"]
    note = trend_summary["review_notes"][0]
    assert note["reason"] == "absolute_strength_floor"
    assert note["setup_type"] == "PULLBACK_CONTINUATION"
    assert note["daily_return_pct_7d"] == 0.02


def test_main_v2_stdout_surfaces_rotation_b2_price_extension_review_notes(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    market = load_fixture("market_context_v2.json")
    market["symbols"]["SOLUSDT"]["4h"]["close"] = 155.0
    market["symbols"]["SOLUSDT"]["1h"]["close"] = 153.0
    market["symbols"]["LINKUSDT"]["1h"]["close"] = 24.75

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(market))
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "SOLUSDT",
                        "funding_rate": 0.00004,
                        "open_interest_usdt": 2_900_000_000,
                        "open_interest_change_24h_pct": 0.01,
                        "mark_price_change_24h_pct": 0.018,
                        "taker_buy_sell_ratio": 1.01,
                        "basis_bps": 12,
                    },
                    {
                        "symbol": "LINKUSDT",
                        "funding_rate": 0.00003,
                        "open_interest_usdt": 1_750_000_000,
                        "open_interest_change_24h_pct": 0.009,
                        "mark_price_change_24h_pct": 0.008,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 10,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "build_universes",
        lambda *args, **kwargs: UniverseBuildResult(
            major_universe=[],
            rotation_universe=[
                {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high", "liquidity_meta": {"rolling_notional": 2_900_000_000.0, "slippage_bps": 8.0}},
                {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high", "liquidity_meta": {"rolling_notional": 1_750_000_000.0, "slippage_bps": 8.0}},
            ],
            short_universe=[],
        ),
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    rotation_report = payload["regime"]["rotation"]
    assert rotation_report["candidate_count"] == 1
    assert [row["symbol"] for row in rotation_report["leaders"]] == ["LINKUSDT"]
    assert [note["symbol"] for note in rotation_report["review_notes"]] == ["SOLUSDT"]
    note = rotation_report["review_notes"][0]
    assert note["reason"] == "price_extension_overheat"
    assert note["setup_type"] == "RS_REACCELERATION"
    assert note["h4_extension_pct"] > 0.03
    assert note["h1_extension_pct"] > 0.01
    assert "overheat" in note["message"]


def test_main_v2_stdout_surfaces_trend_b2_funding_basis_blowoff_review_notes(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    market = load_fixture("market_context_v2.json")

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(market))
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": 0.00022,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": 0.01,
                        "mark_price_change_24h_pct": 0.012,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 26,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": 0.00003,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.009,
                        "mark_price_change_24h_pct": 0.008,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 10,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    trend_report = payload["regime"]["trend"]
    assert trend_report["candidate_count"] == 1
    assert [row["symbol"] for row in trend_report["leaders"]] == ["ETHUSDT"]
    assert [note["symbol"] for note in trend_report["review_notes"]] == ["BTCUSDT"]
    note = trend_report["review_notes"][0]
    assert note["reason"] == "funding_basis_blowoff"
    assert note["setup_type"] == "BREAKOUT_CONTINUATION"
    assert note["funding_rate"] == 0.00022
    assert note["basis_bps"] == 26.0
    assert "funding" in note["message"]
    assert "basis" in note["message"]


def test_main_v2_stdout_surfaces_rotation_b2_late_stage_blowoff_review_notes(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    market = load_fixture("market_context_v2.json")
    market["symbols"]["SOLUSDT"]["4h"]["close"] = 155.0
    market["symbols"]["SOLUSDT"]["1h"]["close"] = 153.0
    market["symbols"]["LINKUSDT"]["1h"]["close"] = 24.75

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(market))
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "SOLUSDT",
                        "funding_rate": 0.00005,
                        "open_interest_usdt": 2_900_000_000,
                        "open_interest_change_24h_pct": 0.045,
                        "mark_price_change_24h_pct": 0.024,
                        "taker_buy_sell_ratio": 1.01,
                        "basis_bps": 14,
                    },
                    {
                        "symbol": "LINKUSDT",
                        "funding_rate": 0.00003,
                        "open_interest_usdt": 1_750_000_000,
                        "open_interest_change_24h_pct": 0.009,
                        "mark_price_change_24h_pct": 0.008,
                        "taker_buy_sell_ratio": 1.0,
                        "basis_bps": 10,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "build_universes",
        lambda *args, **kwargs: UniverseBuildResult(
            major_universe=[],
            rotation_universe=[
                {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high", "liquidity_meta": {"rolling_notional": 2_900_000_000.0, "slippage_bps": 8.0}},
                {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high", "liquidity_meta": {"rolling_notional": 1_750_000_000.0, "slippage_bps": 8.0}},
            ],
            short_universe=[],
        ),
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    rotation_report = payload["regime"]["rotation"]
    assert rotation_report["candidate_count"] == 1
    assert [row["symbol"] for row in rotation_report["leaders"]] == ["LINKUSDT"]
    assert [note["symbol"] for note in rotation_report["review_notes"]] == ["SOLUSDT"]
    note = rotation_report["review_notes"][0]
    assert note["reason"] == "late_stage_long_blowoff"
    assert note["setup_type"] == "RS_REACCELERATION"
    assert note["open_interest_change_24h_pct"] == 0.045
    assert note["mark_price_change_24h_pct"] == 0.024
    assert "late-stage" in note["message"]


def _defensive_short_market() -> dict:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {
                    "close": 96000.0,
                    "ema_20": 97500.0,
                    "ema_50": 99000.0,
                    "rsi": 38.0,
                    "atr_pct": 0.041,
                    "return_pct_7d": -0.052,
                    "volume_usdt_24h": 12_500_000_000.0,
                },
                "4h": {
                    "close": 95800.0,
                    "ema_20": 97000.0,
                    "ema_50": 98500.0,
                    "rsi": 36.0,
                    "atr_pct": 0.028,
                    "volume_usdt_24h": 12_500_000_000.0,
                    "return_pct_3d": -0.031,
                },
                "1h": {
                    "close": 95750.0,
                    "ema_20": 96500.0,
                    "ema_50": 97200.0,
                    "rsi": 34.0,
                    "atr_pct": 0.019,
                    "volume_usdt_24h": 12_500_000_000.0,
                    "return_pct_24h": -0.011,
                },
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {
                    "close": 4920.0,
                    "ema_20": 5000.0,
                    "ema_50": 5100.0,
                    "rsi": 40.0,
                    "atr_pct": 0.039,
                    "return_pct_7d": -0.038,
                    "volume_usdt_24h": 6_800_000_000.0,
                },
                "4h": {
                    "close": 4905.0,
                    "ema_20": 4975.0,
                    "ema_50": 5060.0,
                    "rsi": 37.0,
                    "atr_pct": 0.024,
                    "volume_usdt_24h": 6_800_000_000.0,
                    "return_pct_3d": -0.026,
                },
                "1h": {
                    "close": 4898.0,
                    "ema_20": 4940.0,
                    "ema_50": 4988.0,
                    "rsi": 35.0,
                    "atr_pct": 0.018,
                    "volume_usdt_24h": 6_800_000_000.0,
                    "return_pct_24h": -0.008,
                },
            },
        }
    }


def test_main_v2_short_allocations_propagate_explicit_stop_and_invalidation_source(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
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
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
    )

    short_market = _defensive_short_market()
    short_candidates = generate_real_short_candidates(
        short_market,
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
            {"symbol": "ETHUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 6_800_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )
    assert short_candidates
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: short_candidates)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    short_candidate_rows = [row for row in state.get("latest_candidates", []) if row.get("engine") == "short"]
    assert short_candidate_rows
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in short_candidate_rows)
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > float(short_market["symbols"][row["symbol"]]["daily"]["close"]) for row in short_candidate_rows)
    assert all(row.get("invalidation_source") == "short_breakdown_failure_above_4h_ema20" for row in short_candidate_rows)
    assert all(row.get("stop_family") == "structure_stop" for row in short_candidate_rows)
    assert all(row.get("stop_reference") == "4h_ema20" for row in short_candidate_rows)

    accepted_short = [
        row
        for row in state.get("latest_allocations", [])
        if row.get("engine") == "short" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
    ]
    assert accepted_short
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in accepted_short)
    assert all(row.get("invalidation_source") == "short_breakdown_failure_above_4h_ema20" for row in accepted_short)
    assert all(row.get("stop_family") == "structure_stop" for row in accepted_short)
    assert all(row.get("stop_reference") == "4h_ema20" for row in accepted_short)
    assert all(row.get("execution", {}).get("status") == "SKIPPED" for row in accepted_short)
    assert all(row.get("execution", {}).get("reason") == "short_execution_not_enabled" for row in accepted_short)


def test_main_v2_disabled_setup_types_remove_matching_candidates_before_validation_and_summary(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setenv("TRADING_DISABLED_SETUP_TYPES", "rs_pullback")
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="BREAKOUT_CONTINUATION",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                sector="majors",
                timeframe_meta={"entry_tf": "4h"},
                liquidity_meta={"volume_usdt_24h": 12_500_000_000.0},
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "generate_rotation_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="rotation",
                setup_type="RS_PULLBACK",
                symbol="ETHUSDT",
                side="LONG",
                score=0.88,
                sector="alts",
                timeframe_meta={"entry_tf": "4h"},
                liquidity_meta={"volume_usdt_24h": 2_100_000_000.0},
            ),
            EngineCandidate(
                engine="rotation",
                setup_type="RS_REACCELERATION",
                symbol="SOLUSDT",
                side="LONG",
                score=0.91,
                sector="alts",
                timeframe_meta={"entry_tf": "4h"},
                liquidity_meta={"volume_usdt_24h": 3_400_000_000.0},
            ),
        ],
    )
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    assert {(row["engine"], row["setup_type"], row["symbol"]) for row in state["latest_candidates"]} == {
        ("trend", "BREAKOUT_CONTINUATION", "BTCUSDT"),
        ("rotation", "RS_REACCELERATION", "SOLUSDT"),
    }
    assert state["latest_allocations"] == []
    assert state["rotation_summary"]["candidate_count"] == 1
    assert state["rotation_summary"]["leaders"][0]["symbol"] == "SOLUSDT"
    assert [row["symbol"] for row in state["rotation_summary"]["leaders"]] == ["SOLUSDT"]

    filtered_candidates = state["disabled_setup_type_filtered_candidates"]
    assert len(filtered_candidates) == 1
    assert {key: filtered_candidates[0].get(key) for key in ("symbol", "engine", "setup_type", "reason", "disabled_by")} == {
        "symbol": "ETHUSDT",
        "engine": "rotation",
        "setup_type": "RS_PULLBACK",
        "reason": "disabled_setup_type",
        "disabled_by": "RS_PULLBACK",
    }


def test_main_v2_short_runtime_surfaces_setup_specific_stop_and_invalidation_semantics(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
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
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [
            AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1),
            AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.003, rank=2),
        ],
    )
    monkeypatch.setattr(
        main_module,
        "generate_short_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDT",
                side="SHORT",
                score=0.91,
                timeframe_meta={"daily_bias": "down", "h4_structure": "breakdown", "h1_trigger": "confirmed"},
                sector="majors",
                liquidity_meta={"volume_usdt_24h": 12_500_000_000.0},
            ),
            EngineCandidate(
                engine="short",
                setup_type="FAILED_BOUNCE_SHORT",
                symbol="ETHUSDT",
                side="SHORT",
                score=0.83,
                timeframe_meta={"daily_bias": "down", "h4_structure": "failed_bounce", "h1_trigger": "confirmed"},
                sector="majors",
                liquidity_meta={"volume_usdt_24h": 6_800_000_000.0},
            ),
        ],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    short_candidate_rows = {
        row["setup_type"]: row for row in state.get("latest_candidates", []) if row.get("engine") == "short"
    }
    assert short_candidate_rows["BREAKDOWN_SHORT"]["stop_loss"] == pytest.approx(97000.0)
    assert short_candidate_rows["BREAKDOWN_SHORT"]["stop_family"] == "structure_stop"
    assert short_candidate_rows["BREAKDOWN_SHORT"]["stop_reference"] == "4h_ema20"
    assert short_candidate_rows["BREAKDOWN_SHORT"]["invalidation_source"] == "short_breakdown_failure_above_4h_ema20"
    assert short_candidate_rows["BREAKDOWN_SHORT"]["invalidation_reason"] == "breakdown continuation lost 4h breakdown resistance"
    assert short_candidate_rows["FAILED_BOUNCE_SHORT"]["stop_loss"] == pytest.approx(4988.0)
    assert short_candidate_rows["FAILED_BOUNCE_SHORT"]["stop_family"] == "failure_stop"
    assert short_candidate_rows["FAILED_BOUNCE_SHORT"]["stop_reference"] == "1h_ema50"
    assert short_candidate_rows["FAILED_BOUNCE_SHORT"]["invalidation_source"] == "short_failed_bounce_reclaim_above_1h_ema50"
    assert short_candidate_rows["FAILED_BOUNCE_SHORT"]["invalidation_reason"] == "failed-bounce short reclaimed the 1h rejection structure"

    accepted_short = {
        row["setup_type"]: row
        for row in state.get("latest_allocations", [])
        if row.get("engine") == "short" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
    }
    assert accepted_short["BREAKDOWN_SHORT"]["stop_reference"] == "4h_ema20"
    assert accepted_short["BREAKDOWN_SHORT"]["invalidation_source"] == "short_breakdown_failure_above_4h_ema20"
    assert accepted_short["FAILED_BOUNCE_SHORT"]["stop_reference"] == "1h_ema50"
    assert accepted_short["FAILED_BOUNCE_SHORT"]["invalidation_source"] == "short_failed_bounce_reclaim_above_1h_ema50"
    assert all(row.get("execution", {}).get("reason") == "short_execution_not_enabled" for row in accepted_short.values())


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
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [
            {
                "symbol": "BTCUSDT",
                "setup_type": "BREAKDOWN_SHORT",
                "score": 0.81,
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "derivatives": {},
                "volume_usdt_24h": 12500000000.0,
                "liquidity_tier": "",
            }
        ],
    }

    short_allocations = [row for row in state["latest_allocations"] if row["engine"] == "short"]
    assert short_allocations == []


def test_main_v2_short_downsized_cascade_allocation_keeps_same_execution_decision(monkeypatch, tmp_path, load_fixture):
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
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
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
                sector="majors",
                timeframe_meta={"daily_bias": "down", "h4_structure": "breakdown", "h1_trigger": "confirmed"},
                liquidity_meta={"volume_usdt_24h": 12_500_000_000.0},
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda *args, **kwargs: [
            AllocationDecision(
                status="DOWNSIZED",
                engine="short",
                final_risk_budget=0.0014,
                rank=1,
                meta={"aggressiveness_multiplier": 0.79, "regime_hazard_multiplier": 0.84, "late_stage_heat_multiplier": 1.0},
            )
        ],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    short_allocations = [row for row in state["latest_allocations"] if row["engine"] == "short"]

    assert len(short_allocations) == 1
    assert short_allocations[0]["status"] == "DOWNSIZED"
    assert short_allocations[0]["compression_reasons"] == ["regime_hazard"]
    assert short_allocations[0]["execution"] == {"status": "SKIPPED", "reason": "short_execution_not_enabled"}


def test_main_v2_cycle_suppresses_crowded_short_candidates_from_runtime_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00021,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.043,
                        "mark_price_change_24h_pct": -0.019,
                        "taker_buy_sell_ratio": 0.94,
                        "basis_bps": -31,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00002,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.011,
                        "mark_price_change_24h_pct": -0.012,
                        "taker_buy_sell_ratio": 0.99,
                        "basis_bps": -8,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    short_universe = [row["symbol"] for row in state["latest_universes"]["short_universe"]]
    runtime_short_candidates = [row for row in state["latest_candidates"] if row.get("engine") == "short"]

    assert short_universe == ["BTCUSDT", "ETHUSDT"]
    assert [row["symbol"] for row in state["short_candidates"]] == ["ETHUSDT"]
    assert [row["symbol"] for row in runtime_short_candidates] == ["ETHUSDT"]
    assert state["short_summary"]["candidate_count"] == 1
    assert [row["symbol"] for row in state["short_summary"]["leaders"]] == ["ETHUSDT"]
    assert all(row["symbol"] != "BTCUSDT" for row in runtime_short_candidates)


def test_main_v2_short_derivatives_meta_survives_allocator_runtime_and_report_serialization(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00021,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.043,
                        "mark_price_change_24h_pct": -0.019,
                        "taker_buy_sell_ratio": 0.94,
                        "basis_bps": -31,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00002,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.011,
                        "mark_price_change_24h_pct": -0.012,
                        "taker_buy_sell_ratio": 0.99,
                        "basis_bps": -8,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
    )
    main_module.main()

    state = json.loads(Path(output_path).read_text())
    short_allocations = [row for row in state["latest_allocations"] if row["engine"] == "short"]
    assert [row["symbol"] for row in state["short_candidates"]] == ["ETHUSDT"]
    assert short_allocations[0]["symbol"] == "ETHUSDT"
    assert short_allocations[0]["timeframe_meta"]["derivatives"] == {"crowding_bias": "balanced", "basis_bps": -8.0}
    assert short_allocations[0]["execution"] == {"status": "SKIPPED", "reason": "short_execution_not_enabled"}
    assert state["short_summary"]["candidate_count"] == 1
    assert state["short_summary"]["leaders"][0]["derivatives"] == {"crowding_bias": "balanced", "basis_bps": -8.0}


def test_main_v2_stdout_surfaces_surviving_short_derivatives_reporting(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00021,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.043,
                        "mark_price_change_24h_pct": -0.019,
                        "taker_buy_sell_ratio": 0.94,
                        "basis_bps": -31,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00002,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.011,
                        "mark_price_change_24h_pct": -0.012,
                        "taker_buy_sell_ratio": 0.99,
                        "basis_bps": -8,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["regime"]["short"]["universe_count"] == 2
    assert payload["regime"]["short"]["candidate_count"] == 1
    assert payload["regime"]["short"]["accepted_symbols"] == ["ETHUSDT"]
    assert payload["regime"]["short"]["deferred_execution_symbols"] == ["ETHUSDT"]
    assert len(payload["regime"]["short"]["leaders"]) == 1
    leader = payload["regime"]["short"]["leaders"][0]
    assert leader["symbol"] == "ETHUSDT"
    assert leader["setup_type"] == "BREAKDOWN_SHORT"
    assert leader["daily_bias"] == "down"
    assert leader["h4_structure"] == "breakdown"
    assert leader["h1_trigger"] == "confirmed"
    assert leader["derivatives"] == {"crowding_bias": "balanced", "basis_bps": -8.0}
    assert leader["volume_usdt_24h"] == 6800000000.0
    assert leader["liquidity_tier"] == "top"
    assert leader["stop_family"] == "structure_stop"
    assert leader["stop_reference"] == "4h_ema20"
    assert leader["invalidation_source"] == "short_breakdown_failure_above_4h_ema20"
    assert leader["invalidation_reason"] == "breakdown continuation lost 4h breakdown resistance"
    assert leader["stop_policy_source"] == "shared_taxonomy"


def test_main_v2_stdout_surfaces_crowded_short_suppression_in_short_reporting(monkeypatch, tmp_path, load_fixture, capsys):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00021,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.043,
                        "mark_price_change_24h_pct": -0.019,
                        "taker_buy_sell_ratio": 0.94,
                        "basis_bps": -31,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00002,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": 0.011,
                        "mark_price_change_24h_pct": -0.012,
                        "taker_buy_sell_ratio": 0.99,
                        "basis_bps": -8,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    short_report = payload["regime"]["short"]
    leader_symbols = [row["symbol"] for row in short_report["leaders"]]

    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 1
    assert short_report["accepted_symbols"] == ["ETHUSDT"]
    assert short_report["deferred_execution_symbols"] == ["ETHUSDT"]
    assert leader_symbols == ["ETHUSDT"]
    assert "BTCUSDT" not in short_report["accepted_symbols"]
    assert "BTCUSDT" not in short_report["deferred_execution_symbols"]
    assert "BTCUSDT" not in leader_symbols
    assert len(short_report["review_notes"]) == 1
    note = short_report["review_notes"][0]
    assert note["symbol"] == "BTCUSDT"
    assert note["setup_type"] == "BREAKDOWN_SHORT"
    assert note["reason"] == "crowded_short_squeeze_risk"
    assert note["crowding_bias"] == "crowded_short"
    assert note["basis_bps"] == -31.0
    assert "suppressed" in note["message"]
    assert "crowded-short squeeze risk" in note["message"]


def test_main_v2_stdout_reports_empty_short_lists_when_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )
    deriv_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "funding_rate": -0.00021,
                        "open_interest_usdt": 23_100_000_000,
                        "open_interest_change_24h_pct": -0.043,
                        "mark_price_change_24h_pct": -0.019,
                        "taker_buy_sell_ratio": 0.94,
                        "basis_bps": -31,
                    },
                    {
                        "symbol": "ETHUSDT",
                        "funding_rate": -0.00024,
                        "open_interest_usdt": 11_800_000_000,
                        "open_interest_change_24h_pct": -0.036,
                        "mark_price_change_24h_pct": -0.014,
                        "taker_buy_sell_ratio": 0.95,
                        "basis_bps": -29,
                    },
                ],
            }
        )
    )
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )

    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_stdout_clears_previous_short_reporting_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )

    main_module.main()
    initial_payload = json.loads(capsys.readouterr().out)

    assert initial_payload["regime"]["short"]["candidate_count"] == 1
    assert [row["symbol"] for row in initial_payload["regime"]["short"]["leaders"]] == ["ETHUSDT"]

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    main_module.main()
    payload = json.loads(capsys.readouterr().out)

    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_direct_state_store_reload_path_clears_stale_short_candidate_rows_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    def configure_short_cycle(module) -> None:
        monkeypatch.setattr(module, "generate_trend_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "generate_rotation_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            module,
            "classify_regime",
            lambda *args, **kwargs: RegimeSnapshot(
                label="HIGH_VOL_DEFENSIVE",
                confidence=0.74,
                risk_multiplier=0.55,
                bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
                suppression_rules=["rotation"],
            ),
        )
        monkeypatch.setattr(
            module,
            "validate_candidate_for_allocation",
            lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
        )
        monkeypatch.setattr(
            module,
            "allocate_candidates",
            lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    configure_short_cycle(main_module)

    main_module.main()
    initial_payload = json.loads(capsys.readouterr().out)

    persisted_store = build_state_store(replace(DEFAULT_CONFIG, state_file=output_path))
    preloaded_state = persisted_store.load()

    assert initial_payload["regime"]["short"]["accepted_symbols"] == ["ETHUSDT"]
    assert initial_payload["regime"]["short"]["deferred_execution_symbols"] == ["ETHUSDT"]
    assert [row["symbol"] for row in preloaded_state.short_candidates] == ["ETHUSDT"]
    assert [row["symbol"] for row in preloaded_state.latest_candidates if row.get("engine") == "short"] == ["ETHUSDT"]
    assert preloaded_state.short_summary["accepted_symbols"] == ["ETHUSDT"]

    class PreloadedStore:
        def __init__(self, state, backing_store):
            self._state = state
            self._backing_store = backing_store
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return self._state

        def save(self, state):
            self._state = state
            self._backing_store.save(state)

        def __getattr__(self, name):
            return getattr(self._backing_store, name)

    preloaded_store = PreloadedStore(preloaded_state, persisted_store)
    monkeypatch.setattr(main_module, "build_state_store", lambda config: preloaded_store)

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    main_module.main()
    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())

    assert preloaded_store.load_calls == 1
    assert state["short_candidates"] == []
    assert state["latest_candidates"] == []
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }
    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_direct_state_store_reload_path_clears_stale_short_latest_allocations_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    def configure_short_cycle(module) -> None:
        monkeypatch.setattr(module, "generate_trend_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "generate_rotation_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            module,
            "classify_regime",
            lambda *args, **kwargs: RegimeSnapshot(
                label="HIGH_VOL_DEFENSIVE",
                confidence=0.74,
                risk_multiplier=0.55,
                bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
                suppression_rules=["rotation"],
            ),
        )
        monkeypatch.setattr(
            module,
            "validate_candidate_for_allocation",
            lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
        )
        monkeypatch.setattr(
            module,
            "allocate_candidates",
            lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    configure_short_cycle(main_module)

    main_module.main()
    initial_payload = json.loads(capsys.readouterr().out)

    persisted_store = build_state_store(replace(DEFAULT_CONFIG, state_file=output_path))
    preloaded_state = persisted_store.load()

    assert initial_payload["regime"]["short"]["accepted_symbols"] == ["ETHUSDT"]
    assert initial_payload["regime"]["short"]["deferred_execution_symbols"] == ["ETHUSDT"]
    short_allocations = [row for row in preloaded_state.latest_allocations if row.get("engine") == "short"]
    assert len(short_allocations) == 1
    assert short_allocations[0]["symbol"] == "ETHUSDT"
    assert short_allocations[0]["status"] == "ACCEPTED"
    assert short_allocations[0]["execution"] == {"status": "SKIPPED", "reason": "short_execution_not_enabled"}
    assert preloaded_state.short_summary["accepted_symbols"] == ["ETHUSDT"]
    assert preloaded_state.short_summary["deferred_execution_symbols"] == ["ETHUSDT"]

    class PreloadedStore:
        def __init__(self, state, backing_store):
            self._state = state
            self._backing_store = backing_store
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return self._state

        def save(self, state):
            self._state = state
            self._backing_store.save(state)

        def __getattr__(self, name):
            return getattr(self._backing_store, name)

    preloaded_store = PreloadedStore(preloaded_state, persisted_store)
    monkeypatch.setattr(main_module, "build_state_store", lambda config: preloaded_store)

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    main_module.main()
    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())

    assert preloaded_store.load_calls == 1
    assert state["latest_allocations"] == []
    assert state["short_candidates"] == []
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }
    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_direct_state_store_reload_path_clears_persisted_and_emitted_short_outputs_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    def configure_short_cycle(module) -> None:
        monkeypatch.setattr(module, "generate_trend_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "generate_rotation_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            module,
            "classify_regime",
            lambda *args, **kwargs: RegimeSnapshot(
                label="HIGH_VOL_DEFENSIVE",
                confidence=0.74,
                risk_multiplier=0.55,
                bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
                suppression_rules=["rotation"],
            ),
        )
        monkeypatch.setattr(
            module,
            "validate_candidate_for_allocation",
            lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
        )
        monkeypatch.setattr(
            module,
            "allocate_candidates",
            lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    configure_short_cycle(main_module)

    main_module.main()
    initial_payload = json.loads(capsys.readouterr().out)

    persisted_store = build_state_store(replace(DEFAULT_CONFIG, state_file=output_path))
    preloaded_state = persisted_store.load()

    assert initial_payload["regime"]["short"]["accepted_symbols"] == ["ETHUSDT"]
    assert initial_payload["regime"]["short"]["deferred_execution_symbols"] == ["ETHUSDT"]
    assert preloaded_state.short_summary["accepted_symbols"] == ["ETHUSDT"]
    assert preloaded_state.short_summary["deferred_execution_symbols"] == ["ETHUSDT"]
    assert [row["symbol"] for row in preloaded_state.short_summary["leaders"]] == ["ETHUSDT"]

    class PreloadedStore:
        def __init__(self, state, backing_store):
            self._state = state
            self._backing_store = backing_store
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return self._state

        def save(self, state):
            self._state = state
            self._backing_store.save(state)

        def __getattr__(self, name):
            return getattr(self._backing_store, name)

    preloaded_store = PreloadedStore(preloaded_state, persisted_store)
    monkeypatch.setattr(main_module, "build_state_store", lambda config: preloaded_store)

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    main_module.main()
    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())

    assert preloaded_store.load_calls == 1
    assert state["short_candidates"] == []
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }
    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_reload_boundary_clears_persisted_and_emitted_short_outputs_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    def configure_short_cycle(module) -> None:
        monkeypatch.setattr(module, "generate_trend_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(module, "generate_rotation_candidates", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            module,
            "classify_regime",
            lambda *args, **kwargs: RegimeSnapshot(
                label="HIGH_VOL_DEFENSIVE",
                confidence=0.74,
                risk_multiplier=0.55,
                bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
                suppression_rules=["rotation"],
            ),
        )
        monkeypatch.setattr(
            module,
            "validate_candidate_for_allocation",
            lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
        )
        monkeypatch.setattr(
            module,
            "allocate_candidates",
            lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="short", final_risk_budget=0.004, rank=1)],
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    configure_short_cycle(main_module)

    main_module.main()
    initial_payload = json.loads(capsys.readouterr().out)
    initial_state = json.loads(Path(output_path).read_text())

    assert initial_payload["regime"]["short"]["accepted_symbols"] == ["ETHUSDT"]
    assert initial_payload["regime"]["short"]["deferred_execution_symbols"] == ["ETHUSDT"]
    assert [row["symbol"] for row in initial_payload["regime"]["short"]["leaders"]] == ["ETHUSDT"]
    assert initial_state["short_summary"]["accepted_symbols"] == ["ETHUSDT"]
    assert initial_state["short_summary"]["deferred_execution_symbols"] == ["ETHUSDT"]
    assert [row["symbol"] for row in initial_state["short_summary"]["leaders"]] == ["ETHUSDT"]

    reloaded_main_module = importlib.reload(main_module)
    configure_short_cycle(reloaded_main_module)

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    reloaded_main_module.main()
    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())

    assert state["short_candidates"] == []
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }
    short_report = payload["regime"]["short"]
    assert short_report["universe_count"] == 2
    assert short_report["candidate_count"] == 0
    assert short_report["accepted_symbols"] == []
    assert short_report["deferred_execution_symbols"] == []
    assert short_report["leaders"] == []
    assert [note["symbol"] for note in short_report["review_notes"]] == ["BTCUSDT", "ETHUSDT"]


def test_main_v2_persisted_state_clears_previous_short_summary_when_later_all_short_candidates_are_rejected(
    monkeypatch,
    tmp_path,
    load_fixture,
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(
        json.dumps(
            {
                "as_of": "2026-03-25T00:00:00Z",
                "schema_version": "v2",
                **_defensive_short_market(),
            }
        )
    )

    def write_derivatives_snapshot(*, eth_basis_bps: float, eth_taker_ratio: float, eth_oi_change_24h_pct: float) -> None:
        deriv_path.write_text(
            json.dumps(
                {
                    "as_of": "2026-03-25T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": -0.00021,
                            "open_interest_usdt": 23_100_000_000,
                            "open_interest_change_24h_pct": -0.043,
                            "mark_price_change_24h_pct": -0.019,
                            "taker_buy_sell_ratio": 0.94,
                            "basis_bps": -31,
                        },
                        {
                            "symbol": "ETHUSDT",
                            "funding_rate": -0.00024,
                            "open_interest_usdt": 11_800_000_000,
                            "open_interest_change_24h_pct": eth_oi_change_24h_pct,
                            "mark_price_change_24h_pct": -0.014,
                            "taker_buy_sell_ratio": eth_taker_ratio,
                            "basis_bps": eth_basis_bps,
                        },
                    ],
                }
            )
        )

    write_derivatives_snapshot(eth_basis_bps=-8, eth_taker_ratio=0.99, eth_oi_change_24h_pct=0.011)
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="HIGH_VOL_DEFENSIVE",
            confidence=0.74,
            risk_multiplier=0.55,
            bucket_targets={"trend": 0.2, "rotation": 0.0, "short": 0.8},
            suppression_rules=["rotation"],
        ),
    )

    main_module.main()

    initial_state = json.loads(Path(output_path).read_text())

    assert [row["symbol"] for row in initial_state["short_candidates"]] == ["ETHUSDT"]
    assert initial_state["short_summary"]["candidate_count"] == 1
    assert [row["symbol"] for row in initial_state["short_summary"]["leaders"]] == ["ETHUSDT"]

    write_derivatives_snapshot(eth_basis_bps=-29, eth_taker_ratio=0.95, eth_oi_change_24h_pct=-0.036)
    main_module.main()

    state = json.loads(Path(output_path).read_text())

    assert state["short_candidates"] == []
    assert state["short_summary"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
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
    trend_candidates = [row for row in state.get("latest_candidates", []) if row.get("engine") == "trend"]
    assert trend_candidates
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in trend_candidates)
    assert all(row.get("invalidation_source") == "trend_breakout_failure_below_4h_ema20" for row in trend_candidates)
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    trend_allocations = [row for row in accepted_allocations if row.get("engine") == "trend"]
    assert trend_allocations
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in trend_allocations)
    assert all(row.get("invalidation_source") == "trend_breakout_failure_below_4h_ema20" for row in trend_allocations)
    assert all(row.get("execution", {}).get("status") == "BLOCKED" for row in trend_allocations)
    assert all("显式止损" not in row.get("execution", {}).get("reason", "") for row in trend_allocations)
    assert all("invalidation_source" not in row.get("execution", {}).get("reason", "") for row in trend_allocations)
    blocked_non_trend = [row for row in accepted_allocations if row.get("engine") not in {"trend", "short"}]
    assert all(row.get("execution", {}).get("status") == "BLOCKED" for row in blocked_non_trend)
    assert all("显式止损" in row.get("execution", {}).get("reason", "") for row in blocked_non_trend)
    assert all("invalidation_source" in row.get("execution", {}).get("reason", "") for row in blocked_non_trend)


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


def test_main_v2_backfills_candidate_stop_and_invalidation_from_shared_taxonomy_before_execution(monkeypatch, tmp_path, load_fixture):
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
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.9,
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    trend_candidates = [row for row in state.get("latest_candidates", []) if row.get("engine") == "trend"]
    assert trend_candidates
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in trend_candidates)
    assert all(row.get("invalidation_source") == "trend_structure_loss_below_4h_ema50" for row in trend_candidates)
    assert all(row.get("stop_policy_source") == "shared_taxonomy" for row in trend_candidates)
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in accepted_allocations)
    assert all(row.get("invalidation_source") == "trend_structure_loss_below_4h_ema50" for row in accepted_allocations)
    assert all(row.get("stop_policy_source") == "shared_taxonomy" for row in accepted_allocations)
    assert all(row.get("execution", {}).get("status") == "BLOCKED" for row in accepted_allocations)
    assert all("显式止损" not in row.get("execution", {}).get("reason", "") for row in accepted_allocations)
    assert all("invalidation_source" not in row.get("execution", {}).get("reason", "") for row in accepted_allocations)
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())


def test_main_v2_rewrites_trend_breakout_stop_fields_from_shared_taxonomy(monkeypatch, tmp_path, load_fixture):
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
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    trend_candidates = [row for row in state.get("latest_candidates", []) if row.get("engine") == "trend"]
    assert trend_candidates
    assert trend_candidates[0]["stop_loss"] == pytest.approx(63620.0)
    assert trend_candidates[0]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert trend_candidates[0]["stop_family"] == "structure_stop"
    assert trend_candidates[0]["stop_reference"] == "4h_ema20"

    trend_allocations = [
        row for row in state.get("latest_allocations", []) if row.get("engine") == "trend" and row.get("status") == "ACCEPTED"
    ]
    assert trend_allocations
    assert trend_allocations[0]["stop_loss"] == pytest.approx(63620.0)
    assert trend_allocations[0]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert trend_allocations[0]["stop_family"] == "structure_stop"
    assert trend_allocations[0]["stop_reference"] == "4h_ema20"
    assert trend_allocations[0].get("execution", {}).get("status") == "SENT"


def test_main_v2_applies_crash_defensive_stop_taxonomy_to_trend_entry(monkeypatch, tmp_path, load_fixture):
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
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda market, derivatives, **_kwargs: RegimeSnapshot(
            label="CRASH_DEFENSIVE",
            confidence=0.3,
            risk_multiplier=0.45,
            execution_policy="suppress",
            bucket_targets={"trend": 1.0, "rotation": 0.0, "short": 0.0},
            suppression_rules=["rotation"],
        ),
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    trend_candidates = [row for row in state.get("latest_candidates", []) if row.get("engine") == "trend"]
    assert trend_candidates
    assert trend_candidates[0]["stop_loss"] == pytest.approx(63940.0)
    assert trend_candidates[0]["invalidation_source"] == "crash_defensive_squeeze_loss_below_1h_ema20_or_1d_atr_band"
    assert trend_candidates[0]["stop_family"] == "squeeze_stop"
    assert trend_candidates[0]["stop_reference"] == "1h_ema20_or_1d_atr_band"

    trend_allocations = [
        row for row in state.get("latest_allocations", []) if row.get("engine") == "trend" and row.get("status") == "ACCEPTED"
    ]
    assert trend_allocations
    assert trend_allocations[0]["stop_loss"] == pytest.approx(63940.0)
    assert trend_allocations[0]["invalidation_source"] == "crash_defensive_squeeze_loss_below_1h_ema20_or_1d_atr_band"
    assert trend_allocations[0]["stop_family"] == "squeeze_stop"
    assert trend_allocations[0]["stop_reference"] == "1h_ema20_or_1d_atr_band"


def test_main_v2_rotation_allocations_propagate_explicit_stop_and_invalidation_source(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
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
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="rotation", final_risk_budget=0.005, rank=1)],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    rotation_candidates = [row for row in state.get("latest_candidates", []) if row.get("engine") == "rotation"]
    assert rotation_candidates
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in rotation_candidates)
    assert all(row.get("invalidation_source") == "rotation_pullback_failure_below_1h_ema50" for row in rotation_candidates)

    accepted_rotation = [
        row
        for row in state.get("latest_allocations", [])
        if row.get("engine") == "rotation" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
    ]
    assert accepted_rotation
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in accepted_rotation)
    assert all(row.get("invalidation_source") == "rotation_pullback_failure_below_1h_ema50" for row in accepted_rotation)
    assert all(row.get("execution", {}).get("status") == "SENT" for row in accepted_rotation)
    assert all("显式止损" not in row.get("execution", {}).get("reason", "") for row in accepted_rotation)
    assert all("invalidation_source" not in row.get("execution", {}).get("reason", "") for row in accepted_rotation)


def test_allocation_summary_surfaces_aggressiveness_metrics():
    decision = AllocationDecision(
        status="DOWNSIZED",
        engine="rotation",
        final_risk_budget=0.0042,
        rank=1,
        meta={
            "aggressiveness_multiplier": 0.84,
            "quality_multiplier": 1.08,
            "crowding_multiplier": 0.91,
            "execution_friction_multiplier": 0.85,
            "regime_hazard_multiplier": 0.8,
            "late_stage_heat_multiplier": 0.78,
        },
    )
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": 0.84,
        "stop_loss": 152.0,
        "invalidation_source": "rotation_pullback_failure_below_1h_ema50",
    }

    summary = main_module._allocation_summary(decision, candidate)

    assert summary["aggressiveness_multiplier"] == pytest.approx(0.84)
    assert summary["quality_multiplier"] == pytest.approx(1.08)
    assert summary["crowding_multiplier"] == pytest.approx(0.91)
    assert summary["execution_friction_multiplier"] == pytest.approx(0.85)
    assert summary["regime_hazard_multiplier"] == pytest.approx(0.8)
    assert summary["late_stage_heat_multiplier"] == pytest.approx(0.78)
    assert summary["compression_reasons"] == ["regime_hazard", "late_stage_heat"]


def test_allocation_summary_rejects_non_mapping_decision_payload() -> None:
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": 0.84,
    }

    with pytest.raises(ValueError, match="decision must be a mapping or dataclass"):
        main_module._allocation_summary("bad-decision", candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", 123),
        ("symbol", " solusdt "),
        ("side", True),
        ("side", "long"),
        ("setup_type", 5),
        ("setup_type", "rs_reacceleration"),
    ],
)
def test_allocation_summary_rejects_present_invalid_candidate_strings(field: str, value: object) -> None:
    decision = AllocationDecision(status="ACCEPTED", engine="rotation", final_risk_budget=0.01, rank=1)
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": 0.84,
    }
    candidate[field] = value

    with pytest.raises(ValueError, match=field):
        main_module._allocation_summary(decision, candidate)


@pytest.mark.parametrize("score", [True, "0.84"])
def test_allocation_summary_rejects_present_invalid_candidate_score(score: object) -> None:
    decision = AllocationDecision(status="ACCEPTED", engine="rotation", final_risk_budget=0.01, rank=1)
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": score,
    }

    with pytest.raises(ValueError, match="score"):
        main_module._allocation_summary(decision, candidate)


def test_allocation_summary_rejects_present_invalid_timeframe_meta() -> None:
    decision = AllocationDecision(status="ACCEPTED", engine="rotation", final_risk_budget=0.01, rank=1)
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": 0.84,
        "timeframe_meta": [("1h", "uptrend")],
    }

    with pytest.raises(ValueError, match="timeframe_meta"):
        main_module._allocation_summary(decision, candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aggressiveness_multiplier", True),
        ("quality_multiplier", "1.08"),
        ("crowding_multiplier", True),
        ("execution_friction_multiplier", "0.85"),
        ("regime_hazard_multiplier", True),
        ("late_stage_heat_multiplier", "0.78"),
    ],
)
def test_allocation_summary_rejects_present_invalid_meta_multipliers(field: str, value: object) -> None:
    decision = AllocationDecision(
        status="DOWNSIZED",
        engine="rotation",
        final_risk_budget=0.0042,
        rank=1,
        meta={field: value},
    )
    candidate = {
        "engine": "rotation",
        "setup_type": "RS_REACCELERATION",
        "symbol": "SOLUSDT",
        "side": "LONG",
        "score": 0.84,
    }

    with pytest.raises(ValueError, match=field):
        main_module._allocation_summary(decision, candidate)


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


def test_main_v2_blocks_when_execution_would_breach_net_exposure_cap(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 500.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "ADAUSDT",
                        "side": "LONG",
                        "qty": 8.0,
                        "entry_price": 100.0,
                        "mark_price": 100.0,
                        "notional": 800.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_MAX_TOTAL_RISK_PCT", "1.0")
    monkeypatch.setenv("TRADING_MAX_SYMBOL_RISK_PCT", "1.0")
    monkeypatch.setenv("TRADING_MAX_NET_EXPOSURE_PCT", "0.85")
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

    def net_exposure_signal(*args, **kwargs):
        return main_module.TradeSignal(
            signal_id="net-exposure-block",
            symbol="XRPUSDT",
            side="LONG",
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=104.0,
            source="strategy",
            timeframe="4h",
            tags=["v2", "trend"],
            meta={"setup_type": "BREAKOUT", "score": 0.9},
        )

    monkeypatch.setattr(main_module, "_candidate_signal", net_exposure_signal)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    blocked = [row for row in accepted_allocations if row.get("execution", {}).get("status") == "BLOCKED"]
    assert blocked
    assert all("净敞口" in row.get("execution", {}).get("reason", "") for row in blocked)
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())


def test_main_v2_execution_sizing_uses_allocation_budget_for_net_exposure_guardrail(monkeypatch, tmp_path, load_fixture):
    @dataclass(slots=True)
    class SizingRegressionState(RuntimeStateV2):
        disabled_setup_type_filtered_candidates: list[dict] = field(default_factory=list)

    class SizingRegressionStore(StateStore):
        def load(self):
            return SizingRegressionState.empty()

    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 500.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "ADAUSDT",
                        "side": "LONG",
                        "qty": 8.0,
                        "entry_price": 100.0,
                        "mark_price": 100.0,
                        "notional": 800.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setenv("TRADING_MAX_TOTAL_RISK_PCT", "1.0")
    monkeypatch.setenv("TRADING_MAX_SYMBOL_RISK_PCT", "1.0")
    monkeypatch.setenv("TRADING_MAX_NET_EXPOSURE_PCT", "0.85")
    monkeypatch.setenv("TRADING_MAX_STOP_DISTANCE_PCT", "0.20")
    monkeypatch.setattr(main_module, "build_state_store", lambda config: SizingRegressionStore(config.state_file))
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="NORMAL",
            confidence=0.9,
            risk_multiplier=1.0,
            execution_policy="normal",
            bucket_targets={"trend": 1.0},
            suppression_rules=[],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT",
                "symbol": "XRPUSDT",
                "side": "LONG",
                "score": 0.9,
                "stop_loss": 90.0,
                "invalidation_source": "test_stop",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.004, rank=1)],
    )

    def allocation_sized_signal(*args, **kwargs):
        return main_module.TradeSignal(
            signal_id="allocation-sized-net-exposure",
            symbol="XRPUSDT",
            side="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            source="strategy",
            timeframe="4h",
            tags=["v2", "trend"],
            meta={"setup_type": "BREAKOUT", "score": 0.9},
        )

    monkeypatch.setattr(main_module, "_candidate_signal", allocation_sized_signal)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert len(accepted_allocations) == 1
    execution = accepted_allocations[0].get("execution", {})
    assert execution.get("status") == "SENT"
    assert "净敞口" not in execution.get("reason", "")
    assert state.get("active_orders") == {}


def _active_paper_first_rotation_probe_allocation(**overrides):
    row = {
        "engine": "rotation",
        "setup_type": "RS_PULLBACK",
        "symbol": "BNBUSDT",
        "side": "LONG",
        "score": 0.78,
        "status": "DOWNSIZED",
        "final_risk_budget": 0.0045,
        "rank": 1,
        "meta": {
            "active_paper_first_rotation_probe": True,
            "alt_seed_exception_applied": True,
        },
    }
    row.update(overrides)
    return row


def _near_stop_rotation_market():
    return {
        "symbols": {
            "BNBUSDT": {
                "sector": "exchange",
                "liquidity_tier": "high",
                "daily": {"close": 638.0, "ema_20": 626.0, "ema_50": 629.0, "atr_pct": 0.026},
                "4h": {"close": 638.0, "ema_20": 636.0, "ema_50": 633.0},
                "1h": {"close": 638.0, "ema_20": 637.4, "ema_50": 637.0},
            }
        }
    }


def test_testnet_order_qty_uses_risk_budget_before_notional_cap():
    account = AccountSnapshot(equity=100000.0, available_balance=100000.0, futures_wallet_balance=100000.0)
    signal = main_module.TradeSignal(
        signal_id="signal-risk-sized-btc",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=None,
    )
    allocation = {"final_risk_budget": 0.008}

    qty = main_module._testnet_order_qty(
        account,
        signal,
        allocation,
        max_notional_usdt=2000.0,
    )

    # Risk sizing comes first: 100000 * 0.8% / 1000 stop distance = 0.8 BTC.
    # The testnet single-symbol notional guard then caps it to 2000 / 80000 = 0.025 BTC.
    assert qty == pytest.approx(0.025, abs=1e-12)
    assert qty * signal.entry_price <= 2000.0


def test_testnet_order_qty_keeps_smaller_risk_sized_position_below_notional_cap():
    account = AccountSnapshot(equity=100000.0, available_balance=100000.0, futures_wallet_balance=100000.0)
    signal = main_module.TradeSignal(
        signal_id="signal-risk-sized-eth",
        symbol="ETHUSDT",
        side="LONG",
        entry_price=2000.0,
        stop_loss=1000.0,
        take_profit=None,
    )
    allocation = {"final_risk_budget": 0.001}

    qty = main_module._testnet_order_qty(
        account,
        signal,
        allocation,
        max_notional_usdt=2000.0,
    )

    # Risk sizing gives 100000 * 0.1% / 1000 = 0.1 ETH, notional only 200 USDT,
    # so the 2000 USDT cap must not force the position upward.
    assert qty == pytest.approx(0.1, abs=1e-12)
    assert qty * signal.entry_price == pytest.approx(200.0)


@pytest.mark.parametrize(
    "allocation",
    [
        {"execution_risk_budget": True},
        {"execution_risk_budget": "0.01"},
        {"final_risk_budget": True},
        {"final_risk_budget": "0.01"},
    ],
)
def test_order_qty_rejects_present_non_numeric_risk_budget(allocation):
    account = AccountSnapshot(equity=100000.0, available_balance=100000.0, futures_wallet_balance=100000.0)
    signal = main_module.TradeSignal(
        signal_id="signal-risk-budget-boundary",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=None,
    )

    with pytest.raises((TypeError, ValueError), match="execution_risk_budget|final_risk_budget"):
        main_module._order_qty(account, signal, allocation)


def test_cap_order_qty_by_notional_limits_testnet_probe_size():
    qty = main_module._cap_order_qty_by_notional(qty=0.4931058, entry_price=77938.38, max_notional_usdt=2000.0)

    assert qty * 77938.38 <= 2000.0
    assert qty == pytest.approx(0.02566129, abs=1e-8)


def test_align_testnet_order_to_exchange_filters_keeps_order_within_caps():
    order = OrderIntent(
        intent_id="intent-btc-align",
        signal_id="signal-btc-align",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.02564894,
        entry_price=77975.92,
        stop_loss=76829.74596105,
        take_profit=None,
    )
    metadata = {"quantity_step_size": 0.0001, "price_tick_size": 0.1, "min_notional": 100.0}

    aligned = main_module._align_testnet_order_to_exchange_filters(order, metadata, max_notional_usdt=2000.0)

    assert aligned.qty == pytest.approx(0.0256, abs=1e-12)
    assert aligned.entry_price == pytest.approx(77975.9, abs=1e-12)
    assert aligned.stop_loss == pytest.approx(76829.7, abs=1e-12)
    assert aligned.qty * aligned.entry_price <= 2000.0


def test_align_testnet_order_to_exchange_filters_generates_default_take_profit_when_missing():
    order = OrderIntent(
        intent_id="intent-btc-default-tp",
        signal_id="signal-btc-default-tp",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.05,
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=None,
    )
    metadata = {"quantity_step_size": 0.0001, "price_tick_size": 0.1, "min_notional": 100.0}

    aligned = main_module._align_testnet_order_to_exchange_filters(order, metadata, max_notional_usdt=5000.0)

    assert aligned.take_profit == pytest.approx(81500.0)
    assert aligned.meta["default_take_profit_generated"] is True
    assert aligned.meta["default_take_profit_r_multiple"] == pytest.approx(1.5)
    assert aligned.meta["second_take_profit"] == pytest.approx(82000.0)
    assert aligned.meta["second_take_profit_r_multiple"] == pytest.approx(2.0)


def test_align_testnet_order_to_exchange_filters_preserves_structure_take_profit():
    order = OrderIntent(
        intent_id="intent-btc-structure-tp",
        signal_id="signal-btc-structure-tp",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.05,
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=81234.56,
    )
    metadata = {"quantity_step_size": 0.0001, "price_tick_size": 0.1, "min_notional": 100.0}

    aligned = main_module._align_testnet_order_to_exchange_filters(order, metadata, max_notional_usdt=5000.0)

    assert aligned.take_profit == pytest.approx(81234.5)
    assert "default_take_profit_generated" not in aligned.meta


@pytest.mark.parametrize(
    ("metadata", "field_name"),
    [
        ({"quantity_step_size": 0.0001, "price_tick_size": True}, "price_tick_size"),
        ({"quantity_step_size": 0.0001, "price_tick_size": "0.1"}, "price_tick_size"),
        ({"quantity_step_size": False, "price_tick_size": 0.1}, "quantity_step_size"),
        ({"quantity_step_size": "0.0001", "price_tick_size": 0.1}, "quantity_step_size"),
    ],
)
def test_align_testnet_order_to_exchange_filters_rejects_present_invalid_filter_numbers(metadata, field_name):
    order = OrderIntent(
        intent_id="intent-btc-invalid-filter",
        signal_id="signal-btc-invalid-filter",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.05,
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=81234.56,
    )

    with pytest.raises((TypeError, ValueError), match=field_name):
        main_module._align_testnet_order_to_exchange_filters(order, metadata, max_notional_usdt=5000.0)


def test_align_testnet_order_to_exchange_filters_rejects_non_mapping_meta():
    order = OrderIntent(
        intent_id="intent-btc-invalid-meta",
        signal_id="signal-btc-invalid-meta",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.05,
        entry_price=80000.0,
        stop_loss=79000.0,
        take_profit=81234.56,
        meta=["not", "mapping"],  # type: ignore[arg-type]
    )
    metadata = {"quantity_step_size": 0.0001, "price_tick_size": 0.1, "min_notional": 100.0}

    with pytest.raises(TypeError, match="meta"):
        main_module._align_testnet_order_to_exchange_filters(order, metadata, max_notional_usdt=5000.0)


@pytest.mark.parametrize("allowed_symbols", [("BTCUSDT", 123), ("btcusdt",), (" BTCUSDT ",)])
def test_build_testnet_order_preview_rejects_invalid_allowlist_entries(monkeypatch, allowed_symbols):
    order = OrderIntent(
        intent_id="intent-btc-preview",
        signal_id="signal-btc-preview",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
    )

    class Execution:
        testnet_allowed_symbols = allowed_symbols
        testnet_max_order_notional_usdt = 1000.0
        testnet_order_submission_enabled = False
        entry_order_policy = "maker_only"
        maker_entry_timeout_seconds = 15

    class Config:
        execution = Execution()

    monkeypatch.setattr(main_module, "load_testnet_exchange_metadata", lambda symbols: {})
    monkeypatch.setattr(main_module, "build_validated_order_preview", lambda *args, **kwargs: {"unexpected": True})

    with pytest.raises((TypeError, ValueError), match="testnet_allowed_symbols"):
        main_module._build_testnet_order_preview(order, Config())


def test_build_testnet_order_preview_rejects_present_non_bool_submission_flag(monkeypatch):
    order = OrderIntent(
        intent_id="intent-btc-preview-flag",
        signal_id="signal-btc-preview-flag",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
    )

    class Execution:
        testnet_allowed_symbols = ("BTCUSDT",)
        testnet_max_order_notional_usdt = 1000.0
        testnet_order_submission_enabled = "false"
        entry_order_policy = "maker_only"
        maker_entry_timeout_seconds = 15

    class Config:
        execution = Execution()

    monkeypatch.setattr(main_module, "load_testnet_exchange_metadata", lambda symbols: {})
    monkeypatch.setattr(main_module, "build_validated_order_preview", lambda *args, **kwargs: {"unexpected": True})

    with pytest.raises(TypeError, match="testnet_order_submission_enabled"):
        main_module._build_testnet_order_preview(order, Config())


def test_notify_testnet_position_close_events_sends_once_and_marks_notified():
    sent = []

    class Execution:
        feishu_notifications_enabled = True

    class Config:
        execution = Execution()

    class Executor:
        mode = "testnet"
        config = Config()
        feishu_notifier = sent.append

    class State:
        active_orders = {
            "position-closed-ETHUSDT": {
                "event": "POSITION_CLOSED",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "intent_id": "intent-eth-long",
                "entry_price": 2329.52,
                "take_profit": 2343.82,
                "stop_loss": 2318.03,
                "closed_at_bj": "2026-04-26T22:00:00+08:00",
                "notified": False,
            }
        }

    first = main_module._notify_testnet_position_close_events(State, Executor())
    second = main_module._notify_testnet_position_close_events(State, Executor())

    assert len(sent) == 1
    assert "Trading testnet CLOSED" in sent[0]
    assert "symbol=ETHUSDT" in sent[0]
    assert State.active_orders["position-closed-ETHUSDT"]["notified"] is True
    assert first[0]["symbol"] == "ETHUSDT"
    assert second == []


def test_candidate_signal_generates_default_take_profit_for_cost_coverage():
    signal = main_module._candidate_signal(
        _active_paper_first_rotation_probe_allocation(engine="trend", setup_type="BREAKOUT_CONTINUATION"),
        _near_stop_rotation_market(),
        regime={"label": "MIXED"},
    )

    assert signal.take_profit is not None
    assert signal.take_profit > signal.entry_price
    assert signal.take_profit == pytest.approx(signal.entry_price + signal.risk_per_unit() * 1.5)
    assert signal.meta["structure_target_price"] == pytest.approx(signal.take_profit)


def test_candidate_signal_rejects_present_non_mapping_candidate_meta() -> None:
    allocation = _active_paper_first_rotation_probe_allocation(
        engine="trend",
        setup_type="BREAKOUT_CONTINUATION",
        meta=[("stop_family", "structure_stop")],
    )

    with pytest.raises(ValueError, match="candidate.meta"):
        main_module._candidate_signal(allocation, _near_stop_rotation_market(), regime={"label": "MIXED"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeframe_meta", [("entry_tf", "4h")]),
        ("liquidity_meta", "high_liquidity"),
    ],
)
def test_candidate_signal_rejects_present_non_mapping_object_candidate_metadata(field: str, value: object) -> None:
    class ObjectCandidate:
        engine = "trend"
        setup_type = "BREAKOUT_CONTINUATION"
        symbol = "BNBUSDT"
        side = "LONG"
        score = 0.78
        stop_loss = 0.0
        invalidation_source = ""
        sector = "exchange"
        timeframe_meta = {"entry_tf": "4h"}
        liquidity_meta = {"volume_usdt_24h": 1_000_000_000.0}

    setattr(ObjectCandidate, field, value)

    with pytest.raises(ValueError, match=field):
        main_module._candidate_signal(ObjectCandidate(), _near_stop_rotation_market(), regime={"label": "MIXED"})


def test_main_v2_active_paper_first_rotation_probe_uses_valid_stop_and_tiny_execution_budget():
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    allocation = _active_paper_first_rotation_probe_allocation()
    signal = main_module._candidate_signal(allocation, _near_stop_rotation_market(), regime={"label": "MIXED"})

    stop_distance_pct = signal.risk_per_unit() / signal.entry_price
    execution_budget = main_module._execution_risk_budget(account, signal, allocation, DEFAULT_CONFIG.risk)
    validation, context = main_module.validate_signal(signal, account, DEFAULT_CONFIG.risk, risk_pct_override=execution_budget)
    sized_allocation = dict(allocation, execution_risk_budget=execution_budget)
    qty = main_module._order_qty(account, signal, sized_allocation)

    assert stop_distance_pct >= DEFAULT_CONFIG.risk.min_stop_distance_pct
    assert execution_budget < float(allocation["final_risk_budget"])
    assert validation.allowed is True
    assert context["sizing"].planned_notional_usdt / account.equity <= DEFAULT_CONFIG.risk.max_symbol_risk_pct
    assert qty * signal.entry_price / account.equity <= DEFAULT_CONFIG.risk.max_symbol_risk_pct


def test_main_v2_conservative_rotation_keeps_near_stop_blocked():
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    allocation = _active_paper_first_rotation_probe_allocation(meta={})
    signal = main_module._candidate_signal(allocation, _near_stop_rotation_market(), regime={"label": "MIXED"})

    validation, _context = main_module.validate_signal(
        signal,
        account,
        DEFAULT_CONFIG.risk,
        risk_pct_override=float(allocation["final_risk_budget"]),
    )

    assert signal.stop_loss == pytest.approx(637.0)
    assert validation.allowed is False
    assert any("止损太近" in reason for reason in validation.reasons)


def test_main_v2_active_paper_probe_cap_does_not_apply_to_trend_or_short_entries():
    account = AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)
    market = _near_stop_rotation_market()

    trend_signal = main_module._candidate_signal(
        _active_paper_first_rotation_probe_allocation(engine="trend", setup_type="BREAKOUT_CONTINUATION"),
        market,
        regime={"label": "MIXED"},
    )
    short_signal = main_module._candidate_signal(
        _active_paper_first_rotation_probe_allocation(engine="short", side="SHORT", setup_type="BREAKDOWN_SHORT"),
        {
            "symbols": {
                "BNBUSDT": {
                    "daily": {"close": 638.0},
                    "4h": {"close": 638.0, "ema_20": 640.0},
                    "1h": {"ema_50": 641.0},
                }
            }
        },
        regime={"label": "MIXED"},
    )

    trend_allocation = {
        "engine": "trend",
        "side": "LONG",
        "final_risk_budget": 0.0045,
        "meta": {"active_paper_first_rotation_probe": True},
    }
    short_allocation = {
        "engine": "short",
        "side": "SHORT",
        "final_risk_budget": 0.0045,
        "meta": {"active_paper_first_rotation_probe": True},
    }
    assert main_module._execution_risk_budget(
        account,
        trend_signal,
        trend_allocation,
        DEFAULT_CONFIG.risk,
    ) == pytest.approx(0.0045)
    assert main_module._execution_risk_budget(
        account,
        short_signal,
        short_allocation,
        DEFAULT_CONFIG.risk,
    ) == pytest.approx(0.0045)


def test_main_v2_active_paper_probe_still_enforces_existing_total_and_symbol_caps():
    account = AccountSnapshot(
        equity=1000.0,
        available_balance=1000.0,
        futures_wallet_balance=1000.0,
        open_positions=[
            PositionSnapshot(
                symbol="BNBUSDT",
                side="LONG",
                qty=0.047,
                entry_price=638.0,
                mark_price=638.0,
                notional=30.0,
            )
        ],
    )
    allocation = _active_paper_first_rotation_probe_allocation()
    signal = main_module._candidate_signal(allocation, _near_stop_rotation_market(), regime={"label": "MIXED"})
    execution_budget = main_module._execution_risk_budget(account, signal, allocation, DEFAULT_CONFIG.risk)

    validation, _context = main_module.validate_signal(signal, account, DEFAULT_CONFIG.risk, risk_pct_override=execution_budget)

    assert execution_budget == pytest.approx(float(allocation["final_risk_budget"]))
    assert validation.allowed is False
    assert any("单标的风险" in reason or "总风险暴露" in reason for reason in validation.reasons)


def test_main_v2_paper_execution_persists_stop_taxonomy_into_tracked_state(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    position = state.get("positions", {}).get("BTCUSDT")
    assert position
    assert position.get("tracked_from_intent") is True
    assert position.get("stop_loss") == pytest.approx(63620.0)
    assert position.get("taxonomy_stop_loss") == pytest.approx(63620.0)
    assert position.get("invalidation_source") == "trend_breakout_failure_below_4h_ema20"
    assert position.get("invalidation_reason") == "breakout continuation lost 4h breakout support"
    assert position.get("stop_family") == "structure_stop"
    assert position.get("stop_reference") == "4h_ema20"
    assert position.get("stop_policy_source") == "shared_taxonomy"



def test_main_v2_paper_cycle_emits_paper_trading_summary_and_records_ledger(
    monkeypatch, tmp_path, load_fixture, capsys
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    ledger_path = tmp_path / "paper_ledger.jsonl"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())
    ledger_lines = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
    assert len(ledger_lines) == 1
    ledger_event = ledger_lines[0]
    paper_trading = payload["portfolio"]["paper_trading"]
    position = state["positions"]["BTCUSDT"]
    expected_qty = 125000.0 * 0.01 / abs(float(position["entry_price"]) - float(position["stop_loss"]))

    assert paper_trading["mode"] == "paper"
    assert paper_trading["ledger_path"] == str(ledger_path)
    assert paper_trading["ledger_event_count"] == 1
    assert paper_trading["emitted_count"] == 1
    assert paper_trading["replayed_count"] == 0
    assert paper_trading["intents"][0]["intent_id"] == ledger_event["intent_id"]
    assert paper_trading["intents"][0]["status"] == "FILLED"
    assert state["latest_allocations"][0]["execution"] == {"status": "FILLED", "intent_id": ledger_event["intent_id"]}
    assert ledger_event["replay_result"] == {"status": "FILLED", "intent_id": ledger_event["intent_id"]}
    assert ledger_event["position_update"]["intent_id"] == ledger_event["intent_id"]
    assert position["qty"] == pytest.approx(expected_qty)


def test_main_v2_paper_cycle_replays_from_ledger_when_state_is_missing(
    monkeypatch, tmp_path, load_fixture, capsys
):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    ledger_path = tmp_path / "paper_ledger.jsonl"
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()
    capsys.readouterr()
    ledger_event = json.loads(ledger_path.read_text().splitlines()[-1])
    output_path.unlink()

    def fail_execute(self, order, state):
        raise AssertionError("expected paper ledger replay before execute")

    monkeypatch.setattr(main_module.OrderExecutor, "execute", fail_execute)

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    state = json.loads(Path(output_path).read_text())
    paper_trading = payload["portfolio"]["paper_trading"]

    assert paper_trading["mode"] == "paper"
    assert paper_trading["ledger_path"] == str(ledger_path)
    assert paper_trading["ledger_event_count"] == 1
    assert paper_trading["emitted_count"] == 0
    assert paper_trading["replayed_count"] == 1
    assert paper_trading["intents"][0]["intent_id"] == ledger_event["intent_id"]
    assert paper_trading["intents"][0]["replay_source"] == "paper_ledger"
    assert state["positions"]["BTCUSDT"]["intent_id"] == ledger_event["intent_id"]
    assert state["latest_allocations"][0]["execution"] == {"status": "FILLED", "intent_id": ledger_event["intent_id"]}


def test_main_v2_runtime_bucket_replays_from_bucket_ledger_when_state_is_missing(
    monkeypatch, tmp_path, load_fixture, capsys
):
    bucket_dir = tmp_path / "data" / "runtime" / "paper" / "testnet"
    output_path = bucket_dir / "runtime_state.json"
    account_path = bucket_dir / "account_snapshot.json"
    market_path = bucket_dir / "market_context.json"
    deriv_path = bucket_dir / "derivatives_snapshot.json"
    ledger_path = bucket_dir / "paper_ledger.jsonl"
    bucket_dir.mkdir(parents=True)
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "testnet")
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.delenv("TRADING_STATE_FILE", raising=False)
    monkeypatch.delenv("TRADING_ACCOUNT_SNAPSHOT_FILE", raising=False)
    monkeypatch.delenv("TRADING_MARKET_CONTEXT_FILE", raising=False)
    monkeypatch.delenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", raising=False)
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config, **_kwargs: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    main_module.main()
    capsys.readouterr()
    ledger_event = json.loads(ledger_path.read_text().splitlines()[-1])
    output_path.unlink()

    def fail_execute(self, order, state):
        raise AssertionError("expected bucket paper ledger replay before execute")

    monkeypatch.setattr(main_module.OrderExecutor, "execute", fail_execute)

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    state = json.loads(output_path.read_text())
    paper_trading = payload["portfolio"]["paper_trading"]

    assert ledger_path.exists()
    assert paper_trading["mode"] == "paper"
    assert paper_trading["ledger_path"] == str(ledger_path)
    assert paper_trading["ledger_event_count"] == 1
    assert paper_trading["emitted_count"] == 0
    assert paper_trading["replayed_count"] == 1
    assert paper_trading["intents"][0]["intent_id"] == ledger_event["intent_id"]
    assert paper_trading["intents"][0]["replay_source"] == "paper_ledger"
    assert state["positions"]["BTCUSDT"]["intent_id"] == ledger_event["intent_id"]
    assert state["latest_allocations"][0]["execution"] == {"status": "FILLED", "intent_id": ledger_event["intent_id"]}


def test_main_v2_tracked_position_restores_missing_stop_from_taxonomy(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-03-26T22:00:00+08:00",
                "last_signal_ids": {},
                "cooldowns": {},
                "active_orders": {},
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 64120.0,
                        "mark_price": 64400.0,
                        "unrealized_pnl": 56.0,
                        "notional": 12824.0,
                        "stop_loss": None,
                        "taxonomy_stop_loss": 63620.0,
                        "take_profit": 66684.8,
                        "status": "OPEN",
                        "intent_id": "v2-trend-breakout-continuation-btcusdt",
                        "signal_id": "v2-trend-breakout-continuation-btcusdt",
                        "source": "paper_execution",
                        "tracked_from_snapshot": False,
                        "tracked_from_intent": True,
                        "opened_at_bj": "2026-03-26T21:00:00+08:00",
                        "updated_at_bj": "2026-03-26T21:00:00+08:00",
                        "last_synced_from": "executed_intent",
                        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                        "invalidation_reason": "breakout continuation lost 4h breakout support",
                        "stop_family": "structure_stop",
                        "stop_reference": "4h_ema20",
                        "stop_policy_source": "shared_taxonomy"
                    }
                },
                "management_suggestions": [],
                "management_action_previews": []
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 64120.0,
                        "mark_price": 64400.0,
                        "unrealized_pnl": 56.0,
                        "notional": 12880.0,
                        "leverage": 2.0
                    }
                ],
                "open_orders": []
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps({"as_of": "2026-03-15T00:00:00Z", "schema_version": "v2", "rows": []}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    position = state.get("positions", {}).get("BTCUSDT")
    assert position
    assert position.get("taxonomy_stop_loss") == pytest.approx(63620.0)
    assert position.get("invalidation_source") == "trend_breakout_failure_below_4h_ema20"
    assert position.get("stop_family") == "structure_stop"
    management = [row for row in state.get("management_suggestions", []) if row.get("symbol") == "BTCUSDT"]
    assert management
    protective = [row for row in management if row.get("action") == "ADD_PROTECTIVE_STOP"]
    assert protective
    assert protective[0]["suggested_stop_loss"] == pytest.approx(63620.0)
    assert protective[0]["meta"]["heuristic"] == "shared_stop_taxonomy"
    assert protective[0]["meta"]["stop_family"] == "structure_stop"
    assert protective[0]["meta"]["stop_reference"] == "4h_ema20"
    assert protective[0]["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert protective[0]["meta"]["invalidation_reason"] == "breakout continuation lost 4h breakout support"


def test_main_v2_break_even_and_partial_take_profit_preserve_taxonomy_semantics(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-03-26T22:00:00+08:00",
                "last_signal_ids": {},
                "cooldowns": {},
                "active_orders": {},
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 110.0,
                        "unrealized_pnl": 2.0,
                        "notional": 22.0,
                        "stop_loss": 95.0,
                        "taxonomy_stop_loss": 95.0,
                        "take_profit": 110.0,
                        "status": "OPEN",
                        "intent_id": "v2-trend-breakout-btcusdt",
                        "signal_id": "v2-trend-breakout-btcusdt",
                        "source": "paper_execution",
                        "tracked_from_snapshot": False,
                        "tracked_from_intent": True,
                        "opened_at_bj": "2026-03-26T21:00:00+08:00",
                        "updated_at_bj": "2026-03-26T21:00:00+08:00",
                        "last_synced_from": "executed_intent",
                        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                        "invalidation_reason": "breakout continuation lost 4h breakout support",
                        "stop_family": "structure_stop",
                        "stop_reference": "4h_ema20",
                        "stop_policy_source": "shared_taxonomy"
                    }
                },
                "management_suggestions": [],
                "management_action_previews": []
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 110.0,
                        "unrealized_pnl": 2.0,
                        "notional": 22.0,
                        "leverage": 2.0
                    }
                ],
                "open_orders": []
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    suggestions = [row for row in state.get("management_suggestions", []) if row.get("symbol") == "BTCUSDT"]
    actions = {row.get("action"): row for row in suggestions}
    assert {"BREAK_EVEN", "PARTIAL_TAKE_PROFIT"}.issubset(actions)

    break_even = actions["BREAK_EVEN"]
    assert break_even["suggested_stop_loss"] == pytest.approx(100.0)
    assert "breakout continuation lost 4h breakout support" in break_even["reason"]
    assert break_even["meta"]["stop_family"] == "structure_stop"
    assert break_even["meta"]["stop_reference"] == "4h_ema20"
    assert break_even["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert break_even["meta"]["invalidation_reason"] == "breakout continuation lost 4h breakout support"
    assert break_even["meta"]["stop_policy_source"] == "shared_taxonomy"

    partials = [row for row in suggestions if row.get("action") == "PARTIAL_TAKE_PROFIT"]
    assert len(partials) == 2
    first_partial = next(row for row in partials if row.get("meta", {}).get("target_stage") == "first")
    second_partial = next(row for row in partials if row.get("meta", {}).get("target_stage") == "second")

    assert first_partial["qty_fraction"] == pytest.approx(0.5)
    assert "第一目标位" in first_partial["reason"]
    assert first_partial["meta"]["target_price"] == pytest.approx(105.0)
    assert first_partial["meta"]["stop_family"] == "structure_stop"
    assert first_partial["meta"]["stop_reference"] == "4h_ema20"
    assert first_partial["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert first_partial["meta"]["invalidation_reason"] == "breakout continuation lost 4h breakout support"
    assert first_partial["meta"]["stop_policy_source"] == "shared_taxonomy"
    assert second_partial["qty_fraction"] == pytest.approx(0.25)
    assert second_partial["meta"]["target_price"] == pytest.approx(110.0)
    assert second_partial["meta"]["runner_stop_price"] == pytest.approx(105.0)

    previews = [row for row in state.get("management_action_previews", []) if row.get("intent", {}).get("symbol") == "BTCUSDT"]
    preview_break_even = next(row for row in previews if row.get("intent", {}).get("action") == "BREAK_EVEN")
    partial_previews = [row for row in previews if row.get("intent", {}).get("action") == "PARTIAL_TAKE_PROFIT"]
    assert preview_break_even["preview"]["intent"]["meta"]["stop_family"] == "structure_stop"
    assert {row["preview"]["intent"]["meta"].get("target_stage") for row in partial_previews} == {"first", "second"}
    assert all(
        row["preview"]["intent"]["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
        for row in partial_previews
    )



def test_main_v2_exit_handling_uses_taxonomy_invalidation_semantics(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-03-26T22:00:00+08:00",
                "last_signal_ids": {},
                "cooldowns": {},
                "active_orders": {},
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 94.0,
                        "unrealized_pnl": -1.2,
                        "notional": 18.8,
                        "stop_loss": 95.0,
                        "taxonomy_stop_loss": 95.0,
                        "take_profit": 110.0,
                        "status": "OPEN",
                        "intent_id": "v2-trend-breakout-btcusdt",
                        "signal_id": "v2-trend-breakout-btcusdt",
                        "source": "paper_execution",
                        "tracked_from_snapshot": False,
                        "tracked_from_intent": True,
                        "opened_at_bj": "2026-03-26T21:00:00+08:00",
                        "updated_at_bj": "2026-03-26T21:00:00+08:00",
                        "last_synced_from": "executed_intent",
                        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                        "invalidation_reason": "breakout continuation lost 4h breakout support",
                        "stop_family": "structure_stop",
                        "stop_reference": "4h_ema20",
                        "stop_policy_source": "shared_taxonomy"
                    }
                },
                "management_suggestions": [],
                "management_action_previews": []
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 94.0,
                        "unrealized_pnl": -1.2,
                        "notional": 18.8,
                        "leverage": 2.0
                    }
                ],
                "open_orders": []
            }
        )
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    exit_rows = [
        row for row in state.get("management_suggestions", [])
        if row.get("symbol") == "BTCUSDT" and row.get("action") == "EXIT"
    ]
    assert exit_rows
    exit_row = exit_rows[0]
    assert "breakout continuation lost 4h breakout support" in exit_row["reason"]
    assert "trend_breakout_failure_below_4h_ema20" in exit_row["reason"]
    assert exit_row["meta"]["stop_family"] == "structure_stop"
    assert exit_row["meta"]["stop_reference"] == "4h_ema20"
    assert exit_row["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    assert exit_row["meta"]["invalidation_reason"] == "breakout continuation lost 4h breakout support"
    assert state.get("latest_lifecycle", {}).get("BTCUSDT", {}).get("invalidation_source") == "trend_breakout_failure_below_4h_ema20"

    previews = [row for row in state.get("management_action_previews", []) if row.get("intent", {}).get("action") == "EXIT"]
    assert previews
    assert previews[0]["preview"]["intent"]["meta"]["stop_family"] == "structure_stop"
    assert previews[0]["preview"]["intent"]["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"


def test_main_v2_emits_review_ready_lifecycle_summary_for_taxonomy_aware_exits(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-03-26T22:00:00+08:00",
                "last_signal_ids": {},
                "cooldowns": {},
                "active_orders": {},
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 110.0,
                        "unrealized_pnl": 2.0,
                        "notional": 22.0,
                        "stop_loss": 95.0,
                        "taxonomy_stop_loss": 95.0,
                        "take_profit": 110.0,
                        "status": "OPEN",
                        "intent_id": "v2-trend-breakout-btcusdt",
                        "signal_id": "v2-trend-breakout-btcusdt",
                        "source": "paper_execution",
                        "tracked_from_snapshot": False,
                        "tracked_from_intent": True,
                        "opened_at_bj": "2026-03-26T21:00:00+08:00",
                        "updated_at_bj": "2026-03-26T21:00:00+08:00",
                        "last_synced_from": "executed_intent",
                        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                        "invalidation_reason": "breakout continuation lost 4h breakout support",
                        "stop_family": "structure_stop",
                        "stop_reference": "4h_ema20",
                        "stop_policy_source": "shared_taxonomy"
                    }
                },
                "management_suggestions": [],
                "management_action_previews": []
            }
        )
    )
    account_path.write_text(json.dumps({
        "equity": 125000.0,
        "available_balance": 96000.0,
        "futures_wallet_balance": 118500.0,
        "open_positions": [{
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": 0.2,
            "entry_price": 100.0,
            "mark_price": 110.0,
            "unrealized_pnl": 2.0,
            "notional": 22.0,
            "leverage": 2.0
        }],
        "open_orders": []
    }))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    summary = state["lifecycle_summary"]
    assert summary["management_action_counts"] == {"BREAK_EVEN": 1, "PARTIAL_TAKE_PROFIT": 2}
    assert summary["review_actions"][0]["action"] == "BREAK_EVEN"
    assert summary["review_actions"][0]["stop_family"] == "structure_stop"
    assert summary["review_actions"][0]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"
    partial_review_rows = [row for row in summary["review_actions"] if row["action"] == "PARTIAL_TAKE_PROFIT"]
    assert len(partial_review_rows) == 2
    first_review = next(row for row in partial_review_rows if row.get("target_stage") == "first")
    second_review = next(row for row in partial_review_rows if row.get("target_stage") == "second")
    assert first_review["target_price"] == pytest.approx(105.0)
    assert second_review["target_price"] == pytest.approx(110.0)
    assert summary["leaders"][0]["invalidation_reason"] == "breakout continuation lost 4h breakout support"


def test_main_v2_surfaces_defensive_regime_de_risk_action_path(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-03-26T22:00:00+08:00",
                "last_signal_ids": {},
                "cooldowns": {},
                "active_orders": {},
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.2,
                        "entry_price": 100.0,
                        "mark_price": 104.0,
                        "unrealized_pnl": 0.8,
                        "notional": 20.8,
                        "stop_loss": 95.0,
                        "taxonomy_stop_loss": 95.0,
                        "take_profit": None,
                        "status": "OPEN",
                        "intent_id": "v2-trend-breakout-btcusdt",
                        "signal_id": "v2-trend-breakout-btcusdt",
                        "source": "paper_execution",
                        "tracked_from_snapshot": False,
                        "tracked_from_intent": True,
                        "opened_at_bj": "2026-03-26T21:00:00+08:00",
                        "updated_at_bj": "2026-03-26T21:00:00+08:00",
                        "last_synced_from": "executed_intent",
                        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                        "invalidation_reason": "breakout continuation lost 4h breakout support",
                        "stop_family": "structure_stop",
                        "stop_reference": "4h_ema20",
                        "stop_policy_source": "shared_taxonomy"
                    }
                },
                "management_suggestions": [],
                "management_action_previews": []
            }
        )
    )
    account_path.write_text(json.dumps({
        "equity": 125000.0,
        "available_balance": 96000.0,
        "futures_wallet_balance": 118500.0,
        "open_positions": [{
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": 0.2,
            "entry_price": 100.0,
            "mark_price": 104.0,
            "unrealized_pnl": 0.8,
            "notional": 20.8,
            "leverage": 2.0
        }],
        "open_orders": []
    }))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "classify_regime",
        lambda *args, **kwargs: RegimeSnapshot(
            label="CRASH_DEFENSIVE",
            confidence=0.93,
            risk_multiplier=0.35,
            execution_policy="downsize",
        ),
    )
    monkeypatch.setattr(
        main_module,
        "summarize_derivatives_risk",
        lambda *args, **kwargs: {"late_stage_heat": "cascade", "execution_hazard": "compress_risk"},
    )

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    de_risk_rows = [
        row for row in state.get("management_suggestions", [])
        if row.get("symbol") == "BTCUSDT" and row.get("action") == "DE_RISK"
    ]
    assert de_risk_rows
    de_risk = de_risk_rows[0]
    assert de_risk["qty_fraction"] == pytest.approx(0.25)
    assert de_risk["meta"]["regime_label"] == "CRASH_DEFENSIVE"
    assert de_risk["meta"]["execution_policy"] == "downsize"
    assert de_risk["meta"]["stop_family"] == "structure_stop"
    assert de_risk["meta"]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"

    previews = [
        row for row in state.get("management_action_previews", [])
        if row.get("intent", {}).get("symbol") == "BTCUSDT" and row.get("intent", {}).get("action") == "DE_RISK"
    ]
    assert previews
    assert previews[0]["preview"]["preview_kind"] == "REDUCE_ONLY_DE_RISK_CLOSE"
    assert previews[0]["preview"]["payload"]["create_order"]["quantity"] == pytest.approx(0.05)

    summary = state["lifecycle_summary"]
    assert summary["management_action_counts"]["DE_RISK"] == 1
    assert summary["review_actions"][0]["action"] == "DE_RISK"
    assert summary["review_actions"][0]["qty_fraction"] == pytest.approx(0.25)

def test_load_account_snapshot_rejects_non_list_open_positions(tmp_path):
    account_path = tmp_path / "account_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 900.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": {"symbol": "BTCUSDT", "qty": 1.0},
                "open_orders": [],
            }
        )
    )

    with pytest.raises(ValueError, match="open_positions must be a list"):
        main_module.load_account_snapshot(account_path)


def test_load_account_snapshot_rejects_non_object_meta(tmp_path):
    account_path = tmp_path / "account_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 900.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [],
                "open_orders": [],
                "meta": [["account_type", "paper"]],
            }
        )
    )

    with pytest.raises(ValueError, match="meta must be an object"):
        main_module.load_account_snapshot(account_path)

def test_load_account_snapshot_rejects_invalid_open_position_qty(tmp_path):
    account_path = tmp_path / "account_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 900.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": "not-a-number",
                        "entry_price": 100.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )

    with pytest.raises(ValueError, match=r"open_positions\[0\]\.qty"):
        main_module.load_account_snapshot(account_path)

def test_load_v1_account_snapshot_rejects_non_list_positions(tmp_path):
    account_path = tmp_path / "account_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "futures": {
                    "total_wallet_balance": 1000.0,
                    "available_balance": 900.0,
                    "positions": {"symbol": "BTCUSDT", "qty": 1.0},
                    "open_orders": [],
                }
            }
        )
    )

    with pytest.raises(ValueError, match="futures.positions must be a list"):
        main_module.load_account_snapshot(account_path)


def test_load_v1_account_snapshot_rejects_non_list_open_orders(tmp_path):
    account_path = tmp_path / "account_snapshot.json"
    account_path.write_text(
        json.dumps(
            {
                "futures": {
                    "total_wallet_balance": 1000.0,
                    "available_balance": 900.0,
                    "positions": [],
                    "open_orders": {"symbol": "BTCUSDT"},
                }
            }
        )
    )

    with pytest.raises(ValueError, match="futures.open_orders must be a list"):
        main_module.load_account_snapshot(account_path)
