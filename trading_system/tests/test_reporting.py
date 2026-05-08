import pytest

from trading_system.app.reporting.daily_report import (
    build_lifecycle_report,
    build_rotation_report,
    build_short_report,
    build_trend_report,
)
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeframe_meta", [("relative_strength", {"daily_spread": 0.017})]),
        ("liquidity_meta", [("volume_usdt_24h", 3900000000.0)]),
    ],
)
def test_build_rotation_report_rejects_present_non_mapping_candidate_metadata(field, value):
    candidate = {
        "engine": "rotation",
        "symbol": "SOLUSDT",
        "score": 0.83,
        "timeframe_meta": {"relative_strength": {"daily_spread": 0.017}},
        "liquidity_meta": {"volume_usdt_24h": 3900000000.0},
    }
    candidate[field] = value

    with pytest.raises(ValueError, match=field):
        build_rotation_report(
            rotation_candidates=[candidate],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


def test_build_rotation_report_rejects_present_non_mapping_relative_strength():
    with pytest.raises(ValueError, match="relative_strength"):
        build_rotation_report(
            rotation_candidates=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "score": 0.83,
                    "timeframe_meta": {"relative_strength": [("daily_spread", 0.017)]},
                    "liquidity_meta": {"volume_usdt_24h": 3900000000.0},
                }
            ],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize("candidate", [object(), [("symbol", "SOLUSDT")]])
