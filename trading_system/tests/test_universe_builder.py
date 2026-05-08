import pytest

from trading_system.app.config import AppConfig
import trading_system.app.universe.builder as builder_module
from trading_system.app.universe.sector_map import sector_for_symbol
from trading_system.app.universe.liquidity_filter import evaluate_liquidity, passes_liquidity_filter
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


def test_liquidity_filter_rejects_present_non_bool_wick_risk_flag():
    with pytest.raises(ValueError, match="wick_risk_flag"):
        evaluate_liquidity(
            {
                "rolling_notional": 5_000_000,
                "depth_proxy_notional": 500_000,
                "slippage_bps": 8,
                "listing_age_days": 365,
                "wick_risk_flag": "false",
            }
        )


def test_liquidity_filter_preserves_wick_risk_bool_and_flags_behavior():
    liquid_metrics = {
        "rolling_notional": 5_000_000,
        "depth_proxy_notional": 500_000,
        "slippage_bps": 8,
        "listing_age_days": 365,
    }

    assert evaluate_liquidity(liquid_metrics)["wick_risk_ok"] is True
    assert evaluate_liquidity({**liquid_metrics, "wick_risk_flag": False})["wick_risk_ok"] is True
    assert evaluate_liquidity({**liquid_metrics, "wick_risk_flag": True})["wick_risk_ok"] is False
    assert evaluate_liquidity({**liquid_metrics, "wick_risk_flags": ["long_upper_wick"]})["wick_risk_ok"] is False


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


def test_default_rotation_universe_includes_symbols_above_200m_spot_volume(monkeypatch):
    monkeypatch.delenv("TRADING_UNIVERSE_MIN_LIQUIDITY_USDT_24H", raising=False)
    monkeypatch.setattr(builder_module, "DEFAULT_CONFIG", AppConfig())

    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
            "AVAXUSDT": _market_symbol(sector="alt_l1", liquidity_tier="high", volume_usdt_24h=250_000_000),
        }
    }

    universes = build_universes(market)

    assert {row["symbol"] for row in universes.rotation_universe} == {"AVAXUSDT"}


def test_build_universes_rejects_non_string_market_symbol_keys():
    market = {
        "symbols": {
            123: _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
        }
    }

    with pytest.raises(ValueError, match="symbol"):
        build_universes(market)


def test_build_universes_rejects_present_non_string_liquidity_tier():
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier=123, volume_usdt_24h=19_800_000_000),
        }
    }

    with pytest.raises(ValueError, match="liquidity_tier"):
        build_universes(market)


@pytest.mark.parametrize("sector", [123, "", "   "])
def test_build_universes_rejects_present_invalid_sector(sector):
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector=sector, liquidity_tier="top", volume_usdt_24h=19_800_000_000),
        }
    }

    with pytest.raises(ValueError, match="sector"):
        build_universes(market)


def test_build_universes_rejects_non_mapping_liquidity_result(monkeypatch):
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
        }
    }
    monkeypatch.setattr(builder_module, "evaluate_liquidity", lambda _inputs: ["passes_liquidity", True])

    with pytest.raises(ValueError, match="evaluate_liquidity"):
        build_universes(market)


def test_build_universes_rejects_liquidity_result_with_non_string_keys(monkeypatch):
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
        }
    }
    monkeypatch.setattr(
        builder_module,
        "evaluate_liquidity",
        lambda _inputs: {
            1: "unexpected",
            "passes_liquidity": True,
            "listing_age_ok": True,
            "rolling_notional": 19_800_000_000,
        },
    )

    with pytest.raises(ValueError, match="evaluate_liquidity"):
        build_universes(market)


def test_build_universes_canonicalizes_liquidity_result_keys(monkeypatch):
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
        }
    }
    monkeypatch.setattr(
        builder_module,
        "evaluate_liquidity",
        lambda _inputs: {
            " passes_liquidity ": True,
            " listing_age_ok ": True,
            " rolling_notional ": 19_800_000_000,
        },
    )

    universes = build_universes(market)

    liquidity_meta = universes.major_universe[0]["liquidity_meta"]
    assert liquidity_meta["passes_liquidity"] is True
    assert liquidity_meta["listing_age_ok"] is True
    assert liquidity_meta["rolling_notional"] == 19_800_000_000


@pytest.mark.parametrize("field", ["passes_liquidity", "listing_age_ok"])
def test_build_universes_rejects_non_bool_liquidity_flags(monkeypatch, field):
    market = {
        "symbols": {
            "AVAXUSDT": _market_symbol(sector="alt_l1", liquidity_tier="high", volume_usdt_24h=900_000_000),
        }
    }
    liquidity = {
        "passes_liquidity": True,
        "listing_age_ok": True,
        "rolling_notional": 900_000_000,
    }
    liquidity[field] = "false"
    monkeypatch.setattr(builder_module, "evaluate_liquidity", lambda _inputs: liquidity)

    with pytest.raises(ValueError, match=field):
        build_universes(market)


def test_rotation_universe_can_use_derivatives_liquidity_not_just_spot_volume():
    market = {
        "symbols": {
            "BTCUSDT": _market_symbol(sector="majors", liquidity_tier="top", volume_usdt_24h=19_800_000_000),
            "SEIUSDT": _market_symbol(sector="alt_l1", liquidity_tier="high", volume_usdt_24h=150_000_000),
        }
    }
    derivatives = [
        {
            "symbol": "SEIUSDT",
            "funding_rate": 0.00011,
            "open_interest_usdt": 900_000_000,
            "open_interest_change_24h_pct": 0.07,
            "mark_price_change_24h_pct": 0.04,
            "taker_buy_sell_ratio": 1.06,
            "basis_bps": 18,
        }
    ]

    current_universes = build_universes(market)
    assert {row["symbol"] for row in current_universes.rotation_universe} == set()

    universes = build_universes(market, derivatives=derivatives)

    assert {row["symbol"] for row in universes.rotation_universe} == {"SEIUSDT"}
    assert universes.rotation_universe[0]["liquidity_meta"]["spot_volume_usdt_24h"] == 150_000_000
    assert universes.rotation_universe[0]["liquidity_meta"]["open_interest_usdt"] == 900_000_000
    assert universes.rotation_universe[0]["liquidity_meta"]["rolling_notional"] == 900_000_000
