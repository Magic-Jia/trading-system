import pytest

import trading_system.app.portfolio.lifecycle as lifecycle
from trading_system.app.portfolio.lifecycle import advance_lifecycle_positions, build_management_action_intents, evaluate_position
from trading_system.app.portfolio.exit_policy import ExitDecision
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_target_hit", "false"),
        ("second_target_hit", 1),
        ("runner_protected", "true"),
    ],
)
def test_advance_lifecycle_positions_rejects_present_non_bool_target_management_flags(field, value):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "first_target_status": "pending",
                field: value,
            }
        },
    )

    with pytest.raises(ValueError, match=f"{field} must be a bool when present"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


def test_advance_lifecycle_positions_rejects_present_non_bool_tracked_from_intent():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "tracked_from_intent": "false",
            }
        },
    )

    with pytest.raises(ValueError, match="tracked_from_intent must be a bool when present"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


def test_advance_lifecycle_positions_rejects_present_non_mapping_scale_out_plan():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "first_target_status": "pending",
                "scale_out_plan": [("first", 0.5)],
            }
        },
    )

    with pytest.raises(TypeError, match="scale_out_plan must be a mapping when present"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


def test_advance_lifecycle_positions_rejects_scale_out_plan_non_string_keys():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "first_target_status": "pending",
                "scale_out_plan": {1: 0.5},
            }
        },
    )

    with pytest.raises(TypeError, match="scale_out_plan keys must be strings"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", 123),
        ("invalidation_source", 123),
        ("invalidation_reason", True),
        ("stop_family", 123),
        ("stop_reference", True),
        ("stop_policy_source", 123),
    ],
)
def test_evaluate_position_rejects_present_non_string_identity_and_taxonomy_fields(field, value):
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "stop_loss": 95.0,
        "status": "OPEN",
        field: value,
    }

    with pytest.raises(ValueError, match=f"{field} must be a string when present"):
        evaluate_position(position)


def test_evaluate_position_rejects_present_non_bool_tracked_from_intent_before_target_exit(monkeypatch):
    def fake_exit_policy(position, *, regime=None):
        return [
            ExitDecision(
                action="PARTIAL_TAKE_PROFIT",
                qty_fraction=0.5,
                priority="MEDIUM",
                reason="target hit",
                reference_price=105.0,
                meta={"exit_trigger": "first_target_hit"},
            )
        ]

    monkeypatch.setattr(lifecycle, "evaluate_exit_policy", fake_exit_policy)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "stop_loss": 95.0,
        "status": "OPEN",
        "tracked_from_intent": "false",
    }

    with pytest.raises(ValueError, match="tracked_from_intent must be a bool when present"):
        evaluate_position(position)


def test_evaluate_position_rejects_non_mapping_exit_decision_meta(monkeypatch):
    def fake_exit_policy(position, *, regime=None):
        return [
            ExitDecision(
                action="DE_RISK",
                qty_fraction=0.25,
                priority="HIGH",
                reason="risk reduction",
                reference_price=104.0,
                meta=[("exit_trigger", "defensive_regime_de_risk")],
            )
        ]

    monkeypatch.setattr(lifecycle, "evaluate_exit_policy", fake_exit_policy)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 104.0,
        "stop_loss": 95.0,
        "status": "OPEN",
    }

    with pytest.raises(TypeError, match="decision.meta must be a mapping when present"):
        evaluate_position(position)


def test_evaluate_position_rejects_exit_decision_meta_non_string_keys(monkeypatch):
    def fake_exit_policy(position, *, regime=None):
        return [
            ExitDecision(
                action="DE_RISK",
                qty_fraction=0.25,
                priority="HIGH",
                reason="risk reduction",
                reference_price=104.0,
                meta={1: "defensive_regime_de_risk"},
            )
        ]

    monkeypatch.setattr(lifecycle, "evaluate_exit_policy", fake_exit_policy)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 104.0,
        "stop_loss": 95.0,
        "status": "OPEN",
    }

    with pytest.raises(TypeError, match="decision.meta keys must be strings"):
        evaluate_position(position)