def test_build_rotation_report_rejects_non_mapping_candidate_rows(candidate):
    with pytest.raises(ValueError, match="rotation_candidates"):
        build_rotation_report(
            rotation_candidates=[candidate],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize("invalid_symbol", [123, " SOLUSDT"])
def test_build_rotation_report_rejects_present_non_canonical_candidate_symbol(invalid_symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_rotation_report(
            rotation_candidates=[
                {
                    "engine": "rotation",
                    "symbol": invalid_symbol,
                    "score": 0.83,
                }
            ],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize("invalid_symbol", [None, ""])
def test_build_rotation_report_rejects_missing_candidate_symbol(invalid_symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_rotation_report(
            rotation_candidates=[
                {
                    "engine": "rotation",
                    "symbol": invalid_symbol,
                    "score": 0.83,
                }
            ],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize("invalid_score", [None, "0.83", True])
def test_build_rotation_report_rejects_missing_or_invalid_candidate_score(invalid_score):
    with pytest.raises(ValueError, match="score"):
        build_rotation_report(
            rotation_candidates=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "score": invalid_score,
                }
            ],
            allocations=[],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize(("field", "value"), [("status", 7), ("status", " ACCEPTED")])
def test_build_rotation_report_rejects_present_non_canonical_allocation_status(field, value):
    allocation = {"engine": "rotation", "symbol": "SOLUSDT", "status": value}

    with pytest.raises(ValueError, match=field):
        build_rotation_report(
            rotation_candidates=[],
            allocations=[allocation],
            executions=[],
            rotation_universe=[],
        )


@pytest.mark.parametrize("allocation", [object(), [("symbol", "SOLUSDT")]])
def test_build_rotation_report_rejects_non_mapping_allocation_rows(allocation):
    with pytest.raises(ValueError, match="allocations"):
        build_rotation_report(
            rotation_candidates=[],
            allocations=[allocation],
            executions=[],
            rotation_universe=[],
        )


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
            "stop_family": "structure_stop",
            "stop_reference": "4h_ema20",
            "invalidation_source": "short_breakdown_failure_above_4h_ema20",
            "invalidation_reason": "breakdown continuation lost 4h breakdown resistance",
            "stop_policy_source": "shared_taxonomy",
            "timeframe_meta": {
                "daily_bias": "down",
                "h4_structure": "breakdown",
                "h1_trigger": "confirmed",
                "derivatives": {"crowding_bias": "balanced", "basis_bps": -8.0},
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
                "derivatives": {"crowding_bias": "balanced", "basis_bps": -8.0},
                "volume_usdt_24h": 12500000000.0,
                "liquidity_tier": "top",
                "stop_family": "structure_stop",
                "stop_reference": "4h_ema20",
                "invalidation_source": "short_breakdown_failure_above_4h_ema20",
                "invalidation_reason": "breakdown continuation lost 4h breakdown resistance",
                "stop_policy_source": "shared_taxonomy",
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
            "review_notes": [
                {
                    "symbol": "BTCUSDT",
                    "reason": "crowded_short_squeeze_risk",
                    "message": "BTCUSDT BREAKDOWN_SHORT suppressed: crowded-short squeeze risk remained elevated (crowding bias crowded_short, basis -31.0 bps).",
                }
            ],
        },
    )
    assert summary["short"]["review_notes"][0]["reason"] == "crowded_short_squeeze_risk"


def test_build_short_report_rejects_present_non_mapping_derivatives():
    with pytest.raises(ValueError, match="derivatives"):
        build_short_report(
            short_candidates=[
                {
                    "engine": "short",
                    "symbol": "BTCUSDT",
                    "setup_type": "BREAKDOWN_SHORT",
                    "score": 0.81,
                    "timeframe_meta": {"derivatives": [("crowding_bias", "balanced")]},
                }
            ],
            allocations=[],
            short_universe=[],
        )


@pytest.mark.parametrize("candidate", [object(), [("symbol", "BTCUSDT")]])
def test_build_short_report_rejects_non_mapping_candidate_rows(candidate):
    with pytest.raises(ValueError, match="short_candidates"):
        build_short_report(
            short_candidates=[candidate],
            allocations=[],
            short_universe=[],
        )


@pytest.mark.parametrize("invalid_setup_type", [7, " BREAKDOWN_SHORT"])
def test_build_short_report_rejects_present_non_canonical_setup_type(invalid_setup_type):
    with pytest.raises(ValueError, match="setup_type"):
        build_short_report(
            short_candidates=[
                {
                    "engine": "short",
                    "symbol": "BTCUSDT",
                    "setup_type": invalid_setup_type,
                    "score": 0.81,
                }
            ],
            allocations=[],
            short_universe=[],
        )


@pytest.mark.parametrize("invalid_score", [None, "0.81", False])
def test_build_short_report_rejects_missing_or_invalid_candidate_score(invalid_score):
    with pytest.raises(ValueError, match="score"):
        build_short_report(
            short_candidates=[
                {
                    "engine": "short",
                    "symbol": "BTCUSDT",
                    "setup_type": "BREAKDOWN_SHORT",
                    "score": invalid_score,
                }
            ],
            allocations=[],
            short_universe=[],
        )


def test_build_trend_report_rejects_present_non_mapping_timeframe_meta():
    with pytest.raises(ValueError, match="timeframe_meta"):
        build_trend_report(
            trend_candidates=[
                {
                    "engine": "trend",
                    "symbol": "ETHUSDT",
                    "setup_type": "BREAKOUT_LONG",
                    "score": 0.72,
                    "timeframe_meta": [("daily_bias", "up")],
                }
            ],
            allocations=[],
            major_universe=[],
        )


@pytest.mark.parametrize("candidate", [object(), [("symbol", "ETHUSDT")]])
def test_build_trend_report_rejects_non_mapping_candidate_rows(candidate):
    with pytest.raises(ValueError, match="trend_candidates"):
        build_trend_report(
            trend_candidates=[candidate],
            allocations=[],
            major_universe=[],
        )


@pytest.mark.parametrize("invalid_symbol", [None, "", 123, " ETHUSDT"])
def test_build_trend_report_rejects_invalid_candidate_symbol(invalid_symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_trend_report(
            trend_candidates=[
                {
                    "engine": "trend",
                    "symbol": invalid_symbol,
                    "setup_type": "BREAKOUT_LONG",
                    "score": 0.72,
                }
            ],
            allocations=[],
            major_universe=[],
        )


@pytest.mark.parametrize("invalid_score", [None, "0.72", True])
def test_build_trend_report_rejects_missing_or_invalid_candidate_score(invalid_score):
    with pytest.raises(ValueError, match="score"):
        build_trend_report(
            trend_candidates=[
                {
                    "engine": "trend",
                    "symbol": "ETHUSDT",
                    "setup_type": "BREAKOUT_LONG",
                    "score": invalid_score,
                }
            ],
            allocations=[],
            major_universe=[],
        )


def test_build_regime_summary_surfaces_allocation_aggressiveness_stats():
    summary = build_regime_summary(
        regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
        universes={"major_universe": [], "rotation_universe": [{"symbol": "SOLUSDT"}], "short_universe": []},
        candidates=[{"engine": "rotation", "symbol": "SOLUSDT"}, {"engine": "rotation", "symbol": "LINKUSDT"}],
        allocations=[
            {
                "engine": "rotation",
                "symbol": "SOLUSDT",
                "status": "ACCEPTED",
                "final_risk_budget": 0.006,
                "aggressiveness_multiplier": 1.08,
                "regime_hazard_multiplier": 1.0,
                "late_stage_heat_multiplier": 1.0,
            },
            {
                "engine": "rotation",
                "symbol": "LINKUSDT",
                "status": "DOWNSIZED",
                "final_risk_budget": 0.004,
                "aggressiveness_multiplier": 0.82,
                "regime_hazard_multiplier": 0.84,
                "late_stage_heat_multiplier": 0.8,
                "compression_reasons": ["regime_hazard", "late_stage_heat"],
            },
        ],
        executions=[],
    )

    assert summary["allocations"]["avg_aggressiveness"] == 0.95
    assert summary["allocations"]["compressed_count"] == 1
    assert summary["allocations"]["compression_reason_counts"] == {"regime_hazard": 1, "late_stage_heat": 1}
    assert summary["allocations"]["regime_hazard_compressed_count"] == 1
    assert summary["allocations"]["late_stage_heat_compressed_count"] == 1


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("label", " RISK_ON_TREND"),
        ("execution_policy", " normal"),
    ],
)
def test_build_regime_summary_rejects_present_non_canonical_regime_strings(field, invalid_value):
    regime = {"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"}
    regime[field] = invalid_value

    with pytest.raises(ValueError, match=f"regime.{field}"):
        build_regime_summary(
            regime=regime,
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("confidence", True),
        ("confidence", "0.88"),
        ("confidence", float("nan")),
        ("risk_multiplier", False),
        ("risk_multiplier", "0.95"),
        ("risk_multiplier", float("inf")),
    ],
)
def test_build_regime_summary_rejects_present_invalid_regime_numbers(field, invalid_value):
    regime = {"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"}
    regime[field] = invalid_value

    with pytest.raises(ValueError, match=f"regime.{field}"):
        build_regime_summary(
            regime=regime,
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize("candidate", [object(), [("engine", "trend")]])
def test_build_regime_summary_rejects_non_mapping_candidate_rows(candidate):
    with pytest.raises(ValueError, match="candidates"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[candidate],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize("invalid_engine", [123, "", " rotation"])
def test_build_regime_summary_rejects_present_invalid_candidate_engine(invalid_engine):
    with pytest.raises(ValueError, match="candidates.engine"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[{"engine": invalid_engine, "symbol": "SOLUSDT"}],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize("execution", [object(), [("symbol", "SOLUSDT")]])
def test_build_regime_summary_rejects_non_mapping_execution_rows(execution):
    with pytest.raises(ValueError, match="executions"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[],
            executions=[execution],
        )


@pytest.mark.parametrize("invalid_value", [True, False, "0.006"])
def test_build_regime_summary_rejects_non_numeric_final_risk_budget(invalid_value):
    with pytest.raises(ValueError, match="final_risk_budget"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "status": "ACCEPTED",
                    "final_risk_budget": invalid_value,
                }
            ],
            executions=[],
        )


@pytest.mark.parametrize("invalid_value", [True, False, "0.82"])
def test_build_regime_summary_rejects_non_numeric_aggressiveness_multiplier(invalid_value):
    with pytest.raises(ValueError, match="aggressiveness_multiplier"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "status": "ACCEPTED",
                    "final_risk_budget": 0.006,
                    "aggressiveness_multiplier": invalid_value,
                }
            ],
            executions=[],
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("regime_hazard_multiplier", True),
        ("regime_hazard_multiplier", False),
        ("regime_hazard_multiplier", "0.84"),
        ("late_stage_heat_multiplier", True),
        ("late_stage_heat_multiplier", False),
        ("late_stage_heat_multiplier", "0.8"),
    ],
)
def test_build_regime_summary_rejects_non_numeric_compression_multipliers(field, invalid_value):
    with pytest.raises(ValueError, match=field):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "status": "ACCEPTED",
                    "final_risk_budget": 0.006,
                    field: invalid_value,
                }
            ],
            executions=[],
        )


