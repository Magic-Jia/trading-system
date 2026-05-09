import math

import pytest

from trading_system.app.signals import short_engine
from trading_system.app.signals.scoring import score_short_candidate
from trading_system.app.signals.short_engine import generate_short_candidates


def _defensive_market() -> dict:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {
                    "close": 96000.0,
                    "ema_20": 97500.0,
                    "ema_50": 99000.0,
                    "return_pct_7d": -0.052,
                    "volume_usdt_24h": 12_500_000_000.0,
                },
                "4h": {
                    "close": 95800.0,
                    "ema_20": 97000.0,
                    "ema_50": 98500.0,
                    "return_pct_3d": -0.031,
                },
                "1h": {
                    "close": 95750.0,
                    "ema_20": 96500.0,
                    "ema_50": 97200.0,
                    "return_pct_24h": -0.011,
                },
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {
                    "close": 4920.0,
                    "ema_20": 5000.0,
                    "ema_50": 5100.0,
                    "return_pct_7d": -0.038,
                    "volume_usdt_24h": 6_800_000_000.0,
                },
                "4h": {
                    "close": 4905.0,
                    "ema_20": 4975.0,
                    "ema_50": 5060.0,
                    "return_pct_3d": -0.026,
                },
                "1h": {
                    "close": 4898.0,
                    "ema_20": 4940.0,
                    "ema_50": 4988.0,
                    "return_pct_24h": -0.008,
                },
            },
            "SOLUSDT": {
                "sector": "alt_l1",
                "liquidity_tier": "high",
                "daily": {
                    "close": 188.0,
                    "ema_20": 182.0,
                    "ema_50": 176.0,
                    "return_pct_7d": 0.041,
                    "volume_usdt_24h": 1_900_000_000.0,
                },
                "4h": {
                    "close": 189.0,
                    "ema_20": 184.0,
                    "ema_50": 179.0,
                    "return_pct_3d": 0.017,
                },
                "1h": {
                    "close": 190.0,
                    "ema_20": 186.0,
                    "ema_50": 183.0,
                    "return_pct_24h": 0.006,
                },
            },
        }
    }


def test_score_short_candidate_rewards_downtrend_alignment_and_liquidity():
    score = score_short_candidate(
        {
            "daily_bias": "down",
            "h4_structure": "breakdown",
            "h1_trigger": "confirmed",
            "momentum_quality": 0.86,
            "liquidity_quality": 0.94,
        }
    )

    assert score["total"] > 0
    assert score["components"]["timeframe_alignment"] > 0
    assert score["components"]["momentum_quality"] > 0


