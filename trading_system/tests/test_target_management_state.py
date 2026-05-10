import pytest

from trading_system.app.portfolio.target_management import (
    derive_target_management_fields,
    ensure_target_management_state,
    reconciled_stage_qty,
    stage_completed,
    stage_requested_qty,
    terminalize_all_unreachable_stages,
)
from trading_system.app.portfolio.positions import (
    _has_explicit_target_management_state,
    apply_executed_intent,
    sync_positions_from_account,
)
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import AccountSnapshot, OrderIntent, PositionSnapshot


def test_has_explicit_target_management_state_detects_persisted_target_fields():
    assert _has_explicit_target_management_state({"first_target_price": 105.0}) is True
    assert _has_explicit_target_management_state({"first_target_status": "pending"}) is True
    assert _has_explicit_target_management_state({"second_target_status": "pending"}) is True
    assert _has_explicit_target_management_state(
        {
            "first_target_status": "pending",
            "second_target_status": "pending",
            "first_target_source": "fallback_1r",
            "second_target_source": "fixed_2r",
        }
    ) is False
    assert _has_explicit_target_management_state({"first_target_status": "filled"}) is True
    assert _has_explicit_target_management_state({"second_target_status": "satisfied_by_external_reduction"}) is True
    assert _has_explicit_target_management_state({"first_target_filled_qty": 0.01}) is True
    assert _has_explicit_target_management_state({"runner_protected": True}) is True
    assert _has_explicit_target_management_state({"legacy_partial_filled_qty": 0.25}) is True
    assert _has_explicit_target_management_state({"first_target_source": "fallback_1r"}) is False
    assert _has_explicit_target_management_state({"qty": 1.0}) is False


def test_has_explicit_target_management_state_rejects_non_bool_runner_protected():
    with pytest.raises(ValueError, match="runner_protected"):
        _has_explicit_target_management_state({"runner_protected": "false"})


def test_has_explicit_target_management_state_rejects_non_string_target_status():
    with pytest.raises(TypeError, match="first_target_status"):
        _has_explicit_target_management_state({"first_target_status": 123})


def test_has_explicit_target_management_state_rejects_non_string_target_source():
    with pytest.raises(TypeError, match="first_target_source"):
        _has_explicit_target_management_state(
            {
                "first_target_status": "pending",
                "second_target_status": "pending",
                "first_target_source": 123,
                "second_target_source": "fixed_2r",
            }
        )


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


@pytest.mark.parametrize("side", [123, True])
def test_derive_target_management_fields_rejects_present_non_string_side(side):
    with pytest.raises(TypeError, match="side must be a string"):
        derive_target_management_fields(
            side=side,
            entry_price=100.0,
            stop_loss=95.0,
            structure_target_price=None,
            legacy_take_profit=None,
            original_position_qty=2.0,
        )


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


def test_ensure_target_management_state_rejects_present_numeric_string_stop_loss():
    with pytest.raises(ValueError, match="stop_loss must be a finite non-bool number when present"):
        ensure_target_management_state(
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 100.0,
                "stop_loss": "95.0",
                "qty": 2.0,
            }
        )


def test_ensure_target_management_state_rejects_present_non_string_side():
    with pytest.raises(TypeError, match="side must be a string when present"):
        ensure_target_management_state(
            {
                "symbol": "BTCUSDT",
                "side": 123,
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "qty": 2.0,
            }
        )


@pytest.mark.parametrize("field", ["first_target_status", "second_target_status"])
def test_ensure_target_management_state_rejects_present_non_string_target_status(field):
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "qty": 2.0,
        "first_target_price": 105.0,
        "first_target_source": "fallback_1r",
        "second_target_price": 110.0,
        "second_target_source": "fixed_2r",
        "first_target_status": "pending",
        "second_target_status": "pending",
        field: 123,
    }

    with pytest.raises(TypeError, match=f"{field} must be a string when present"):
        ensure_target_management_state(payload)


