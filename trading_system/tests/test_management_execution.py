import pytest

from trading_system.app.portfolio.lifecycle import build_management_action_intents
from trading_system.app.storage.state_store import RuntimeStateV2


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
