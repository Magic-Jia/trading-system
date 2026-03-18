from trading_system.app.universe.sector_map import sector_for_symbol
from trading_system.app.universe.liquidity_filter import passes_liquidity_filter
from trading_system.app.universe.builder import build_universes


def test_sector_for_symbol_uses_fallback_taxonomy():
    assert sector_for_symbol("BTCUSDT") == "majors"
    assert sector_for_symbol("DOGEUSDT")


def test_passes_liquidity_filter_rejects_thin_symbols():
    assert passes_liquidity_filter(
        {
            "rolling_notional": 5_000_000,
            "depth_proxy_notional": 500_000,
            "slippage_bps": 8,
            "listing_age_days": 365,
            "wick_risk_flag": False,
        }
    )


def test_build_universes_returns_major_rotation_and_short_pools(load_fixture):
    market = load_fixture("market_context_v2.json")
    universes = build_universes(market)

    assert universes.major_universe
    assert hasattr(universes, "rotation_universe")
    assert hasattr(universes, "short_universe")
    assert {row["symbol"] for row in universes.major_universe} == {"BTCUSDT", "ETHUSDT"}
    assert {row["symbol"] for row in universes.short_universe} == {"BTCUSDT", "ETHUSDT"}
    assert "sector" in universes.major_universe[0]
    assert "liquidity_meta" in universes.major_universe[0]
    assert "passes_liquidity" in universes.major_universe[0]


def test_rotation_universe_only_contains_liquid_mature_names(load_fixture):
    market = load_fixture("market_context_v2.json")
    universes = build_universes(market)

    assert universes.rotation_universe
    for row in universes.rotation_universe:
        assert row["passes_liquidity"] is True
        assert row["listing_age_ok"] is True
        assert row["sector"] != "majors"
        assert row["liquidity_meta"]["rolling_notional"] >= 800_000_000
        assert row["liquidity_meta"]["slippage_bps"] <= 20
    assert not passes_liquidity_filter(
        {
            "rolling_notional": 10_000,
            "depth_proxy_notional": 20_000,
            "slippage_bps": 80,
            "listing_age_days": 7,
            "wick_risk_flag": True,
        }
    )
