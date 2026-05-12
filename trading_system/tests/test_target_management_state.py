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


def test_reconciled_stage_qty_caps_stage_order_to_remaining_position_qty():
    payload = {
        "original_position_qty": 2.0,
        "remaining_position_qty": 0.3,
        "first_target_filled_qty": 0.0,
        "symbol_step_size": 0.01,
    }

    assert reconciled_stage_qty(payload, stage="first") == pytest.approx(0.3)


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


@pytest.mark.parametrize(
    ("qty", "expected_message"),
    [
        (0.0, r"account\.open_positions\[BTCUSDT\]\.qty must be positive"),
        (-0.5, r"account\.open_positions\[BTCUSDT\]\.qty must be positive"),
    ],
)
def test_sync_positions_from_account_rejects_non_positive_open_position_qty_without_mutating_state(
    monkeypatch,
    qty: float,
    expected_message: str,
):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 101.0,
                "status": "OPEN",
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )
    before = dict(state.positions["BTCUSDT"])

    with pytest.raises(ValueError, match=expected_message):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=qty, entry_price=100.0, mark_price=101.0)
                ],
            ),
        )

    assert state.positions["BTCUSDT"] == before


@pytest.mark.parametrize("status", ["CLOSED", "SKIPPED", "FAILED", "CANCELLED", "CANCELED"])
def test_sync_positions_from_account_rejects_terminal_open_position_status_without_mutating_state(
    monkeypatch,
    status: str,
):
    monkeypatch.setattr("trading_system.app.portfolio.positions._now_bj", lambda: "2026-04-09T18:00:00+08:00")
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 101.0,
                "status": "OPEN",
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
    )
    before = dict(state.positions["BTCUSDT"])

    with pytest.raises(ValueError, match=r"account\.open_positions\[BTCUSDT\]\.status"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.5,
                        entry_price=100.0,
                        mark_price=101.0,
                        status=status,
                    )
                ],
            ),
        )

    assert state.positions["BTCUSDT"] == before


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


