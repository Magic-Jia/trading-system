from __future__ import annotations

from .types import BacktestCosts, PortfolioSide

_BPS_DENOMINATOR = 10_000.0
_FUNDING_INTERVAL_HOURS = 8.0


def fee_bps_for_market(costs: BacktestCosts, market_type: str) -> float:
    return float(costs.fee_bps_by_market.get(str(market_type), 0.0))


def slippage_bps_for_tier(costs: BacktestCosts, liquidity_tier: str) -> float:
    return float(costs.slippage_bps_by_tier.get(str(liquidity_tier).lower(), 0.0))


def fee_cost(*, position_notional: float, market_type: str, costs: BacktestCosts) -> float:
    if position_notional <= 0.0:
        return 0.0
    return (position_notional * 2.0 * fee_bps_for_market(costs, market_type)) / _BPS_DENOMINATOR


def slippage_cost(*, position_notional: float, liquidity_tier: str, costs: BacktestCosts) -> float:
    if position_notional <= 0.0:
        return 0.0
    return (position_notional * 2.0 * slippage_bps_for_tier(costs, liquidity_tier)) / _BPS_DENOMINATOR


def funding_cost(
    *,
    position_notional: float,
    market_type: str,
    side: PortfolioSide,
    funding_rate: float,
    holding_hours: float,
    costs: BacktestCosts,
) -> float:
    if position_notional <= 0.0 or str(market_type) != "futures" or costs.funding_mode != "historical_series":
        return 0.0
    if holding_hours <= 0.0 or funding_rate == 0.0:
        return 0.0
    intervals = holding_hours / _FUNDING_INTERVAL_HOURS
    direction = 1.0 if str(side).lower() == "long" else -1.0
    return position_notional * float(funding_rate) * intervals * direction
