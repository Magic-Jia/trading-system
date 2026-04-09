import json

import pytest

from trading_system.app import main as main_module


def test_main_dry_run_cycle_surfaces_gap_through_suggestions_and_previews(monkeypatch, tmp_path):
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
                        "take_profit": 107.0,
                        "first_target_price": 105.0,
                        "first_target_source": "fallback_1r",
                        "second_target_price": 110.0,
                        "second_target_source": "fixed_2r",
                        "original_position_qty": 2.0,
                        "first_target_status": "pending",
                        "first_target_hit": False,
                        "first_target_filled_qty": 0.0,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
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
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "dry-run")
    monkeypatch.setattr(main_module, "load_market_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "load_derivatives_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "generate_trend_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "allocate_candidates", lambda **kwargs: [])

    main_module.main()

    state = json.loads(output_path.read_text())
    btc_rows = [row for row in state["management_suggestions"] if row["symbol"] == "BTCUSDT"]
    assert [row["meta"]["target_stage"] for row in btc_rows if row["action"] == "PARTIAL_TAKE_PROFIT"] == ["first", "second"]
    preview_rows = [
        row
        for row in state["management_action_previews"]
        if row["intent"]["symbol"] == "BTCUSDT" and row["intent"]["action"] == "PARTIAL_TAKE_PROFIT"
    ]
    assert [row["preview"]["intent"]["meta"].get("target_stage") for row in preview_rows] == ["first", "second"]
    assert [row["preview"]["intent"]["qty"] for row in preview_rows] == [1.0, 0.5]
    assert state["lifecycle_summary"]["management_action_counts"]["PARTIAL_TAKE_PROFIT"] == 2
    position = state["positions"]["BTCUSDT"]
    assert position["remaining_position_qty"] == pytest.approx(2.0)
    assert position["first_target_status"] == "pending"
    assert position["first_target_hit"] is False
    assert position["first_target_filled_qty"] == pytest.approx(0.0)
    assert position["second_target_status"] == "pending"
    assert position["second_target_hit"] is False
    assert position["second_target_filled_qty"] == pytest.approx(0.0)
    assert position["runner_protected"] is False
    assert position["runner_stop_price"] is None
    lifecycle_summary = state["lifecycle_summary"]
    leader = lifecycle_summary["leaders"][0]
    assert leader["first_target_hit"] is False
    assert leader["second_target_hit"] is False
    assert leader["runner_protected"] is False
    assert leader["scale_out_plan"] == {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"}
    assert leader["second_target_source"] == "fixed_2r"
    assert lifecycle_summary["audit_target_states"] == [
        {"symbol": "BTCUSDT", "first_target_status": "pending", "second_target_status": "pending"}
    ]


def test_main_dry_run_cycle_surfaces_terminalized_stage_in_lifecycle_audit(monkeypatch, tmp_path):
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
                        "take_profit": 107.0,
                        "first_target_price": 105.0,
                        "first_target_source": "legacy_take_profit_mapped",
                        "second_target_price": 110.0,
                        "second_target_source": "fixed_2r",
                        "original_position_qty": 1.0,
                        "first_target_status": "filled",
                        "first_target_hit": True,
                        "first_target_filled_qty": 0.5,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
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
    position = state["positions"]["BTCUSDT"]
    assert position["second_target_status"] == "satisfied_by_external_reduction"
    assert state["lifecycle_summary"]["audit_target_states"] == [
        {
            "symbol": "BTCUSDT",
            "first_target_status": "filled",
            "second_target_status": "satisfied_by_external_reduction",
        }
    ]


def test_main_paper_cycle_terminalizes_unreachable_second_stage_after_first_stage_writeback(monkeypatch, tmp_path):
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
                        "mark_price": 110.5,
                        "stop_loss": 95.0,
                        "first_target_price": 105.0,
                        "first_target_source": "fallback_1r",
                        "second_target_price": 110.0,
                        "second_target_source": "fixed_2r",
                        "original_position_qty": 0.28,
                        "first_target_status": "pending",
                        "first_target_hit": False,
                        "first_target_filled_qty": 0.0,
                        "second_target_status": "pending",
                        "second_target_hit": False,
                        "second_target_filled_qty": 0.0,
                        "runner_protected": False,
                        "runner_stop_price": None,
                        "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
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
                        "mark_price": 110.5,
                        "unrealized_pnl": 2.94,
                        "notional": 30.94,
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
    assert position["runner_protected"] is False
    assert position["runner_stop_price"] is None
    assert not any(row.get("action") == "PARTIAL_TAKE_PROFIT" for row in state["management_suggestions"])
    assert not any(
        row.get("intent", {}).get("action") == "PARTIAL_TAKE_PROFIT"
        for row in state["management_action_previews"]
    )
    assert state["lifecycle_summary"]["management_action_counts"].get("PARTIAL_TAKE_PROFIT", 0) == 0
    assert state["lifecycle_summary"]["audit_target_states"] == [
        {
            "symbol": "BTCUSDT",
            "first_target_status": "filled",
            "second_target_status": "satisfied_by_external_reduction",
        }
    ]