@pytest.mark.parametrize(
    "field",
    [
        "qty",
        "original_position_qty",
        "remaining_position_qty",
        "first_target_filled_qty",
        "second_target_filled_qty",
        "symbol_step_size",
        "min_order_qty",
        "runner_stop_price",
    ],
)
def test_ensure_target_management_state_rejects_present_invalid_default_state_numbers(field):
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "qty": 2.0,
        "first_target_price": 105.0,
        "first_target_source": "fallback_1r",
        "second_target_price": 110.0,
        "second_target_source": "fixed_2r",
        "first_target_status": "pending",
        "second_target_status": "filled",
        "runner_protected": True,
        "remaining_position_qty": 0.5,
        "symbol_step_size": 0.01,
        field: "bad",
    }

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        ensure_target_management_state(payload)


@pytest.mark.parametrize("field", ["first_target_status", "second_target_status"])
def test_ensure_target_management_state_rejects_unknown_target_status(field):
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "qty": 2.0,
        "first_target_price": 105.0,
        "first_target_source": "fallback_1r",
        "second_target_price": 110.0,
        "second_target_source": "fixed_2r",
        "first_target_status": "pending",
        "second_target_status": "pending",
        field: "done",
    }

    with pytest.raises(ValueError, match=f"{field} must be one of"):
        ensure_target_management_state(payload)


@pytest.mark.parametrize(
    "position, message",
    [
        ([("symbol", "BTCUSDT"), ("side", "LONG")], "position must be a mapping"),
        ({"symbol": "BTCUSDT", 1: "bad", "side": "LONG"}, "position keys must be strings"),
    ],
)
def test_ensure_target_management_state_rejects_non_mapping_or_non_string_keys(position, message):
    with pytest.raises(TypeError, match=message):
        ensure_target_management_state(position)


@pytest.mark.parametrize("field", ["first_target_status", "second_target_status"])
def test_terminalize_all_unreachable_stages_rejects_present_non_string_target_status(field):
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.01,
        "original_position_qty": 2.0,
        "remaining_position_qty": 0.01,
        "symbol_step_size": 0.01,
        "first_target_status": "pending",
        "second_target_status": "pending",
        field: 123,
    }

    with pytest.raises(TypeError, match=f"{field} must be a string when present"):
        terminalize_all_unreachable_stages(payload)


def test_terminalize_all_unreachable_stages_rejects_present_numeric_string_original_qty():
    with pytest.raises(ValueError, match="original_position_qty must be a finite non-bool number when present"):
        terminalize_all_unreachable_stages(
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.01,
                "original_position_qty": "2.0",
                "remaining_position_qty": 0.01,
                "symbol_step_size": 0.01,
                "first_target_status": "pending",
                "second_target_status": "pending",
            }
        )


@pytest.mark.parametrize(
    "position, message",
    [
        ([("symbol", "BTCUSDT"), ("side", "LONG")], "position must be a mapping"),
        ({"symbol": "BTCUSDT", 1: "bad", "side": "LONG"}, "position keys must be strings"),
    ],
)
def test_terminalize_all_unreachable_stages_rejects_non_mapping_or_non_string_keys(position, message):
    with pytest.raises(TypeError, match=message):
        terminalize_all_unreachable_stages(position)


@pytest.mark.parametrize("field", ["original_position_qty"])
def test_stage_requested_qty_rejects_present_invalid_numeric_string(field):
    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        stage_requested_qty({field: "bad"}, stage="first")


@pytest.mark.parametrize("field", ["original_position_qty", "first_target_filled_qty", "symbol_step_size"])
def test_stage_completed_rejects_present_invalid_numeric_string(field):
    payload = {
        "original_position_qty": 2.0,
        "first_target_filled_qty": 0.5,
        "symbol_step_size": 0.01,
        field: "bad",
    }

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        stage_completed(payload, stage="first")