def test_generate_short_candidates_emit_explicit_stop_loss_and_invalidation_source():
    market = _defensive_market()
    candidates = generate_short_candidates(
        market,
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
            {"symbol": "ETHUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 6_800_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    assert candidates
    for candidate in candidates:
        reference = market["symbols"][candidate.symbol]["daily"]["close"]
        assert candidate.stop_loss > 0
        assert candidate.stop_loss > reference
        assert candidate.invalidation_source == "short_structure_reclaim_above_4h_ema50"


def test_generate_short_candidates_accepts_numeric_score_total(monkeypatch):
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    monkeypatch.setattr(
        short_engine,
        "score_short_candidate",
        lambda _features: {"total": 0.9, "components": {"test": 0.9}},
    )

    candidates = generate_short_candidates(market, short_universe=short_universe, regime=regime)

    assert [(candidate.symbol, candidate.score) for candidate in candidates] == [("BTCUSDT", 0.9)]


@pytest.mark.parametrize("bad_total", ["0.9", True, math.nan, math.inf])
def test_generate_short_candidates_rejects_present_invalid_score_total(monkeypatch, bad_total):
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    monkeypatch.setattr(
        short_engine,
        "score_short_candidate",
        lambda _features: {"total": bad_total, "components": {"test": 0.9}},
    )

    with pytest.raises(ValueError, match=r"short score\.total must be a finite non-bool number"):
        generate_short_candidates(market, short_universe=short_universe, regime=regime)


def test_generate_short_candidates_emits_major_short_setups_in_defensive_regime():
    candidates = generate_short_candidates(
        _defensive_market(),
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
            {"symbol": "ETHUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 6_800_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    assert candidates
    assert {candidate.engine for candidate in candidates} == {"short"}
    assert {candidate.symbol for candidate in candidates} == {"BTCUSDT", "ETHUSDT"}
    assert {candidate.setup_type for candidate in candidates} <= {"BREAKDOWN_SHORT", "FAILED_BOUNCE_SHORT"}
    assert {candidate.side for candidate in candidates} == {"SHORT"}



def test_generate_short_candidates_labels_true_breakdown_short_setup():
    candidates = generate_short_candidates(
        _defensive_market(),
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    candidate = next(item for item in candidates if item.symbol == "BTCUSDT")

    assert candidate.setup_type == "BREAKDOWN_SHORT"



def test_generate_short_candidates_labels_true_failed_bounce_short_setup():
    market = _defensive_market()
    market["symbols"]["ETHUSDT"]["4h"]["close"] = 4960.0
    market["symbols"]["ETHUSDT"]["4h"]["return_pct_3d"] = -0.015
    market["symbols"]["ETHUSDT"]["1h"]["close"] = 4925.0
    market["symbols"]["ETHUSDT"]["1h"]["return_pct_24h"] = -0.004

    candidates = generate_short_candidates(
        market,
        short_universe=[
            {"symbol": "ETHUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 6_800_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    candidate = next(item for item in candidates if item.symbol == "ETHUSDT")

    assert candidate.setup_type == "FAILED_BOUNCE_SHORT"



def test_generate_short_candidates_rejects_weak_ambiguous_downside():
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = -0.014
    market["symbols"]["BTCUSDT"]["4h"]["close"] = 96950.0
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = -0.006
    market["symbols"]["BTCUSDT"]["1h"]["close"] = 96480.0
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = -0.0015

    candidates = generate_short_candidates(
        market,
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    assert candidates == []


def test_generate_short_candidates_rejects_crowded_short_squeeze_risk():
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
        {"symbol": "ETHUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 6_800_000_000.0}},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": -0.00023,
                "open_interest_usdt": 31_500_000_000,
                "open_interest_change_24h_pct": -0.046,
                "mark_price_change_24h_pct": -0.022,
                "taker_buy_sell_ratio": 0.94,
                "basis_bps": -28,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": -0.00003,
                "open_interest_usdt": 17_200_000_000,
                "open_interest_change_24h_pct": -0.008,
                "mark_price_change_24h_pct": -0.014,
                "taker_buy_sell_ratio": 0.99,
                "basis_bps": -8,
            },
        ]
    }

    candidates = generate_short_candidates(
        market,
        short_universe=short_universe,
        derivatives=derivatives,
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    assert {candidate.symbol for candidate in candidates} == {"ETHUSDT"}


def test_generate_short_candidates_attach_derivatives_meta():
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": -0.00003,
                "open_interest_usdt": 31_500_000_000,
                "open_interest_change_24h_pct": -0.008,
                "mark_price_change_24h_pct": -0.014,
                "taker_buy_sell_ratio": 0.99,
                "basis_bps": -8,
            }
        ]
    }

    candidates = generate_short_candidates(
        market,
        short_universe=short_universe,
        derivatives=derivatives,
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
    )

    candidate = next(item for item in candidates if item.symbol == "BTCUSDT")

    assert candidate.timeframe_meta["derivatives"]["crowding_bias"] == "balanced"
    assert candidate.timeframe_meta["derivatives"]["basis_bps"] == -8.0


@pytest.mark.parametrize("bad_basis_bps", ["-25", True])
def test_generate_short_candidates_rejects_present_invalid_derivatives_basis_bps(monkeypatch, bad_basis_bps):
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    monkeypatch.setattr(
        short_engine,
        "symbol_derivatives_features",
        lambda _derivatives, _symbol: {"crowding_bias": "balanced", "basis_bps": bad_basis_bps},
    )

    with pytest.raises(ValueError, match="basis_bps"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            derivatives={"rows": [{"symbol": "BTCUSDT"}]},
            regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
        )


def test_short_term_short_candidates_expose_intraday_entry_reference_metadata():
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["30m"] = {
        "close": 95600.0,
        "ema_20": 95800.0,
        "ema_50": 96000.0,
    }
    market["symbols"]["BTCUSDT"]["15m"] = {
        "close": 95500.0,
        "ema_20": 95650.0,
        "ema_50": 95800.0,
    }

    candidates = generate_short_candidates(
        market,
        short_universe=[
            {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
        ],
        regime={"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}},
        entry_profile="short_term",
    )

    candidate = next(item for item in candidates if item.symbol == "BTCUSDT")

    assert candidate.timeframe_meta["gate_timeframes"] == ["daily", "4h", "1h"]
    assert candidate.timeframe_meta["trigger_timeframes"] == ["30m", "15m"]
    assert candidate.timeframe_meta["entry_reference_timeframes"] == ["15m", "30m", "1h", "4h", "daily"]
    assert candidate.timeframe_meta["stop_reference_timeframe"] == "15m"


def test_generate_short_candidates_rejects_present_string_numeric_required_timeframe_field():
    market = _defensive_market()
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    assert [
        candidate.symbol
        for candidate in generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
        )
    ] == ["BTCUSDT"]

    market["symbols"]["BTCUSDT"]["daily"]["close"] = "100"

    with pytest.raises(ValueError, match=r"BTCUSDT\.daily\.close"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_object_liquidity_meta():
    market = _defensive_market()
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    assert [
        candidate.symbol
        for candidate in generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "BTCUSDT",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": 12_500_000_000.0},
                },
            ],
            regime=regime,
        )
    ] == ["BTCUSDT"]

    with pytest.raises(ValueError, match=r"BTCUSDT\.liquidity_meta"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "BTCUSDT",
                    "sector": "majors",
                    "liquidity_meta": [("rolling_notional", 12_500_000_000.0)],
                },
            ],
            regime=regime,
        )


def test_generate_short_candidates_rejects_non_string_liquidity_meta_key():
    market = _defensive_market()
    market["symbols"]["DOGEUSDT"] = {
        "sector": "majors",
        "liquidity_tier": "high",
        "daily": {
            "close": 0.142,
            "ema_20": 0.148,
            "ema_50": 0.155,
            "return_pct_7d": -0.052,
            "volume_usdt_24h": 12_000_000_000.0,
        },
        "4h": {
            "close": 0.141,
            "ema_20": 0.146,
            "ema_50": 0.151,
            "return_pct_3d": -0.031,
        },
        "1h": {
            "close": 0.1405,
            "ema_20": 0.144,
            "ema_50": 0.149,
            "return_pct_24h": -0.011,
        },
    }
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    assert [
        candidate.liquidity_meta
        for candidate in generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "DOGEUSDT",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": 1_000_000.0, "source": "fixture"},
                },
            ],
            regime=regime,
        )
    ] == [
        {
            "rolling_notional": 1_000_000.0,
            "source": "fixture",
            "liquidity_tier": "high",
            "volume_usdt_24h": 12_000_000_000.0,
        }
    ]

    assert [
        candidate.liquidity_meta
        for candidate in generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "DOGEUSDT",
                    "sector": "majors",
                },
            ],
            regime=regime,
        )
    ] == [
        {
            "liquidity_tier": "high",
            "volume_usdt_24h": 12_000_000_000.0,
        }
    ]

    with pytest.raises(ValueError, match=r"DOGEUSDT\.liquidity_meta key must be a string"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "DOGEUSDT",
                    "sector": "majors",
                    "liquidity_meta": {123: "bad", "rolling_notional": 1_000_000.0},
                },
            ],
            regime=regime,
        )


