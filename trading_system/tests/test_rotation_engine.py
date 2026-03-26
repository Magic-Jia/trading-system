from trading_system.app.signals.rotation_engine import generate_rotation_candidates
from trading_system.app.signals.scoring import score_rotation_candidate


def test_score_rotation_candidate_rewards_leadership_persistence_and_liquidity():
    candidate = {
        "relative_strength_rank": 0.92,
        "persistence": 0.88,
        "pullback_quality": 0.72,
        "liquidity_quality": 0.81,
        "volatility_quality": 0.67,
    }
    score = score_rotation_candidate(candidate)
    assert score["total"] > 0
    assert score["components"]["relative_strength_rank"] > score["components"]["pullback_quality"]
    assert score["components"]["persistence"] > 0


def test_generate_rotation_candidates_uses_rotation_universe_and_emits_relative_strength_setups(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "DOGEUSDT", "sector": "memes", "liquidity_tier": "medium"},
    ]

    candidates = generate_rotation_candidates(market, rotation_universe=rotation_universe)

    assert candidates
    assert {candidate.engine for candidate in candidates} == {"rotation"}
    assert {candidate.setup_type for candidate in candidates} <= {"RS_PULLBACK", "RS_REACCELERATION"}
    assert {candidate.symbol for candidate in candidates} == {"SOLUSDT", "LINKUSDT", "ADAUSDT"}
    assert all(candidate.sector != "majors" for candidate in candidates)


def test_generate_rotation_candidates_emit_explicit_stop_loss_and_invalidation_source(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]

    candidates = generate_rotation_candidates(market, rotation_universe=rotation_universe)

    assert candidates
    for candidate in candidates:
        assert candidate.stop_loss > 0
        assert candidate.stop_loss < market["symbols"][candidate.symbol]["daily"]["close"]
        assert candidate.invalidation_source == "rotation_pullback_failure_below_1h_ema50"


def test_generate_rotation_candidates_rejects_overheated_crowded_leader(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "SOLUSDT",
                "funding_rate": 0.00024,
                "open_interest_usdt": 2900000000,
                "open_interest_change_24h_pct": 0.059,
                "mark_price_change_24h_pct": 0.026,
                "taker_buy_sell_ratio": 1.11,
                "basis_bps": 31,
            },
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ADAUSDT",
                "funding_rate": 0.00021,
                "open_interest_usdt": 1250000000,
                "open_interest_change_24h_pct": 0.041,
                "mark_price_change_24h_pct": 0.018,
                "taker_buy_sell_ratio": 1.04,
                "basis_bps": 24,
            },
        ]
    }

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def test_generate_rotation_candidates_require_absolute_strength_alongside_relative_strength(load_fixture):
    market = load_fixture("market_context_v2.json")
    rotation_universe = [
        {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        {"symbol": "ADAUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
    ]
    derivatives = {
        "rows": [
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00004,
                "open_interest_usdt": 1750000000,
                "open_interest_change_24h_pct": 0.01,
                "mark_price_change_24h_pct": 0.012,
                "taker_buy_sell_ratio": 1.01,
                "basis_bps": 12,
            },
            {
                "symbol": "ADAUSDT",
                "funding_rate": 0.00003,
                "open_interest_usdt": 1250000000,
                "open_interest_change_24h_pct": 0.009,
                "mark_price_change_24h_pct": 0.006,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 10,
            },
        ]
    }

    market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.008
    market["symbols"]["BTCUSDT"]["4h"]["return_pct_3d"] = 0.004
    market["symbols"]["BTCUSDT"]["1h"]["return_pct_24h"] = 0.001
    market["symbols"]["ETHUSDT"]["daily"]["return_pct_7d"] = 0.007
    market["symbols"]["ETHUSDT"]["4h"]["return_pct_3d"] = 0.003
    market["symbols"]["ETHUSDT"]["1h"]["return_pct_24h"] = 0.001

    market["symbols"]["ADAUSDT"]["daily"]["return_pct_7d"] = 0.012
    market["symbols"]["ADAUSDT"]["4h"]["return_pct_3d"] = 0.004
    market["symbols"]["ADAUSDT"]["1h"]["return_pct_24h"] = 0.0015

    candidates = generate_rotation_candidates(
        market,
        rotation_universe=rotation_universe,
        derivatives=derivatives,
    )

    assert {candidate.symbol for candidate in candidates} == {"LINKUSDT"}


def test_generate_rotation_candidates_respects_regime_suppression(load_fixture):
    market = load_fixture("market_context_v2.json")
    candidates = generate_rotation_candidates(
        market,
        rotation_universe=[
            {"symbol": "SOLUSDT", "sector": "alt_l1", "liquidity_tier": "high"},
            {"symbol": "LINKUSDT", "sector": "oracle", "liquidity_tier": "high"},
        ],
        regime={"suppression_rules": ["rotation"]},
    )

    assert candidates == []