@pytest.mark.parametrize(
    "field",
    ["original_position_qty", "remaining_position_qty", "first_target_filled_qty", "symbol_step_size"],
)
def test_reconciled_stage_qty_rejects_present_invalid_numeric_string(field):
    payload = {
        "original_position_qty": 2.0,
        "remaining_position_qty": 1.0,
        "first_target_filled_qty": 0.25,
        "symbol_step_size": 0.01,
        field: "1.0",
    }

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        reconciled_stage_qty(payload, stage="first")


@pytest.mark.parametrize(("qty", "exception_type"), [("1.0", ValueError), (True, TypeError)])
def test_reconciled_stage_qty_rejects_masked_fallback_qty(qty, exception_type):
    payload = {
        "original_position_qty": 2.0,
        "qty": qty,
        "first_target_filled_qty": 0.25,
        "symbol_step_size": 0.01,
    }

    with pytest.raises(exception_type, match="qty must be a finite non-bool number when present"):
        reconciled_stage_qty(payload, stage="first")


def test_ensure_target_management_state_rederives_invalid_frozen_target_order():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 2.0,
            "first_target_price": 111.0,
            "first_target_source": "structure",
            "second_target_price": 110.0,
            "second_target_source": "fixed_2r",
            "original_position_qty": 2.0,
            "remaining_position_qty": 2.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(105.0)
    assert position["first_target_source"] == "fallback_1r"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


def test_ensure_target_management_state_rederives_invalid_frozen_target_order_without_losing_stage_progress():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.0,
            "take_profit": 107.0,
            "first_target_price": 111.0,
            "first_target_source": "structure",
            "second_target_price": 110.0,
            "second_target_source": "fixed_2r",
            "first_target_status": "filled",
            "first_target_hit": True,
            "first_target_filled_qty": 1.0,
            "legacy_partial_filled_qty": 0.7,
            "original_position_qty": 2.0,
            "remaining_position_qty": 1.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)


def test_ensure_target_management_state_rederives_invalid_frozen_first_target_when_second_target_is_missing():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 1.0,
            "take_profit": 107.0,
            "first_target_price": 111.0,
            "first_target_source": "structure",
            "first_target_status": "filled",
            "first_target_hit": True,
            "first_target_filled_qty": 1.0,
            "legacy_partial_filled_qty": 0.7,
            "original_position_qty": 2.0,
            "remaining_position_qty": 1.0,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)


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


def test_ensure_target_management_state_rejects_present_non_bool_runner_protected():
    with pytest.raises(ValueError, match="runner_protected"):
        ensure_target_management_state(
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "qty": 0.5,
                "first_target_price": 105.0,
                "first_target_source": "fallback_1r",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "first_target_status": "filled",
                "first_target_hit": True,
                "first_target_filled_qty": 1.0,
                "second_target_status": "filled",
                "second_target_hit": True,
                "second_target_filled_qty": 0.5,
                "runner_protected": "false",
                "runner_stop_price": 105.0,
                "original_position_qty": 2.0,
                "remaining_position_qty": 0.5,
            }
        )


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


def test_sync_positions_from_account_rejects_non_bool_tracked_from_intent(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "status": "OPEN",
                "tracked_from_intent": "false",
            }
        },
    )

    with pytest.raises(ValueError, match="tracked_from_intent"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=101.0)],
            ),
        )


