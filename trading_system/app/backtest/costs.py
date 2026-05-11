from __future__ import annotations

import math

from .types import BacktestCosts, PortfolioSide

_BPS_DENOMINATOR = 10_000.0
_FUNDING_INTERVAL_HOURS = 8.0


def _canonical_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a string")
    return value


def _finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _non_negative_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return parsed


def _portfolio_side(value: object) -> PortfolioSide:
    if value not in {"long", "short"}:
        raise ValueError("side must be a valid portfolio side")
    return value  # type: ignore[return-value]


def fee_bps_for_market(costs: BacktestCosts, market_type: str) -> float:
    market = _canonical_string(market_type, field_name="market_type")
    return _non_negative_finite_number(
        costs.fee_bps_by_market.get(market, 0.0),
        field_name=f"fee_bps_by_market.{market}",
    )


def slippage_bps_for_tier(costs: BacktestCosts, liquidity_tier: str) -> float:
    tier = _canonical_string(liquidity_tier, field_name="liquidity_tier")
    tier_key = tier.lower()
    return _non_negative_finite_number(
        costs.slippage_bps_by_tier.get(tier_key, 0.0),
        field_name=f"slippage_bps_by_tier.{tier_key}",
    )


def fee_cost(*, position_notional: float, market_type: str, costs: BacktestCosts) -> float:
    notional = _non_negative_finite_number(position_notional, field_name="position_notional")
    if notional <= 0.0:
        return 0.0
    return (notional * 2.0 * fee_bps_for_market(costs, market_type)) / _BPS_DENOMINATOR


def slippage_cost(*, position_notional: float, liquidity_tier: str, costs: BacktestCosts) -> float:
    notional = _non_negative_finite_number(position_notional, field_name="position_notional")
    if notional <= 0.0:
        return 0.0
    return (notional * 2.0 * slippage_bps_for_tier(costs, liquidity_tier)) / _BPS_DENOMINATOR


def funding_cost(
    *,
    position_notional: float,
    market_type: str,
    side: PortfolioSide,
    funding_rate: float,
    holding_hours: float,
    costs: BacktestCosts,
) -> float:
    position = _non_negative_finite_number(position_notional, field_name="position_notional")
    rate = _finite_number(funding_rate, field_name="funding_rate")
    hours = _non_negative_finite_number(holding_hours, field_name="holding_hours")
    if position <= 0.0 or _canonical_string(market_type, field_name="market_type") != "futures" or costs.funding_mode != "historical_series":
        return 0.0
    if hours <= 0.0 or rate == 0.0:
        return 0.0
    intervals = hours / _FUNDING_INTERVAL_HOURS
    direction = 1.0 if _portfolio_side(side) == "long" else -1.0
    return position * rate * intervals * direction