@pytest.mark.parametrize("taxonomy_stop_loss", ["95.0", True, float("nan")])
def test_evaluate_position_rejects_present_invalid_taxonomy_stop_loss(taxonomy_stop_loss):
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 106.0,
        "status": "OPEN",
        "taxonomy_stop_loss": taxonomy_stop_loss,
    }

    with pytest.raises(ValueError, match="taxonomy_stop_loss must be a finite non-bool number when present"):
        evaluate_position(position)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runner_stop_price", "bad"),
        ("runner_stop_price", True),
        ("runner_stop_price", float("nan")),
        ("take_profit", "110.0"),
        ("take_profit", True),
        ("take_profit", float("nan")),
    ],
)
def test_advance_lifecycle_positions_rejects_present_invalid_numeric_position_fields(field, value):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "first_target_status": "pending",
                field: value,
            }
        },
    )

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


@pytest.mark.parametrize("field", ["protect_r_multiple", "max_holding_hours"])
@pytest.mark.parametrize("value", ["bad", True, float("nan")])
def test_advance_lifecycle_positions_rejects_present_invalid_numeric_config_fields(field, value):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
            }
        },
    )

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        advance_lifecycle_positions(state, {field: value})


def test_advance_lifecycle_positions_rejects_config_non_string_keys():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
            }
        },
    )

    with pytest.raises(TypeError, match="lifecycle_config keys must be strings"):
        advance_lifecycle_positions(state, {1: 1.2})


@pytest.mark.parametrize(("field", "value"), [("entry_profile", 123), ("strategy_profile", True)])
def test_advance_lifecycle_positions_rejects_present_non_string_entry_profile_fields(field, value):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                field: value,
            }
        },
    )

    with pytest.raises(ValueError, match=f"{field} must be a string when present"):
        advance_lifecycle_positions(state, {"max_holding_hours": 24.0})


def test_advance_lifecycle_positions_rejects_non_mapping_latest_lifecycle_state_shape():
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
                "tracked_from_intent": True,
            }
        },
        latest_lifecycle={"BTCUSDT": [("state", "PAYLOAD")]},
    )

    with pytest.raises(TypeError, match="latest_lifecycle.BTCUSDT must be a mapping when present"):
        advance_lifecycle_positions(state, {"protect_r_multiple": 1.2})


@pytest.mark.parametrize(
    ("position_qty", "qty_fraction", "field"),
    [
        ("bad", 0.25, "qty"),
        (1.0, "0.25", "qty_fraction"),
        (1.0, True, "qty_fraction"),
        (float("nan"), 0.25, "qty"),
    ],
)
def test_build_management_action_intents_rejects_present_invalid_quantities(position_qty, qty_fraction, field):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": position_qty,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
            }
        },
    )

    with pytest.raises(ValueError, match=f"{field} must be a finite non-bool number when present"):
        build_management_action_intents(
            state,
            [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "action": "DE_RISK",
                    "qty_fraction": qty_fraction,
                    "reference_price": 106.0,
                    "meta": {},
                }
            ],
        )


def test_build_management_action_intents_rejects_non_mapping_suggestion_row():
    state = RuntimeStateV2(updated_at_bj="2026-04-09T20:00:00+08:00", positions={})

    with pytest.raises(TypeError, match="management suggestion row must be a mapping"):
        build_management_action_intents(state, [["symbol", "BTCUSDT"]])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", 123, "management suggestion symbol must be a canonical string"),
        ("symbol", " btcusdt", "management suggestion symbol must be a canonical string"),
        ("action", 123, "management suggestion action must be a canonical action"),
        ("action", " break_even", "management suggestion action must be a canonical action"),
    ],
)
def test_build_management_action_intents_rejects_non_string_or_noncanonical_row_identity(field, value, message):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
            }
        },
    )
    row = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "action": "DE_RISK",
        "qty_fraction": 0.25,
        "reference_price": 106.0,
        "meta": {},
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        build_management_action_intents(state, [row])


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        ([("target_stage", "first")], "management suggestion meta must be a mapping when present"),
        ({1: "first"}, "management suggestion meta keys must be strings"),
        ({"target_stage": 1}, "management suggestion meta.target_stage must be a string when present"),
    ],
)
def test_build_management_action_intents_rejects_invalid_row_meta_contract(meta, message):
    state = RuntimeStateV2(
        updated_at_bj="2026-04-09T20:00:00+08:00",
        positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 1.0,
                "entry_price": 100.0,
                "mark_price": 106.0,
                "stop_loss": 95.0,
                "status": "OPEN",
            }
        },
    )

    with pytest.raises((TypeError, ValueError), match=message):
        build_management_action_intents(
            state,
            [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "action": "DE_RISK",
                    "qty_fraction": 0.25,
                    "reference_price": 106.0,
                    "meta": meta,
                }
            ],
        )