def test_sync_positions_from_account_clears_runner_protection_until_second_stage_is_filled(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 106.0,
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
                "second_target_status": "pending",
                "second_target_hit": False,
                "second_target_filled_qty": 0.0,
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
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["second_target_status"] == "pending"
    assert position["second_target_hit"] is False
    assert position["runner_protected"] is False
    assert position["runner_stop_price"] is None


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


def test_ensure_target_management_state_preserves_first_stage_progress_when_backfilling_first_target():
    position = ensure_target_management_state(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "qty": 0.5,
            "take_profit": 107.0,
            "second_target_price": 110.0,
            "second_target_source": "fixed_2r",
            "first_target_status": "filled",
            "first_target_hit": True,
            "first_target_filled_qty": 1.0,
            "second_target_status": "filled",
            "second_target_hit": True,
            "second_target_filled_qty": 0.5,
            "runner_protected": True,
            "runner_stop_price": 107.0,
            "original_position_qty": 2.0,
            "remaining_position_qty": 0.5,
        }
    )

    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["first_target_status"] == "filled"
    assert position["first_target_hit"] is True
    assert position["first_target_filled_qty"] == pytest.approx(1.0)
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_status"] == "filled"
    assert position["runner_protected"] is True
    assert position["runner_stop_price"] == pytest.approx(107.0)


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


def test_apply_executed_intent_rejects_non_bool_tracked_from_snapshot():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "tracked_from_snapshot": "false",
            }
        },
    )

    with pytest.raises(ValueError, match="tracked_from_snapshot"):
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


def test_apply_executed_intent_rejects_present_non_mapping_order_meta_before_state_mutation():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    with pytest.raises(TypeError, match="order.meta"):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id="intent-invalid-meta",
                signal_id="signal-invalid-meta",
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                entry_price=102.0,
                stop_loss=95.0,
                take_profit=107.0,
                status="FILLED",
                meta=[("first_target_price", 107.0)],
            ),
        )

    assert state.positions == {}


def test_apply_executed_intent_rejects_present_non_string_existing_source_without_mutating_state():
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "source": 123,
        "tracked_from_snapshot": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[BTCUSDT\\]\\.source"):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id="intent-invalid-source",
                signal_id="signal-invalid-source",
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

    assert state.positions["BTCUSDT"] == original_position


@pytest.mark.parametrize("taxonomy_stop_loss", [True, "95.0", float("nan")])
def test_apply_executed_intent_rejects_present_invalid_taxonomy_stop_loss_without_mutating_state(taxonomy_stop_loss):
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "tracked_from_snapshot": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises((TypeError, ValueError), match="taxonomy_stop_loss"):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id="intent-invalid-taxonomy-stop",
                signal_id="signal-invalid-taxonomy-stop",
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                entry_price=102.0,
                stop_loss=95.0,
                take_profit=107.0,
                status="FILLED",
                meta={"taxonomy_stop_loss": taxonomy_stop_loss},
            ),
        )

    assert state.positions["BTCUSDT"] == original_position


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("invalidation_source", 123, TypeError),
        ("invalidation_reason", "", ValueError),
        ("stop_family", True, TypeError),
        ("stop_reference", "   ", ValueError),
        ("stop_policy_source", 123, TypeError),
    ],
)
def test_apply_executed_intent_rejects_present_invalid_taxonomy_string_fields_without_mutating_state(
    field, value, exception
):
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    with pytest.raises(exception, match=field):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id=f"intent-invalid-{field}",
                signal_id=f"signal-invalid-{field}",
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                entry_price=102.0,
                stop_loss=95.0,
                take_profit=107.0,
                status="FILLED",
                meta={field: value},
            ),
        )

    assert state.positions == {}


@pytest.mark.parametrize("field", ["first_target_filled_qty", "second_target_filled_qty"])
@pytest.mark.parametrize("filled_qty", [True, "0.25", float("inf")])
def test_apply_executed_intent_rejects_present_invalid_target_filled_qty_without_mutating_state(field, filled_qty):
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    with pytest.raises((TypeError, ValueError), match=field):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id=f"intent-invalid-{field}",
                signal_id=f"signal-invalid-{field}",
                symbol="BTCUSDT",
                side="LONG",
                qty=0.5,
                entry_price=102.0,
                stop_loss=95.0,
                take_profit=107.0,
                status="FILLED",
                meta={
                    "first_target_price": 107.0,
                    "first_target_source": "legacy_take_profit_mapped",
                    "second_target_price": 110.0,
                    "second_target_source": "fixed_2r",
                    field: filled_qty,
                },
            ),
        )

    assert state.positions == {}


