from __future__ import annotations

import pytest

from trading_system.app.backtest.metrics import (
    calmar_ratio,
    cost_drag,
    expectancy,
    max_drawdown,
    payoff_ratio,
    sharpe_ratio,
    sortino_ratio,
    total_return,
    turnover,
    win_rate,
)


def test_backtest_metrics_are_deterministic() -> None:
    returns = [0.05, -0.02, 0.03, -0.01]
    trade_returns = [0.08, -0.03, 0.04, -0.02]

    assert total_return(returns) == pytest.approx(0.04927, rel=1e-4)
    assert max_drawdown(returns) == pytest.approx(-0.02, rel=1e-4)
    assert sharpe_ratio(returns, periods_per_year=4) == pytest.approx(0.8737, rel=1e-3)
    assert sortino_ratio(returns, periods_per_year=4) == pytest.approx(2.2361, rel=1e-3)
    assert calmar_ratio(returns, periods_per_year=4) == pytest.approx(2.4636, rel=1e-3)
    assert win_rate(trade_returns) == pytest.approx(0.5)
    assert payoff_ratio(trade_returns) == pytest.approx(2.4)
    assert expectancy(trade_returns) == pytest.approx(0.0175)
    assert turnover([12_000.0, -4_500.0, 3_000.0], average_equity=100_000.0) == pytest.approx(0.195)
    assert cost_drag([0.06, -0.01, 0.04], [0.05, -0.015, 0.032]) == pytest.approx(0.02403, rel=1e-4)
