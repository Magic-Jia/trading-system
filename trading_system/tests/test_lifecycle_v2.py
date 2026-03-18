from trading_system.app.portfolio.lifecycle_v2 import advance_lifecycle_state, advance_lifecycle_transition
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