def test_apply_executed_intent_rejects_invalid_same_side_existing_qty_without_mutating_state():
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": True,
        "entry_price": 100.0,
        "tracked_from_snapshot": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[BTCUSDT\\]\\.qty"):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id="intent-bad-qty",
                signal_id="signal-bad-qty",
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

    assert state.positions["BTCUSDT"] == original_position


def test_apply_executed_intent_rejects_invalid_same_side_existing_entry_price_without_mutating_state():
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": "100.0",
        "tracked_from_snapshot": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[BTCUSDT\\]\\.entry_price"):
        apply_executed_intent(
            state,
            OrderIntent(
                intent_id="intent-bad-entry",
                signal_id="signal-bad-entry",
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

    assert state.positions["BTCUSDT"] == original_position


def test_apply_executed_intent_keeps_frozen_first_target_when_only_second_target_is_missing():
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
                "first_target_price": 108.0,
                "first_target_source": "structure",
                "original_position_qty": 1.0,
                "remaining_position_qty": 1.0,
                "tracked_from_snapshot": False,
                "tracked_from_intent": True,
            }
        },
    )

    apply_executed_intent(
        state,
        OrderIntent(
            intent_id="intent-3",
            signal_id="signal-3",
            symbol="BTCUSDT",
            side="LONG",
            qty=0.5,
            entry_price=102.0,
            stop_loss=95.0,
            take_profit=107.0,
            status="FILLED",
            meta={
                "first_target_price": 107.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 110.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 0.5,
            },
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["first_target_price"] == pytest.approx(108.0)
    assert position["first_target_source"] == "structure"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


def test_apply_executed_intent_does_not_carry_opposite_side_frozen_targets():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "SHORT",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 99.0,
                "stop_loss": 105.0,
                "take_profit": 95.0,
                "first_target_price": 108.0,
                "first_target_source": "structure",
                "second_target_price": 111.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 1.0,
                "remaining_position_qty": 1.0,
                "tracked_from_snapshot": True,
                "tracked_from_intent": True,
            }
        },
    )

    apply_executed_intent(
        state,
        OrderIntent(
            intent_id="intent-4",
            signal_id="signal-4",
            symbol="BTCUSDT",
            side="LONG",
            qty=1.0,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=107.0,
            status="FILLED",
            meta={},
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["side"] == "LONG"
    assert position["stop_loss"] == pytest.approx(95.0)
    assert position["first_target_price"] == pytest.approx(107.0)
    assert position["first_target_source"] == "legacy_take_profit_mapped"
    assert position["second_target_price"] == pytest.approx(110.0)
    assert position["second_target_source"] == "fixed_2r"


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


def test_sync_positions_from_account_rejects_present_non_mapping_account_meta():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    with pytest.raises(TypeError, match="account.meta"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
                meta=[("snapshot_source", "manual")],
            ),
        )

    assert state.positions == {}


def test_sync_positions_from_account_rejects_present_non_string_snapshot_source():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    with pytest.raises(TypeError, match="account.meta.snapshot_source"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
                meta={"snapshot_source": 123},
            ),
        )

    assert state.positions == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_source", " binance_futures_testnet"),
        ("snapshot_source", "BINANCE_FUTURES_TESTNET"),
        ("source", " binance_futures_testnet"),
        ("source", "BINANCE_FUTURES_TESTNET"),
    ],
)
def test_sync_positions_from_account_rejects_noncanonical_snapshot_origin_before_any_position_mutation(
    field, value
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

    with pytest.raises(ValueError, match=f"account\\.meta\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)
                ],
                meta={field: value},
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_rejects_invalid_preserved_paper_position_qty_without_mutating_state():
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": "1.25",
        "entry_price": 100.0,
        "mark_price": 101.0,
        "tracked_from_intent": True,
        "tracked_from_snapshot": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[BTCUSDT\\]\\.qty"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
                meta={"snapshot_source": "manual"},
            ),
        )

    assert state.positions["BTCUSDT"] == original_position


