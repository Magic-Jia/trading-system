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