@pytest.mark.parametrize(
    ("value", "exception"),
    [
        (123, TypeError),
        ("", ValueError),
        (" signal-eth-long ", ValueError),
        ("signal-eth-long\n", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_existing_signal_id_before_any_position_mutation(
    value, exception
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
        "signal_id": value,
        "tracked_from_snapshot": False,
        "tracked_from_intent": True,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position), "ETHUSDT": dict(eth_position)},
    )

    with pytest.raises(exception, match="positions\\[ETHUSDT\\]\\.signal_id"):
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


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("signal_id", 123, TypeError),
        ("signal_id", "", ValueError),
        ("signal_id", " signal-btc-long ", ValueError),
        ("signal_id", "signal-btc-long\n", ValueError),
        ("signalId", True, TypeError),
        ("signalId", "   ", ValueError),
        ("signalId", "signal-btc-long ", ValueError),
        ("signalId", "signal-btc-long\r", ValueError),
        ("order_id", 123, TypeError),
        ("order_id", "", ValueError),
        ("order_id", " order-btc-long ", ValueError),
        ("order_id", "order-btc-long\n", ValueError),
        ("orderId", True, TypeError),
        ("orderId", "   ", ValueError),
        ("orderId", "order-btc-long ", ValueError),
        ("orderId", "order-btc-long\r", ValueError),
        ("client_order_id", 123, TypeError),
        ("client_order_id", "", ValueError),
        ("client_order_id", " client-btc-long ", ValueError),
        ("client_order_id", "client-btc-long\n", ValueError),
        ("clientOrderId", True, TypeError),
        ("clientOrderId", "   ", ValueError),
        ("clientOrderId", "client-btc-long ", ValueError),
        ("clientOrderId", "client-btc-long\r", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_metadata_identity_before_any_position_mutation(
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
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 100.0,
        "mark_price": 106.0,
        field: value,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

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

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("trade_id", 123, TypeError),
        ("tradeId", "", ValueError),
        ("execution_id", " execution-btc-long ", ValueError),
        ("executionId", "execution-btc-long\n", ValueError),
        ("fill_id", "fill/btc/long", ValueError),
        ("fillId", "fill-btc-long\t1", ValueError),
        ("strategy_id", True, TypeError),
        ("strategyId", "   ", ValueError),
        ("setup_id", " setup-btc-long", ValueError),
        ("setupId", "setup-btc-long\r", ValueError),
        ("batch_id", "batch/btc/long", ValueError),
        ("batchId", "batch-btc-long\x1f", ValueError),
        ("source_id", 123, TypeError),
        ("sourceId", "source btc long", ValueError),
        ("correlation_id", " correlation-btc-long ", ValueError),
        ("correlationId", "correlation/btc/long", ValueError),
        ("parent_order_id", True, TypeError),
        ("parentOrderId", "parent-order-btc-long\n", ValueError),
        ("exchange_order_id", "exchange/order/btc/long", ValueError),
        ("exchangeOrderId", "exchange-order-btc-long\t1", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_remaining_snapshot_identity_before_any_position_mutation(
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
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 100.0,
        "mark_price": 106.0,
        field: value,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

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

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("opened_at", 123, TypeError),
        ("updated_at", "", ValueError),
        ("as_of", " 2026-04-09T04:00:00Z", ValueError),
        ("timestamp", "2026-04-09T04:00:00Z\n", ValueError),
        ("last_update_time", "2026-04-09T04:00:00\x1fZ", ValueError),
        ("event_time", "2026-04-09T12:00:00+08:00", ValueError),
        ("trade_time", "2026-04-09T04:00:00", ValueError),
        ("execution_time", "2026-04-09T04:00:00+00:00", ValueError),
        ("fill_time", "2026-04-09T04:00:00.1Z", ValueError),
        ("order_time", "2026-04-09", ValueError),
        ("close_time", "not-a-timestamp", ValueError),
        ("expiry_time", True, TypeError),
        ("settlement_time", "2026-04-09T04:00:00.000000Z", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_time_provenance_before_any_position_mutation(
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
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 100.0,
        "mark_price": 106.0,
        field: value,
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )

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

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("event_time", "2026-04-09T02:59:59Z", r"account\.open_positions\[BTCUSDT\]\.event_time must be at or after opened_at"),
        ("trade_time", "2026-04-09T02:59:59Z", r"account\.open_positions\[BTCUSDT\]\.trade_time must be at or after opened_at"),
    ],
)
def test_sync_positions_from_account_rejects_event_and_trade_time_before_opened_at_without_mutating_state(
    field, value, match
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
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "opened_at": "2026-04-09T03:00:00Z",
        "event_time": "2026-04-09T03:00:00Z",
        "trade_time": "2026-04-09T03:00:00Z",
    }
    snapshot_payload[field] = value

    with pytest.raises(ValueError) as exc_info:
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(**snapshot_payload)],
            ),
        )

    assert str(exc_info.value) == (
        "account.open_positions[BTCUSDT]."
        f"{field} must be at or after opened_at"
    )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_snapshot_time_provenance_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    opened_at="2026-04-09T03:00:00Z",
                    updated_at="2026-04-09T04:00:00Z",
                    as_of="2026-04-09T04:00:00Z",
                    timestamp="2026-04-09T04:00:00Z",
                    last_update_time="2026-04-09T04:00:00Z",
                    event_time="2026-04-09T03:59:00Z",
                    trade_time="2026-04-09T03:55:00Z",
                    execution_time="2026-04-09T03:55:01Z",
                    fill_time="2026-04-09T03:55:02Z",
                    order_time="2026-04-09T03:54:59Z",
                    close_time="2026-04-10T04:00:00Z",
                    expiry_time="2026-06-09T04:00:00Z",
                    settlement_time="2026-06-10T04:00:00Z",
                )
            ],
        ),
    )

    assert synced[0]["opened_at"] == "2026-04-09T03:00:00Z"
    assert synced[0]["updated_at"] == "2026-04-09T04:00:00Z"
    assert synced[0]["as_of"] == "2026-04-09T04:00:00Z"
    assert synced[0]["timestamp"] == "2026-04-09T04:00:00Z"
    assert synced[0]["last_update_time"] == "2026-04-09T04:00:00Z"
    assert synced[0]["event_time"] == "2026-04-09T03:59:00Z"
    assert synced[0]["trade_time"] == "2026-04-09T03:55:00Z"
    assert synced[0]["execution_time"] == "2026-04-09T03:55:01Z"
    assert synced[0]["fill_time"] == "2026-04-09T03:55:02Z"
    assert synced[0]["order_time"] == "2026-04-09T03:54:59Z"
    assert synced[0]["close_time"] == "2026-04-10T04:00:00Z"
    assert synced[0]["expiry_time"] == "2026-06-09T04:00:00Z"
    assert synced[0]["settlement_time"] == "2026-06-10T04:00:00Z"
    assert state.positions["BTCUSDT"]["settlement_time"] == "2026-06-10T04:00:00Z"


