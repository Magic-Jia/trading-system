import math

import pytest

from trading_system.app.signals.entry_profile import ACTIVE_PAPER_ENTRY_PROFILE
from trading_system.app.signals.scoring import score_trend_candidate
from trading_system.app.signals import trend_engine
from trading_system.app.signals.trend_engine import generate_trend_candidates


def test_score_trend_candidate_rewards_multi_timeframe_alignment():
    candidate = {
        "daily_bias": "up",
        "h4_structure": "intact",
        "h1_trigger": "confirmed",
        "volume_quality": 0.8,
    }
    score = score_trend_candidate(candidate)
    assert score["total"] > 0
    assert score["components"]["timeframe_alignment"] > 0


def test_score_trend_candidate_rejects_non_string_categorical_flag():
    candidate = {
        "daily_bias": True,
        "h4_structure": "intact",
        "h1_trigger": "confirmed",
        "volume_quality": 1.0,
    }

    with pytest.raises(ValueError):
        score_trend_candidate(candidate)


def test_score_trend_candidate_rejects_string_numeric_volume_quality():
    candidate = {
        "daily_bias": "up",
        "h4_structure": "intact",
        "h1_trigger": "confirmed",
        "volume_quality": "0.8",
    }

    with pytest.raises(ValueError):
        score_trend_candidate(candidate)


def test_generate_trend_candidates_produces_engine_candidates(load_fixture):
    market = load_fixture("market_context_v2.json")
    candidates = generate_trend_candidates(market)
    assert candidates
    assert candidates[0].engine == "trend"


def test_trend_engine_only_emits_breakout_or_pullback_setup_types(load_fixture):
    market = load_fixture("market_context_v2.json")
    setup_types = {candidate.setup_type for candidate in generate_trend_candidates(market)}
    assert setup_types <= {"BREAKOUT_CONTINUATION", "PULLBACK_CONTINUATION"}


def test_generate_trend_candidates_emit_explicit_stop_loss_and_invalidation_source(load_fixture):
    market = load_fixture("market_context_v2.json")

    candidates = generate_trend_candidates(market)

    assert candidates
    for candidate in candidates:
        assert candidate.stop_loss > 0
        assert candidate.stop_loss < market["symbols"][candidate.symbol]["daily"]["close"]
        assert candidate.invalidation_source == "trend_structure_loss_below_4h_ema50"


def test_generate_trend_candidates_filters_crowded_longs_from_symbol_level_derivatives(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.017,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": 0.00024,
                "open_interest_usdt": 11800000000,
                "open_interest_change_24h_pct": 0.052,
                "mark_price_change_24h_pct": 0.013,
                "taker_buy_sell_ratio": 1.08,
                "basis_bps": 28,
            },
        ]
    }

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    assert {candidate.symbol for candidate in candidates} == {"BTCUSDT"}


def test_generate_trend_candidates_reject_funding_basis_blowoff_even_when_structure_strength_and_extension_pass(
    load_fixture,
):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00022,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 26,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 11800000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    assert {candidate.symbol for candidate in candidates} == {"ETHUSDT"}


def test_generate_trend_candidates_reject_price_and_open_interest_acceleration_blowoff_before_funding_basis_extremes(
    load_fixture,
):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00005,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.045,
                "mark_price_change_24h_pct": 0.024,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 14,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 11800000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    market["symbols"]["BTCUSDT"]["4h"]["close"] = 65330.0
    market["symbols"]["BTCUSDT"]["1h"]["close"] = 64510.0

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    assert {candidate.symbol for candidate in candidates} == {"ETHUSDT"}


def test_generate_trend_candidates_require_absolute_strength_before_surviving(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 11800000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.02
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = 0.008
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = 0.002

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    assert {candidate.symbol for candidate in candidates} == {"ETHUSDT"}


def _modest_positive_trend_market() -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {
                    "close": 100.0,
                    "ema_20": 99.0,
                    "ema_50": 97.0,
                    "atr_pct": 0.04,
                    "return_pct_7d": 0.02,
                    "volume_usdt_24h": 18_000_000_000,
                },
                "4h": {
                    "close": 101.0,
                    "ema_20": 100.4,
                    "ema_50": 98.5,
                    "return_pct_3d": 0.006,
                    "volume_usdt_24h": 18_000_000_000,
                },
                "1h": {
                    "close": 101.0,
                    "ema_20": 100.6,
                    "ema_50": 99.2,
                    "return_pct_24h": 0.002,
                    "volume_usdt_24h": 18_000_000_000,
                },
            }
        }
    }