@pytest.mark.parametrize("status", [123, True])
def test_sync_positions_from_account_rejects_present_non_string_missing_position_status_without_mutating_state(status):
    original_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": status,
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"ETHUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[ETHUSDT\\]\\.status"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[],
            ),
        )

    assert state.positions["ETHUSDT"] == original_position


def test_sync_positions_from_account_rejects_unknown_missing_position_status_without_mutating_state():
    original_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "MYSTERY",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"ETHUSDT": dict(original_position)},
    )

    with pytest.raises(ValueError, match="positions\\[ETHUSDT\\]\\.status"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[],
            ),
        )

    assert state.positions["ETHUSDT"] == original_position


def test_sync_positions_from_account_rejects_unknown_existing_status_before_any_position_mutation():
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "MYSTERY",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(ValueError, match="positions\\[ETHUSDT\\]\\.status"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", " OPEN "),
        ("source", " account_snapshot "),
    ],
)
def test_sync_positions_from_account_rejects_padded_existing_status_source_before_any_position_mutation(field, value):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "source": "account_snapshot",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "source": "paper_execution",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    eth_position[field] = value
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(ValueError, match=f"positions\\[ETHUSDT\\]\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("intent_id", 123, TypeError),
        ("intent_id", "", ValueError),
        ("intent_id", " intent-eth-long ", ValueError),
        ("intent_id", "intent-eth-long\n", ValueError),
        ("strategy_tag", True, TypeError),
        ("strategy_tag", "   ", ValueError),
        ("strategy_tag", " breakout ", ValueError),
        ("strategy_tag", "breakout\n", ValueError),
        ("setup_type", 123, TypeError),
        ("setup_type", "", ValueError),
        ("setup_type", " pullback ", ValueError),
        ("setup_type", "pullback\n", ValueError),
        ("engine", True, TypeError),
        ("engine", "   ", ValueError),
        ("engine", " trend ", ValueError),
        ("engine", "trend\n", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_existing_intent_identity_before_any_position_mutation(
    field, value, exception
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "strategy_tag": "breakout",
        "setup_type": "pullback",
        "engine": "trend",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    eth_position[field] = value
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(exception, match=f"positions\\[ETHUSDT\\]\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


def test_sync_positions_from_account_rejects_padded_existing_side_before_any_position_mutation():
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": " LONG ",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(ValueError, match="positions\\[ETHUSDT\\]\\.side"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


@pytest.mark.parametrize(
    ("position_key", "embedded_symbol"),
    [
        (" ETHUSDT", "ETHUSDT"),
        ("ETHUSDT", " ETHUSDT "),
        ("ETHUSDT", "ethusdt"),
        ("ETHUSDT", "BTCUSDT"),
    ],
)
def test_sync_positions_from_account_rejects_noncanonical_existing_symbol_identity_before_any_position_mutation(
    position_key, embedded_symbol
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": embedded_symbol,
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), position_key: dict(eth_position)},
    )

    with pytest.raises(ValueError, match="positions\\[.*ETHUSDT.*\\]\\.symbol|position key"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, position_key: eth_position}


def test_sync_positions_from_account_rejects_invalid_snapshot_side_before_any_position_mutation():
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

    with pytest.raises(ValueError, match="account\\.open_positions\\[ETHUSDT\\]\\.side"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0),
                    PositionSnapshot(symbol="ETHUSDT", side="HEDGE", qty=0.8, entry_price=2300.0, mark_price=2310.0),
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize("snapshot_symbol", [" ETHUSDT", "ethusdt", "ETH/USDT"])
def test_sync_positions_from_account_rejects_noncanonical_snapshot_symbol_before_any_position_mutation(
    snapshot_symbol,
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

    with pytest.raises(ValueError, match="account\\.open_positions\\[.*\\]\\.symbol"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0),
                    PositionSnapshot(symbol=snapshot_symbol, side="LONG", qty=0.8, entry_price=2300.0, mark_price=2310.0),
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize("status", [" OPEN", "open", "Open", "ACTIVE"])
def test_sync_positions_from_account_rejects_noncanonical_snapshot_status_before_any_position_mutation(status):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(ValueError, match="account\\.open_positions\\[ETHUSDT\\]\\.status"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0),
                    PositionSnapshot(
                        symbol="ETHUSDT",
                        side="LONG",
                        qty=0.8,
                        entry_price=2300.0,
                        mark_price=2310.0,
                        unrealized_pnl=8.0,
                        notional=1848.0,
                        leverage=None,
                        strategy_tag=None,
                        status=status,
                    ),
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


@pytest.mark.parametrize("strategy_tag", [123, True, "", "   ", " trend_v2", "trend_v2\n"])
def test_sync_positions_from_account_rejects_malformed_snapshot_strategy_tag_before_any_position_mutation(
    strategy_tag,
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises((TypeError, ValueError), match="account\\.open_positions\\[ETHUSDT\\]\\.strategy_tag"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0),
                    PositionSnapshot(
                        symbol="ETHUSDT",
                        side="LONG",
                        qty=0.8,
                        entry_price=2300.0,
                        mark_price=2310.0,
                        unrealized_pnl=8.0,
                        notional=1848.0,
                        leverage=None,
                        strategy_tag=strategy_tag,
                        status="OPEN",
                    ),
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


def test_sync_positions_from_account_rejects_present_non_string_existing_source_without_mutating_state():
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "source": 123,
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match="positions\\[BTCUSDT\\]\\.source"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions["BTCUSDT"] == original_position


def test_sync_positions_from_account_rejects_present_non_string_existing_source_before_any_position_mutation():
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    eth_position = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.858,
        "entry_price": 2329.52,
        "source": 123,
        "status": "OPEN",
        "intent_id": "intent-eth-long",
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(TypeError, match="positions\\[ETHUSDT\\]\\.source"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position, "ETHUSDT": eth_position}


@pytest.mark.parametrize("field", ["invalidation_source", "invalidation_reason", "stop_family", "stop_reference", "stop_policy_source"])
def test_sync_positions_from_account_rejects_present_non_string_existing_taxonomy_fields_without_mutating_state(field):
    original_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        field: 123,
        "tracked_from_snapshot": True,
        "tracked_from_intent": False,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(original_position)},
    )

    with pytest.raises(TypeError, match=f"positions\\[BTCUSDT\\]\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.4, entry_price=100.0, mark_price=106.0)],
            ),
        )

    assert state.positions["BTCUSDT"] == original_position


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("qty", True, TypeError),
        ("entry_price", "100.0", TypeError),
        ("mark_price", float("inf"), ValueError),
        ("notional", "40.0", TypeError),
        ("unrealized_pnl", float("nan"), ValueError),
    ],
)
def test_sync_positions_from_account_rejects_invalid_snapshot_numeric_fields_without_mutating_state(
    field, value, exception
):
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
    }
    snapshot_payload[field] = value

    with pytest.raises(exception, match=f"account.open_positions\\[BTCUSDT\\]\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(**snapshot_payload)],
            ),
        )

    assert state.positions == {}


