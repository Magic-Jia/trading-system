import pytest

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