@pytest.mark.parametrize("invalid_regime", [object(), [("label", "RISK_ON_TREND")]])
def test_build_regime_summary_rejects_non_mapping_regime_payloads(invalid_regime):
    with pytest.raises(ValueError, match="regime"):
        build_regime_summary(
            regime=invalid_regime,
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("major_universe", "BTCUSDT"),
        ("major_universe", 1),
        ("rotation_universe", "SOLUSDT"),
        ("rotation_universe", 2),
        ("short_universe", "ETHUSDT"),
        ("short_universe", 3),
    ],
)
def test_build_regime_summary_rejects_non_sequence_universe_values(field, invalid_value):
    universes = {"major_universe": [], "rotation_universe": [], "short_universe": []}
    universes[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes=universes,
            candidates=[],
            allocations=[],
            executions=[],
        )


@pytest.mark.parametrize("compression_reasons", ["regime_hazard", [123], ["regime_hazard", " late_stage_heat"]])
def test_build_regime_summary_rejects_non_canonical_compression_reasons(compression_reasons):
    with pytest.raises(ValueError, match="compression_reasons"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[
                {
                    "engine": "rotation",
                    "symbol": "SOLUSDT",
                    "status": "DOWNSIZED",
                    "final_risk_budget": 0.004,
                    "aggressiveness_multiplier": 0.82,
                    "compression_reasons": compression_reasons,
                }
            ],
            executions=[],
        )