def test_sync_positions_from_account_marks_tracked_intent_position_closed_when_exchange_position_disappears(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "qty": 0.858,
                "entry_price": 2329.52,
                "mark_price": 2346.09,
                "stop_loss": 2318.03,
                "take_profit": 2343.82,
                "status": "OPEN",
                "intent_id": "intent-eth-long",
                "signal_id": "signal-eth-long",
                "original_position_qty": 0.858,
                "remaining_position_qty": 0.858,
                "tracked_from_snapshot": True,
                "tracked_from_intent": True,
                "opened_at_bj": "2026-04-09T12:00:00+08:00",
            }
        },
    )

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[],
        ),
    )

    position = state.positions["ETHUSDT"]
    assert position["status"] == "CLOSED"
    assert position["qty"] == pytest.approx(0.0)
    assert position["remaining_position_qty"] == pytest.approx(0.0)
    assert position["closed_at_bj"] == "2026-04-09T18:00:00+08:00"
    assert position["last_synced_from"] == "account_snapshot_closed"
    assert state.active_orders["position-closed-ETHUSDT"]["event"] == "POSITION_CLOSED"
    assert state.active_orders["position-closed-ETHUSDT"]["notified"] is False
    assert synced[0]["status"] == "CLOSED"


def test_sync_positions_from_account_marks_tracked_intent_position_closed_even_if_snapshot_marker_was_lost(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "qty": 0.858,
                "entry_price": 2329.52,
                "status": "OPEN",
                "intent_id": "intent-eth-long",
                "tracked_from_snapshot": False,
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
            open_positions=[],
        ),
    )

    assert state.positions["ETHUSDT"]["status"] == "CLOSED"
    assert state.active_orders["position-closed-ETHUSDT"]["notified"] is False


