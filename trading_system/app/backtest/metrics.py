from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Sequence


def total_return(returns: Sequence[float]) -> float:
    equity = 1.0
    for period_return in returns:
        equity *= 1.0 + float(period_return)
    return equity - 1.0


def max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for period_return in returns:
        equity *= 1.0 + float(period_return)
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity / peak) - 1.0)
    return drawdown


def annualized_return(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    if not returns:
        return 0.0
    compounded = 1.0 + total_return(returns)
    if compounded <= 0:
        return -1.0
    return compounded ** (periods_per_year / len(returns)) - 1.0


def volatility(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    return pstdev(float(value) for value in returns) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(float(value) for value in returns)
    if sigma == 0:
        return 0.0
    return (mean(float(value) for value in returns) / sigma) * math.sqrt(periods_per_year)


def sortino_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    downside = [min(float(value), 0.0) for value in returns]
    downside_sigma = math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0.0
    if downside_sigma == 0:
        return 0.0
    return (mean(float(value) for value in returns) / downside_sigma) * math.sqrt(periods_per_year)


def calmar_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    drawdown = abs(max_drawdown(returns))
    if drawdown == 0:
        return 0.0
    return annualized_return(returns, periods_per_year=periods_per_year) / drawdown


def win_rate(trade_returns: Sequence[float]) -> float:
    if not trade_returns:
        return 0.0
    wins = sum(1 for trade_return in trade_returns if float(trade_return) > 0)
    return wins / len(trade_returns)


def payoff_ratio(trade_returns: Sequence[float]) -> float:
    wins = [float(value) for value in trade_returns if float(value) > 0]
    losses = [abs(float(value)) for value in trade_returns if float(value) < 0]
    if not wins or not losses:
        return 0.0
    return mean(wins) / mean(losses)


def expectancy(trade_returns: Sequence[float]) -> float:
    if not trade_returns:
        return 0.0
    return mean(float(value) for value in trade_returns)


def turnover(traded_notional: Sequence[float], *, average_equity: float) -> float:
    if average_equity <= 0:
        return 0.0
    return sum(abs(float(value)) for value in traded_notional) / average_equity


def cost_drag(gross_returns: Sequence[float], net_returns: Sequence[float]) -> float:
    return total_return(gross_returns) - total_return(net_returns)