@pytest.mark.parametrize("invalid_symbol", [123, " SOLUSDT"])
def test_build_regime_summary_rejects_non_canonical_execution_symbol(invalid_symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_regime_summary(
            regime={"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
            universes={"major_universe": [], "rotation_universe": [], "short_universe": []},
            candidates=[],
            allocations=[],
            executions=[{"symbol": invalid_symbol, "status": "FILLED"}],
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("trend_report", object()),
        ("trend_report", [("candidate_count", 1)]),
        ("rotation_report", object()),
        ("rotation_report", [("candidate_count", 1)]),
        ("short_report", object()),
        ("short_report", [("candidate_count", 1)]),
    ],
)
def test_build_regime_summary_rejects_non_mapping_optional_reports(field, invalid_value):
    kwargs = {
        "regime": {"label": "RISK_ON_TREND", "confidence": 0.88, "risk_multiplier": 0.95, "execution_policy": "normal"},
        "universes": {"major_universe": [], "rotation_universe": [], "short_universe": []},
        "candidates": [],
        "allocations": [],
        "executions": [],
    }
    kwargs[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        build_regime_summary(**kwargs)


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
        "management_action_counts": {"ADD_PROTECTIVE_STOP": 1, "EXIT": 1},
        "review_actions": [],
        "audit_target_states": [],
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


@pytest.mark.parametrize("invalid_state", [3, " PROTECT"])
def test_build_lifecycle_report_rejects_present_non_canonical_state(invalid_state):
    with pytest.raises(ValueError, match="state"):
        build_lifecycle_report(
            lifecycle_updates={"BTCUSDT": {"state": invalid_state, "reason_codes": ["payload_to_protect_trend_mature"]}},
            management_suggestions=[],
        )


@pytest.mark.parametrize("payload", [object(), [("state", "PROTECT")]])
def test_build_lifecycle_report_rejects_non_mapping_lifecycle_updates(payload):
    with pytest.raises(ValueError, match="lifecycle_updates"):
        build_lifecycle_report(
            lifecycle_updates={"BTCUSDT": payload},
            management_suggestions=[],
        )


@pytest.mark.parametrize("symbol", [123, " BTCUSDT", ""])
def test_build_lifecycle_report_rejects_non_canonical_lifecycle_update_symbol(symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_lifecycle_report(
            lifecycle_updates={symbol: {"state": "PROTECT", "r_multiple": 1.0}},
            management_suggestions=[],
        )


@pytest.mark.parametrize("reason_codes", [[123], ["valid", " invalid"]])
def test_build_lifecycle_report_rejects_present_non_canonical_reason_codes(reason_codes):
    with pytest.raises(ValueError, match="reason_codes"):
        build_lifecycle_report(
            lifecycle_updates={"BTCUSDT": {"state": "PROTECT", "reason_codes": reason_codes}},
            management_suggestions=[],
        )


def test_build_lifecycle_report_rejects_present_non_mapping_review_meta():
    with pytest.raises(ValueError, match="meta"):
        build_lifecycle_report(
            lifecycle_updates={},
            management_suggestions=[
                {
                    "symbol": "BTCUSDT",
                    "action": "BREAK_EVEN",
                    "meta": [("stop_family", "structure_stop")],
                }
            ],
        )


@pytest.mark.parametrize("suggestion", [object(), [("symbol", "BTCUSDT")]])
def test_build_lifecycle_report_rejects_non_mapping_management_suggestions(suggestion):
    with pytest.raises(ValueError, match="management_suggestions"):
        build_lifecycle_report(lifecycle_updates={}, management_suggestions=[suggestion])


@pytest.mark.parametrize("symbol", [123, " BTCUSDT"])
def test_build_lifecycle_report_rejects_non_canonical_attention_symbol(symbol):
    with pytest.raises(ValueError, match="symbol"):
        build_lifecycle_report(
            lifecycle_updates={},
            management_suggestions=[{"symbol": symbol, "action": "EXIT"}],
        )


@pytest.mark.parametrize("r_multiple", [None, "2.0", True])
def test_build_lifecycle_report_rejects_present_invalid_r_multiple(r_multiple):
    with pytest.raises(ValueError, match="r_multiple"):
        build_lifecycle_report(
            lifecycle_updates={"BTCUSDT": {"state": "PROTECT", "r_multiple": r_multiple}},
            management_suggestions=[],
        )


@pytest.mark.parametrize("suggested_stop_loss", ["100.0", False])
def test_build_lifecycle_report_rejects_present_invalid_review_numeric_fields(suggested_stop_loss):
    with pytest.raises(ValueError, match="suggested_stop_loss"):
        build_lifecycle_report(
            lifecycle_updates={},
            management_suggestions=[
                {
                    "symbol": "BTCUSDT",
                    "action": "BREAK_EVEN",
                    "suggested_stop_loss": suggested_stop_loss,
                    "meta": {"stop_family": "structure_stop"},
                }
            ],
        )


@pytest.mark.parametrize(("field", "value"), [("symbol", 123), ("action", " BREAK_EVEN"), ("priority", 5)])
def test_build_lifecycle_report_rejects_present_non_canonical_review_strings(field, value):
    row = {
        "symbol": "BTCUSDT",
        "action": "BREAK_EVEN",
        "priority": "MEDIUM",
        "meta": {"stop_family": "structure_stop"},
    }
    row[field] = value

    with pytest.raises(ValueError, match=field):
        build_lifecycle_report(lifecycle_updates={}, management_suggestions=[row])


def test_build_lifecycle_report_surfaces_review_ready_taxonomy_semantics():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PROTECT",
                "reason_codes": ["payload_to_protect_trend_mature"],
                "r_multiple": 2.0,
                "stop_family": "structure_stop",
                "stop_reference": "4h_ema20",
                "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                "invalidation_reason": "breakout continuation lost 4h breakout support",
            }
        },
        management_suggestions=[
            {
                "symbol": "BTCUSDT",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "reason": "breakout continuation lost 4h breakout support（trend_breakout_failure_below_4h_ema20）仍是当前失效条件，价格已至少走出 1R，允许把止损上提到保本位。",
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                    "invalidation_reason": "breakout continuation lost 4h breakout support",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "BTCUSDT",
                "action": "PARTIAL_TAKE_PROFIT",
                "priority": "MEDIUM",
                "qty_fraction": 0.5,
                "reason": "breakout continuation lost 4h breakout support（trend_breakout_failure_below_4h_ema20）仍是当前失效条件，已触及第一目标位，建议先兑现 50% 仓位并保留剩余仓位观察延伸。",
                "meta": {
                    "target_price": 110.0,
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                    "invalidation_reason": "breakout continuation lost 4h breakout support",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
        ],
    )

    assert summary["management_action_counts"] == {"BREAK_EVEN": 1, "PARTIAL_TAKE_PROFIT": 1}
    assert summary["review_actions"] == [
        {
            "symbol": "BTCUSDT",
            "action": "BREAK_EVEN",
            "priority": "MEDIUM",
            "stop_family": "structure_stop",
            "stop_reference": "4h_ema20",
            "invalidation_source": "trend_breakout_failure_below_4h_ema20",
            "invalidation_reason": "breakout continuation lost 4h breakout support",
            "stop_policy_source": "shared_taxonomy",
            "suggested_stop_loss": 100.0,
        },
        {
            "symbol": "BTCUSDT",
            "action": "PARTIAL_TAKE_PROFIT",
            "priority": "MEDIUM",
            "stop_family": "structure_stop",
            "stop_reference": "4h_ema20",
            "invalidation_source": "trend_breakout_failure_below_4h_ema20",
            "invalidation_reason": "breakout continuation lost 4h breakout support",
            "stop_policy_source": "shared_taxonomy",
            "qty_fraction": 0.5,
            "target_price": 110.0,
        },
    ]
    assert summary["leaders"][0]["stop_family"] == "structure_stop"
    assert summary["leaders"][0]["invalidation_source"] == "trend_breakout_failure_below_4h_ema20"


def test_build_lifecycle_report_surfaces_b_view_target_runner_fields():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PROTECT",
                "reason_codes": ["payload_to_protect_trend_mature"],
                "r_multiple": 2.0,
                "first_target_hit": True,
                "second_target_hit": True,
                "first_target_status": "filled",
                "second_target_status": "filled",
                "runner_protected": True,
                "runner_stop_price": 105.0,
                "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
                "second_target_source": "fixed_2r",
            },
            "ETHUSDT": {
                "state": "PAYLOAD",
                "reason_codes": ["payload_waiting_second_stage"],
                "r_multiple": 0.9,
                "first_target_hit": False,
                "second_target_hit": False,
                "first_target_status": "satisfied_by_external_reduction",
                "second_target_status": "pending",
                "runner_protected": False,
                "runner_stop_price": None,
                "scale_out_plan": {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"},
                "second_target_source": "fixed_2r",
            },
        },
        management_suggestions=[
            {
                "symbol": "BTCUSDT",
                "action": "PARTIAL_TAKE_PROFIT",
                "priority": "MEDIUM",
                "qty_fraction": 0.25,
                "meta": {
                    "target_stage": "second",
                    "fraction_basis": "original_position",
                    "runner_stop_price": 105.0,
                    "invalidation_source": "trend_breakout_failure_below_4h_ema20",
                    "invalidation_reason": "breakout continuation lost 4h breakout support",
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "stop_policy_source": "shared_taxonomy",
                },
            }
        ],
    )

    leader = summary["leaders"][0]
    assert leader["symbol"] == "BTCUSDT"
    assert leader["first_target_hit"] is True
    assert leader["second_target_hit"] is True
    assert leader["runner_protected"] is True
    assert leader["runner_stop_price"] == pytest.approx(105.0)
    assert leader["scale_out_plan"] == {"first": 0.5, "second": 0.25, "runner": 0.25, "basis": "original_position"}
    assert leader["second_target_source"] == "fixed_2r"
    assert summary["review_actions"][0]["target_stage"] == "second"
    assert summary["review_actions"][0]["fraction_basis"] == "original_position"
    assert summary["review_actions"][0]["runner_stop_price"] == pytest.approx(105.0)
    audit_rows = {row["symbol"]: row for row in summary["audit_target_states"]}
    assert audit_rows["BTCUSDT"] == {"symbol": "BTCUSDT", "first_target_status": "filled", "second_target_status": "filled"}
    assert audit_rows["ETHUSDT"] == {
        "symbol": "ETHUSDT",
        "first_target_status": "satisfied_by_external_reduction",
        "second_target_status": "pending",
    }


