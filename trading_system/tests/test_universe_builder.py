from trading_system.app.universe.sector_map import sector_for_symbol
from trading_system.app.universe.liquidity_filter import passes_liquidity_filter


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
    assert not passes_liquidity_filter(
        {
            "rolling_notional": 10_000,
            "depth_proxy_notional": 20_000,
            "slippage_bps": 80,
            "listing_age_days": 7,
            "wick_risk_flag": True,
        }
    )
