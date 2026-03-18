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
