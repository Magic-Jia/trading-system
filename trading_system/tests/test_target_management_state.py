import pytest

from trading_system.app.portfolio.target_management import (
    derive_target_management_fields,
    ensure_target_management_state,
)
from trading_system.app.portfolio.positions import apply_executed_intent, sync_positions_from_account
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import AccountSnapshot, OrderIntent, PositionSnapshot


def test_derive_target_management_fields_prefers_structure_target_between_1r_and_2r():
    payload = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=107.5,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert payload["first_target_price"] == pytest.approx(107.5)
    assert payload["first_target_source"] == "structure"
    assert payload["second_target_price"] == pytest.approx(110.0)
    assert payload["scale_out_plan"] == {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"}


def test_derive_target_management_fields_accepts_structure_target_exactly_at_1r():
    payload = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=105.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert payload["first_target_price"] == pytest.approx(105.0)
    assert payload["first_target_source"] == "structure"


def test_derive_target_management_fields_falls_back_to_1r_when_structure_target_is_too_near_or_too_far():
    too_near = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=104.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )
    too_far = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=110.0,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )
    no_structure = derive_target_management_fields(
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        structure_target_price=None,
        legacy_take_profit=None,
        original_position_qty=2.0,
    )

    assert too_near["first_target_price"] == pytest.approx(105.0)
    assert too_near["first_target_source"] == "fallback_1r"
    assert too_far["first_target_price"] == pytest.approx(105.0)
    assert too_far["first_target_source"] == "fallback_1r"
    assert no_structure["first_target_price"] == pytest.approx(105.0)
    assert no_structure["first_target_source"] == "fallback_1r"


def test_ensure_target_management_state_maps_invalid_legacy_take_profit_back_to_1r():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 2.0,
            "take_profit": 111.0,
            "original_position_qty": 2.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(105.0)
    assert position["first_target_source"] == "fallback_1r"
    assert position["second_target_price"] == pytest.approx(110.0)


def test_ensure_target_management_state_normalizes_null_frozen_stage_fields():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 2.0,
            "first_target_price": 105.0,
            "first_target_source": "fallback_1r",
            "second_target_price": 110.0,
            "second_target_source": "fixed_2r",
            "first_target_status": None,
            "first_target_hit": None,
            "first_target_filled_qty": None,
            "second_target_status": None,
            "second_target_hit": None,
            "second_target_filled_qty": None,
            "runner_protected": None,
            "runner_stop_price": None,
        }
    )

    assert position["first_target_status"] == "pending"
    assert position["first_target_hit"] is False
    assert position["first_target_filled_qty"] == pytest.approx(0.0)
    assert position["second_target_status"] == "pending"
    assert position["second_target_hit"] is False
    assert position["second_target_filled_qty"] == pytest.approx(0.0)
    assert position["runner_protected"] is False
    assert position["runner_stop_price"] is None


def test_ensure_target_management_state_maps_legacy_take_profit_and_completed_partial():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.0,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 1.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)


def test_ensure_target_management_state_caps_completed_legacy_partial_to_stage_target_qty():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 0.8,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 1.3,
        }
    )

    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)


def test_ensure_target_management_state_keeps_legacy_stage_one_pending_when_only_partially_filled():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.3,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 0.7,
        }
    )

    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "pending"
    assert position["first_target_hit"] is False
    assert position["first_target_filled_qty"] == pytest.approx(0.7)


def test_ensure_target_management_state_terminalizes_legacy_stage_one_when_external_reduction_makes_it_unreachable():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 0.04,
            "remaining_position_qty": 0.04,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "legacy_partial_filled_qty": 0.7,
            "symbol_step_size": 0.01,
            "min_order_qty": 0.1,
        }
    )

    assert position["first_target_status"] == "satisfied_by_external_reduction"
    assert position["first_target_hit"] is False


