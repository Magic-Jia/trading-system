from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any, Sequence


def _finite_float_sequence(values: Sequence[Any], *, field_name: str) -> list[float]:
    parsed_values: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool):
            raise ValueError(f"{field_name}[{index}] must be a finite number")
        try:
            parsed = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}[{index}] must be a finite number") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"{field_name}[{index}] must be a finite number")
        parsed_values.append(parsed)
    return parsed_values


def total_return(returns: Sequence[float]) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    equity = 1.0
    for period_return in returns:
        equity *= 1.0 + period_return
    return equity - 1.0


def max_drawdown(returns: Sequence[float]) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for period_return in returns:
        equity *= 1.0 + period_return
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity / peak) - 1.0)
    return drawdown


def annualized_return(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    if not returns:
        return 0.0
    compounded = 1.0 + total_return(returns)
    if compounded <= 0:
        return -1.0
    return compounded ** (periods_per_year / len(returns)) - 1.0


def volatility(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    if len(returns) < 2:
        return 0.0
    return pstdev(returns) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if sigma == 0:
        return 0.0
    return (mean(returns) / sigma) * math.sqrt(periods_per_year)


def sortino_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    downside = [min(value, 0.0) for value in returns]
    downside_sigma = math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0.0
    if downside_sigma == 0:
        return 0.0
    return (mean(returns) / downside_sigma) * math.sqrt(periods_per_year)


def calmar_ratio(returns: Sequence[float], *, periods_per_year: int = 252) -> float:
    returns = _finite_float_sequence(returns, field_name="returns")
    drawdown = abs(max_drawdown(returns))
    if drawdown == 0:
        return 0.0
    return annualized_return(returns, periods_per_year=periods_per_year) / drawdown


def win_rate(trade_returns: Sequence[float]) -> float:
    trade_returns = _finite_float_sequence(trade_returns, field_name="trade_returns")
    if not trade_returns:
        return 0.0
    wins = sum(1 for trade_return in trade_returns if trade_return > 0)
    return wins / len(trade_returns)


def payoff_ratio(trade_returns: Sequence[float]) -> float:
    trade_returns = _finite_float_sequence(trade_returns, field_name="trade_returns")
    wins = [value for value in trade_returns if value > 0]
    losses = [abs(value) for value in trade_returns if value < 0]
    if not wins or not losses:
        return 0.0
    return mean(wins) / mean(losses)


def expectancy(trade_returns: Sequence[float]) -> float:
    trade_returns = _finite_float_sequence(trade_returns, field_name="trade_returns")
    if not trade_returns:
        return 0.0
    return mean(trade_returns)


def turnover(traded_notional: Sequence[float], *, average_equity: float) -> float:
    traded_notional = _finite_float_sequence(traded_notional, field_name="traded_notional")
    if isinstance(average_equity, bool):
        raise ValueError("average_equity must be a finite number")
    try:
        average_equity = float(average_equity)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("average_equity must be a finite number") from exc
    if not math.isfinite(average_equity):
        raise ValueError("average_equity must be a finite number")
    if average_equity <= 0:
        return 0.0
    return sum(abs(value) for value in traded_notional) / average_equity


def cost_drag(gross_returns: Sequence[float], net_returns: Sequence[float]) -> float:
    gross_returns = _finite_float_sequence(gross_returns, field_name="gross_returns")
    net_returns = _finite_float_sequence(net_returns, field_name="net_returns")
    return total_return(gross_returns) - total_return(net_returns)