def test_sync_positions_from_account_does_not_carry_opposite_side_target_state(monkeypatch):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "SHORT",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 99.0,
                "stop_loss": 105.0,
                "take_profit": 95.0,
                "first_target_price": 95.0,
                "first_target_source": "legacy_take_profit_mapped",
                "second_target_price": 90.0,
                "second_target_source": "fixed_2r",
                "original_position_qty": 1.0,
                "remaining_position_qty": 1.0,
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
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=1.0, entry_price=100.0, mark_price=101.0)],
        ),
    )

    position = state.positions["BTCUSDT"]
    assert position["side"] == "LONG"
    assert position["stop_loss"] is None
    assert position["take_profit"] is None
    assert "first_target_price" not in position
    assert "second_target_price" not in position


def test_sync_positions_from_account_marks_pending_intent_closed_when_missing_from_testnet_snapshot():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-27T01:30:00+08:00",
        positions={
            "LINKUSDT": {
                "symbol": "LINKUSDT",
                "side": "LONG",
                "qty": 212.089077,
                "entry_price": 9.43,
                "mark_price": 9.43,
                "stop_loss": 9.3657405,
                "status": "PENDING",
                "tracked_from_snapshot": False,
                "tracked_from_intent": True,
                "intent_id": "intent-link",
                "signal_id": "sig-link",
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=5000.0,
            available_balance=4500.0,
            futures_wallet_balance=5000.0,
            open_positions=[],
            meta={"snapshot_source": "binance_futures_testnet"},
        ),
    )

    link = state.positions["LINKUSDT"]
    assert link["status"] == "CLOSED"
    assert link["qty"] == 0.0
    event = state.active_orders["position-closed-LINKUSDT"]
    assert event["event"] == "POSITION_CLOSED"
    assert event["symbol"] == "LINKUSDT"
    assert event["notified"] is False


def test_sync_positions_from_account_clears_stale_closed_event_when_position_is_open_again():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-26T23:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.0256,
                "entry_price": 78090.8,
                "mark_price": 78090.8,
                "status": "OPEN",
                "tracked_from_snapshot": True,
                "tracked_from_intent": True,
            }
        },
        active_orders={
            "position-closed-BTCUSDT": {
                "event": "POSITION_CLOSED",
                "symbol": "BTCUSDT",
                "notified": True,
            }
        },
    )

    sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.0896, entry_price=78036.9, mark_price=78081.7)],
            meta={"snapshot_source": "binance_futures_testnet"},
        ),
    )

    assert "position-closed-BTCUSDT" not in state.active_orders
    assert state.positions["BTCUSDT"]["status"] == "OPEN"
    assert state.positions["BTCUSDT"]["qty"] == pytest.approx(0.0896)


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
