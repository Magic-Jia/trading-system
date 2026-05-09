import pytest

from trading_system.app.portfolio.lifecycle import advance_lifecycle_positions
from trading_system.app.portfolio.lifecycle_v2 import advance_lifecycle_state, advance_lifecycle_transition
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import LifecycleState


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confirmed", "true"),
        ("confirmed", 1),
        ("payload_ready", "true"),
        ("payload_ready", 1),
        ("trend_mature", "true"),
        ("trend_mature", 1),
        ("stop_hit", "true"),
        ("stop_hit", 1),
        ("exit_requested", "true"),
        ("exit_requested", 1),
        ("force_exit", "true"),
        ("force_exit", 1),
        ("protect_breached", "true"),
        ("protect_breached", 1),
        ("target_hit", "true"),
        ("target_hit", 1),
    ],
)
def test_lifecycle_rejects_present_non_bool_boolean_signals(field, value):
    with pytest.raises(ValueError, match=f"{field} must be a bool when present"):
        advance_lifecycle_transition(LifecycleState.PAYLOAD, {field: value})


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "bad"])
def test_lifecycle_rejects_present_invalid_r_multiple(value):
    with pytest.raises(ValueError, match="r_multiple must be a finite non-bool number when present"):
        advance_lifecycle_transition(LifecycleState.INIT, {"r_multiple": value})


@pytest.mark.parametrize("field", ["confirm_r_multiple", "protect_r_multiple", "exit_r_multiple"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "bad"])
def test_lifecycle_rejects_present_invalid_thresholds(field, value):
    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        advance_lifecycle_transition(LifecycleState.INIT, {}, config={field: value})


@pytest.mark.parametrize("current_state", [1, None, object()])
def test_lifecycle_rejects_present_non_string_non_lifecycle_state(current_state):
    with pytest.raises(ValueError, match="current_state must be a LifecycleState or string when present"):
        advance_lifecycle_transition(current_state, {})


def test_lifecycle_accepts_valid_string_current_state():
    next_state, reason_codes = advance_lifecycle_transition("PAYLOAD", {"r_multiple": 2.2, "trend_mature": True})

    assert next_state == LifecycleState.PROTECT
    assert reason_codes == ["payload_to_protect_trend_mature"]


def test_lifecycle_keeps_unknown_string_current_state_as_init():
    next_state, reason_codes = advance_lifecycle_transition("unknown", {"r_multiple": 0.8, "confirmed": True})

    assert next_state == LifecycleState.CONFIRM
    assert reason_codes == ["init_to_confirm_confirmed"]


def test_lifecycle_moves_from_init_to_confirm_on_confirmation_signal():
    state = advance_lifecycle_state(LifecycleState.INIT, {"r_multiple": 0.8, "confirmed": True})
    assert state == LifecycleState.CONFIRM


def test_lifecycle_moves_to_protect_after_profit_threshold():
    state = advance_lifecycle_state(LifecycleState.PAYLOAD, {"r_multiple": 2.2, "trend_mature": True})
    assert state == LifecycleState.PROTECT


def test_lifecycle_transition_returns_reason_codes():
    state, reason_codes = advance_lifecycle_transition(
        LifecycleState.PAYLOAD,
        {"r_multiple": 2.2, "trend_mature": True},
    )
    assert state == LifecycleState.PROTECT
    assert reason_codes == ["payload_to_protect_trend_mature"]


def test_advance_lifecycle_positions_keeps_snapshot_only_positions_stateless_between_identical_runs():
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
                "tracked_from_snapshot": True,
                "tracked_from_intent": False,
            }
        },
        latest_lifecycle={
            "BTCUSDT": {
                "state": "CONFIRM",
                "reason_codes": ["init_to_confirm_confirmed"],
                "r_multiple": 1.010342,
            }
        },
    )

    updates = advance_lifecycle_positions(state, {"confirm_r_multiple": 0.8, "protect_r_multiple": 1.2, "exit_r_multiple": 2.0})

    assert updates["BTCUSDT"]["state"] == "CONFIRM"
    assert updates["BTCUSDT"]["reason_codes"] == ["init_to_confirm_confirmed"]
