from trading_system.app.reporting.daily_report import build_rotation_report
from trading_system.app.reporting.regime_report import build_regime_summary


def test_build_rotation_report_returns_compact_deterministic_rotation_surface():
    rotation_candidates = [
        {
            "engine": "rotation",
            "symbol": "SOLUSDT",
            "score": 0.83,
            "timeframe_meta": {"relative_strength": {"daily_spread": 0.017, "h4_spread": 0.006, "h1_spread": 0.001}},
            "liquidity_meta": {"volume_usdt_24h": 3900000000.0, "slippage_bps": 8.0},
        },
        {
            "engine": "rotation",
            "symbol": "LINKUSDT",
            "score": 0.77,
            "timeframe_meta": {"relative_strength": {"daily_spread": 0.005, "h4_spread": -0.001, "h1_spread": -0.0015}},
            "liquidity_meta": {"volume_usdt_24h": 1010000000.0, "slippage_bps": 8.0},
        },
    ]
    allocations = [
        {"engine": "rotation", "symbol": "SOLUSDT", "status": "ACCEPTED"},
        {"engine": "rotation", "symbol": "LINKUSDT", "status": "DOWNSIZED"},
        {"engine": "trend", "symbol": "BTCUSDT", "status": "ACCEPTED"},
    ]
    executions = [
        {"symbol": "SOLUSDT", "status": "FILLED"},
        {"symbol": "LINKUSDT", "status": "FILLED"},
        {"symbol": "BTCUSDT", "status": "FILLED"},
    ]
    rotation_universe = [{"symbol": "SOLUSDT"}, {"symbol": "LINKUSDT"}, {"symbol": "ADAUSDT"}]

    summary = build_rotation_report(
        rotation_candidates=rotation_candidates,
        allocations=allocations,
        executions=executions,
        rotation_universe=rotation_universe,
    )

    assert summary == {
        "universe_count": 3,
        "candidate_count": 2,
        "accepted_symbols": ["LINKUSDT", "SOLUSDT"],
        "executed_symbols": ["LINKUSDT", "SOLUSDT"],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "score": 0.83,
                "daily_spread": 0.017,
                "h4_spread": 0.006,
                "h1_spread": 0.001,
                "volume_usdt_24h": 3900000000.0,
                "slippage_bps": 8.0,
            },
            {
                "symbol": "LINKUSDT",
                "score": 0.77,
                "daily_spread": 0.005,
                "h4_spread": -0.001,
                "h1_spread": -0.0015,
                "volume_usdt_24h": 1010000000.0,
                "slippage_bps": 8.0,
            },
        ],
    }


def test_build_regime_summary_includes_rotation_report_when_provided():
    summary = build_regime_summary(
        regime={"label": "RISK_ON_TREND", "confidence": 0.9, "risk_multiplier": 0.95, "execution_policy": "normal"},
        universes={"major_universe": [{"symbol": "BTCUSDT"}], "rotation_universe": [{"symbol": "SOLUSDT"}], "short_universe": []},
        candidates=[{"engine": "rotation", "symbol": "SOLUSDT"}],
        allocations=[{"engine": "rotation", "symbol": "SOLUSDT", "status": "ACCEPTED", "final_risk_budget": 0.01}],
        executions=[{"symbol": "SOLUSDT", "status": "FILLED"}],
        rotation_report={
            "universe_count": 1,
            "candidate_count": 1,
            "accepted_symbols": ["SOLUSDT"],
            "executed_symbols": ["SOLUSDT"],
            "leaders": [{"symbol": "SOLUSDT", "score": 0.83}],
        },
    )

    assert "rotation" in summary
    assert summary["rotation"]["candidate_count"] == 1
    assert summary["rotation"]["accepted_symbols"] == ["SOLUSDT"]
