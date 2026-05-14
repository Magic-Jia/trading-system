from __future__ import annotations

import math

import pytest

from trading_system.app.backtest.costs import fee_bps_for_market, fee_cost, funding_cost, slippage_bps_for_tier, slippage_cost
from trading_system.app.backtest.types import BacktestCosts


class _StringSubclass(str):
    pass


@pytest.mark.parametrize("market_type", [True, "", " futures", "futures "])
def test_fee_bps_rejects_noncanonical_market_type(market_type: object) -> None:
    costs = BacktestCosts(fee_bps_by_market={"True": 99.0})

    with pytest.raises(ValueError, match="market_type must be a string"):
        fee_bps_for_market(costs, market_type)  # type: ignore[arg-type]


@pytest.mark.parametrize("liquidity_tier", [True, "", " high", "high "])
def test_slippage_bps_rejects_noncanonical_liquidity_tier(liquidity_tier: object) -> None:
    costs = BacktestCosts(slippage_bps_by_tier={"true": 99.0})

    with pytest.raises(ValueError, match="liquidity_tier must be a string"):
        slippage_bps_for_tier(costs, liquidity_tier)  # type: ignore[arg-type]


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


@pytest.mark.parametrize("position_notional", [True, "1000.0", math.nan, math.inf, -math.inf, -1.0])
def test_trade_costs_reject_invalid_position_notional(position_notional: object) -> None:
    costs = BacktestCosts(fee_bps_by_market={"futures": 5.0}, slippage_bps_by_tier={"high": 10.0})

    with pytest.raises(ValueError, match="position_notional must be a non-negative finite number"):
        fee_cost(position_notional=position_notional, market_type="futures", costs=costs)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="position_notional must be a non-negative finite number"):
        slippage_cost(position_notional=position_notional, liquidity_tier="high", costs=costs)  # type: ignore[arg-type]


def test_trade_costs_return_zero_for_zero_notional() -> None:
    costs = BacktestCosts(fee_bps_by_market={"futures": 5.0}, slippage_bps_by_tier={"high": 10.0})

    assert fee_cost(position_notional=0.0, market_type="futures", costs=costs) == 0.0
    assert slippage_cost(position_notional=0.0, liquidity_tier="high", costs=costs) == 0.0


def test_funding_cost_rejects_negative_position_notional() -> None:
    costs = BacktestCosts(funding_mode="historical_series")

    with pytest.raises(ValueError, match="position_notional must be a non-negative finite number"):
        funding_cost(
            position_notional=-1_000.0,
            market_type="futures",
            side="long",
            funding_rate=0.001,
            holding_hours=8.0,
            costs=costs,
        )


def test_funding_cost_rejects_negative_holding_hours_before_zero_funding_short_circuit() -> None:
    costs = BacktestCosts(funding_mode=None)

    with pytest.raises(ValueError, match="holding_hours must be a non-negative finite number"):
        funding_cost(
            position_notional=1_000.0,
            market_type="spot",
            side="long",
            funding_rate=0.001,
            holding_hours=-1.0,
            costs=costs,
        )


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


@pytest.mark.parametrize(
    ("funding_rate", "holding_hours", "expected_message"),
    [
        (True, 8.0, "funding_rate must be a finite number"),
        ("0.001", 8.0, "funding_rate must be a finite number"),
        (math.nan, 8.0, "funding_rate must be a finite number"),
        (math.inf, 8.0, "funding_rate must be a finite number"),
        (0.001, True, "holding_hours must be a non-negative finite number"),
        (0.001, "8.0", "holding_hours must be a non-negative finite number"),
        (0.001, math.nan, "holding_hours must be a non-negative finite number"),
        (0.001, math.inf, "holding_hours must be a non-negative finite number"),
    ],
)
def test_funding_cost_rejects_invalid_numeric_inputs_before_zero_funding_short_circuit(
    funding_rate: object,
    holding_hours: object,
    expected_message: str,
) -> None:
    costs = BacktestCosts(funding_mode=None)

    with pytest.raises(ValueError, match=expected_message):
        funding_cost(
            position_notional=1_000.0,
            market_type="spot",
            side="long",
            funding_rate=funding_rate,  # type: ignore[arg-type]
            holding_hours=holding_hours,  # type: ignore[arg-type]
            costs=costs,
        )


@pytest.mark.parametrize(
    ("market_type", "side", "funding_mode", "expected_message"),
    [
        ("spot", "LONG", None, "side must be a valid portfolio side"),
        ("spot", "", None, "side must be a valid portfolio side"),
        ("spot", _StringSubclass("long"), None, "side must be a valid portfolio side"),
        (True, "long", None, "market_type must be a string"),
        ("", "long", None, "market_type must be a string"),
        (" futures", "long", None, "market_type must be a string"),
        ("futures ", "long", None, "market_type must be a string"),
        ("perps", "long", None, "market_type must be spot or futures"),
        (_StringSubclass("spot"), "long", None, "market_type must be a string"),
        ("spot", "long", "disabled", "funding_mode must be historical_series or None"),
        ("spot", "long", _StringSubclass("historical_series"), "funding_mode must be a string"),
    ],
)
def test_funding_cost_rejects_invalid_domain_inputs_before_zero_funding_short_circuit(
    market_type: object,
    side: object,
    funding_mode: object,
    expected_message: str,
) -> None:
    costs = BacktestCosts(funding_mode=funding_mode)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=expected_message):
        funding_cost(
            position_notional=1_000.0,
            market_type=market_type,  # type: ignore[arg-type]
            side=side,  # type: ignore[arg-type]
            funding_rate=0.0,
            holding_hours=0.0,
            costs=costs,
        )


@pytest.mark.parametrize(
    ("market_type", "funding_mode", "funding_rate", "holding_hours"),
    [
        ("spot", "historical_series", 0.001, 8.0),
        ("futures", None, 0.001, 8.0),
        ("futures", "historical_series", 0.001, 0.0),
        ("futures", "historical_series", 0.0, 8.0),
    ],
)
def test_funding_cost_returns_zero_for_inactive_funding_cases(
    market_type: str,
    funding_mode: str | None,
    funding_rate: float,
    holding_hours: float,
) -> None:
    costs = BacktestCosts(funding_mode=funding_mode)  # type: ignore[arg-type]

    assert (
        funding_cost(
            position_notional=1_000.0,
            market_type=market_type,
            side="long",
            funding_rate=funding_rate,
            holding_hours=holding_hours,
            costs=costs,
        )
        == 0.0
    )


def test_costs_apply_two_sided_fee_slippage_and_directional_historical_funding() -> None:
    costs = BacktestCosts(
        fee_bps_by_market={"futures": 5.0},
        slippage_bps_by_tier={"high": 10.0},
        funding_mode="historical_series",
    )

    assert fee_cost(position_notional=1_000.0, market_type="futures", costs=costs) == 1.0
    assert slippage_cost(position_notional=1_000.0, liquidity_tier="HIGH", costs=costs) == 2.0
    assert (
        funding_cost(
            position_notional=1_000.0,
            market_type="futures",
            side="long",
            funding_rate=0.001,
            holding_hours=16.0,
            costs=costs,
        )
        == 2.0
    )
    assert (
        funding_cost(
            position_notional=1_000.0,
            market_type="futures",
            side="short",
            funding_rate=0.001,
            holding_hours=16.0,
            costs=costs,
        )
        == -2.0
    )