def test_ensure_target_management_state_terminalizes_unreachable_legacy_stage_even_without_partial_history():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 0.04,
            "remaining_position_qty": 0.04,
            "take_profit": 107.0,
            "original_position_qty": 2.0,
            "symbol_step_size": 0.01,
            "min_order_qty": 0.1,
        }
    )

    assert position["first_target_status"] == "satisfied_by_external_reduction"
    assert position["first_target_hit"] is False


def test_sync_positions_from_account_preserves_existing_target_management_state(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 111.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 2.0,
                "remaining_position_qty": 0.5,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "filled",
                "second_target_hit": True,
                "second_target_filled_qty": 0.5,
                "runner_protected": True,
                "runner_stop_price": 107.0,
                "tracked_from_snapshot": True,
                "tracked_from_intent": True,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=111.0)],
        ),
    )

    assert state.positions["BTCUSDT"]["runner_protected"] is True
    assert state.positions["BTCUSDT"]["runner_stop_price"] == pytest.approx(107.0)
    assert state.positions["BTCUSDT"]["remaining_position_qty"] == pytest.approx(0.5)


def test_sync_positions_from_account_keeps_frozen_first_target_when_only_second_target_is_missing(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 2.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 108.0,
                "first_target_source": "structure",
                "original_position_qty": 2.0,
                "remaining_position_qty": 2.0,
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=2.0, entry_price=100.0, mark_price=106.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["first_target_price"] == pytest.approx(108.0)
    assert position["first_target_source"] == "structure"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


def test_sync_positions_from_account_preserves_first_stage_progress_when_backfilling_second_target(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 109.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "original_position_qty": 2.0,
                "remaining_position_qty": 1.0,
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=1.0, entry_price=100.0, mark_price=109.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


def test_ensure_target_management_state_does_not_reseed_frozen_first_stage_from_stale_legacy_partial():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.0,
            "take_profit": 107.0,
            "first_target_price": 107.0,
            "first_target_source": "legacy_take_profit_mapped",
            "first_target_status": "filled",
            "first_target_hit": True,
            "first_target_filled_qty": 1.0,
            "legacy_partial_filled_qty": 0.7,
            "original_position_qty": 2.0,
            "remaining_position_qty": 1.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


def test_apply_executed_intent_refreshes_remaining_qty_when_position_is_topped_up():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 101.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 1.0,
                "remaining_position_qty": 1.0,
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

    apply_executed_intent(
        state,
        OrderIntent(
            intent_id="intent-2",
            signal_id="signal-2",
            symbol="BTCUSDT",
            side="LONG",
            qty=0.5,
            entry_price=102.0,
            stop_loss=95.0,
            take_profit=107.0,
            status="FILLED",
            meta={},
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["qty"] == pytest.approx(1.5)
    assert position["remaining_position_qty"] == pytest.approx(1.5)
    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["second_target_price"] == pytest.approx(110.0)


def test_sync_positions_from_account_refreshes_remaining_qty_for_external_reductions(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "take_profit": 107.0,
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 2.0,
                "remaining_position_qty": 1.0,
                "first_target_status": "pending",
                "first_target_hit": False,
                "first_target_filled_qty": 0.0,
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
                "runner_protected": False,
                "runner_stop_price": None,
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["qty"] == pytest.approx(0.4)
    assert position["remaining_position_qty"] == pytest.approx(0.4)
    assert position["first_target_status"] == "pending"
    assert position["second_target_status"] == "pending"


def test_sync_positions_from_account_derives_target_management_when_snapshot_has_usable_entry_and_stop(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
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

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.62, entry_price=62850.0, mark_price=64120.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["stop_loss"] == pytest.approx(61593.0)
    assert position["first_target_price"] == pytest.approx(64107.0)
    assert position["first_target_source"] == "fallback_1r"
    assert position["second_target_price"] == pytest.approx(65364.0)
    assert position["second_target_source"] == "fixed_2r"
    assert position["first_target_status"] == "pending"
    assert position["second_target_status"] == "pending"
