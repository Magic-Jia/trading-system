import pytest

from trading_system.app.config import RiskConfig
from trading_system.app.risk.position_sizer import size_signal
from trading_system.app.types import AccountSnapshot, TradeSignal


def _signal() -> TradeSignal:
    return TradeSignal(
        signal_id="position-sizing-boundary",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        source="strategy",
        timeframe="4h",
        tags=["v2", "trend"],
        meta={"setup_type": "BREAKOUT", "score": 0.8},
    )


def _account() -> AccountSnapshot:
    return AccountSnapshot(equity=1000.0, available_balance=1000.0, futures_wallet_balance=1000.0)


@pytest.mark.parametrize("risk_pct_override", [True, "0.01", float("nan"), float("inf")])
def test_size_signal_rejects_invalid_risk_pct_override_without_coercion(risk_pct_override):
    result = size_signal(_signal(), _account(), RiskConfig(), risk_pct_override=risk_pct_override)

    assert result.allowed is False
    assert result.qty == 0.0
    assert result.risk_budget_usdt == 0.0
    assert result.planned_loss_usdt == 0.0
    assert result.planned_notional_usdt == 0.0
    assert result.risk_pct_of_equity == 0.0
    assert "执行分配风险预算无效，拒绝 sizing" in result.notes


@pytest.mark.parametrize(
    ("risk_pct_override", "expected_risk_budget", "expected_qty"),
    [
        (0.01, 10.0, 1.2),
        (1, 1000.0, 1.2),
        (-0.01, 0.0, 0.0),
    ],
)
def test_size_signal_preserves_finite_numeric_risk_pct_override_semantics(
    risk_pct_override, expected_risk_budget, expected_qty
):
    result = size_signal(_signal(), _account(), RiskConfig(), risk_pct_override=risk_pct_override)

    assert result.allowed is (expected_qty > 0)
    assert result.qty == pytest.approx(expected_qty)
    assert result.risk_budget_usdt == pytest.approx(expected_risk_budget)
    assert result.planned_notional_usdt == pytest.approx(expected_qty * 100.0)