def test_sync_positions_from_account_rejects_execution_before_order_time_without_mutating_state():
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

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[BTCUSDT\]\.execution_time must be at or after order_time",
    ):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.5,
                        entry_price=100.0,
                        mark_price=106.0,
                        order_time="2026-04-09T03:55:02Z",
                        execution_time="2026-04-09T03:55:01Z",
                        fill_time="2026-04-09T03:55:03Z",
                    )
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_rejects_updated_before_opened_at_without_mutating_state():
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

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[BTCUSDT\]\.updated_at must be at or after opened_at",
    ):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.5,
                        entry_price=100.0,
                        mark_price=106.0,
                        opened_at="2026-04-09T03:00:02Z",
                        updated_at="2026-04-09T03:00:01Z",
                    )
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("close_time", "2026-04-09T02:59:59Z", r"account\.open_positions\[BTCUSDT\]\.close_time must be at or after opened_at"),
        ("settlement_time", "2026-04-09T03:00:02Z", r"account\.open_positions\[BTCUSDT\]\.settlement_time must be at or after close_time"),
    ],
)
def test_sync_positions_from_account_rejects_impossible_close_lifecycle_without_mutating_state(
    field, value, match
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
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "opened_at": "2026-04-09T03:00:00Z",
        "close_time": "2026-04-09T03:00:03Z",
        "settlement_time": "2026-04-09T03:00:04Z",
        field: value,
    }

    with pytest.raises(ValueError, match=match):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[PositionSnapshot(**snapshot_payload)],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_rejects_expiry_before_opened_at_without_mutating_state():
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

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[BTCUSDT\]\.expiry_time must be at or after opened_at",
    ):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.5,
                        entry_price=100.0,
                        mark_price=106.0,
                        opened_at="2026-04-09T03:00:00Z",
                        expiry_time="2026-04-09T02:59:59Z",
                    )
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_rejects_settlement_before_opened_at_without_close_time_without_mutating_state():
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

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[BTCUSDT\]\.settlement_time must be at or after opened_at",
    ):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.5,
                        entry_price=100.0,
                        mark_price=106.0,
                        opened_at="2026-04-09T03:00:00Z",
                        settlement_time="2026-04-09T02:59:59Z",
                    )
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_remaining_snapshot_identity_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    trade_id="trade-btc-long",
                    tradeId="trade-btc-long-alias",
                    execution_id="execution-btc-long",
                    executionId="execution-btc-long-alias",
                    fill_id="fill-btc-long",
                    fillId="fill-btc-long-alias",
                    strategy_id="strategy-btc-long",
                    strategyId="strategy-btc-long-alias",
                    setup_id="setup-btc-long",
                    setupId="setup-btc-long-alias",
                    batch_id="batch-btc-long",
                    batchId="batch-btc-long-alias",
                    source_id="source-btc-long",
                    sourceId="source-btc-long-alias",
                    correlation_id="correlation-btc-long",
                    correlationId="correlation-btc-long-alias",
                    parent_order_id="parent-order-btc-long",
                    parentOrderId="parent-order-btc-long-alias",
                    exchange_order_id="exchange-order-btc-long",
                    exchangeOrderId="exchange-order-btc-long-alias",
                )
            ],
        ),
    )

    assert synced[0]["trade_id"] == "trade-btc-long"
    assert synced[0]["tradeId"] == "trade-btc-long-alias"
    assert synced[0]["execution_id"] == "execution-btc-long"
    assert synced[0]["executionId"] == "execution-btc-long-alias"
    assert synced[0]["fill_id"] == "fill-btc-long"
    assert synced[0]["fillId"] == "fill-btc-long-alias"
    assert synced[0]["strategy_id"] == "strategy-btc-long"
    assert synced[0]["strategyId"] == "strategy-btc-long-alias"
    assert synced[0]["setup_id"] == "setup-btc-long"
    assert synced[0]["setupId"] == "setup-btc-long-alias"
    assert synced[0]["batch_id"] == "batch-btc-long"
    assert synced[0]["batchId"] == "batch-btc-long-alias"
    assert synced[0]["source_id"] == "source-btc-long"
    assert synced[0]["sourceId"] == "source-btc-long-alias"
    assert synced[0]["correlation_id"] == "correlation-btc-long"
    assert synced[0]["correlationId"] == "correlation-btc-long-alias"
    assert synced[0]["parent_order_id"] == "parent-order-btc-long"
    assert synced[0]["parentOrderId"] == "parent-order-btc-long-alias"
    assert synced[0]["exchange_order_id"] == "exchange-order-btc-long"
    assert synced[0]["exchangeOrderId"] == "exchange-order-btc-long-alias"


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


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("opened_at_bj", 123, TypeError),
        ("opened_at_bj", "", ValueError),
        ("opened_at_bj", " 2026-04-09T12:00:00+08:00 ", ValueError),
        ("opened_at_bj", "2026-04-09T12:00:00+08:00\n", ValueError),
        ("opened_at_bj", "2026/04/09 12:00:00", ValueError),
        ("updated_at_bj", True, TypeError),
        ("updated_at_bj", "", ValueError),
        ("updated_at_bj", " 2026-04-09T12:00:00+08:00 ", ValueError),
        ("updated_at_bj", "2026-04-09T12:00:00+08:00\r", ValueError),
        ("updated_at_bj", "April 9 2026 12:00", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_existing_time_metadata_before_any_position_mutation(
    field, value, exception
):
    btc_position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 101.0,
        "status": "OPEN",
        "opened_at_bj": "2026-04-09T12:00:00+08:00",
        "updated_at_bj": "2026-04-09T12:00:00+08:00",
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
        "opened_at_bj": "2026-04-09T12:00:00+08:00",
        "updated_at_bj": "2026-04-09T12:00:00+08:00",
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
        ("invalidation_source", 123, TypeError),
        ("invalidation_reason", "", ValueError),
        ("stop_family", True, TypeError),
        ("stop_reference", " 4h_ema20 ", ValueError),
        ("stop_policy_source", "shared_taxonomy\n", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_taxonomy_before_any_position_mutation(
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
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    eth_snapshot_payload = {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "qty": 0.8,
        "entry_price": 2300.0,
        "mark_price": 2310.0,
        "unrealized_pnl": 8.0,
        "notional": 1848.0,
        "status": "OPEN",
        field: value,
    }

    with pytest.raises(exception, match=f"account.open_positions\\[ETHUSDT\\]\\.{field}"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(symbol="BTCUSDT", side="LONG", qty=0.5, entry_price=100.0, mark_price=106.0),
                    PositionSnapshot(**eth_snapshot_payload),
                ],
            ),
        )

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("qty", True, TypeError),
        ("entry_price", "100.0", TypeError),
        ("entry_price", 0.0, ValueError),
        ("mark_price", float("inf"), ValueError),
        ("mark_price", 0.0, ValueError),
        ("notional", "40.0", TypeError),
        ("notional", 0.0, ValueError),
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


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("fee_paid", True, TypeError),
        ("commission", "1.25", TypeError),
        ("funding_paid", float("nan"), ValueError),
        ("funding_fee", float("inf"), ValueError),
        ("slippage_paid", -0.01, ValueError),
        ("carry_cost", "0.5", TypeError),
        ("borrow_fee", -0.01, ValueError),
    ],
)
def test_sync_positions_from_account_rejects_invalid_snapshot_cost_metadata_without_mutating_state(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("liquidation_price", True, TypeError),
        ("liquidationPrice", "95.0", TypeError),
        ("break_even_price", float("nan"), ValueError),
        ("breakEvenPrice", float("inf"), ValueError),
        ("risk_price", -1.0, ValueError),
        ("stop_price", 0.0, ValueError),
        ("take_profit_price", "112.0", TypeError),
        ("trailing_stop_price", 0.0, ValueError),
        ("mark_spread_bps", -0.01, ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_risk_price_metadata_before_any_position_mutation(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("position_value", True, TypeError),
        ("market_value", "42.4", TypeError),
        ("exposure_value", float("nan"), ValueError),
        ("margin_used", float("inf"), ValueError),
        ("position_value", 0.0, ValueError),
        ("initial_margin", -0.01, ValueError),
        ("maintenance_margin", -0.01, ValueError),
        ("collateral_value", -0.01, ValueError),
        ("risk_pct", 1.01, ValueError),
        ("exposure_pct", -0.01, ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_position_sizing_metadata_before_any_position_mutation(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_snapshot_position_sizing_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    position_value=42.4,
                    market_value=42.4,
                    exposure_value=42.4,
                    margin_used=4.24,
                    initial_margin=4.24,
                    maintenance_margin=0.21,
                    collateral_value=100.0,
                    risk_pct=0.01,
                    exposure_pct=0.0424,
                )
            ],
        ),
    )

    assert synced[0]["position_value"] == 42.4
    assert synced[0]["market_value"] == 42.4
    assert synced[0]["exposure_value"] == 42.4
    assert synced[0]["margin_used"] == 4.24
    assert synced[0]["initial_margin"] == 4.24
    assert synced[0]["maintenance_margin"] == 0.21
    assert synced[0]["collateral_value"] == 100.0
    assert synced[0]["risk_pct"] == 0.01
    assert synced[0]["exposure_pct"] == 0.0424


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("order_type", 123, ValueError),
        ("order_type", "", ValueError),
        ("order_type", " LIMIT ", ValueError),
        ("order_type", "LIMIT\n", ValueError),
        ("order_type", "ICEBERG", ValueError),
        ("time_in_force", True, ValueError),
        ("time_in_force", " GTX ", ValueError),
        ("time_in_force", "DAY", ValueError),
        ("execution_venue", " binance_futures ", ValueError),
        ("execution_venue", "dark_pool", ValueError),
        ("liquidity_role", " maker ", ValueError),
        ("liquidity_role", "auction", ValueError),
        ("maker_status", " filled ", ValueError),
        ("maker_status", "unknown", ValueError),
        ("reduce_only", "false", ValueError),
        ("post_only", 1, ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_order_execution_metadata_before_any_position_mutation(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_snapshot_order_execution_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    order_type="LIMIT",
                    time_in_force="GTX",
                    execution_venue="binance_futures_testnet",
                    liquidity_role="maker",
                    maker_status="filled",
                    reduce_only=False,
                    post_only=True,
                )
            ],
        ),
    )

    assert synced[0]["order_type"] == "LIMIT"
    assert synced[0]["time_in_force"] == "GTX"
    assert synced[0]["execution_venue"] == "binance_futures_testnet"
    assert synced[0]["liquidity_role"] == "maker"
    assert synced[0]["maker_status"] == "filled"
    assert synced[0]["reduce_only"] is False
    assert synced[0]["post_only"] is True


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("source", 123, TypeError),
        ("source", "", ValueError),
        ("source", " account_snapshot", ValueError),
        ("source", "account_snapshot\n", ValueError),
        ("source", "manual_import", ValueError),
        ("position_source", "live_exchange", ValueError),
        ("signal_source", "trend engine", ValueError),
        ("strategy_source", "trend/engine", ValueError),
        ("data_source", "binance futures", ValueError),
        ("margin_type", "CROSS ", ValueError),
        ("margin_type", "PORTFOLIO", ValueError),
        ("product_type", [], TypeError),
        ("product_type", "PERPETUAL", ValueError),
        ("account_type", "paper\n", ValueError),
        ("account_type", "live", ValueError),
        ("venue", "binance", ValueError),
        ("venue", "COINBASE", ValueError),
        ("exchange", "BINANCE ", ValueError),
        ("exchange", "COINBASE", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_provenance_taxonomy_before_any_position_mutation(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_snapshot_provenance_taxonomy_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    source="account_snapshot",
                    position_source="account_snapshot",
                    signal_source="trend_engine",
                    strategy_source="trend_v2",
                    data_source="binance_futures",
                    margin_type="CROSS",
                    product_type="FUTURES",
                    account_type="testnet",
                    venue="BINANCE",
                    exchange="BINANCE",
                )
            ],
        ),
    )

    assert synced[0]["source"] == "account_snapshot"
    assert synced[0]["position_source"] == "account_snapshot"
    assert synced[0]["signal_source"] == "trend_engine"
    assert synced[0]["strategy_source"] == "trend_v2"
    assert synced[0]["data_source"] == "binance_futures"
    assert synced[0]["margin_type"] == "CROSS"
    assert synced[0]["product_type"] == "FUTURES"
    assert synced[0]["account_type"] == "testnet"
    assert synced[0]["venue"] == "BINANCE"
    assert synced[0]["exchange"] == "BINANCE"


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("quote_asset", 123, TypeError),
        ("base_asset", "", ValueError),
        ("margin_asset", " USDT", ValueError),
        ("collateral_asset", "usdt", ValueError),
        ("settlement_asset", "USDT\n", ValueError),
        ("fee_asset", "USD-T", ValueError),
        ("funding_asset", "USD.T", ValueError),
        ("pnl_asset", [], TypeError),
        ("pnl_currency", "BTC_USD", ValueError),
    ],
)
def test_sync_positions_from_account_rejects_malformed_snapshot_asset_identity_before_any_position_mutation(
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
        "source": "account_snapshot",
    }
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={"BTCUSDT": dict(btc_position)},
    )
    snapshot_payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.4,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "notional": 42.4,
        "unrealized_pnl": 2.4,
        field: value,
    }

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

    assert state.positions == {"BTCUSDT": btc_position}


