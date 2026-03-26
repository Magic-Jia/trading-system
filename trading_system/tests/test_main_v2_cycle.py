import importlib
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
from trading_system.app.signals.short_engine import generate_short_candidates as generate_real_short_candidates


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
        "tracked_count": 3,
        "state_counts": {
            "INIT": 3,
            "CONFIRM": 0,
            "PAYLOAD": 0,
            "PROTECT": 0,
            "EXIT": 0,
        },
        "pending_confirmation_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "protected_symbols": [],
        "exit_symbols": [],
        "attention_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.037671, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "ETHUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.02648, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.020207, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
        ],
    }
    assert state.get("rotation_summary") == {
        "universe_count": 5,
        "candidate_count": 2,
        "accepted_symbols": [],
        "executed_symbols": [],
        "leaders": [
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


def test_main_v2_cycle_filters_crowded_long_trend_candidates_from_runtime_state(monkeypatch, tmp_path, load_fixture):
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
    trend_candidates = [row for row in state["latest_candidates"] if row.get("engine") == "trend"]

    assert [row["symbol"] for row in trend_candidates] == ["ETHUSDT"]
    assert trend_candidates[0]["timeframe_meta"]["derivatives"] == {
        "crowding_bias": "crowded_long",
        "basis_bps": 19.0,
    }
    assert all(row["symbol"] != "BTCUSDT" for row in trend_candidates)


def test_main_v2_cycle_filters_crowded_long_rotation_candidates_from_runtime_state(monkeypatch, tmp_path, load_fixture):
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
    rotation_universe = [row["symbol"] for row in state["latest_universes"]["rotation_universe"]]
    rotation_candidates = [row for row in state["latest_candidates"] if row.get("engine") == "rotation"]

    assert "SOLUSDT" in rotation_universe
    assert [row["symbol"] for row in rotation_candidates] == ["LINKUSDT", "ADAUSDT"]
    assert state["rotation_summary"]["candidate_count"] == 2
    assert [row["symbol"] for row in state["rotation_summary"]["leaders"]] == ["LINKUSDT", "ADAUSDT"]
    assert all(row["symbol"] != "SOLUSDT" for row in rotation_candidates)


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
    assert payload["regime"]["rotation"]["candidate_count"] == 2
    assert payload["regime"]["rotation"]["accepted_symbols"] == []
    assert payload["regime"]["rotation"]["executed_symbols"] == []
    assert [row["symbol"] for row in payload["regime"]["rotation"]["leaders"]] == ["LINKUSDT", "ADAUSDT"]
    assert payload["portfolio"]["lifecycle_summary"] == {
        "tracked_count": 3,
        "state_counts": {
            "INIT": 3,
            "CONFIRM": 0,
            "PAYLOAD": 0,
            "PROTECT": 0,
            "EXIT": 0,
        },
        "pending_confirmation_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "protected_symbols": [],
        "exit_symbols": [],
        "attention_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.037671, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "ETHUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.02648, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "INIT",
                "r_multiple": pytest.approx(0.020207, abs=1e-6),
                "reason_codes": ["init_waiting_confirmation"],
            },
        ],
    }


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
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
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
    assert all(row.get("invalidation_source") == "short_structure_reclaim_above_4h_ema50" for row in short_candidate_rows)

    accepted_short = [
        row
        for row in state.get("latest_allocations", [])
        if row.get("engine") == "short" and row.get("status") in {"ACCEPTED", "DOWNSIZED"}
    ]
    assert accepted_short
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in accepted_short)
    assert all(row.get("invalidation_source") == "short_structure_reclaim_above_4h_ema50" for row in accepted_short)
    assert all(row.get("execution", {}).get("status") == "SKIPPED" for row in accepted_short)
    assert all(row.get("execution", {}).get("reason") == "short_execution_not_enabled" for row in accepted_short)


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


def test_main_v2_stdout_omits_crowded_short_rejections_from_short_reporting(monkeypatch, tmp_path, load_fixture, capsys):
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

    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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

    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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
    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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
    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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
    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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
    assert payload["regime"]["short"] == {
        "universe_count": 2,
        "candidate_count": 0,
        "accepted_symbols": [],
        "deferred_execution_symbols": [],
        "leaders": [],
    }


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
    assert all(row.get("invalidation_source") == "trend_structure_loss_below_4h_ema50" for row in trend_candidates)
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    trend_allocations = [row for row in accepted_allocations if row.get("engine") == "trend"]
    assert trend_allocations
    assert all(float(row.get("stop_loss", 0.0) or 0.0) > 0 for row in trend_allocations)
    assert all(row.get("invalidation_source") == "trend_structure_loss_below_4h_ema50" for row in trend_allocations)
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


def test_main_v2_blocks_candidate_missing_explicit_stop_or_invalidation_before_execution(monkeypatch, tmp_path, load_fixture):
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

    def should_not_execute(self, order, state):
        raise AssertionError("executor should not run for rejected no-stop candidates")

    monkeypatch.setattr(main_module.OrderExecutor, "execute", should_not_execute)

    main_module.main()

    state = json.loads(Path(output_path).read_text())
    accepted_allocations = [row for row in state.get("latest_allocations", []) if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    assert accepted_allocations
    blocked = [row for row in accepted_allocations if row.get("execution", {}).get("status") == "BLOCKED"]
    assert blocked
    assert all("显式止损" in row.get("execution", {}).get("reason", "") for row in blocked)
    assert all("invalidation_source" in row.get("execution", {}).get("reason", "") for row in blocked)
    assert state.get("active_orders") == {}
    assert all(not position.get("tracked_from_intent") for position in state.get("positions", {}).values())


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
        lambda signal, account, config: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
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