@pytest.mark.parametrize("flag", ["first_target_hit", "second_target_hit", "runner_protected"])
def test_build_lifecycle_report_rejects_non_bool_target_runner_flags(flag):
    with pytest.raises(ValueError, match=flag):
        build_lifecycle_report(
            lifecycle_updates={
                "BTCUSDT": {
                    "state": "PROTECT",
                    "reason_codes": ["payload_to_protect_trend_mature"],
                    "r_multiple": 2.0,
                    flag: "false",
                }
            },
            management_suggestions=[],
        )


def test_build_lifecycle_report_keeps_missing_target_runner_flags_absent():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PROTECT",
                "reason_codes": ["payload_to_protect_trend_mature"],
                "r_multiple": 2.0,
            }
        },
        management_suggestions=[],
    )

    leader = summary["leaders"][0]
    assert "first_target_hit" not in leader
    assert "second_target_hit" not in leader
    assert "runner_protected" not in leader


@pytest.mark.parametrize("field", ["first_target_status", "second_target_status"])
def test_build_lifecycle_report_rejects_present_invalid_target_status_fields(field):
    payload = {
        "state": "PAYLOAD",
        "reason_codes": ["payload_waiting_second_stage"],
        "r_multiple": 1.6,
        "first_target_status": "filled",
        "second_target_status": "pending",
    }
    payload[field] = None

    with pytest.raises(ValueError, match=field):
        build_lifecycle_report(
            lifecycle_updates={"BTCUSDT": payload},
            management_suggestions=[],
        )


