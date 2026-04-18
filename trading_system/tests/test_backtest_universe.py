from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_system.app.backtest.types import InstrumentSnapshotRow, UniverseFilterConfig
from trading_system.app.backtest.universe import filter_universe


def sample_universe_config() -> UniverseFilterConfig:
    return UniverseFilterConfig(
        listing_age_days=90,
        min_quote_volume_usdt_24h={"spot": 5_000_000.0, "futures": 20_000_000.0},
        require_complete_funding=True,
    )


def make_instrument(
    *,
    symbol: str,
    market_type: str,
    listing_age_days: int,
    quote_volume_usdt_24h: float,
    liquidity_tier: str = "high",
    quantity_step: float = 1.0,
    price_tick: float = 0.01,
    has_complete_funding: bool = True,
) -> InstrumentSnapshotRow:
    return InstrumentSnapshotRow(
        symbol=symbol,
        market_type=market_type,
        base_asset=symbol.removesuffix("USDT"),
        listing_timestamp=datetime.now(UTC) - timedelta(days=listing_age_days),
        quote_volume_usdt_24h=quote_volume_usdt_24h,
        liquidity_tier=liquidity_tier,
        quantity_step=quantity_step,
        price_tick=price_tick,
        has_complete_funding=has_complete_funding,
    )


def test_filter_universe_excludes_symbols_that_fail_listing_age_or_liquidity() -> None:
    rows = [
        make_instrument(symbol="BTCUSDT", market_type="spot", listing_age_days=400, quote_volume_usdt_24h=20_000_000.0),
        make_instrument(
            symbol="NEWCOINUSDT",
            market_type="spot",
            listing_age_days=10,
            quote_volume_usdt_24h=50_000_000.0,
        ),
        make_instrument(
            symbol="THINUSDT",
            market_type="futures",
            listing_age_days=200,
            quote_volume_usdt_24h=500_000.0,
        ),
    ]

    included, excluded = filter_universe(rows, universe_config=sample_universe_config())

    assert [row.symbol for row in included] == ["BTCUSDT"]
    assert {(row.symbol, row.reason_code) for row in excluded} == {
        ("NEWCOINUSDT", "listing_age_below_minimum"),
        ("THINUSDT", "quote_volume_below_minimum"),
    }


def test_filter_universe_excludes_missing_funding_or_tradeability_metadata() -> None:
    rows = [
        make_instrument(
            symbol="ETHUSDT",
            market_type="futures",
            listing_age_days=400,
            quote_volume_usdt_24h=30_000_000.0,
            has_complete_funding=False,
        ),
        make_instrument(
            symbol="BADMETAUSDT",
            market_type="spot",
            listing_age_days=400,
            quote_volume_usdt_24h=8_000_000.0,
            quantity_step=0.0,
        ),
        make_instrument(
            symbol="SOLUSDT",
            market_type="spot",
            listing_age_days=400,
            quote_volume_usdt_24h=10_000_000.0,
            has_complete_funding=False,
        ),
    ]

    included, excluded = filter_universe(rows, universe_config=sample_universe_config())

    assert [row.symbol for row in included] == ["SOLUSDT"]
    assert {(row.symbol, row.reason_code) for row in excluded} == {
        ("ETHUSDT", "missing_funding_series"),
        ("BADMETAUSDT", "missing_tradeability_metadata"),
    }
