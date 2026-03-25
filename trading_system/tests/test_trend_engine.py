from trading_system.app.signals.scoring import score_trend_candidate
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