@pytest.mark.parametrize("bad_rolling_notional", ["12500000000", True])
def test_generate_short_candidates_rejects_present_invalid_liquidity_rolling_notional(bad_rolling_notional):
    market = _defensive_market()
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    assert [
        candidate.symbol
        for candidate in generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "BTCUSDT",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": 12_500_000_000.0},
                },
            ],
            regime=regime,
        )
    ] == ["BTCUSDT"]

    with pytest.raises(ValueError, match=r"BTCUSDT\.liquidity_meta\.rolling_notional"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "BTCUSDT",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": bad_rolling_notional},
                },
            ],
            regime=regime,
        )


def test_generate_short_candidates_uses_canonical_symbol_for_liquidity_meta_errors():
    market = _defensive_market()
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match=r"BTCUSDT\.liquidity_meta\.rolling_notional"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": " btcusdt ",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": "12500000000"},
                },
            ],
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_string_short_universe_symbol():
    market = _defensive_market()
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match="short_universe.symbol"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": True,
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": 12_500_000_000.0},
                },
            ],
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_string_market_symbol_key():
    market = _defensive_market()
    market["symbols"][123] = market["symbols"]["BTCUSDT"]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match="market.symbols key"):
        generate_short_candidates(
            market,
            short_universe=[
                {
                    "symbol": "BTCUSDT",
                    "sector": "majors",
                    "liquidity_meta": {"rolling_notional": 12_500_000_000.0},
                },
            ],
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_string_payload_sector():
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["sector"] = True
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match=r"BTCUSDT\.sector"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_string_short_universe_sector_when_payload_sector_blank():
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["sector"] = ""
    short_universe = [
        {"symbol": "BTCUSDT", "sector": True, "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match=r"BTCUSDT\.short_universe\.sector"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
        )


def test_generate_short_candidates_rejects_present_non_string_payload_liquidity_tier():
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["liquidity_tier"] = True
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    with pytest.raises(ValueError, match=r"BTCUSDT\.liquidity_tier"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
        )


@pytest.mark.parametrize("bad_value", [True, math.nan, math.inf, -math.inf])
def test_short_term_candidates_reject_present_invalid_required_numeric(bad_value):
    market = _defensive_market()
    market["symbols"]["BTCUSDT"]["30m"] = {
        "close": 95600.0,
        "ema_20": 95800.0,
        "ema_50": 96000.0,
    }
    market["symbols"]["BTCUSDT"]["15m"] = {
        "close": 95500.0,
        "ema_20": 95650.0,
        "ema_50": 95800.0,
    }
    short_universe = [
        {"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}},
    ]
    regime = {"label": "HIGH_VOL_DEFENSIVE", "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8}}

    valid_candidates = generate_short_candidates(
        market,
        short_universe=short_universe,
        regime=regime,
        entry_profile="short_term",
    )
    assert {candidate.symbol for candidate in valid_candidates} == {"BTCUSDT"}

    market["symbols"]["BTCUSDT"]["daily"]["close"] = bad_value

    with pytest.raises(ValueError, match=r"BTCUSDT\.daily\.close"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime=regime,
            entry_profile="short_term",
        )


def test_generate_short_candidates_respects_regime_gate_and_suppression():
    market = _defensive_market()
    short_universe = [{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}}]

    assert (
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime={"label": "RISK_ON_TREND", "bucket_targets": {"trend": 0.8, "rotation": 0.2, "short": 0.0}},
        )
        == []
    )
    assert (
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime={
                "label": "HIGH_VOL_DEFENSIVE",
                "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8},
                "suppression_rules": ["short"],
            },
        )
        == []
    )


def test_generate_short_candidates_rejects_present_non_string_suppression_rule():
    market = _defensive_market()
    short_universe = [{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}}]

    with pytest.raises(ValueError, match=r"regime\.suppression_rules"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime={
                "label": "HIGH_VOL_DEFENSIVE",
                "bucket_targets": {"trend": 0.2, "rotation": 0.0, "short": 0.8},
                "suppression_rules": [True],
            },
        )


@pytest.mark.parametrize("bad_short_target", ["0.8", True])
def test_generate_short_candidates_rejects_present_invalid_short_bucket_target(bad_short_target):
    market = _defensive_market()
    short_universe = [{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}}]

    with pytest.raises(ValueError, match=r"regime\.bucket_targets\.short"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime={"label": "RISK_ON_TREND", "bucket_targets": {"short": bad_short_target}},
        )


def test_generate_short_candidates_rejects_present_non_string_regime_label():
    market = _defensive_market()
    short_universe = [{"symbol": "BTCUSDT", "sector": "majors", "liquidity_meta": {"rolling_notional": 12_500_000_000.0}}]

    with pytest.raises(ValueError, match="regime.label"):
        generate_short_candidates(
            market,
            short_universe=short_universe,
            regime={"label": True, "bucket_targets": {"short": 0.8}},
        )
