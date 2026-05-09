import pytest
import json
from dataclasses import replace

from trading_system.app import main as main_module
from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.portfolio.lifecycle import build_management_action_intents, evaluate_position
from trading_system.app.portfolio.positions import apply_management_action_fill
from trading_system.app.portfolio.target_management import terminalize_all_unreachable_stages
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import ManagementActionIntent


def _manageable_position(**overrides):
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "stop_loss": 95.0,
        "status": "OPEN",
    }
    position.update(overrides)
    return position


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("side", "HEDGE"),
        ("entry_price", True),
        ("entry_price", float("inf")),
        ("mark_price", False),
        ("mark_price", float("nan")),
        ("stop_loss", True),
        ("stop_loss", float("-inf")),
    ],
)
def test_evaluate_position_rejects_present_invalid_position_boundaries(field, value):
    with pytest.raises(ValueError, match=field):
        evaluate_position(_manageable_position(**{field: value}))


def test_evaluate_position_preserves_missing_mark_price_no_suggestion_behavior():
    position = _manageable_position()
    position.pop("mark_price")

    assert evaluate_position(position) == []


def test_evaluate_position_preserves_valid_break_even_suggestion():
    suggestions = evaluate_position(_manageable_position(mark_price=106.0))

    assert [row["action"] for row in suggestions] == ["BREAK_EVEN"]
    assert suggestions[0]["suggested_stop_loss"] == pytest.approx(100.0)


def test_build_management_action_intents_uses_original_position_basis_and_stage_fill_progress():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.6,
                "remaining_position_qty": 0.6,
                "original_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.2,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.1,
                "min_order_qty": 0.1,
            }
        },
    )
    rows = [
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "action": "PARTIAL_TAKE_PROFIT",
            "qty_fraction": 0.25,
            "reference_price": 110.2,
            "meta": {"exit_trigger": "second_target_hit", "target_stage": "second", "fraction_basis": "original_position"},
        }
    ]

    intents = build_management_action_intents(state, rows)

    assert intents[0].qty == pytest.approx(0.5)


def test_build_management_action_intents_skips_second_stage_when_reconciled_qty_falls_below_min_order_qty():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.07,
                "remaining_position_qty": 0.07,
                "original_position_qty": 0.28,
                "entry_price": 100.0,
                "mark_price": 110.2,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 0.14,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.01,
                "min_order_qty": 0.1,
            }
        },
    )

    intents = build_management_action_intents(
        state,
        [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "action": "PARTIAL_TAKE_PROFIT",
                "qty_fraction": 0.25,
                "reference_price": 110.2,
                "meta": {"exit_trigger": "second_target_hit", "target_stage": "second", "fraction_basis": "original_position"},
            }
        ],
    )

    assert intents == []


def _paper_app_config(tmp_path):
    state_file = tmp_path / "runtime_state.json"
    return replace(
        DEFAULT_CONFIG,
        state_file=state_file,
        data_dir=state_file.parent,
        execution=replace(DEFAULT_CONFIG.execution, mode="paper"),
    )


def test_execute_management_actions_writes_back_first_then_second_stage_and_runner_state(tmp_path):
    executor = OrderExecutor(_paper_app_config(tmp_path), mode="paper")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "remaining_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )
    intents = [
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-first",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=2.0,
            qty=1.0,
            reference_price=110.5,
            meta={"target_stage": "first", "exit_trigger": "first_target_hit", "fraction_basis": "original_position"},
        ),
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-second",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=1.0,
            qty=0.5,
            reference_price=110.5,
            meta={
                "target_stage": "second",
                "exit_trigger": "second_target_hit",
                "fraction_basis": "original_position",
                "runner_protected": True,
                "runner_stop_price": 105.0,
            },
        ),
    ]

    results = executor.execute_management_actions(intents, state)

    assert [row["intent"]["action"] for row in results] == ["PARTIAL_TAKE_PROFIT", "PARTIAL_TAKE_PROFIT"]
    assert state.positions["BTCUSDT"]["qty"] == pytest.approx(0.5)
    assert state.positions["BTCUSDT"]["remaining_position_qty"] == pytest.approx(0.5)
    assert state.positions["BTCUSDT"]["first_target_status"] == "filled"
    assert state.positions["BTCUSDT"]["second_target_status"] == "filled"
    assert state.positions["BTCUSDT"]["runner_protected"] is True
    assert state.positions["BTCUSDT"]["runner_stop_price"] == pytest.approx(105.0)