def test_build_lifecycle_report_keeps_target_stage_review_rows_without_stop_taxonomy_meta():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PAYLOAD",
                "reason_codes": ["payload_waiting_second_stage"],
                "r_multiple": 1.6,
                "first_target_hit": True,
                "second_target_hit": False,
                "first_target_status": "filled",
                "second_target_status": "pending",
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
        management_suggestions=[
            {
                "symbol": "BTCUSDT",
                "action": "PARTIAL_TAKE_PROFIT",
                "priority": "MEDIUM",
                "qty_fraction": 0.25,
                "meta": {
                    "target_stage": "second",
                    "fraction_basis": "original_position",
                    "runner_stop_price": 105.0,
                },
            }
        ],
    )

    assert summary["review_actions"] == [
        {
            "symbol": "BTCUSDT",
            "action": "PARTIAL_TAKE_PROFIT",
            "priority": "MEDIUM",
            "qty_fraction": 0.25,
            "target_stage": "second",
            "fraction_basis": "original_position",
            "runner_stop_price": 105.0,
        }
    ]


def test_build_lifecycle_report_retains_target_stage_review_rows_under_review_cap():
    summary = build_lifecycle_report(
        lifecycle_updates={
            "BTCUSDT": {
                "state": "PAYLOAD",
                "reason_codes": ["payload_waiting_second_stage"],
                "r_multiple": 1.6,
                "first_target_hit": True,
                "second_target_hit": False,
                "first_target_status": "filled",
                "second_target_status": "pending",
                "runner_protected": False,
                "runner_stop_price": None,
            }
        },
        management_suggestions=[
            {
                "symbol": "S0",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "foo",
                    "invalidation_reason": "bar",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "S1",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "foo",
                    "invalidation_reason": "bar",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "S2",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "foo",
                    "invalidation_reason": "bar",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "S3",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "foo",
                    "invalidation_reason": "bar",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "S4",
                "action": "BREAK_EVEN",
                "priority": "MEDIUM",
                "suggested_stop_loss": 100.0,
                "meta": {
                    "stop_family": "structure_stop",
                    "stop_reference": "4h_ema20",
                    "invalidation_source": "foo",
                    "invalidation_reason": "bar",
                    "stop_policy_source": "shared_taxonomy",
                },
            },
            {
                "symbol": "BTCUSDT",
                "action": "PARTIAL_TAKE_PROFIT",
                "priority": "MEDIUM",
                "qty_fraction": 0.25,
                "meta": {
                    "target_stage": "second",
                    "fraction_basis": "original_position",
                    "runner_stop_price": 105.0,
                },
            },
        ],
    )

    assert len(summary["review_actions"]) == 5
    assert any(
        row["action"] == "PARTIAL_TAKE_PROFIT" and row.get("target_stage") == "second"
        for row in summary["review_actions"]
    )
