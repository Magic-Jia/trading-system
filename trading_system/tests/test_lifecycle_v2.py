from trading_system.app.portfolio.lifecycle import advance_lifecycle_positions
from trading_system.app.portfolio.lifecycle_v2 import advance_lifecycle_state, advance_lifecycle_transition
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import LifecycleState


def test_lifecycle_ignores_present_non_bool_boolean_signals():
    cases = [
        (LifecycleState.INIT, {"r_multiple": 0.8, "confirmed": "true"}, LifecycleState.INIT, ["init_waiting_confirmation"]),
        (LifecycleState.CONFIRM, {"payload_ready": "true"}, LifecycleState.CONFIRM, ["confirm_waiting_payload"]),
        (
            LifecycleState.PAYLOAD,
            {"r_multiple": 2.2, "trend_mature": "true"},
            LifecycleState.PAYLOAD,
            ["payload_active"],
        ),
        (LifecycleState.PAYLOAD, {"stop_hit": "true"}, LifecycleState.PAYLOAD, ["payload_active"]),
        (LifecycleState.PAYLOAD, {"exit_requested": "true"}, LifecycleState.PAYLOAD, ["payload_active"]),
        (LifecycleState.PROTECT, {"force_exit": "true"}, LifecycleState.PROTECT, ["protect_active"]),
        (LifecycleState.PROTECT, {"protect_breached": "true"}, LifecycleState.PROTECT, ["protect_active"]),
        (
            LifecycleState.PROTECT,
            {"r_multiple": 2.2, "target_hit": "true"},
            LifecycleState.PROTECT,
            ["protect_active"],
        ),
    ]

    for current_state, signals, expected_state, expected_reasons in cases:
        next_state, reason_codes = advance_lifecycle_transition(current_state, signals)

        assert next_state == expected_state
        assert reason_codes == expected_reasons


def test_lifecycle_ignores_bool_and_non_finite_r_multiple():
    cases = [
        (LifecycleState.INIT, {"r_multiple": True, "confirmed": True}, LifecycleState.INIT, ["init_waiting_confirmation"]),
        (
            LifecycleState.PAYLOAD,
            {"r_multiple": float("inf"), "trend_mature": True},
            LifecycleState.PAYLOAD,
            ["payload_active"],
        ),
        (
            LifecycleState.PROTECT,
            {"r_multiple": float("nan"), "target_hit": True},
            LifecycleState.PROTECT,
            ["protect_active"],
        ),
    ]

    for current_state, signals, expected_state, expected_reasons in cases:
        next_state, reason_codes = advance_lifecycle_transition(current_state, signals)

        assert next_state == expected_state
        assert reason_codes == expected_reasons


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