def test_execute_management_action_does_not_write_back_snapshot_only_stop_updates(tmp_path):
    executor = OrderExecutor(_paper_app_config(tmp_path), mode="paper")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.62,
                "entry_price": 62850.0,
                "mark_price": 64120.0,
                "stop_loss": 61593.0,
                "status": "OPEN",
                "source": "account_snapshot",
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )

    result = executor.execute_management_action(
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-break-even",
            symbol="BTCUSDT",
            action="BREAK_EVEN",
            side="LONG",
            position_qty=0.62,
            stop_loss=62850.0,
            reference_price=64120.0,
        ),
        state,
    )

    assert result["result"]["status"] == "FILLED"
    assert result["result"]["updated_stop_loss"] == pytest.approx(62850.0)
    assert result["result"]["writeback_skipped"] is True
    assert state.positions["BTCUSDT"]["stop_loss"] == pytest.approx(61593.0)


def run_management_terminalization_pass(state: RuntimeStateV2) -> None:
    for symbol, position in list(state.positions.items()):
        state.positions[symbol] = terminalize_all_unreachable_stages(dict(position))


def test_run_management_terminalization_pass_marks_second_stage_satisfied_by_external_reduction_even_without_actions():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.04,
                "remaining_position_qty": 0.04,
                "original_position_qty": 1.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 0.5,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "symbol_step_size": 0.01,
                "min_order_qty": 0.1,
            }
        },
    )

    run_management_terminalization_pass(state)

    assert state.positions["BTCUSDT"]["second_target_status"] == "satisfied_by_external_reduction"
    assert state.positions["BTCUSDT"]["second_target_hit"] is False


