from __future__ import annotations

import math

import pytest

from trading_system.app.backtest.costs import fee_bps_for_market, funding_cost, slippage_bps_for_tier
from trading_system.app.backtest.types import BacktestCosts


def test_fee_bps_rejects_non_string_market_type() -> None:
    costs = BacktestCosts(fee_bps_by_market={"True": 99.0})

    with pytest.raises(ValueError, match="market_type must be a string"):
        fee_bps_for_market(costs, True)  # type: ignore[arg-type]


def test_slippage_bps_rejects_non_string_liquidity_tier() -> None:
    costs = BacktestCosts(slippage_bps_by_tier={"true": 99.0})

    with pytest.raises(ValueError, match="liquidity_tier must be a string"):
        slippage_bps_for_tier(costs, True)  # type: ignore[arg-type]


@pytest.mark.parametrize("fee_bps", [True, "7.5", math.nan, math.inf, -math.inf, -1.0])
def test_fee_bps_rejects_invalid_configured_market_rate(fee_bps: object) -> None:
    costs = BacktestCosts(fee_bps_by_market={"futures": fee_bps})  # type: ignore[dict-item]

    with pytest.raises(ValueError, match="fee_bps_by_market.futures must be a non-negative finite number"):
        fee_bps_for_market(costs, "futures")


@pytest.mark.parametrize("slippage_bps", [True, "3.0", math.nan, math.inf, -math.inf, -1.0])
def test_slippage_bps_rejects_invalid_configured_tier_rate(slippage_bps: object) -> None:
    costs = BacktestCosts(slippage_bps_by_tier={"high": slippage_bps})  # type: ignore[dict-item]

    with pytest.raises(ValueError, match="slippage_bps_by_tier.high must be a non-negative finite number"):
        slippage_bps_for_tier(costs, "high")


def test_funding_cost_rejects_non_string_side_and_non_numeric_rate() -> None:
    costs = BacktestCosts(funding_mode="historical_series")

    with pytest.raises(ValueError, match="side must be a valid portfolio side"):
        funding_cost(
            position_notional=1_000.0,
            market_type="futures",
            side=True,  # type: ignore[arg-type]
            funding_rate=0.001,
            holding_hours=8.0,
            costs=costs,
        )

    with pytest.raises(ValueError, match="funding_rate must be a finite number"):
        funding_cost(
            position_notional=1_000.0,
            market_type="futures",
            side="long",
            funding_rate=True,  # type: ignore[arg-type]
            holding_hours=8.0,
            costs=costs,
        )