def test_sync_positions_from_account_carries_valid_snapshot_asset_identity_metadata():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T12:00:00+08:00")

    synced = sync_positions_from_account(
        state,
        AccountSnapshot(
            equity=1000.0,
            available_balance=1000.0,
            futures_wallet_balance=1000.0,
            open_positions=[
                PositionSnapshot(
                    symbol="BTCUSDT",
                    side="LONG",
                    qty=0.4,
                    entry_price=100.0,
                    mark_price=106.0,
                    notional=42.4,
                    unrealized_pnl=2.4,
                    base_asset="BTC",
                    quote_asset="USDT",
                    margin_asset="USDT",
                    collateral_asset="USDT",
                    settlement_asset="USDT",
                    fee_asset="BNB",
                    funding_asset="USDT",
                    pnl_asset="USDT",
                    pnl_currency="USDT",
                )
            ],
        ),
    )

    assert synced[0]["base_asset"] == "BTC"
    assert synced[0]["quote_asset"] == "USDT"
    assert synced[0]["margin_asset"] == "USDT"
    assert synced[0]["collateral_asset"] == "USDT"
    assert synced[0]["settlement_asset"] == "USDT"
    assert synced[0]["fee_asset"] == "BNB"
    assert synced[0]["funding_asset"] == "USDT"
    assert synced[0]["pnl_asset"] == "USDT"
    assert synced[0]["pnl_currency"] == "USDT"


@pytest.mark.parametrize("leverage", [True, "3.0", float("nan"), float("inf"), 0.0])
def test_sync_positions_from_account_rejects_invalid_snapshot_leverage_without_mutating_state(leverage):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T12:00:00+08:00",
        positions={
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "qty": 0.8,
                "entry_price": 2300.0,
                "mark_price": 2310.0,
                "unrealized_pnl": 8.0,
                "notional": 1848.0,
                "status": "OPEN",
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
                "source": "account_snapshot",
            }
        },
    )
    original_positions = dict(state.positions)

    with pytest.raises((TypeError, ValueError), match=r"account\.open_positions\[BTCUSDT\]\.leverage"):
        sync_positions_from_account(
            state,
            AccountSnapshot(
                equity=1000.0,
                available_balance=1000.0,
                futures_wallet_balance=1000.0,
                open_positions=[
                    PositionSnapshot(
                        symbol="BTCUSDT",
                        side="LONG",
                        qty=0.4,
                        entry_price=100.0,
                        mark_price=106.0,
                        notional=42.4,
                        unrealized_pnl=2.4,
                        leverage=leverage,
                    )
                ],
            ),
        )

    assert state.positions == original_positions


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