def test_main_runs_terminalization_pass_when_no_management_intents(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-04-09T20:00:00+08:00",
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.04,
                        "remaining_position_qty": 0.04,
                        "entry_price": 100.0,
                        "mark_price": 110.5,
                        "stop_loss": 95.0,
                        "first_target_price": 105.0,
                        "second_target_price": 110.0,
                        "original_position_qty": 1.0,
                        "first_target_status": "filled",
                        "first_target_hit": True,
                        "first_target_filled_qty": 0.5,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "symbol_step_size": 0.01,
                        "min_order_qty": 0.1,
                        "status": "OPEN",
                    }
                },
                "management_suggestions": [],
                "management_action_previews": [],
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 1000.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.04,
                        "entry_price": 100.0,
                        "mark_price": 110.5,
                        "unrealized_pnl": 0.42,
                        "notional": 4.42,
                        "leverage": 3.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps({}))
    deriv_path.write_text(json.dumps({}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "load_market_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    assert state["positions"]["BTCUSDT"]["second_target_status"] == "satisfied_by_external_reduction"
    assert state["positions"]["BTCUSDT"]["second_target_hit"] is False
    assert not any(
        row.get("action") == "PARTIAL_TAKE_PROFIT" and (row.get("meta") or {}).get("target_stage") == "second"
        for row in state.get("management_suggestions", [])
    )
    assert state["lifecycle_summary"]["management_action_counts"].get("PARTIAL_TAKE_PROFIT", 0) == 0


def test_main_paper_cycle_executes_break_even_before_target_scale_out(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-04-09T20:00:00+08:00",
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 2.0,
                        "remaining_position_qty": 2.0,
                        "entry_price": 100.0,
                        "mark_price": 110.5,
                        "stop_loss": 95.0,
                        "first_target_price": 105.0,
                        "second_target_price": 110.0,
                        "original_position_qty": 2.0,
                        "first_target_status": "pending",
                        "first_target_hit": False,
                        "first_target_filled_qty": 0.0,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "symbol_step_size": 0.1,
                        "min_order_qty": 0.1,
                        "status": "OPEN",
                    }
                },
                "management_suggestions": [],
                "management_action_previews": [],
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 1000.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 2.0,
                        "entry_price": 100.0,
                        "mark_price": 110.5,
                        "unrealized_pnl": 21.0,
                        "notional": 221.0,
                        "leverage": 3.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps({}))
    deriv_path.write_text(json.dumps({}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.setattr(main_module, "load_market_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    position = state["positions"]["BTCUSDT"]
    assert position["qty"] == pytest.approx(0.5)
    assert position["remaining_position_qty"] == pytest.approx(0.5)
    assert position["stop_loss"] == pytest.approx(100.0)
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)
    assert position["second_target_status"] == "filled"
    assert position["second_target_hit"] is True
    assert position["second_target_filled_qty"] == pytest.approx(0.5)
    assert position["runner_protected"] is True
    assert position["runner_stop_price"] == pytest.approx(105.0)
    assert not any(row.get("action") == "BREAK_EVEN" for row in state.get("management_suggestions", []))
    assert not any(row.get("action") == "PARTIAL_TAKE_PROFIT" for row in state.get("management_suggestions", []))
    assert state["lifecycle_summary"]["management_action_counts"].get("PARTIAL_TAKE_PROFIT", 0) == 0


def test_main_paper_cycle_terminalizes_second_stage_after_first_stage_writeback(monkeypatch, tmp_path):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    output_path.write_text(
        json.dumps(
            {
                "updated_at_bj": "2026-04-09T20:00:00+08:00",
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.28,
                        "remaining_position_qty": 0.28,
                        "entry_price": 100.0,
                        "mark_price": 110.2,
                        "stop_loss": 95.0,
                        "first_target_price": 105.0,
                        "second_target_price": 110.0,
                        "original_position_qty": 0.28,
                        "first_target_status": "pending",
                        "first_target_hit": False,
                        "first_target_filled_qty": 0.0,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "symbol_step_size": 0.01,
                        "min_order_qty": 0.1,
                        "status": "OPEN",
                    }
                },
                "management_suggestions": [],
                "management_action_previews": [],
            }
        )
    )
    account_path.write_text(
        json.dumps(
            {
                "equity": 1000.0,
                "available_balance": 1000.0,
                "futures_wallet_balance": 1000.0,
                "open_positions": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "qty": 0.28,
                        "entry_price": 100.0,
                        "mark_price": 110.2,
                        "unrealized_pnl": 2.86,
                        "notional": 30.86,
                        "leverage": 3.0,
                    }
                ],
                "open_orders": [],
            }
        )
    )
    market_path.write_text(json.dumps({}))
    deriv_path.write_text(json.dumps({}))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "paper")
    monkeypatch.setattr(main_module, "load_market_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    position = state["positions"]["BTCUSDT"]
    assert position["qty"] == pytest.approx(0.14)
    assert position["remaining_position_qty"] == pytest.approx(0.14)
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(0.14)
    assert position["second_target_status"] == "satisfied_by_external_reduction"
    assert position["second_target_hit"] is False
    assert not any(
        row.get("action") == "PARTIAL_TAKE_PROFIT" and (row.get("meta") or {}).get("target_stage") == "second"
        for row in state.get("management_suggestions", [])
    )
    assert state["lifecycle_summary"]["management_action_counts"].get("PARTIAL_TAKE_PROFIT", 0) == 0
    assert state["lifecycle_summary"]["audit_target_states"] == [
        {
            "symbol": "BTCUSDT",
            "first_target_status": "filled",
            "second_target_status": "satisfied_by_external_reduction",
        }
    ]


def test_execute_management_actions_stops_same_round_sequence_when_first_stage_remains_pending(monkeypatch, tmp_path):
    executor = OrderExecutor(_paper_app_config(tmp_path), mode="paper")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "remaining_position_qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )
    intents = [
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-first",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=2.0,
            qty=1.0,
            reference_price=110.5,
            meta={"target_stage": "first", "exit_trigger": "first_target_hit", "fraction_basis": "original_position"},
        ),
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-second",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=1.0,
            qty=0.5,
            reference_price=110.5,
            meta={
                "target_stage": "second",
                "exit_trigger": "second_target_hit",
                "fraction_basis": "original_position",
                "runner_protected": True,
                "runner_stop_price": 105.0,
            },
        ),
    ]

    def fake_apply(state, intent):
        position = dict(state.positions[intent.symbol])
        if intent.meta["target_stage"] == "first":
            position["qty"] = 1.4
            position["remaining_position_qty"] = 1.4
            position["first_target_filled_qty"] = 0.6
            position["first_target_status"] = "pending"
            position["first_target_hit"] = False
            state.positions[intent.symbol] = position
            return position
        raise AssertionError("second-stage intent should not run after incomplete first stage")

    monkeypatch.setattr("trading_system.app.execution.executor.apply_management_action_fill", fake_apply)

    results = executor.execute_management_actions(intents, state)

    assert [row["intent"]["intent_id"] for row in results] == ["mgmt-btcusdt-partial-first"]
    assert state.positions["BTCUSDT"]["second_target_status"] == "pending"