def test_generate_trend_candidates_keep_default_absolute_strength_gate_conservative_for_modest_positive_market():
    candidates = generate_trend_candidates(
        _modest_positive_trend_market(),
        include_high_liquidity_strong_names=False,
    )

    assert candidates == []


def test_generate_trend_candidates_active_paper_profile_allows_modest_positive_market():
    candidates = generate_trend_candidates(
        _modest_positive_trend_market(),
        include_high_liquidity_strong_names=False,
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )

    assert [candidate.symbol for candidate in candidates] == ["BTCUSDT"]
    assert candidates[0].stop_loss > 0


def test_generate_trend_candidates_rejects_invalid_required_numeric_boundaries():
    valid_candidates = generate_trend_candidates(
        _modest_positive_trend_market(),
        include_high_liquidity_strong_names=False,
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )
    assert [candidate.symbol for candidate in valid_candidates] == ["BTCUSDT"]

    invalid_cases = [
        ("daily", "return_pct_7d", True),
        ("4h", "return_pct_3d", math.inf),
        ("1h", "return_pct_24h", -math.inf),
        ("daily", "volume_usdt_24h", math.nan),
    ]
    for timeframe, field, invalid_value in invalid_cases:
        market = _modest_positive_trend_market()
        market["symbols"]["BTCUSDT"][timeframe][field] = invalid_value

        with pytest.raises(ValueError, match=f"BTCUSDT.{timeframe}.{field}"):
            generate_trend_candidates(
                market,
                include_high_liquidity_strong_names=False,
                entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
            )


