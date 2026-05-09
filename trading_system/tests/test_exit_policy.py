import pytest

from trading_system.app.portfolio.exit_policy import ExitDecision, evaluate_exit_policy


def _position(**overrides):
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 1.0,
        "entry_price": 100.0,
        "mark_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": None,
        "status": "OPEN",
        "source": "paper_execution",
        "invalidation_source": "trend_breakout_failure_below_4h_ema20",
        "invalidation_reason": "breakout continuation lost 4h breakout support",
        "invalidation_triggered": False,
    }
    position.update(overrides)
    return position


def test_evaluate_exit_policy_emits_partial_take_profit_at_first_target():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=110.0,
            take_profit=110.0,
        )
    )

    assert decisions == [
        ExitDecision(
            action="PARTIAL_TAKE_PROFIT",
            qty_fraction=0.5,
            priority="MEDIUM",
            reason="breakout continuation lost 4h breakout support（trend_breakout_failure_below_4h_ema20）仍是当前失效条件，已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。",
            reference_price=pytest.approx(110.0),
            meta={
                "target_price": pytest.approx(110.0),
                "exit_trigger": "first_target_hit",
                "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                "invalidation_reason": "breakout continuation lost 4h breakout support",
            },
        )
    ]


def test_evaluate_exit_policy_emits_fail_fast_exit_when_invalidation_triggers_before_stop_loss():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=99.0,
            stop_loss=95.0,
            invalidation_triggered=True,
        )
    )

    assert decisions == [
        ExitDecision(
            action="EXIT",
            qty_fraction=1.0,
            priority="HIGH",
            reason="breakout continuation lost 4h breakout support（trend_breakout_failure_below_4h_ema20）已触发 thesis invalidation，建议先于硬止损执行 fail-fast 退出。",
            reference_price=pytest.approx(99.0),
            meta={
                "exit_trigger": "thesis_invalidation",
                "position_stop_loss": pytest.approx(95.0),
                "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                "invalidation_reason": "breakout continuation lost 4h breakout support",
            },
        )
    ]


def test_evaluate_exit_policy_rejects_non_bool_invalidation_flag():
    with pytest.raises(ValueError, match="invalidation_triggered must be a bool"):
        evaluate_exit_policy(
            _position(
                mark_price=99.0,
                stop_loss=95.0,
                invalidation_triggered="false",
            )
        )


def test_evaluate_exit_policy_emits_defensive_regime_de_risking_when_trade_is_in_profit():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=104.0,
            stop_loss=95.0,
        ),
        regime={
            "label": "CRASH_DEFENSIVE",
            "execution_policy": "downsize",
            "risk_multiplier": 0.35,
        },
    )

    assert decisions == [
        ExitDecision(
            action="DE_RISK",
            qty_fraction=0.25,
            priority="HIGH",
            reason="CRASH_DEFENSIVE regime is active, and the trade is already in profit; de-risk 25% instead of waiting for a full invalidation.",
            reference_price=pytest.approx(104.0),
            meta={
                "exit_trigger": "defensive_regime_de_risk",
                "regime_label": "CRASH_DEFENSIVE",
                "execution_policy": "downsize",
                "risk_multiplier": pytest.approx(0.35),
            },
        )
    ]


def test_evaluate_exit_policy_emits_first_and_second_partials_in_order_on_gap_through_second_target():
    decisions = evaluate_exit_policy(
        _position(
            side="LONG",
            mark_price=110.5,
            stop_loss=95.0,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="pending",
            second_target_status="pending",
            runner_protected=False,
        )
    )

    assert [(item.action, item.qty_fraction, item.meta["target_stage"]) for item in decisions] == [
        ("PARTIAL_TAKE_PROFIT", 0.5, "first"),
        ("PARTIAL_TAKE_PROFIT", 0.25, "second"),
    ]
    assert decisions[1].meta["runner_stop_price"] == pytest.approx(105.0)
    assert decisions[1].meta["runner_protected"] is True


def test_evaluate_exit_policy_emits_runner_exit_after_second_target_protection():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=104.5,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="filled",
            second_target_status="filled",
            runner_protected=True,
            runner_stop_price=105.0,
        )
    )

    assert decisions == [
        ExitDecision(
            action="EXIT",
            qty_fraction=1.0,
            priority="HIGH",
            reason="runner 保护价已被击穿，建议退出当前剩余全部尾仓。",
            reference_price=pytest.approx(104.5),
            meta={
                "exit_trigger": "runner_stop_hit",
                "runner_stop_price": pytest.approx(105.0),
                "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                "invalidation_reason": "breakout continuation lost 4h breakout support",
            },
        )
    ]