def test_apply_management_action_fill_keeps_runner_unprotected_on_partial_second_stage_fill():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "remaining_position_qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )

    updated = apply_management_action_fill(
        state,
        ManagementActionIntent(
            intent_id="mgmt-btcusdt-partial-second",
            symbol="BTCUSDT",
            action="PARTIAL_TAKE_PROFIT",
            side="LONG",
            position_qty=1.0,
            qty=0.2,
            reference_price=110.5,
            meta={
                "target_stage": "second",
                "exit_trigger": "second_target_hit",
                "fraction_basis": "original_position",
                "runner_protected": True,
                "runner_stop_price": 105.0,
            },
        ),
    )

    assert updated["second_target_status"] == "pending"
    assert updated["second_target_hit"] is False
    assert updated["runner_protected"] is False
    assert updated["runner_stop_price"] is None


def test_apply_management_action_fill_rejects_non_bool_runner_protected_on_completed_second_stage():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "remaining_position_qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 110.5,
                "stop_loss": 95.0,
                "first_target_price": 105.0,
                "second_target_price": 110.0,
                "original_position_qty": 2.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
    )

    with pytest.raises(ValueError, match="runner_protected"):
        apply_management_action_fill(
            state,
            ManagementActionIntent(
                intent_id="mgmt-btcusdt-complete-second",
                symbol="BTCUSDT",
                action="PARTIAL_TAKE_PROFIT",
                side="LONG",
                position_qty=1.0,
                qty=0.5,
                reference_price=110.5,
                meta={
                    "target_stage": "second",
                    "exit_trigger": "second_target_hit",
                    "fraction_basis": "original_position",
                    "runner_protected": "false",
                    "runner_stop_price": 105.0,
                },
            ),
        )


def _management_fill_state(**position_overrides):
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "remaining_position_qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 105.5,
        "stop_loss": 95.0,
        "first_target_price": 105.0,
        "second_target_price": 110.0,
        "original_position_qty": 2.0,
        "first_target_status": "pending",
        "first_target_hit": False,
        "first_target_filled_qty": 0.0,
        "second_target_status": "pending",
        "second_target_hit": False,
        "second_target_filled_qty": 0.0,
        "runner_protected": False,
        "runner_stop_price": None,
    }
    position.update(position_overrides)
    return RuntimeStateV2(updated_at_bj="2026-04-09T20:00:00+08:00", positions={"BTCUSDT": position})


def _partial_take_profit_intent(**overrides):
    payload = {
        "intent_id": "mgmt-btcusdt-partial-first",
        "symbol": "BTCUSDT",
        "action": "PARTIAL_TAKE_PROFIT",
        "side": "LONG",
        "position_qty": 1.0,
        "qty": 0.25,
        "reference_price": 105.5,
        "meta": {"target_stage": "first", "exit_trigger": "first_target_hit", "fraction_basis": "original_position"},
    }
    payload.update(overrides)
    return ManagementActionIntent(**payload)


