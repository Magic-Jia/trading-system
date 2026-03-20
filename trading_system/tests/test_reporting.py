from trading_system.app.reporting.daily_report import build_lifecycle_report, build_rotation_report, build_short_report
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


def test_build_short_report_returns_compact_deterministic_short_surface():
    short_candidates = [
        {
            "engine": "short",
            "symbol": "BTCUSDT",
            "setup_type": "BREAKDOWN_SHORT",
            "score": 0.81,
            "timeframe_meta": {
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
            },
            "liquidity_meta": {"volume_usdt_24h": 12500000000.0, "liquidity_tier": "top"},
        }
    ]
    allocations = [
        {
            "engine": "short",
            "symbol": "BTCUSDT",
            "status": "ACCEPTED",
            "execution": {"status": "SKIPPED", "reason": "short_execution_not_enabled"},
        },
        {"engine": "trend", "symbol": "ETHUSDT", "status": "ACCEPTED"},
    ]
    short_universe = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]

    summary = build_short_report(
        short_candidates=short_candidates,
        allocations=allocations,
        short_universe=short_universe,
    )

    assert summary == {
        "universe_count": 2,
        "candidate_count": 1,
        "accepted_symbols": ["BTCUSDT"],
        "deferred_execution_symbols": ["BTCUSDT"],
        "leaders": [
            {
                "symbol": "BTCUSDT",
                "setup_type": "BREAKDOWN_SHORT",
                "score": 0.81,
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "volume_usdt_24h": 12500000000.0,
                "liquidity_tier": "top",
            }
        ],
    }


def test_build_regime_summary_includes_short_report_when_provided():
    summary = build_regime_summary(
        regime={"label": "HIGH_VOL_DEFENSIVE", "confidence": 0.82, "risk_multiplier": 0.7, "execution_policy": "downsize"},
        universes={
            "major_universe": [{"symbol": "BTCUSDT"}],
            "rotation_universe": [],
            "short_universe": [{"symbol": "BTCUSDT"}],
        },
        candidates=[{"engine": "short", "symbol": "BTCUSDT"}],
        allocations=[{"engine": "short", "symbol": "BTCUSDT", "status": "ACCEPTED", "final_risk_budget": 0.004}],
        executions=[],
        short_report={
            "universe_count": 1,
            "candidate_count": 1,
            "accepted_symbols": ["BTCUSDT"],
            "deferred_execution_symbols": ["BTCUSDT"],
            "leaders": [{"symbol": "BTCUSDT", "score": 0.81}],
        },
    )

    assert "short" in summary
    assert summary["short"]["candidate_count"] == 1
    assert summary["short"]["deferred_execution_symbols"] == ["BTCUSDT"]


def test_build_lifecycle_report_returns_compact_deterministic_state_surface():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "ETHUSDT": {
                "state": "PROTECT",
                "reason_codes": ["payload_to_protect_trend_mature"],
                "r_multiple": 1.42,
            },
            "BTCUSDT": {
                "state": "INIT",
                "reason_codes": ["init_waiting_confirmation"],
                "r_multiple": 0.35,
            },
            "SOLUSDT": {
                "state": "EXIT",
                "reason_codes": ["protect_to_exit_risk_trigger"],
                "r_multiple": 2.15,
            },
        },
        management_suggestions=[
            {"symbol": "SOLUSDT", "action": "EXIT"},
            {"symbol": "BTCUSDT", "action": "ADD_PROTECTIVE_STOP"},
        ],
    )

    assert summary == {
        "tracked_count": 3,
        "state_counts": {
            "INIT": 1,
            "CONFIRM": 0,
            "PAYLOAD": 0,
            "PROTECT": 1,
            "EXIT": 1,
        },
        "pending_confirmation_symbols": ["BTCUSDT"],
        "protected_symbols": ["ETHUSDT"],
        "exit_symbols": ["SOLUSDT"],
        "attention_symbols": ["BTCUSDT", "SOLUSDT"],
        "leaders": [
            {
                "symbol": "SOLUSDT",
                "state": "EXIT",
                "r_multiple": 2.15,
                "reason_codes": ["protect_to_exit_risk_trigger"],
            },
            {
                "symbol": "ETHUSDT",
                "state": "PROTECT",
                "r_multiple": 1.42,
                "reason_codes": ["payload_to_protect_trend_mature"],
            },
            {
                "symbol": "BTCUSDT",
                "state": "INIT",
                "r_multiple": 0.35,
                "reason_codes": ["init_waiting_confirmation"],
            },
        ],
    }
