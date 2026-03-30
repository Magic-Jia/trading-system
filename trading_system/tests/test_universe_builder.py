from trading_system.app.config import AppConfig
import trading_system.app.universe.builder as builder_module
from trading_system.app.universe.sector_map import sector_for_symbol
from trading_system.app.universe.liquidity_filter import passes_liquidity_filter
from trading_system.app.universe.builder import build_universes
from trading_system.paper_snapshots import _paper_symbols


def _market_symbol(*, sector: str, liquidity_tier: str, volume_usdt_24h: float) -> dict[str, object]:
    return {
        "sector": sector,
        "liquidity_tier": liquidity_tier,
        "daily": {
            "atr_pct": 0.03,
            "volume_usdt_24h": volume_usdt_24h,
        },
        "4h": {
            "atr_pct": 0.02,
            "volume_usdt_24h": volume_usdt_24h,
        },
        "1h": {
            "atr_pct": 0.01,
            "volume_usdt_24h": volume_usdt_24h,
        },
    }


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


def test_default_paper_snapshot_defaults_support_a_real_rotation_universe(monkeypatch):
    monkeypatch.delenv("TRADING_PAPER_SNAPSHOT_SYMBOLS", raising=False)
    monkeypatch.delenv("TRADING_UNIVERSE_MIN_LIQUIDITY_USDT_24H", raising=False)
    monkeypatch.setattr(builder_module, "DEFAULT_CONFIG", AppConfig())

    full_market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
            "ETHUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=12_200_000_000),
            "SOLUSDT": _market_symbol(sector="alt_l1", liquidity_tier="high", volume_usdt_24h=430_000_000),
            "BNBUSDT": _market_symbol(sector="exchange", liquidity_tier="high", volume_usdt_24h=410_000_000),
            "XRPUSDT": _market_symbol(sector="payments", liquidity_tier="high", volume_usdt_24h=1_050_000_000),
            "ADAUSDT": _market_symbol(sector="alt_l1", liquidity_tier="high", volume_usdt_24h=920_000_000),
            "LINKUSDT": _market_symbol(sector="oracle", liquidity_tier="high", volume_usdt_24h=1_010_000_000),
        }
    }
    default_symbols = set(_paper_symbols())
    expected_rotation_symbols = {"SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"}
    default_market = {"symbols": {symbol: full_market["symbols"][symbol] for symbol in default_symbols}}

    universes = build_universes(default_market)
    rotation_symbols = {row["symbol"] for row in universes.rotation_universe}

    assert expected_rotation_symbols.issubset(default_symbols)
    assert expected_rotation_symbols.issubset(rotation_symbols)
