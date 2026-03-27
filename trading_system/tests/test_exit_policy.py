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