def test_generate_trend_candidates_rejects_present_string_numeric_required_field():
    market = _modest_positive_trend_market()
    market["symbols"]["BTCUSDT"]["daily"]["close"] = "100"

    with pytest.raises(ValueError, match="BTCUSDT.daily.close"):
        generate_trend_candidates(
            market,
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


def test_generate_trend_candidates_rejects_non_string_symbol_key():
    market = _modest_positive_trend_market()
    market["symbols"] = {123: market["symbols"]["BTCUSDT"]}

    with pytest.raises(ValueError, match=r"market\.symbols key must be a string"):
        generate_trend_candidates(
            market,
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


@pytest.mark.parametrize("field,invalid_value", [("sector", True), ("liquidity_tier", 123)])
def test_generate_trend_candidates_rejects_present_non_string_category_field(field, invalid_value):
    market = _modest_positive_trend_market()
    market["symbols"]["BTCUSDT"][field] = invalid_value

    with pytest.raises(ValueError, match=f"BTCUSDT.{field}"):
        generate_trend_candidates(
            market,
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


def _active_paper_shallow_h1_pullback_trend_market() -> dict[str, object]:
    market = _modest_positive_trend_market()
    btc = market["symbols"]["BTCUSDT"]
    btc["1h"] = {
        "close": 99.4,
        "ema_20": 100.2,
        "ema_50": 99.0,
        "return_pct_24h": 0.0015,
        "volume_usdt_24h": 18_000_000_000,
    }
    return market


def test_generate_trend_candidates_active_paper_allows_major_shallow_h1_pullback_only_for_profile():
    market = _active_paper_shallow_h1_pullback_trend_market()

    default_candidates = generate_trend_candidates(
        market,
        include_high_liquidity_strong_names=False,
    )
    active_candidates = generate_trend_candidates(
        market,
        include_high_liquidity_strong_names=False,
        entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
    )

    assert default_candidates == []
    assert [candidate.symbol for candidate in active_candidates] == ["BTCUSDT"]
    assert active_candidates[0].stop_loss > 0
    assert active_candidates[0].timeframe_meta["h1_trigger"] == "active_paper_shallow_pullback"


def test_generate_trend_candidates_reject_overextended_longs_even_when_absolute_strength_is_high(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.018,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 11800000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.008,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    market["symbols"]["BTCUSDT"]["daily"]["close"] = 66400.0
    market["symbols"]["BTCUSDT"]["4h"]["close"] = 66400.0
    market["symbols"]["BTCUSDT"]["1h"]["close"] = 65150.0

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    assert {candidate.symbol for candidate in candidates} == {"ETHUSDT"}


def test_generate_trend_candidates_attach_derivatives_meta(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 23100000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.017,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            }
        ]
    }

    candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )

    candidate = next(item for item in candidates if item.symbol == "BTCUSDT")

    assert candidate.timeframe_meta["derivatives"]["crowding_bias"] == "balanced"


def test_generate_trend_candidates_rejects_non_string_derivatives_feature_key(monkeypatch):
    monkeypatch.setattr(
        trend_engine,
        "symbol_derivatives_features",
        lambda _derivatives, _symbol: {123: "bad", "crowding_bias": "balanced"},
    )

    with pytest.raises(ValueError, match=r"BTCUSDT\.derivatives key must be a string"):
        generate_trend_candidates(
            _modest_positive_trend_market(),
            derivatives={"rows": []},
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


@pytest.mark.parametrize("bad_basis_bps", ["25", True, math.inf])
def test_generate_trend_candidates_rejects_present_invalid_derivatives_basis_bps(monkeypatch, bad_basis_bps):
    monkeypatch.setattr(
        trend_engine,
        "symbol_derivatives_features",
        lambda _derivatives, _symbol: {"crowding_bias": "balanced", "basis_bps": bad_basis_bps},
    )

    with pytest.raises(ValueError, match=r"BTCUSDT\.derivatives\.basis_bps"):
        generate_trend_candidates(
            _modest_positive_trend_market(),
            derivatives={"rows": []},
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


@pytest.mark.parametrize("bad_crowding_bias", [True, 1])
def test_generate_trend_candidates_rejects_present_non_string_derivatives_crowding_bias(
    monkeypatch,
    bad_crowding_bias,
):
    monkeypatch.setattr(
        trend_engine,
        "symbol_derivatives_features",
        lambda _derivatives, _symbol: {"crowding_bias": bad_crowding_bias, "basis_bps": 0.0},
    )

    with pytest.raises(ValueError, match=r"BTCUSDT\.derivatives\.crowding_bias"):
        generate_trend_candidates(
            _modest_positive_trend_market(),
            derivatives={"rows": []},
            include_high_liquidity_strong_names=False,
            entry_profile=ACTIVE_PAPER_ENTRY_PROFILE,
        )


def _soft_non_major_trend_market(*, daily_close: float = 100.0, daily_ema50: float = 101.0) -> dict[str, object]:
    return {
        "symbols": {
            "LINKUSDT": {
                "sector": "oracle",
                "liquidity_tier": "high",
                "daily": {
                    "close": daily_close,
                    "ema_20": 99.0,
                    "ema_50": daily_ema50,
                    "atr_pct": 0.045,
                    "return_pct_7d": 0.034,
                    "volume_usdt_24h": 1_400_000_000,
                },
                "4h": {
                    "close": 102.0,
                    "ema_20": 101.0,
                    "ema_50": 99.0,
                    "return_pct_3d": 0.018,
                    "volume_usdt_24h": 1_400_000_000,
                },
                "1h": {
                    "close": 102.0,
                    "ema_20": 101.2,
                    "ema_50": 100.5,
                    "return_pct_24h": 0.005,
                    "volume_usdt_24h": 1_400_000_000,
                },
            }
        }
    }


def test_generate_trend_candidates_allows_non_major_soft_pretrend_in_supportive_regime():
    candidates = generate_trend_candidates(
        _soft_non_major_trend_market(),
        include_high_liquidity_strong_names=False,
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [candidate.symbol for candidate in candidates] == ["LINKUSDT"]


def test_generate_trend_candidates_rejects_non_string_suppression_rule_entries():
    with pytest.raises(ValueError, match="regime.suppression_rules"):
        generate_trend_candidates(
            _soft_non_major_trend_market(),
            include_high_liquidity_strong_names=False,
            regime={"label": "RISK_ON_ROTATION", "suppression_rules": [True]},
        )


def test_generate_trend_candidates_allows_non_major_soft_pretrend_when_daily_close_is_just_above_ema50():
    candidates = generate_trend_candidates(
        _soft_non_major_trend_market(daily_close=102.0, daily_ema50=101.0),
        include_high_liquidity_strong_names=False,
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert [candidate.symbol for candidate in candidates] == ["LINKUSDT"]


def test_generate_trend_candidates_rejects_non_major_soft_pretrend_when_daily_close_is_too_extended_above_ema50():
    candidates = generate_trend_candidates(
        _soft_non_major_trend_market(daily_close=104.0, daily_ema50=101.0),
        include_high_liquidity_strong_names=False,
        regime={"label": "RISK_ON_ROTATION", "suppression_rules": []},
    )

    assert candidates == []


def test_generate_trend_candidates_keeps_non_major_soft_pretrend_strict_in_risk_off():
    candidates = generate_trend_candidates(
        _soft_non_major_trend_market(),
        include_high_liquidity_strong_names=False,
        regime={"label": "RISK_OFF", "suppression_rules": []},
    )

    assert candidates == []