@pytest.mark.parametrize("qty", [True, "1.0"])
def test_apply_management_action_fill_rejects_coerced_existing_position_qty(qty):
    state = _management_fill_state(qty=qty)

    with pytest.raises(ValueError, match="qty"):
        apply_management_action_fill(state, _partial_take_profit_intent())

    assert state.positions["BTCUSDT"]["qty"] == qty
    assert state.positions["BTCUSDT"]["first_target_filled_qty"] == 0.0


@pytest.mark.parametrize("remaining_position_qty", [True, "1.0"])
def test_apply_management_action_fill_rejects_coerced_existing_remaining_position_qty(remaining_position_qty):
    state = _management_fill_state(remaining_position_qty=remaining_position_qty)

    with pytest.raises(ValueError, match="remaining_position_qty"):
        apply_management_action_fill(state, _partial_take_profit_intent())

    assert state.positions["BTCUSDT"]["remaining_position_qty"] == remaining_position_qty
    assert state.positions["BTCUSDT"]["qty"] == 1.0
    assert state.positions["BTCUSDT"]["first_target_filled_qty"] == 0.0


@pytest.mark.parametrize("qty", [True, "0.25"])
def test_apply_management_action_fill_rejects_coerced_intent_qty(qty):
    state = _management_fill_state()

    with pytest.raises(ValueError, match="qty"):
        apply_management_action_fill(state, _partial_take_profit_intent(qty=qty))

    assert state.positions["BTCUSDT"]["qty"] == 1.0
    assert state.positions["BTCUSDT"]["first_target_filled_qty"] == 0.0


@pytest.mark.parametrize("target_stage", [1, True])
def test_apply_management_action_fill_rejects_non_string_target_stage(target_stage):
    state = _management_fill_state()

    with pytest.raises(ValueError, match="target_stage"):
        apply_management_action_fill(
            state,
            _partial_take_profit_intent(
                meta={"target_stage": target_stage, "exit_trigger": "first_target_hit", "fraction_basis": "original_position"}
            ),
        )

    assert state.positions["BTCUSDT"]["qty"] == 1.0
    assert state.positions["BTCUSDT"]["first_target_filled_qty"] == 0.0


@pytest.mark.parametrize(
    ("stage", "field", "filled_qty"),
    [
        ("first", "first_target_filled_qty", True),
        ("first", "first_target_filled_qty", "0.25"),
        ("second", "second_target_filled_qty", True),
        ("second", "second_target_filled_qty", "0.25"),
    ],
)
def test_apply_management_action_fill_rejects_coerced_existing_stage_filled_qty(stage, field, filled_qty):
    state = _management_fill_state(**{field: filled_qty})

    with pytest.raises(ValueError, match=field):
        apply_management_action_fill(
            state,
            _partial_take_profit_intent(
                intent_id=f"mgmt-btcusdt-partial-{stage}",
                meta={"target_stage": stage, "exit_trigger": f"{stage}_target_hit", "fraction_basis": "original_position"},
            ),
        )

    assert state.positions["BTCUSDT"][field] == filled_qty


@pytest.mark.parametrize("runner_stop_price", [True, "105.0", float("nan")])
def test_apply_management_action_fill_rejects_coerced_runner_stop_price_writeback(runner_stop_price):
    state = _management_fill_state(
        qty=0.75,
        remaining_position_qty=0.75,
        first_target_status="filled",
        first_target_hit=True,
        first_target_filled_qty=1.0,
        second_target_status="pending",
        second_target_hit=False,
        second_target_filled_qty=0.0,
    )

    with pytest.raises((TypeError, ValueError), match="runner_stop_price"):
        apply_management_action_fill(
            state,
            _partial_take_profit_intent(
                intent_id="mgmt-btcusdt-partial-second",
                qty=0.5,
                meta={
                    "target_stage": "second",
                    "exit_trigger": "second_target_hit",
                    "fraction_basis": "original_position",
                    "runner_protected": True,
                    "runner_stop_price": runner_stop_price,
                },
            ),
        )

    assert state.positions["BTCUSDT"]["second_target_status"] == "pending"
    assert state.positions["BTCUSDT"]["runner_protected"] is False
    assert state.positions["BTCUSDT"]["runner_stop_price"] is None
