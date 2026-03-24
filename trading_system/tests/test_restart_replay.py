import json

import pytest

from trading_system.app import main as main_module
from trading_system.app.execution import executor as executor_module
from trading_system.app.storage import state_store as state_store_module
from trading_system.app.types import AllocationDecision, EngineCandidate
from trading_system.app.risk.validator import ValidationResult


def test_main_persists_execution_recovery_state_before_post_execution_crash(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    state_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    derivatives_path = tmp_path / "derivatives_snapshot.json"

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    derivatives_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    monkeypatch.setenv("TRADING_STATE_FILE", str(state_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(derivatives_path))

    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="breakout",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90_000.0,
                timeframe_meta={"entry_tf": "4h", "confirm_tf": "1h"},
                liquidity_meta={"adv_usdt": 18_000_000_000.0},
            )
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_execution",
        lambda candidate: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, risk: (ValidationResult(True, "INFO", reasons=[], metrics={}), {}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)

    execute_calls: list[str] = []
    original_execute = main_module.OrderExecutor.execute

    def counting_execute(self, order, state):
        execute_calls.append(order.intent_id)
        return original_execute(self, order, state)

    monkeypatch.setattr(main_module.OrderExecutor, "execute", counting_execute)

    original_build_lifecycle_report = main_module.build_lifecycle_report
    crash_once = {"armed": True}

    def crash_after_execution(*args, **kwargs):
        if crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("crash-after-execution")
        return original_build_lifecycle_report(*args, **kwargs)

    monkeypatch.setattr(main_module, "build_lifecycle_report", crash_after_execution)

    with pytest.raises(RuntimeError, match="crash-after-execution"):
        main_module.main()

    assert state_path.exists()
    persisted = json.loads(state_path.read_text())
    assert persisted["last_signal_ids"].get("BTCUSDT")
    assert persisted["positions"]["BTCUSDT"]["intent_id"] == execute_calls[0]
    assert persisted["active_orders"][execute_calls[0]]["status"] == "FILLED"

    capsys.readouterr()

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    latest_allocations = json.loads(state_path.read_text())["latest_allocations"]

    assert execute_calls == [persisted["positions"]["BTCUSDT"]["intent_id"]]
    assert latest_allocations[0]["execution"] == {
        "status": "FILLED",
        "intent_id": execute_calls[0],
    }
    assert payload["portfolio"]["tracked_positions"] >= 1


def test_main_replays_checkpointed_execution_after_crash_inside_execute_before_signal_mark(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    state_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    derivatives_path = tmp_path / "derivatives_snapshot.json"

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    derivatives_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    monkeypatch.setenv("TRADING_STATE_FILE", str(state_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(derivatives_path))

    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="breakout",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90_000.0,
                timeframe_meta={"entry_tf": "4h", "confirm_tf": "1h"},
                liquidity_meta={"adv_usdt": 18_000_000_000.0},
            )
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_execution",
        lambda candidate: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, risk: (ValidationResult(True, "INFO", reasons=[], metrics={}), {}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    crash_once = {"armed": True}

    def crash_inside_execute(self, order, result):
        if crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("crash-inside-execute")

    monkeypatch.setattr(main_module.OrderExecutor, "append_log", crash_inside_execute)

    execute_calls: list[str] = []
    original_execute = main_module.OrderExecutor.execute

    def counting_execute(self, order, state):
        execute_calls.append(order.intent_id)
        return original_execute(self, order, state)

    monkeypatch.setattr(main_module.OrderExecutor, "execute", counting_execute)

    with pytest.raises(RuntimeError, match="crash-inside-execute"):
        main_module.main()

    assert state_path.exists()
    persisted = json.loads(state_path.read_text())
    assert persisted["positions"]["BTCUSDT"]["intent_id"] == execute_calls[0]
    assert persisted["active_orders"][execute_calls[0]]["status"] == "FILLED"
    assert persisted["last_signal_ids"] == {}

    capsys.readouterr()

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    healed = json.loads(state_path.read_text())

    assert execute_calls == [persisted["positions"]["BTCUSDT"]["intent_id"]]
    assert healed["last_signal_ids"].get("BTCUSDT")
    assert healed["latest_allocations"][0]["execution"] == {
        "status": "FILLED",
        "intent_id": execute_calls[0],
    }
    assert payload["portfolio"]["tracked_positions"] >= 1


def test_main_replays_logged_execution_after_persist_state_crash_inside_execute(
    monkeypatch,
    tmp_path,
    load_fixture,
    capsys,
):
    state_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    derivatives_path = tmp_path / "derivatives_snapshot.json"
    exec_log_path = tmp_path / "execution_log.jsonl"

    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    derivatives_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))

    monkeypatch.setenv("TRADING_STATE_FILE", str(state_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(derivatives_path))
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log_path)

    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="breakout",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90_000.0,
                timeframe_meta={"entry_tf": "4h", "confirm_tf": "1h"},
                liquidity_meta={"adv_usdt": 18_000_000_000.0},
            )
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_execution",
        lambda candidate: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, risk: (ValidationResult(True, "INFO", reasons=[], metrics={}), {}),
    )
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    crash_once = {"armed": True}
    original_save = state_store_module.StateStore.save

    def crash_first_save(self, state):
        if crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("persist-state-crash")
        return original_save(self, state)

    monkeypatch.setattr(state_store_module.StateStore, "save", crash_first_save)

    execute_calls: list[str] = []
    original_execute = main_module.OrderExecutor.execute

    def counting_execute(self, order, state):
        execute_calls.append(order.intent_id)
        return original_execute(self, order, state)

    monkeypatch.setattr(main_module.OrderExecutor, "execute", counting_execute)

    with pytest.raises(RuntimeError, match="persist-state-crash"):
        main_module.main()

    assert exec_log_path.exists()
    assert not state_path.exists()

    capsys.readouterr()

    main_module.main()

    payload = json.loads(capsys.readouterr().out)
    healed = json.loads(state_path.read_text())

    assert execute_calls == [healed["positions"]["BTCUSDT"]["intent_id"]]
    assert healed["active_orders"][execute_calls[0]]["status"] == "FILLED"
    assert healed["last_signal_ids"].get("BTCUSDT")
    assert healed["latest_allocations"][0]["execution"] == {
        "status": "FILLED",
        "intent_id": execute_calls[0],
    }
    assert payload["portfolio"]["tracked_positions"] >= 1