def test_evaluate_exit_policy_rejects_non_bool_runner_protected_flag():
    with pytest.raises(ValueError, match="runner_protected must be a bool"):
        evaluate_exit_policy(
            _position(
                mark_price=104.5,
                first_target_price=105.0,
                second_target_price=110.0,
                first_target_status="filled",
                second_target_status="filled",
                runner_protected="false",
                runner_stop_price=105.0,
            )
        )


@pytest.mark.parametrize("side", [True, 123])
def test_evaluate_exit_policy_rejects_present_non_string_side(side):
    with pytest.raises(ValueError, match="side must be a string when present"):
        evaluate_exit_policy(_position(side=side))


@pytest.mark.parametrize(
    ("field_name", "regime"),
    [
        ("label", {"label": 123}),
        ("execution_hazard", {"execution_hazard": True}),
    ],
)
def test_evaluate_exit_policy_rejects_present_non_string_regime_identity_fields(field_name, regime):
    with pytest.raises(ValueError, match=f"regime.{field_name} must be a string when present"):
        evaluate_exit_policy(
            _position(mark_price=104.0),
            regime=regime,
        )


def test_evaluate_exit_policy_allows_missing_or_none_regime_identity_fields():
    assert evaluate_exit_policy(
        _position(mark_price=104.0),
        regime={"label": None, "execution_hazard": None, "risk_multiplier": 1.0},
    ) == []


@pytest.mark.parametrize("risk_multiplier", [True, float("nan"), float("inf"), "bad"])
def test_evaluate_exit_policy_rejects_present_invalid_regime_risk_multiplier(risk_multiplier):
    with pytest.raises(
        ValueError,
        match="regime.risk_multiplier must be a finite non-bool number when present",
    ):
        evaluate_exit_policy(
            _position(mark_price=104.0),
            regime={"execution_hazard": "compress_risk", "risk_multiplier": risk_multiplier},
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("first_target_status", True),
        ("second_target_status", 123),
    ],
)
def test_evaluate_exit_policy_rejects_present_non_string_target_status_fields(field_name, value):
    with pytest.raises(ValueError, match=f"{field_name} must be a string when present"):
        evaluate_exit_policy(
            _position(
                first_target_price=105.0,
                second_target_price=110.0,
                **{field_name: value},
            )
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("entry_price", "bad"),
        ("entry_price", True),
        ("entry_price", float("nan")),
        ("entry_price", float("inf")),
        ("mark_price", "bad"),
        ("mark_price", False),
        ("mark_price", float("nan")),
        ("mark_price", float("inf")),
        ("stop_loss", "bad"),
        ("stop_loss", True),
        ("stop_loss", float("nan")),
        ("stop_loss", float("inf")),
        ("take_profit", "bad"),
        ("take_profit", False),
        ("take_profit", float("nan")),
        ("take_profit", float("inf")),
        ("first_target_price", "bad"),
        ("first_target_price", True),
        ("first_target_price", float("nan")),
        ("first_target_price", float("inf")),
        ("second_target_price", "bad"),
        ("second_target_price", False),
        ("second_target_price", float("nan")),
        ("second_target_price", float("inf")),
        ("runner_stop_price", "bad"),
        ("runner_stop_price", True),
        ("runner_stop_price", float("nan")),
        ("runner_stop_price", float("inf")),
    ],
)
def test_evaluate_exit_policy_rejects_present_invalid_core_numeric_fields(field_name, value):
    position = _position(
        first_target_price=105.0,
        second_target_price=110.0,
        runner_protected=True,
        runner_stop_price=105.0,
    )
    position[field_name] = value

    with pytest.raises(ValueError, match=f"{field_name} must be a finite non-bool number when present"):
        evaluate_exit_policy(position)


def test_evaluate_exit_policy_skips_invalid_runner_state_without_guessing_stop():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=104.5,
            first_target_price=105.0,
            second_target_price=110.0,
            runner_protected=True,
            runner_stop_price=None,
            first_target_status="filled",
            second_target_status="filled",
        )
    )

    assert decisions == []


def test_evaluate_exit_policy_does_not_stack_defensive_de_risk_on_same_round_as_target_stage():
    decisions = evaluate_exit_policy(
        _position(
            mark_price=105.0,
            first_target_price=105.0,
            second_target_price=110.0,
            first_target_status="pending",
            second_target_status="pending",
        ),
        regime={"label": "CRASH_DEFENSIVE", "execution_policy": "downsize", "risk_multiplier": 0.35},
    )

    assert [item.meta.get("exit_trigger") for item in decisions] == ["first_target_hit"]
