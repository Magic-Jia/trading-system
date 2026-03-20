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
