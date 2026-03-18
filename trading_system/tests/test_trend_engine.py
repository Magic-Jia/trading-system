from trading_system.app.signals.scoring import score_trend_candidate


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
