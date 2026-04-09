from trading_system.app.portfolio.lifecycle import advance_lifecycle_positions
from trading_system.app.portfolio.lifecycle_v2 import advance_lifecycle_state, advance_lifecycle_transition
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import LifecycleState


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
