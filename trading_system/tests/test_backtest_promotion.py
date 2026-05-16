from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from trading_system.app.backtest import cli
import trading_system.app.backtest.promotion as promotion
from trading_system.app.backtest.reporting import render_allocator_friction_report


def _manifest(*, experiment_kind: str, baseline_name: str, variant_name: str, artifacts: list[str]) -> dict[str, object]:
    return {
        "experiment_kind": experiment_kind,
        "dataset_root": "/tmp/dataset",
        "baseline_name": baseline_name,
        "variant_name": variant_name,
        "sample_period": {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-31T00:00:00+00:00"},
        "window_counts": {"full_history": 4},
        "bundle_name": f"{experiment_kind}__{baseline_name}__{variant_name}",
        "snapshot_count": 4,
        "artifacts": artifacts,
        "universe_asof_contract": {
            "schema_version": "universe_asof_contract.v1",
            "membership_source": "historical_instrument_snapshot",
            "as_of_field": "instrument_snapshot.as_of",
            "decision_timestamp_field": "metadata.timestamp",
            "required_lifecycle_fields": [
                "lifecycle_status",
                "delisted_at",
                "previous_symbol",
                "renamed_at",
                "contract_migration",
            ],
            "supports_delisted": True,
            "supports_renames": True,
            "supports_contract_migrations": True,
        },
        "margin_liquidation_path_contract": {
            "schema_version": "margin_liquidation_path_contract.v1",
            "scope": "futures_trade_ledger",
            "margin_mode_field": "trades[].margin_mode",
            "maintenance_tier_field": "trades[].maintenance_tier",
            "leverage_field": "trades[].leverage",
            "notional_field": "trades[].notional",
            "unrealized_pnl_field": "trades[].unrealized_pnl",
            "liquidation_price_field": "trades[].liquidation_price",
            "funding_accrual_field": "trades[].funding_accrual",
            "as_of_field": "trades[].margin_evidence_as_of",
            "accepted_margin_modes": ["isolated", "cross"],
            "fail_closed": True,
        },
        "dynamic_sizing_evidence_contract": {
            "schema_version": "dynamic_sizing_evidence_contract.v1",
            "scope": "futures_trade_ledger",
            "decision_timestamp_field": "trades[].sizing_decision_at",
            "evidence_as_of_field": "trades[].dynamic_sizing_evidence.evidence_as_of",
            "baseline_risk_field": "trades[].dynamic_sizing_evidence.baseline_risk_fraction",
            "final_risk_field": "trades[].dynamic_sizing_evidence.final_risk_fraction",
            "override_evidence_field": "trades[].dynamic_sizing_evidence.override_evidence",
            "required_degradation_axes": ["liquidity", "volatility", "drawdown", "execution"],
            "fail_closed": True,
        },
        "tail_risk_report_contract": {
            "schema_version": "tail_risk_report_contract.v1",
            "scope": "walk_forward_oos_tail_risk",
            "report_field": "summary.tail_risk_report",
            "scorecard_field": "scorecard.tail_risk_report",
            "required_sections": [
                "cvar",
                "worst_n_days",
                "worst_n_trades",
                "stress_loss",
                "liquidation_proximity",
                "correlated_loss_clusters",
                "scenario_provenance",
            ],
            "fail_closed": True,
        },
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _multiple_testing_correction(*, number_of_trials: int, adjusted_pass: bool = True) -> dict[str, object]:
    return {
        "schema_version": "multiple_testing_correction.v1",
        "number_of_trials": number_of_trials,
        "correction_method": "bonferroni",
        "corrected_p_value": 0.02,
        "adjusted_threshold": 0.05,
        "adjusted_pass": adjusted_pass,
    }


def _false_discovery_guardrail(
    *,
    number_of_trials: int = 2,
    effective_trials: float = 2.0,
    non_normality_adjustment: float = 1.1,
    observed_sharpe: float = 0.7,
    deflated_sharpe: float = 0.32,
    min_deflated_sharpe: float = 0.2,
    adjusted_pass: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": "false_discovery_guardrail.v1",
        "method": "deflated_sharpe_conservative",
        "number_of_trials": number_of_trials,
        "effective_trials": effective_trials,
        "non_normality_adjustment": non_normality_adjustment,
        "observed_sharpe": observed_sharpe,
        "deflated_sharpe": deflated_sharpe,
        "min_deflated_sharpe": min_deflated_sharpe,
        "adjusted_pass": adjusted_pass,
    }


def _regime_stratified_oos_evidence(*, crash_total_return: float = 0.01) -> dict[str, object]:
    return {
        "schema_version": "regime_stratified_oos.v1",
        "required_buckets": ["volatility", "liquidity", "funding", "crash", "squeeze"],
        "buckets": [
            {
                "bucket": "volatility",
                "metrics": {"total_return": 0.03, "max_drawdown": -0.04, "sharpe": 0.7, "trade_count": 2},
            },
            {
                "bucket": "liquidity",
                "metrics": {"total_return": 0.02, "max_drawdown": -0.03, "sharpe": 0.6, "trade_count": 2},
            },
            {
                "bucket": "funding",
                "metrics": {"total_return": 0.015, "max_drawdown": -0.02, "sharpe": 0.5, "trade_count": 1},
            },
            {
                "bucket": "crash",
                "metrics": {"total_return": crash_total_return, "max_drawdown": -0.05, "sharpe": 0.2, "trade_count": 1},
            },
            {
                "bucket": "squeeze",
                "metrics": {"total_return": 0.018, "max_drawdown": -0.03, "sharpe": 0.4, "trade_count": 1},
            },
        ],
    }


def _pnl_attribution_evidence(*, reported_pnl: float = 0.08) -> dict[str, object]:
    return {
        "schema_version": "pnl_attribution.v1",
        "reported_pnl": reported_pnl,
        "buckets": [
            {"bucket": "entry_alpha", "contribution": 0.02},
            {"bucket": "exit_alpha", "contribution": 0.01},
            {"bucket": "sizing", "contribution": 0.02},
            {"bucket": "fees", "contribution": -0.005},
            {"bucket": "funding", "contribution": -0.002},
            {"bucket": "slippage_execution_impact", "contribution": -0.003},
            {"bucket": "regime", "contribution": 0.025},
            {"bucket": "symbol_selection", "contribution": reported_pnl - 0.065},
        ],
    }


def _drawdown_anatomy_evidence(
    *,
    severity_pct: object = 0.08,
    as_of: object = "2026-01-31T00:30:00Z",
    decision_timestamp: object = "2026-01-31T00:30:00Z",
    mitigation_evidence: object = ("reduce_cluster_exposure", "tighten_execution_gate"),
) -> dict[str, object]:
    return {
        "schema_version": "drawdown_anatomy.v1",
        "as_of": as_of,
        "decision_timestamp": decision_timestamp,
        "max_age_seconds": 3600,
        "severe_drawdown_threshold_pct": 0.1,
        "drawdowns": [
            {
                "drawdown_id": "dd-001",
                "severity_pct": severity_pct,
                "peak_timestamp": "2026-01-31T00:00:00Z",
                "trough_timestamp": "2026-01-31T00:10:00Z",
                "recovery_timestamp": "2026-01-31T00:25:00Z",
                "regime_cluster_id": "regime-crash",
                "symbol_cluster_id": "majors",
                "trade_cluster_id": "trade-cluster-001",
                "attribution": {
                    "edge_failure_pct": 0.02,
                    "execution_failure_pct": 0.05,
                    "risk_control_failure_pct": 0.01,
                    "primary_failure": "execution_failure",
                },
                "exposure_concentration": {
                    "max_symbol_exposure_pct": 0.22,
                    "max_cluster_exposure_pct": 0.42,
                    "crowded_risk_score": 0.31,
                },
                "mitigation_evidence": list(mitigation_evidence) if isinstance(mitigation_evidence, tuple) else mitigation_evidence,
            }
        ],
    }


def _walk_forward_split_metadata() -> dict[str, object]:
    return {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }


def _dynamic_sizing_evidence(*, final_risk_fraction: float = 0.015) -> dict[str, object]:
    return {
        "schema_version": "dynamic_sizing_evidence.v1",
        "required_axes": ["liquidity", "volatility", "drawdown", "execution"],
        "decisions": [
            {
                "decision_id": "sizing-001",
                "sizing_decision_at": "2026-01-04T00:00:00+00:00",
                "evidence_as_of": "2026-01-03T23:59:00+00:00",
                "baseline_risk_fraction": 0.02,
                "final_risk_fraction": final_risk_fraction,
                "axes": {
                    "liquidity": {"degraded": True, "risk_multiplier": 0.75},
                    "volatility": {"degraded": True, "risk_multiplier": 0.8},
                    "drawdown": {"degraded": True, "risk_multiplier": 0.7},
                    "execution": {"degraded": True, "risk_multiplier": 0.9},
                },
            }
        ],
    }


def _portfolio_correlation_exposure_evidence(
    *,
    net_exposure_pct: object = 0.18,
    gross_exposure_pct: object = 0.42,
    btc_symbol: object = "BTCUSDT",
    eth_cluster: object = "majors",
    risk_hold: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "portfolio_correlation_exposure.v1",
        "as_of": "2026-01-31T00:00:00Z",
        "decision_timestamp": "2026-01-31T00:30:00Z",
        "max_age_seconds": 3600,
        "limits": {
            "max_net_exposure_pct": 0.65,
            "max_gross_exposure_pct": 1.25,
            "max_symbol_gross_exposure_pct": 0.35,
            "max_cluster_gross_exposure_pct": 0.55,
            "max_pairwise_correlation": 0.85,
            "max_crowded_risk_score": 0.7,
        },
        "portfolio": {
            "net_exposure_pct": net_exposure_pct,
            "gross_exposure_pct": gross_exposure_pct,
        },
        "symbols": [
            {"symbol": btc_symbol, "cluster": "majors", "gross_exposure_pct": 0.22, "net_exposure_pct": 0.14},
            {"symbol": "ETHUSDT", "cluster": eth_cluster, "gross_exposure_pct": 0.20, "net_exposure_pct": 0.04},
        ],
        "clusters": [
            {"cluster": "majors", "gross_exposure_pct": 0.42, "net_exposure_pct": 0.18},
        ],
        "correlations": [
            {"left_symbol": "BTCUSDT", "right_symbol": "ETHUSDT", "correlation": 0.62},
        ],
        "crowded_risk": {
            "score": 0.31,
            "evidence": ["funding_neutral", "open_interest_stable"],
        },
        **({"risk_hold": risk_hold} if risk_hold is not None else {}),
    }


def _capacity_analysis_evidence(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "capacity_analysis_evidence.v1",
        "evidence_source": {
            "type": "capacity_analysis_report",
            "run_id": "capacity-1",
            "exported_at": "2026-05-16T09:00:00Z",
        },
        "as_of": "2026-05-16T08:00:00Z",
        "decision_timestamp": "2026-05-16T09:30:00Z",
        "checks": {
            "capital_limits_met": True,
            "liquidity_regime_capacity_met": True,
            "impact_deterioration_met": True,
            "symbol_level_capacity_met": True,
            "turnover_slippage_sensitivity_met": True,
            "assumptions_provenance_met": True,
        },
        "limits": {
            "max_capital_usdt": 100000.0,
            "max_position_notional_usdt": 25000.0,
            "max_turnover_ratio": 3.0,
            "max_slippage_bps": 12.0,
            "max_impact_deterioration_bps": 8.0,
        },
        "summary": {
            "claimed_capacity_usdt": 50000.0,
            "capital_required_usdt": 20000.0,
            "estimated_turnover_ratio": 1.4,
            "estimated_slippage_bps": 5.0,
            "impact_deterioration_bps": 4.0,
            "liquidity_regime": "normal",
        },
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "claimed_capacity_usdt": 30000.0,
                "max_capacity_usdt": 50000.0,
                "liquidity_regime": "normal",
                "impact_bps": 3.0,
                "slippage_bps": 4.0,
            }
        ],
        "provenance": {
            "liquidity": {"source": "historical_l2_tick_archive", "artifact_ref": "capacity/liquidity.json"},
            "impact": {"source": "depth_impact_replay", "artifact_ref": "capacity/impact.json"},
            "assumptions": {"source": "capacity_assumptions", "artifact_ref": "capacity/assumptions.json"},
        },
    }
    payload.update(overrides)
    return payload


def _degradation_replay_evidence(
    *,
    websocket_passed: bool = True,
    rest_passed: bool = True,
    as_of: object = "2026-05-16T09:00:00Z",
    websocket_max_lag_ms: object = 850.0,
    rest_recovery_seconds: object = 42.0,
) -> dict[str, object]:
    return {
        "schema_version": "degradation_replay_evidence.v1",
        "mode": "offline_replay",
        "evidence_source": {
            "type": "offline_replay_fixture",
            "run_id": "degradation-replay-1",
            "exported_at": "2026-05-16T09:10:00Z",
        },
        "as_of": as_of,
        "decision_timestamp": "2026-05-16T09:30:00Z",
        "max_age_seconds": 3600,
        "scenarios": [
            {
                "scenario": "websocket_lag",
                "passed": websocket_passed,
                "max_lag_ms": websocket_max_lag_ms,
                "max_allowed_lag_ms": 1000.0,
                "dropped_message_count": 0,
                "replay_event_count": 12,
                "fail_closed_triggered": not websocket_passed,
            },
            {
                "scenario": "rest_rate_limit_degradation",
                "passed": rest_passed,
                "retry_after_seconds": 30.0,
                "recovery_seconds": rest_recovery_seconds,
                "max_allowed_recovery_seconds": 60.0,
                "rate_limit_event_count": 4,
                "fail_closed_triggered": not rest_passed,
            },
        ],
    }


def _tail_risk_report(
    *,
    cvar_loss_pct: object = 0.08,
    stress_loss_pct: object = 0.11,
    liquidation_distance_pct: object = 0.32,
    cluster_loss_pct: object = 0.06,
    risk_hold: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "tail_risk_report.v1",
        "as_of": "2026-01-31T00:00:00Z",
        "decision_timestamp": "2026-01-31T00:30:00Z",
        "max_age_seconds": 3600,
        "limits": {
            "max_cvar_loss_pct": 0.12,
            "max_stress_loss_pct": 0.18,
            "min_liquidation_distance_pct": 0.20,
            "max_correlated_cluster_loss_pct": 0.10,
        },
        "cvar": {"confidence": 0.95, "loss_pct": cvar_loss_pct, "sample_size": 40},
        "worst_n_days": {
            "n": 3,
            "rows": [
                {"date": "2026-01-12", "loss_pct": 0.07},
                {"date": "2026-01-18", "loss_pct": 0.05},
                {"date": "2026-01-27", "loss_pct": 0.03},
            ],
        },
        "worst_n_trades": {
            "n": 3,
            "rows": [
                {"trade_id": "trade-003", "loss_pct": 0.06},
                {"trade_id": "trade-007", "loss_pct": 0.04},
                {"trade_id": "trade-011", "loss_pct": 0.02},
            ],
        },
        "stress_loss": {"scenario_id": "stress-crash-001", "loss_pct": stress_loss_pct},
        "liquidation_proximity": {
            "nearest_symbol": "BTCUSDT",
            "distance_to_liquidation_pct": liquidation_distance_pct,
        },
        "correlated_loss_clusters": [
            {"cluster_id": "majors", "loss_pct": cluster_loss_pct, "members": ["BTCUSDT", "ETHUSDT"]},
            {"cluster_id": "alts", "loss_pct": 0.04, "members": ["SOLUSDT"]},
        ],
        "scenario_provenance": [
            {
                "scenario_id": "stress-crash-001",
                "source": "offline_backtest_fixture",
                "generated_at": "2026-01-30T00:00:00Z",
            },
            {
                "scenario_id": "stress-correlation-001",
                "source": "offline_backtest_fixture",
                "generated_at": "2026-01-30T00:00:00Z",
            },
        ],
        **({"risk_hold": risk_hold} if risk_hold is not None else {}),
    }


def _stress_replay_contract_evidence(
    *,
    cancel_passed: object = True,
    stuck_partial_passed: object = True,
) -> dict[str, object]:
    passed = cancel_passed is True and stuck_partial_passed is True
    return {
        "schema_version": "stress_replay_contract.v1",
        "mode": "offline_simulated",
        "generated_at": "2026-05-16T10:05:00Z",
        "max_evidence_age_seconds": 600.0,
        "evidence_source": {"type": "simulated_offline", "run_id": "stress-replay-fixture-1"},
        "fail_closed": True,
        "decision": "stress_replay_within_contract" if passed else "reject_for_live_promotion",
        "checks": {
            "stress_replay_contract_present": True,
            "stress_replay_contract_schema_valid": True,
            "stress_replay_scenarios_passed": passed,
            "offline_simulated_evidence_only": True,
            "cancel_failure_scenario_present": True,
            "stuck_partial_order_replay_present": True,
            "all_scenarios_passed": passed,
            "fail_closed": True,
        },
        "reasons": [] if passed else ["stress_replay_scenario_failed"],
        "scenarios": [
            {
                "scenario_id": "cancel-failure-001",
                "scenario_type": "cancel_failure",
                "generated_at": "2026-05-16T10:04:00Z",
                "observed_at": "2026-05-16T10:03:00Z",
                "max_evidence_age_seconds": 600.0,
                "attempt_count": 1,
                "failed_cancel_count": 1,
                "stuck_partial_order_count": 0,
                "fail_closed_triggered": cancel_passed is True,
                "replay_completed": True,
                "passed": cancel_passed,
                "evidence_ref": "stress/cancel-failure-001.json",
            },
            {
                "scenario_id": "stuck-partial-001",
                "scenario_type": "stuck_partial_order_replay",
                "generated_at": "2026-05-16T10:04:30Z",
                "observed_at": "2026-05-16T10:03:30Z",
                "max_evidence_age_seconds": 600.0,
                "attempt_count": 1,
                "failed_cancel_count": 0,
                "stuck_partial_order_count": 1,
                "fail_closed_triggered": stuck_partial_passed is True,
                "replay_completed": True,
                "passed": stuck_partial_passed,
                "evidence_ref": "stress/stuck-partial-001.json",
            },
        ],
    }


def _write_full_market_bundle(
    root: Path,
    *,
    baseline_name: str,
    variant_name: str,
    total_return: float,
    max_drawdown: float,
    sharpe: float,
    cost_drag: float,
    pnl_attribution: dict[str, object] | None = None,
    include_pnl_attribution: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    artifacts = ["manifest.json", "summary.json", "breakdowns.json", "audit.json"]
    _write_json(
        root / "manifest.json",
        _manifest(
            experiment_kind="full_market_baseline",
            baseline_name=baseline_name,
            variant_name=variant_name,
            artifacts=artifacts,
        ),
    )
    _write_json(
        root / "summary.json",
        {
            "metadata": {"baseline_name": baseline_name, "variant_name": variant_name},
            "summary": {
                "experiment_name": f"{baseline_name}__{variant_name}",
                "total_return": total_return,
                "max_drawdown": max_drawdown,
                "sharpe": sharpe,
                "sortino": sharpe + 0.2,
                "calmar": sharpe + 0.1,
                "turnover": 0.4,
                "trade_count": 5,
                "cost_drag": cost_drag,
                "cost_breakdown": {"fees": 0.01, "slippage": 0.005, "funding": 0.0},
                **(
                    {"pnl_attribution": pnl_attribution or _pnl_attribution_evidence(reported_pnl=total_return)}
                    if include_pnl_attribution and total_return > 0.0
                    else {}
                ),
            },
        },
    )
    _write_json(
        root / "breakdowns.json",
        {
            "metadata": {"baseline_name": baseline_name, "variant_name": variant_name},
            "breakdowns": {
                "by_market": [{"market_type": "spot", "trade_count": 3, "net_pnl": 0.08}],
                "by_year": [{"year": "2026", "trade_count": 5, "net_pnl": total_return}],
            },
        },
    )
    _write_json(
        root / "audit.json",
        {
            "metadata": {"baseline_name": baseline_name, "variant_name": variant_name},
            "audit": {
                "trade_count": 5,
                "accepted_count": 4,
                "resized_count": 1,
                "rejection_count": 2,
                "rejection_reasons": {"open_risk_limit_reached": 1},
            },
        },
    )
    return root


def _write_walk_forward_bundle(
    root: Path,
    *,
    baseline_name: str,
    variant_name: str,
    out_of_sample_total_return: float,
    positive_window_ratio: float,
    parameter_stability_score: float,
    worst_window_return: float,
    split_metadata: dict[str, object] | None = None,
    window_split_metadata: list[dict[str, object]] | None = None,
    runtime_fields: list[str] | None = None,
    rollback_target: str | None = None,
    rollback_trigger: str | None = None,
    observation_window: str | None = None,
    multiple_testing_correction: dict[str, object] | None = None,
    include_multiple_testing_correction: bool = True,
    regime_stratified_oos: dict[str, object] | None = None,
    pnl_attribution: dict[str, object] | None = None,
    dynamic_sizing_evidence: dict[str, object] | None = None,
    include_dynamic_sizing_evidence: bool = True,
    portfolio_correlation_exposure: dict[str, object] | None = None,
    capacity_analysis_evidence: dict[str, object] | None = None,
    degradation_replay_evidence: dict[str, object] | None = None,
    include_degradation_replay_evidence: bool = True,
    drawdown_anatomy: dict[str, object] | None = None,
    include_drawdown_anatomy: bool = True,
    tail_risk_report: dict[str, object] | None = None,
    stress_replay_contract: dict[str, object] | None = None,
    include_stress_replay_contract: bool = True,
    include_pnl_attribution: bool = True,
    false_discovery_guardrail: dict[str, object] | None = None,
    include_false_discovery_guardrail: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    artifacts = ["manifest.json", "summary.json", "windows.json", "scorecard.json"]
    summary_payload: dict[str, object] = {
        "metadata": {"baseline_name": baseline_name, "variant_name": variant_name, "window_count": 2},
        "robustness_summary": {
            "out_of_sample_scorecard": {
                "total_return": out_of_sample_total_return,
                "max_drawdown": -0.12,
                "sharpe": 0.7,
                "trade_count": 4,
            },
            "performance_dispersion": {"positive_window_ratio": positive_window_ratio},
            "worst_window": {"window_index": 2, "scorecard": {"total_return": worst_window_return}},
        },
        "parameter_stability": {
            "parameter_stability_score": parameter_stability_score,
            "stability_score_threshold": 0.5,
            "selected_optimum": {
                "parameters": {"score_floor": 0.7},
                "metric": "out_of_sample_total_return",
                "value": out_of_sample_total_return,
            },
            "stability_surface": [
                {
                    "parameter_name": "score_floor",
                    "tested_values": [0.6, 0.7, 0.8],
                    "tested_range": {"min": 0.6, "max": 0.8},
                    "neighborhood_metrics": {
                        "mean_neighbor_metric": max(0.0, out_of_sample_total_return - 0.01),
                        "worst_neighbor_metric": max(0.0, worst_window_return),
                        "neighbor_count": 2,
                    },
                }
            ],
            "isolated_spike": {
                "is_isolated": False,
                "rejection_reason": None,
            },
        },
    }
    if multiple_testing_correction is None and include_multiple_testing_correction:
        multiple_testing_correction = _multiple_testing_correction(number_of_trials=2)
    if multiple_testing_correction is not None:
        summary_payload["multiple_testing_correction"] = multiple_testing_correction
    if false_discovery_guardrail is None and include_false_discovery_guardrail and out_of_sample_total_return > 0.0:
        false_discovery_guardrail = _false_discovery_guardrail(number_of_trials=2)
    if false_discovery_guardrail is not None:
        summary_payload["false_discovery_guardrail"] = false_discovery_guardrail
    if regime_stratified_oos is not None:
        summary_payload["regime_stratified_oos"] = regime_stratified_oos
    if include_pnl_attribution and out_of_sample_total_return > 0.0:
        summary_payload["pnl_attribution"] = pnl_attribution or _pnl_attribution_evidence(
            reported_pnl=out_of_sample_total_return
        )
    if include_dynamic_sizing_evidence and out_of_sample_total_return > 0.0:
        summary_payload["dynamic_sizing_evidence"] = dynamic_sizing_evidence or _dynamic_sizing_evidence()
    if portfolio_correlation_exposure is not None:
        summary_payload["portfolio_correlation_exposure"] = portfolio_correlation_exposure
    if capacity_analysis_evidence is not None:
        summary_payload["capacity_analysis_evidence"] = capacity_analysis_evidence
    if include_degradation_replay_evidence and out_of_sample_total_return > 0.0:
        summary_payload["degradation_replay_evidence"] = degradation_replay_evidence or _degradation_replay_evidence()
    if include_drawdown_anatomy and out_of_sample_total_return > 0.0:
        summary_payload["drawdown_anatomy"] = drawdown_anatomy or _drawdown_anatomy_evidence()
    if tail_risk_report is not None:
        summary_payload["tail_risk_report"] = tail_risk_report
    if include_stress_replay_contract and out_of_sample_total_return > 0.0:
        summary_payload["stress_replay_contract"] = stress_replay_contract or _stress_replay_contract_evidence()
    if runtime_fields:
        summary_payload["runtime_observability"] = {"runtime_fields": runtime_fields}
    if rollback_target and (rollback_trigger or observation_window):
        summary_payload["rollback_plan"] = {
            "rollback_target": rollback_target,
            "rollback_trigger": rollback_trigger,
            "observation_window": observation_window,
        }
    manifest = _manifest(
        experiment_kind="walk_forward_validation",
        baseline_name=baseline_name,
        variant_name=variant_name,
        artifacts=artifacts,
    )
    if split_metadata is not None:
        manifest["split_metadata"] = split_metadata
        summary_payload["metadata"]["split_metadata"] = split_metadata  # type: ignore[index]
    _write_json(
        root / "manifest.json",
        manifest,
    )
    _write_json(root / "summary.json", summary_payload)
    window_metadata = window_split_metadata or [
        {"train_run_ids": ["row-001"], "test_run_ids": ["row-003"]},
        {"train_run_ids": ["row-002"], "test_run_ids": ["row-004"]},
    ]
    _write_json(
        root / "windows.json",
        {
            "metadata": {"baseline_name": baseline_name, "variant_name": variant_name},
            "rows": [
                {
                    "window_index": 1,
                    "train_period": {"start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:00:00+00:00"},
                    "test_period": {"start": "2026-01-03T00:00:00+00:00", "end": "2026-01-03T00:00:00+00:00"},
                    "split_metadata": window_metadata[0],
                    "out_of_sample": {"scorecard": {"total_return": out_of_sample_total_return, "trade_count": 2}},
                },
                {
                    "window_index": 2,
                    "train_period": {"start": "2026-01-02T00:00:00+00:00", "end": "2026-01-02T00:00:00+00:00"},
                    "test_period": {"start": "2026-01-04T00:00:00+00:00", "end": "2026-01-04T00:00:00+00:00"},
                    "split_metadata": window_metadata[1],
                    "out_of_sample": {"scorecard": {"total_return": worst_window_return, "trade_count": 2}},
                },
            ],
        },
    )
    _write_json(
        root / "scorecard.json",
        {
            "metadata": {"experiment_name": "walk_forward_validation", "baseline_name": baseline_name, "variant_name": variant_name},
            "key_metrics": {
                "snapshot_count": 4,
                "window_count": 2,
                "out_of_sample_total_return": out_of_sample_total_return,
                "positive_window_ratio": positive_window_ratio,
                "parameter_stability_score": parameter_stability_score,
            },
            "decision_summary": {"decision": "keep_researching", "summary": "fixture"},
            **({"multiple_testing_correction": multiple_testing_correction} if multiple_testing_correction is not None else {}),
            **({"false_discovery_guardrail": false_discovery_guardrail} if false_discovery_guardrail is not None else {}),
            **({"regime_stratified_oos": regime_stratified_oos} if regime_stratified_oos is not None else {}),
            **(
                {"portfolio_correlation_exposure": portfolio_correlation_exposure}
                if portfolio_correlation_exposure is not None
                else {}
            ),
            **(
                {"capacity_analysis_evidence": capacity_analysis_evidence}
                if capacity_analysis_evidence is not None
                else {}
            ),
            **(
                {"degradation_replay_evidence": degradation_replay_evidence or _degradation_replay_evidence()}
                if include_degradation_replay_evidence and out_of_sample_total_return > 0.0
                else {}
            ),
            **(
                {"drawdown_anatomy": drawdown_anatomy or _drawdown_anatomy_evidence()}
                if include_drawdown_anatomy and out_of_sample_total_return > 0.0
                else {}
            ),
            **(
                {
                    "pnl_attribution": pnl_attribution
                    or _pnl_attribution_evidence(reported_pnl=out_of_sample_total_return)
                }
                if include_pnl_attribution and out_of_sample_total_return > 0.0
                else {}
            ),
            **(
                {
                    "dynamic_sizing_evidence": dynamic_sizing_evidence
                    or _dynamic_sizing_evidence()
                }
                if include_dynamic_sizing_evidence and out_of_sample_total_return > 0.0
                else {}
            ),
            **({"tail_risk_report": tail_risk_report} if tail_risk_report is not None else {}),
            **(
                {"stress_replay_contract": stress_replay_contract or _stress_replay_contract_evidence()}
                if include_stress_replay_contract and out_of_sample_total_return > 0.0
                else {}
            ),
        },
    )
    return root



def test_load_backtest_bundle_rejects_noncanonical_runtime_observability_fields(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        runtime_fields=[" regime"],
    )

    with pytest.raises(ValueError, match="runtime_fields must be canonical strings"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_rollback_plan_fields(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        rollback_target=" baseline_policy ",
        rollback_trigger="drawdown breach",
        observation_window="24h",
    )

    with pytest.raises(ValueError, match="rollback_plan.rollback_target must be canonical"):
        promotion.load_backtest_bundle(bundle)



@pytest.mark.parametrize(
    ("missing_field", "match"),
    [
        ("stability_surface", "summary.json.parameter_stability.stability_surface must be a non-empty list"),
        ("selected_optimum", "summary.json.parameter_stability.selected_optimum must be an object"),
        (
            "stability_score_threshold",
            "summary.json.parameter_stability.stability_score_threshold must be a bounded ratio strict number",
        ),
        ("isolated_spike", "summary.json.parameter_stability.isolated_spike must be an object"),
    ],
)
def test_load_backtest_bundle_requires_canonical_parameter_stability_surface_metadata(
    tmp_path: Path,
    missing_field: str,
    match: str,
) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
    )
    summary_path = bundle / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    del summary["parameter_stability"][missing_field]
    _write_json(summary_path, summary)

    with pytest.raises(ValueError, match=re.escape(match)):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (
            ("stability_surface", 0, "parameter_name"),
            "",
            "summary.json.parameter_stability.stability_surface[0].parameter_name must be a canonical string",
        ),
        (
            ("stability_surface", 0, "tested_values", 0),
            True,
            "summary.json.parameter_stability.stability_surface[0].tested_values[0] must be a finite strict number",
        ),
        (
            ("stability_surface", 0, "tested_range", "max"),
            0.5,
            "summary.json.parameter_stability.stability_surface[0].tested_range.max must be >= min",
        ),
        (
            ("stability_surface", 0, "neighborhood_metrics", "mean_neighbor_metric"),
            "0.02",
            "summary.json.parameter_stability.stability_surface[0].neighborhood_metrics.mean_neighbor_metric must be a finite strict number",
        ),
        (
            ("selected_optimum", "parameters", "score_floor"),
            float("inf"),
            "summary.json.parameter_stability.selected_optimum.parameters.score_floor must be a finite strict number",
        ),
        (
            ("isolated_spike", "is_isolated"),
            0,
            "summary.json.parameter_stability.isolated_spike.is_isolated must be a bool",
        ),
    ],
)
def test_load_backtest_bundle_rejects_nonfinite_coercive_or_ambiguous_stability_surface_data(
    tmp_path: Path,
    path: tuple[object, ...],
    value: object,
    match: str,
) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
    )
    summary_path = bundle / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    cursor: object = summary["parameter_stability"]
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]
    _write_json(summary_path, summary)

    with pytest.raises(ValueError, match=re.escape(match)):
        promotion.load_backtest_bundle(bundle)
def test_allocator_report_rejects_best_of_many_without_multiple_testing_correction() -> None:
    experiment = {
        "variants": {
            "current_allocator": {
                "frictions": {
                    "base": {"net_bucket_pnl": 1.0, "cost_drag": 0.1},
                    "stressed": {"net_bucket_pnl": 0.5},
                }
            },
            "risk_scaled": {
                "frictions": {
                    "base": {"net_bucket_pnl": 5.0, "cost_drag": 0.2},
                    "stressed": {"net_bucket_pnl": 2.0},
                }
            },
        },
        "comparison_rows": [],
    }

    with pytest.raises(ValueError, match="multiple_testing_correction must be present"):
        render_allocator_friction_report(
            experiment_name="allocator_friction",
            experiment=experiment,
            metadata={"snapshot_count": 4},
        )


def test_allocator_report_holds_best_of_many_when_adjusted_correction_fails() -> None:
    report = render_allocator_friction_report(
        experiment_name="allocator_friction",
        experiment={
            "variants": {
                "current_allocator": {
                    "frictions": {
                        "base": {"net_bucket_pnl": 1.0, "cost_drag": 0.1},
                        "stressed": {"net_bucket_pnl": 0.5},
                    }
                },
                "risk_scaled": {
                    "frictions": {
                        "base": {"net_bucket_pnl": 5.0, "cost_drag": 0.2},
                        "stressed": {"net_bucket_pnl": 2.0},
                    }
                },
            },
            "comparison_rows": [],
            "multiple_testing_correction": _multiple_testing_correction(number_of_trials=2, adjusted_pass=False),
        },
        metadata={"snapshot_count": 4},
    )

    assert report["scorecard"]["decision_summary"]["decision"] == "keep_researching"
    assert report["scorecard"]["multiple_testing_correction"]["adjusted_pass"] is False



def test_load_backtest_bundle_rejects_noncanonical_manifest_identity_fields(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name=" current_system ",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )

    with pytest.raises(ValueError, match="manifest.json.baseline_name must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_relative_manifest_dataset_root(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["dataset_root"] = "../dataset"
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match="manifest.json.dataset_root must be an absolute path"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_missing_manifest_universe_asof_contract(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.2,
        max_drawdown=-0.08,
        sharpe=1.1,
        cost_drag=0.01,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["universe_asof_contract"]
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="missing required keys: universe_asof_contract"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_current_universe_as_historical_contract(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.2,
        max_drawdown=-0.08,
        sharpe=1.1,
        cost_drag=0.01,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["universe_asof_contract"] = {
        "schema_version": "universe_asof_contract.v1",
        "membership_source": "current_universe_snapshot",
        "as_of_field": "instrument_snapshot.as_of",
        "decision_timestamp_field": "metadata.timestamp",
        "required_lifecycle_fields": ["lifecycle_status"],
        "supports_delisted": True,
        "supports_renames": True,
        "supports_contract_migrations": True,
    }
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.universe_asof_contract.membership_source must not be current_universe_snapshot"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_missing_manifest_margin_liquidation_path_contract(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.2,
        max_drawdown=-0.08,
        sharpe=1.1,
        cost_drag=0.01,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["margin_liquidation_path_contract"]
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="missing required keys: margin_liquidation_path_contract"):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("schema_version",), "margin_liquidation_path_contract.v0", "schema_version must be margin_liquidation_path_contract.v1"),
        (("scope",), "current_positions", "scope must be futures_trade_ledger"),
        (("margin_mode_field",), " trades[].margin_mode ", "margin_mode_field must be trades\\[\\]\\.margin_mode"),
        (("accepted_margin_modes",), ["isolated"], "accepted_margin_modes must be isolated and cross"),
        (("accepted_margin_modes",), "isolated,cross", "accepted_margin_modes must be a list"),
        (("fail_closed",), "true", "fail_closed must be true"),
    ],
)
def test_load_backtest_bundle_rejects_noncanonical_margin_liquidation_path_contract(
    tmp_path: Path,
    path: tuple[object, ...],
    value: object,
    match: str,
) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.2,
        max_drawdown=-0.08,
        sharpe=1.1,
        cost_drag=0.01,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cursor: object = manifest["margin_liquidation_path_contract"]
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match=match):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_inconsistent_manifest_bundle_name(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["bundle_name"] = "full_market_baseline__current_system__tampered_policy"
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match="manifest.json.bundle_name must match experiment identity"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_sample_period_bounds(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["sample_period"]["start"] = " 2026-01-01T00:00:00+00:00"
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match="manifest.json.sample_period.start must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_unsafe_manifest_artifacts(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"].append("../shadow.json")
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match=r"manifest.json.artifacts\[4\] must be a safe relative path"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_manifest_artifacts(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"].append(" summary.json ")
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match=r"manifest.json.artifacts\[4\] must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_duplicate_manifest_artifacts(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"].append("summary.json")
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match=r"manifest.json.artifacts\[4\] duplicates summary.json"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_window_count_keys(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["window_counts"] = {" full_history ": 4}
    _write_json(bundle / "manifest.json", manifest)

    with pytest.raises(ValueError, match="manifest.json.window_counts. full_history  key must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_invalid_full_market_breakdown_identity(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    breakdowns = json.loads((bundle / "breakdowns.json").read_text(encoding="utf-8"))
    breakdowns["breakdowns"]["by_market"][0]["market_type"] = " spot "
    _write_json(bundle / "breakdowns.json", breakdowns)

    with pytest.raises(ValueError, match=r"breakdowns.json.breakdowns.by_market\[0\].market_type must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_full_market_breakdown_row_keys(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    breakdowns = json.loads((bundle / "breakdowns.json").read_text(encoding="utf-8"))
    breakdowns["breakdowns"]["by_market"][0][" net_pnl "] = 0.08
    _write_json(bundle / "breakdowns.json", breakdowns)

    with pytest.raises(ValueError, match=r"breakdowns.json.breakdowns.by_market\[0\] key must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_audit_rejection_reasons(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.0,
        cost_drag=0.02,
    )
    audit = json.loads((bundle / "audit.json").read_text(encoding="utf-8"))
    audit["audit"]["rejection_reasons"] = {" open_risk_limit_reached ": 1}
    _write_json(bundle / "audit.json", audit)

    with pytest.raises(ValueError, match="audit.json.audit.rejection_reasons key must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_holds_when_out_of_sample_evidence_is_missing(tmp_path: Path) -> None:
    baseline_bundle = _write_full_market_bundle(
        tmp_path / "baseline",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    variant_bundle = _write_full_market_bundle(
        tmp_path / "variant",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.16,
        max_drawdown=-0.08,
        sharpe=1.25,
        cost_drag=0.015,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "hold"
    assert gate["checks"] == {
        "has_baseline_variant_pair": True,
        "has_cost_adjusted_edge": True,
        "has_out_of_sample_evidence": False,
        "has_purged_embargoed_split_metadata": True,
        "has_attribution_or_funnel_explanation": True,
        "has_pnl_attribution_evidence": True,
        "has_dynamic_sizing_evidence": True,
        "has_stress_replay_contract": True,
        "passes_stress_replay_contract": True,
        "has_runtime_observability_plan": False,
        "has_rollback_plan": False,
    }
    assert gate["metric_deltas"]["total_return"] == 0.06
    assert gate["metric_deltas"]["max_drawdown"] == 0.02
    assert gate["metric_deltas"]["sharpe"] == 0.25
    assert gate["metric_deltas"]["cost_drag"] == -0.005
    assert "missing out-of-sample evidence" in gate["why"]


def test_compare_backtest_bundles_rejects_walk_forward_when_oos_direction_reverses(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.8,
        worst_window_return=-0.01,
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=-0.02,
        positive_window_ratio=0.25,
        parameter_stability_score=0.7,
        worst_window_return=-0.04,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_baseline_variant_pair"] is True
    assert gate["checks"]["has_out_of_sample_evidence"] is True
    assert gate["checks"]["has_attribution_or_funnel_explanation"] is True
    assert "out-of-sample direction reverses or clearly collapses" in gate["why"]


def test_load_backtest_bundle_rejects_positive_pnl_without_attribution(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "positive",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.08,
        sharpe=1.20,
        cost_drag=0.015,
        include_pnl_attribution=False,
    )

    with pytest.raises(ValueError, match="summary.json.summary.pnl_attribution must be present for positive PnL claims"):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda payload: payload["buckets"].pop(),
            "pnl_attribution.buckets must include required bucket symbol_selection",
        ),
        (
            lambda payload: payload["buckets"].append({"bucket": "entry_alpha", "contribution": 0.0}),
            "pnl_attribution.buckets bucket values must be unique",
        ),
        (
            lambda payload: payload["buckets"][0].update({"bucket": " entry_alpha "}),
            r"pnl_attribution.buckets\[0\].bucket must be canonical",
        ),
        (
            lambda payload: payload["buckets"][0].update({"contribution": "0.02"}),
            r"pnl_attribution.buckets\[0\].contribution must be a finite strict number",
        ),
        (
            lambda payload: payload["buckets"][0].update({"contribution": True}),
            r"pnl_attribution.buckets\[0\].contribution must be a finite strict number",
        ),
        (
            lambda payload: payload["buckets"][0].update({"contribution": float("nan")}),
            r"pnl_attribution.buckets\[0\].contribution must be a finite strict number",
        ),
        (
            lambda payload: payload["buckets"][0].update({"contribution": 0.50}),
            "pnl_attribution.total_contribution must materially match reported_pnl",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_pnl_attribution(
    tmp_path: Path,
    mutate,
    expected: str,
) -> None:
    attribution = _pnl_attribution_evidence()
    mutate(attribution)
    bundle = _write_full_market_bundle(
        tmp_path / "positive",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.08,
        max_drawdown=-0.08,
        sharpe=1.20,
        cost_drag=0.015,
        pnl_attribution=attribution,
    )

    with pytest.raises(ValueError, match=expected):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_missing_cost_attribution_when_costs_are_nonzero(tmp_path: Path) -> None:
    attribution = _pnl_attribution_evidence()
    attribution["buckets"] = [
        bucket for bucket in attribution["buckets"] if bucket["bucket"] != "funding"  # type: ignore[index]
    ]
    bundle = _write_full_market_bundle(
        tmp_path / "positive",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.08,
        max_drawdown=-0.08,
        sharpe=1.20,
        cost_drag=0.015,
        pnl_attribution=attribution,
    )
    summary = json.loads((bundle / "summary.json").read_text(encoding="utf-8"))
    summary["summary"]["cost_breakdown"]["funding"] = 0.001
    _write_json(bundle / "summary.json", summary)

    with pytest.raises(ValueError, match="pnl_attribution.buckets must include required bucket funding"):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_promotes_walk_forward_when_all_checks_pass(tmp_path: Path) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(
            net_exposure_pct=0.16,
            gross_exposure_pct=0.39,
        ),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "candidate_for_promotion"
    assert gate["checks"] == {
        "has_baseline_variant_pair": True,
        "has_cost_adjusted_edge": True,
        "has_out_of_sample_evidence": True,
        "has_purged_embargoed_split_metadata": True,
        "has_attribution_or_funnel_explanation": True,
        "has_pnl_attribution_evidence": True,
        "has_dynamic_sizing_evidence": True,
        "has_stress_replay_contract": True,
        "passes_stress_replay_contract": True,
        "has_runtime_observability_plan": True,
        "has_rollback_plan": True,
        "has_parameter_stability_surface": True,
        "rejects_isolated_spike_optimum": True,
        "has_regime_stratified_oos_evidence": True,
        "rejects_regime_bucket_collapse": True,
        "has_portfolio_correlation_exposure_evidence": True,
        "rejects_portfolio_correlation_exposure_breach": True,
        "has_capacity_analysis_evidence": True,
        "rejects_capacity_limit_breach": True,
        "has_degradation_replay_evidence": True,
        "rejects_degradation_replay_failure": True,
        "has_drawdown_anatomy_evidence": True,
        "has_tail_risk_report": True,
        "rejects_tail_risk_limit_breach": True,
        "has_false_discovery_guardrail": True,
        "passes_false_discovery_guardrail": True,
    }
    assert gate["why"] == []
    assert gate["regime_stratified_oos"]["buckets"][0]["bucket"] == "volatility"
    assert gate["pnl_attribution"]["buckets"][0]["bucket"] == "entry_alpha"
    assert gate["dynamic_sizing_evidence"]["decisions"][0]["decision_id"] == "sizing-001"
    assert result["decision_summary"]["dynamic_sizing_evidence"]["decisions"][0]["decision_id"] == "sizing-001"
    assert gate["portfolio_correlation_exposure"]["portfolio"]["gross_exposure_pct"] == 0.42
    assert gate["capacity_analysis_evidence"]["summary"]["claimed_capacity_usdt"] == 50000.0
    assert gate["degradation_replay_evidence"]["scenarios"][0]["scenario"] == "websocket_lag"
    assert gate["drawdown_anatomy"]["drawdowns"][0]["regime_cluster_id"] == "regime-crash"
    assert result["decision_summary"]["drawdown_anatomy"]["drawdowns"][0]["trade_cluster_id"] == "trade-cluster-001"
    assert gate["tail_risk_report"]["cvar"]["loss_pct"] == 0.08
    assert result["decision_summary"]["tail_risk_report"]["scenario_provenance"][0]["scenario_id"] == "stress-crash-001"


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_degradation_replay_evidence(
    tmp_path: Path,
) -> None:
    split_metadata = _walk_forward_split_metadata()
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="paper",
        variant_name="baseline",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
        include_degradation_replay_evidence=False,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_degradation_replay_evidence"] is False
    assert "missing websocket/rest degradation replay evidence" in gate["why"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda evidence: evidence.__setitem__("mode", "testnet_probe"),
            "degradation_replay_evidence.mode must be offline_replay",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-05-16T08:00:00Z"),
            "degradation_replay_evidence.as_of must not be stale",
        ),
        (
            lambda evidence: evidence["scenarios"].__delitem__(0),  # type: ignore[index,union-attr]
            "degradation_replay_evidence.scenarios must include websocket_lag",
        ),
        (
            lambda evidence: evidence["scenarios"][0].__setitem__("max_lag_ms", float("nan")),  # type: ignore[index,union-attr]
            "degradation_replay_evidence.scenarios[0].max_lag_ms must be a finite strict number",
        ),
        (
            lambda evidence: evidence["scenarios"][1].__setitem__("passed", "true"),  # type: ignore[index,union-attr]
            "degradation_replay_evidence.scenarios[1].passed must be a bool",
        ),
        (
            lambda evidence: evidence["scenarios"][1].__setitem__("passed", False),  # type: ignore[index,union-attr]
            "degradation_replay_evidence.scenarios[1] did not pass",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_or_failing_degradation_replay_evidence(
    tmp_path: Path,
    mutate: object,
    match: str,
) -> None:
    evidence = _degradation_replay_evidence()
    mutate(evidence)  # type: ignore[operator]
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=_walk_forward_split_metadata(),
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=evidence,
        tail_risk_report=_tail_risk_report(),
    )

    with pytest.raises(ValueError, match=re.escape(match)):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_false_discovery_guardrail(
    tmp_path: Path,
) -> None:
    split_metadata = _walk_forward_split_metadata()
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="paper",
        variant_name="baseline",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.7,
        parameter_stability_score=0.8,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(),
        include_false_discovery_guardrail=False,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_false_discovery_guardrail"] is False
    assert gate["checks"]["passes_false_discovery_guardrail"] is False
    assert "missing false-discovery/deflated-Sharpe guardrail" in gate["why"]


@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    (
        (
            lambda guardrail: guardrail.pop("effective_trials"),
            "invalid false-discovery/deflated-Sharpe guardrail: effective_trials must be present",
        ),
        (
            lambda guardrail: guardrail.update({"effective_trials": "2"}),
            "invalid false-discovery/deflated-Sharpe guardrail: effective_trials must be finite",
        ),
        (
            lambda guardrail: guardrail.update({"non_normality_adjustment": True}),
            "invalid false-discovery/deflated-Sharpe guardrail: non_normality_adjustment must be finite",
        ),
        (
            lambda guardrail: guardrail.update({"deflated_sharpe": float("nan")}),
            "invalid false-discovery/deflated-Sharpe guardrail: deflated_sharpe must be finite",
        ),
        (
            lambda guardrail: guardrail.update({"number_of_trials": True}),
            "invalid false-discovery/deflated-Sharpe guardrail: number_of_trials must be an integer",
        ),
        (
            lambda guardrail: guardrail.update({"number_of_trials": 1}),
            "invalid false-discovery/deflated-Sharpe guardrail: number_of_trials must be greater than one",
        ),
        (
            lambda guardrail: guardrail.update({"effective_trials": 0.5}),
            "invalid false-discovery/deflated-Sharpe guardrail: effective_trials must be >= 1",
        ),
        (
            lambda guardrail: guardrail.update({"effective_trials": 3.0}),
            "invalid false-discovery/deflated-Sharpe guardrail: effective_trials must be <= number_of_trials",
        ),
        (
            lambda guardrail: guardrail.update({"adjusted_pass": False}),
            "false-discovery/deflated-Sharpe guardrail did not pass",
        ),
        (
            lambda guardrail: guardrail.update({"deflated_sharpe": 0.1}),
            "invalid false-discovery/deflated-Sharpe guardrail: deflated_sharpe below minimum",
        ),
    ),
)
def test_compare_backtest_bundles_rejects_malformed_or_failing_false_discovery_guardrail(
    tmp_path: Path,
    mutator: object,
    expected_reason: str,
) -> None:
    guardrail = _false_discovery_guardrail()
    mutator(guardrail)  # type: ignore[operator]
    split_metadata = _walk_forward_split_metadata()
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="paper",
        variant_name="baseline",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.7,
        parameter_stability_score=0.8,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
        false_discovery_guardrail=guardrail,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_false_discovery_guardrail"] is True
    assert gate["checks"]["passes_false_discovery_guardrail"] is False
    assert expected_reason in gate["why"]


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_drawdown_anatomy(
    tmp_path: Path,
) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="paper",
        variant_name="baseline",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.7,
        parameter_stability_score=0.8,
        worst_window_return=0.01,
        split_metadata=_walk_forward_split_metadata(),
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.8,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=_walk_forward_split_metadata(),
        runtime_fields=["optimization_summary"],
        rollback_target="baseline",
        rollback_trigger="regression",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        include_drawdown_anatomy=False,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_drawdown_anatomy_evidence"] is False
    assert "missing drawdown anatomy evidence" in gate["why"]


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (
            lambda evidence: evidence["drawdowns"][0].__setitem__("severity_pct", "0.08"),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0].severity_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence["drawdowns"][0].__setitem__("severity_pct", True),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0].severity_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence["drawdowns"][0].__setitem__("severity_pct", float("inf")),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0].severity_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-31T00:30:00+00:00"),
            "drawdown_anatomy.as_of must be a canonical UTC Z timestamp",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-31T01:31:00Z"),
            "drawdown_anatomy.as_of must be at or before decision_timestamp",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-30T23:00:00Z"),
            "drawdown_anatomy.as_of must not be stale",
        ),
        (
            lambda evidence: evidence["drawdowns"][0].__setitem__("trough_timestamp", "2026-01-30T23:59:00Z"),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0].peak_timestamp must be at or before trough_timestamp",
        ),
        (
            lambda evidence: evidence["drawdowns"].append(dict(evidence["drawdowns"][0])),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns cluster ids must be unique",
        ),
        (
            lambda evidence: evidence["drawdowns"][0].__setitem__("mitigation_evidence", []),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0] severe drawdown must include mitigation evidence",
        ),
        (
            lambda evidence: evidence["drawdowns"][0]["attribution"].__setitem__("primary_failure", "unknown"),  # type: ignore[index,union-attr]
            "drawdown_anatomy.drawdowns[0].attribution.primary_failure must explain severe drawdown",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_drawdown_anatomy(
    tmp_path: Path,
    mutator,
    expected_message: str,
) -> None:
    evidence = _drawdown_anatomy_evidence(severity_pct=0.12)
    mutator(evidence)
    bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="paper",
        variant_name="candidate",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.8,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=_walk_forward_split_metadata(),
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        drawdown_anatomy=evidence,
    )

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_portfolio_exposure_evidence(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(
            net_exposure_pct=0.16,
            gross_exposure_pct=0.39,
        ),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_portfolio_correlation_exposure_evidence"] is False
    assert "missing portfolio correlation/exposure evidence" in gate["why"]


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_capacity_evidence(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_capacity_analysis_evidence"] is False
    assert "missing capacity analysis evidence" in gate["why"]


def test_compare_backtest_bundles_preserves_capacity_evidence_when_all_checks_pass(tmp_path: Path) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "candidate_for_promotion"
    assert gate["checks"]["has_capacity_analysis_evidence"] is True
    assert gate["checks"]["rejects_capacity_limit_breach"] is True
    assert gate["capacity_analysis_evidence"]["summary"]["claimed_capacity_usdt"] == 50000.0
    assert result["decision_summary"]["capacity_analysis_evidence"]["provenance"]["impact"]["source"] == "depth_impact_replay"


def test_load_backtest_bundle_rejects_malformed_capacity_evidence(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "candidate",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 1,
            "embargo_bars": 0,
        },
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(
            summary={
                "claimed_capacity_usdt": "50000",
                "capital_required_usdt": 20000.0,
                "estimated_turnover_ratio": 1.4,
                "estimated_slippage_bps": 5.0,
                "impact_deterioration_bps": 4.0,
                "liquidity_regime": "normal",
            }
        ),
        tail_risk_report=_tail_risk_report(),
    )

    with pytest.raises(
        ValueError,
        match="capacity_analysis_evidence.summary.claimed_capacity_usdt must be a finite strict number",
    ):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_capacity_limit_breach_without_hold(tmp_path: Path) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(
            summary={
                "claimed_capacity_usdt": 150000.0,
                "capital_required_usdt": 20000.0,
                "estimated_turnover_ratio": 1.4,
                "estimated_slippage_bps": 5.0,
                "impact_deterioration_bps": 4.0,
                "liquidity_regime": "normal",
            }
        ),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["rejects_capacity_limit_breach"] is False
    assert "claimed capacity exceeds max capital" in gate["why"]


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_tail_risk_report(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
        degradation_replay_evidence=_degradation_replay_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_tail_risk_report"] is False
    assert "missing tail-risk report evidence" in gate["why"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda evidence: evidence["portfolio"].__setitem__("net_exposure_pct", "0.18"),
            "portfolio_correlation_exposure.portfolio.net_exposure_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence["portfolio"].__setitem__("gross_exposure_pct", float("inf")),
            "portfolio_correlation_exposure.portfolio.gross_exposure_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-31T00:30:01Z"),
            "portfolio_correlation_exposure.as_of must be at or before decision_timestamp",
        ),
        (
            lambda evidence: evidence["symbols"].append(dict(evidence["symbols"][0])),
            "portfolio_correlation_exposure.symbols symbol values must be unique",
        ),
        (
            lambda evidence: evidence["clusters"].append(dict(evidence["clusters"][0])),
            "portfolio_correlation_exposure.clusters cluster values must be unique",
        ),
    ],
)
def test_load_backtest_bundle_rejects_invalid_portfolio_correlation_exposure_contract(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    evidence = _portfolio_correlation_exposure_evidence()
    mutate(evidence)
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 1,
            "embargo_bars": 0,
        },
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=evidence,
        tail_risk_report=_tail_risk_report(),
    )

    with pytest.raises(ValueError, match=re.escape(match)):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda evidence: evidence.__setitem__("schema_version", "tail_risk_report.v0"),
            "tail_risk_report.schema_version must be tail_risk_report.v1",
        ),
        (
            lambda evidence: evidence["cvar"].__setitem__("loss_pct", "0.08"),  # type: ignore[index,union-attr]
            "tail_risk_report.cvar.loss_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence["stress_loss"].__setitem__("loss_pct", True),  # type: ignore[index,union-attr]
            "tail_risk_report.stress_loss.loss_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence["liquidation_proximity"].__setitem__("distance_to_liquidation_pct", float("nan")),  # type: ignore[index,union-attr]
            "tail_risk_report.liquidation_proximity.distance_to_liquidation_pct must be a finite strict number",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-31T00:30:01Z"),
            "tail_risk_report.as_of must be at or before decision_timestamp",
        ),
        (
            lambda evidence: evidence.__setitem__("as_of", "2026-01-30T00:00:00Z"),
            "tail_risk_report.as_of must not be stale",
        ),
        (
            lambda evidence: evidence["worst_n_days"]["rows"].reverse(),  # type: ignore[index,union-attr]
            "tail_risk_report.worst_n_days.rows must be sorted by descending loss_pct",
        ),
        (
            lambda evidence: evidence["worst_n_trades"].__setitem__("n", 2),  # type: ignore[index,union-attr]
            "tail_risk_report.worst_n_trades.n must match rows length",
        ),
        (
            lambda evidence: evidence["correlated_loss_clusters"].append(dict(evidence["correlated_loss_clusters"][0])),  # type: ignore[index,union-attr]
            "tail_risk_report.correlated_loss_clusters cluster_id values must be unique",
        ),
        (
            lambda evidence: evidence["scenario_provenance"].append(dict(evidence["scenario_provenance"][0])),  # type: ignore[index,union-attr]
            "tail_risk_report.scenario_provenance scenario_id values must be unique",
        ),
    ],
)
def test_load_backtest_bundle_rejects_invalid_tail_risk_report_contract(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    tail_risk_report = _tail_risk_report()
    mutate(tail_risk_report)
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 1,
            "embargo_bars": 0,
        },
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=tail_risk_report,
    )

    with pytest.raises(ValueError, match=re.escape(match)):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_tail_risk_limit_breach_without_risk_hold(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(cvar_loss_pct=0.16),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["rejects_tail_risk_limit_breach"] is False
    assert "CVaR loss exceeds configured limit" in gate["why"]


def test_compare_backtest_bundles_allows_tail_risk_limit_breach_with_explicit_risk_hold(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(
            cvar_loss_pct=0.16,
            risk_hold={"active": True, "reason": "tail_risk_review_hold"},
        ),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "candidate_for_promotion"
    assert gate["checks"]["rejects_tail_risk_limit_breach"] is True
    assert gate["tail_risk_report"]["risk_hold"] == {"active": True, "reason": "tail_risk_review_hold"}


def test_compare_backtest_bundles_rejects_portfolio_exposure_limit_breach_without_risk_hold(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(gross_exposure_pct=1.4),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["rejects_portfolio_correlation_exposure_breach"] is False
    assert "portfolio gross exposure exceeds configured limit" in gate["why"]


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_dynamic_sizing_evidence(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        include_dynamic_sizing_evidence=False,
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_dynamic_sizing_evidence"] is False
    assert "missing dynamic sizing evidence" in gate["why"]


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_stress_replay_contract(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
        include_stress_replay_contract=False,
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_stress_replay_contract"] is False
    assert "missing stress replay contract evidence" in gate["why"]


def test_compare_backtest_bundles_rejects_failing_stress_replay_contract(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.03),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        pnl_attribution=_pnl_attribution_evidence(reported_pnl=0.08),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        tail_risk_report=_tail_risk_report(),
        stress_replay_contract=_stress_replay_contract_evidence(cancel_passed=False),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["passes_stress_replay_contract"] is False
    assert "stress replay contract scenario failed" in gate["why"]


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (
            lambda evidence: evidence.__setitem__("schema_version", "stress_replay_contract.v0"),
            "stress_replay_contract.schema_version must be stress_replay_contract.v1",
        ),
        (
            lambda evidence: evidence.__setitem__("mode", "testnet_exchange"),
            "stress_replay_contract.mode must be offline_simulated",
        ),
        (
            lambda evidence: evidence.__setitem__("max_evidence_age_seconds", float("nan")),
            "stress_replay_contract.max_evidence_age_seconds must be a finite strict number",
        ),
        (
            lambda evidence: evidence["scenarios"][0].__setitem__("observed_at", "2026-05-16T09:00:00Z"),  # type: ignore[index,union-attr]
            "stress_replay_contract.scenarios[0] evidence must not be stale",
        ),
        (
            lambda evidence: evidence["scenarios"][0].__setitem__("scenario_type", "cancel_failure "),  # type: ignore[index,union-attr]
            "stress_replay_contract.scenarios[0].scenario_type must be canonical",
        ),
        (
            lambda evidence: evidence["scenarios"][1].__setitem__("stuck_partial_order_count", 0),  # type: ignore[index,union-attr]
            "stress_replay_contract must include stuck partial-order replay evidence",
        ),
        (
            lambda evidence: evidence.__setitem__("scenarios", [evidence["scenarios"][1]]),
            "stress_replay_contract must include cancel failure evidence",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_stress_replay_contract(
    tmp_path: Path,
    mutator,
    expected_message: str,
) -> None:
    evidence = _stress_replay_contract_evidence()
    mutator(evidence)
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        stress_replay_contract=evidence,
    )

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (
            lambda evidence: evidence.__setitem__("schema_version", "dynamic_sizing_evidence.v0"),
            "dynamic_sizing_evidence.schema_version must be dynamic_sizing_evidence.v1",
        ),
        (
            lambda evidence: evidence["decisions"][0].__setitem__("decision_id", " sizing-001 "),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0].decision_id must be canonical",
        ),
        (
            lambda evidence: evidence["decisions"][0].__setitem__("baseline_risk_fraction", "0.02"),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0].baseline_risk_fraction must be a finite strict number",
        ),
        (
            lambda evidence: evidence["decisions"][0]["axes"]["liquidity"].__setitem__("degraded", "true"),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0].axes.liquidity.degraded must be a bool",
        ),
        (
            lambda evidence: evidence["decisions"][0]["axes"]["volatility"].__setitem__("risk_multiplier", float("inf")),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0].axes.volatility.risk_multiplier must be a finite strict number",
        ),
        (
            lambda evidence: evidence["decisions"][0].__setitem__("evidence_as_of", "2026-01-04T00:01:00+00:00"),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0].evidence_as_of must not be after sizing_decision_at",
        ),
        (
            lambda evidence: evidence["decisions"][0].__setitem__("final_risk_fraction", 0.03),  # type: ignore[index,union-attr]
            "dynamic_sizing_evidence.decisions[0] must not increase risk during degraded conditions without override evidence",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_dynamic_sizing_evidence(
    tmp_path: Path,
    mutator,
    expected_message: str,
) -> None:
    evidence = _dynamic_sizing_evidence()
    mutator(evidence)
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        dynamic_sizing_evidence=evidence,
    )

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_allows_dynamic_sizing_risk_increase_with_override_evidence(
    tmp_path: Path,
) -> None:
    evidence = _dynamic_sizing_evidence(final_risk_fraction=0.03)
    evidence["decisions"][0]["override_evidence"] = {  # type: ignore[index]
        "override_id": "override-001",
        "approved_by": "offline_audit",
        "reason": "contract replay scenario",
    }
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        dynamic_sizing_evidence=evidence,
    )

    loaded = promotion.load_backtest_bundle(bundle)

    assert loaded.artifacts["summary.json"]["dynamic_sizing_evidence"]["decisions"][0]["override_evidence"] == {
        "override_id": "override-001",
        "approved_by": "offline_audit",
        "reason": "contract replay scenario",
    }


def test_compare_backtest_bundles_rejects_positive_walk_forward_without_regime_stratified_oos(
    tmp_path: Path,
) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_regime_stratified_oos_evidence"] is False
    assert "missing regime-stratified OOS evidence" in gate["why"]


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (
            lambda evidence: evidence.update({"schema_version": "regime_stratified_oos.v0"}),
            "regime_stratified_oos.schema_version must be regime_stratified_oos.v1",
        ),
        (
            lambda evidence: evidence.__setitem__("required_buckets", ["volatility", "liquidity", "funding", "crash"]),
            "regime_stratified_oos.required_buckets must include volatility, liquidity, funding, crash, squeeze",
        ),
        (
            lambda evidence: evidence["buckets"].pop(),  # type: ignore[index,union-attr]
            "regime_stratified_oos.buckets must include required bucket squeeze",
        ),
        (
            lambda evidence: evidence["buckets"].append(evidence["buckets"][0]),  # type: ignore[index,union-attr]
            "regime_stratified_oos.buckets bucket values must be unique",
        ),
        (
            lambda evidence: evidence["buckets"][0].__setitem__("bucket", " volatility "),  # type: ignore[index,union-attr]
            "regime_stratified_oos.buckets[0].bucket must be a canonical string",
        ),
        (
            lambda evidence: evidence["buckets"][0]["metrics"].__setitem__("total_return", "0.03"),  # type: ignore[index,union-attr]
            "regime_stratified_oos.buckets[0].metrics.total_return must be a finite strict number",
        ),
        (
            lambda evidence: evidence["buckets"][0]["metrics"].__setitem__("trade_count", True),  # type: ignore[index,union-attr]
            "regime_stratified_oos.buckets[0].metrics.trade_count must be a non-negative integer",
        ),
    ],
)
def test_load_backtest_bundle_rejects_malformed_regime_stratified_oos_evidence(
    tmp_path: Path,
    mutator,
    expected_message: str,
) -> None:
    evidence = _regime_stratified_oos_evidence()
    mutator(evidence)
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        regime_stratified_oos=evidence,
    )

    with pytest.raises(ValueError, match=re.escape(expected_message)):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_regime_bucket_level_collapse(tmp_path: Path) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(crash_total_return=-0.02),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_regime_stratified_oos_evidence"] is True
    assert gate["checks"]["rejects_regime_bucket_collapse"] is False
    assert "regime-stratified OOS bucket collapses: crash" in gate["why"]


def test_compare_backtest_bundles_rejects_walk_forward_without_split_metadata(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        degradation_replay_evidence=_degradation_replay_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_purged_embargoed_split_metadata"] is False
    assert "missing purged/embargoed walk-forward split metadata" in gate["why"]


def test_load_backtest_bundle_rejects_negative_walk_forward_embargo(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 0,
            "embargo_bars": -1,
        },
    )

    with pytest.raises(ValueError, match="split_metadata.embargo_bars must be a non-negative integer"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_walk_forward_split_run_id_leakage(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 0,
            "embargo_bars": 0,
        },
        window_split_metadata=[
            {"train_run_ids": ["row-001"], "test_run_ids": ["row-001"]},
            {"train_run_ids": ["row-002"], "test_run_ids": ["row-004"]},
        ],
    )

    with pytest.raises(ValueError, match="windows.json.rows\\[0\\].split_metadata train/test run_ids must be disjoint"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_overlapping_walk_forward_periods(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 0,
            "embargo_bars": 0,
        },
    )
    windows_path = bundle / "windows.json"
    windows = json.loads(windows_path.read_text(encoding="utf-8"))
    windows["rows"][0]["test_period"]["start"] = "2026-01-01T00:00:00+00:00"
    _write_json(windows_path, windows)

    with pytest.raises(ValueError, match="windows.json.rows\\[0\\].train_period.end must be before"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_noncanonical_walk_forward_period_timestamp(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        split_metadata={
            "schema_version": "walk_forward_split_metadata.v1",
            "purge_bars": 0,
            "embargo_bars": 0,
        },
    )
    windows_path = bundle / "windows.json"
    windows = json.loads(windows_path.read_text(encoding="utf-8"))
    windows["rows"][0]["test_period"]["start"] = "2026-01-03T00:00:00Z"
    _write_json(windows_path, windows)

    with pytest.raises(ValueError, match="windows.json.rows\\[0\\].test_period.start must match datetime.isoformat"):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_walk_forward_isolated_spike_optimum(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
    )
    summary_path = variant_bundle / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["parameter_stability"]["isolated_spike"] = {
        "is_isolated": True,
        "rejection_reason": "selected_optimum_neighbors_fail_threshold",
    }
    _write_json(summary_path, summary)

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "reject"
    assert gate["checks"]["has_parameter_stability_surface"] is True
    assert gate["checks"]["rejects_isolated_spike_optimum"] is False
    assert "isolated spike optimum: selected_optimum_neighbors_fail_threshold" in gate["why"]



def test_compare_backtest_bundles_recognizes_runtime_and_rollback_metadata_from_scorecard(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
    )
    scorecard_path = variant_bundle / "scorecard.json"
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scorecard["runtime_observability"] = {"runtime_fields": ["regime", "allocator_decision_reason"]}
    scorecard["rollback_plan"] = {
        "rollback_target": "baseline_walk_forward",
        "rollback_trigger": "oos_total_return_below_zero",
        "observation_window": "14d",
    }
    _write_json(scorecard_path, scorecard)

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["checks"]["has_runtime_observability_plan"] is True
    assert gate["checks"]["has_rollback_plan"] is True


def test_load_backtest_bundle_rejects_walk_forward_without_multiple_testing_correction(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        include_multiple_testing_correction=False,
    )

    with pytest.raises(ValueError, match="multiple_testing_correction must be present"):
        promotion.load_backtest_bundle(bundle)


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    (
        (
            lambda correction: correction.pop("number_of_trials"),
            "multiple_testing_correction.number_of_trials must be present",
        ),
        (
            lambda correction: correction.update({"number_of_trials": True}),
            "multiple_testing_correction.number_of_trials must be an integer greater than one",
        ),
        (
            lambda correction: correction.update({"corrected_p_value": "0.02"}),
            "multiple_testing_correction.corrected_p_value must be a finite number",
        ),
        (
            lambda correction: correction.update({"corrected_p_value": float("nan")}),
            "multiple_testing_correction.corrected_p_value must be a finite number",
        ),
        (
            lambda correction: correction.update({"adjusted_threshold": True}),
            "multiple_testing_correction.adjusted_threshold must be a finite number",
        ),
        (
            lambda correction: correction.update({"adjusted_pass": 1}),
            "multiple_testing_correction.adjusted_pass must be a bool",
        ),
    ),
)
def test_load_backtest_bundle_rejects_malformed_multiple_testing_correction(
    tmp_path: Path,
    mutator: object,
    expected_message: str,
) -> None:
    correction = _multiple_testing_correction(number_of_trials=2)
    mutator(correction)  # type: ignore[operator]
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        multiple_testing_correction=correction,
    )

    with pytest.raises(ValueError, match=expected_message):
        promotion.load_backtest_bundle(bundle)


def test_compare_backtest_bundles_rejects_inconsistent_multiple_testing_trial_counts(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        multiple_testing_correction=_multiple_testing_correction(number_of_trials=2),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        multiple_testing_correction=_multiple_testing_correction(number_of_trials=3),
    )

    with pytest.raises(ValueError, match="multiple_testing_correction.number_of_trials must match"):
        promotion.compare_backtest_bundles(baseline_bundle=baseline_bundle, variant_bundle=variant_bundle)



def test_compare_backtest_bundles_holds_walk_forward_when_stability_regresses_vs_baseline(tmp_path: Path) -> None:
    split_metadata = {
        "schema_version": "walk_forward_split_metadata.v1",
        "purge_bars": 1,
        "embargo_bars": 0,
    }
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.85,
        parameter_stability_score=0.9,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.05,
        positive_window_ratio=0.7,
        parameter_stability_score=0.75,
        worst_window_return=0.01,
        split_metadata=split_metadata,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
        regime_stratified_oos=_regime_stratified_oos_evidence(),
        portfolio_correlation_exposure=_portfolio_correlation_exposure_evidence(),
        capacity_analysis_evidence=_capacity_analysis_evidence(),
        tail_risk_report=_tail_risk_report(),
    )

    result = promotion.compare_backtest_bundles(
        baseline_bundle=baseline_bundle,
        variant_bundle=variant_bundle,
    )

    gate = result["promotion_gate"]
    assert gate["decision"] == "hold"
    assert gate["checks"]["has_cost_adjusted_edge"] is True
    assert gate["metric_deltas"]["positive_window_ratio"] < 0.0
    assert gate["metric_deltas"]["parameter_stability_score"] < 0.0



def test_compare_backtest_bundles_rejects_mismatched_dataset_contract(tmp_path: Path) -> None:
    baseline_bundle = _write_full_market_bundle(
        tmp_path / "baseline",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    variant_bundle = _write_full_market_bundle(
        tmp_path / "variant",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.14,
        max_drawdown=-0.09,
        sharpe=1.10,
        cost_drag=0.018,
    )
    manifest_path = variant_bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = "/tmp/other-dataset"
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="dataset/sample contract"):
        promotion.compare_backtest_bundles(
            baseline_bundle=baseline_bundle,
            variant_bundle=variant_bundle,
        )



def test_backtest_cli_compare_writes_promotion_gate_and_decision_summary(tmp_path: Path) -> None:
    baseline_bundle = _write_full_market_bundle(
        tmp_path / "baseline",
        baseline_name="current_system",
        variant_name="baseline_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    variant_bundle = _write_full_market_bundle(
        tmp_path / "variant",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.14,
        max_drawdown=-0.09,
        sharpe=1.10,
        cost_drag=0.018,
    )

    exit_code = cli.main(
        [
            "compare",
            "--baseline-bundle",
            str(baseline_bundle),
            "--variant-bundle",
            str(variant_bundle),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    promotion_gate = json.loads((tmp_path / "out" / "promotion_gate.json").read_text(encoding="utf-8"))
    decision_summary = json.loads((tmp_path / "out" / "decision_summary.json").read_text(encoding="utf-8"))
    assert promotion_gate["decision"] == "hold"
    assert decision_summary["decision"] == "hold"
    assert decision_summary["experiment_kind"] == "full_market_baseline"
    assert decision_summary["artifacts"] == ["promotion_gate.json", "decision_summary.json"]

def test_load_backtest_bundle_rejects_numeric_strings_in_full_market_summary(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    summary_path = bundle / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["summary"]["total_return"] = "0.10"
    _write_json(summary_path, payload)

    with pytest.raises(ValueError, match="summary.json.summary.total_return must be numeric"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_numeric_strings_in_walk_forward_oos(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
    )
    summary_path = bundle / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["robustness_summary"]["out_of_sample_scorecard"]["total_return"] = "0.08"
    _write_json(summary_path, payload)

    with pytest.raises(ValueError, match="out_of_sample_scorecard.total_return must be numeric"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_noncanonical_comparison_row_keys(tmp_path: Path) -> None:
    bundle = tmp_path / "rotation_row_key"
    bundle.mkdir()
    artifacts = ["manifest.json", "summary.json", "comparison_rows.json", "scorecard.json"]
    _write_json(
        bundle / "manifest.json",
        _manifest(
            experiment_kind="rotation_suppression",
            baseline_name="current_policy",
            variant_name="soft_suppression",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle / "summary.json",
        {
            "metadata": {},
            "policies": {
                "current": {"bucket_level_pnl": 0.04, "trade_count": 5},
                "soft_suppression": {"bucket_level_pnl": 0.08, "trade_count": 4},
            },
            "opportunity_kill_rate": 0.2,
            "avoid_loss_rate": 0.6,
        },
    )
    _write_json(bundle / "comparison_rows.json", {"rows": [{" market_type ": "spot"}]})
    _write_json(
        bundle / "scorecard.json",
        {
            "key_metrics": {
                "current_bucket_level_pnl": 0.04,
                "soft_suppression_bucket_level_pnl": 0.08,
                "opportunity_kill_rate": 0.2,
                "avoid_loss_rate": 0.6,
            }
        },
    )

    with pytest.raises(ValueError, match=r"comparison_rows.json.rows\[0\] key must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_numeric_strings_in_rotation_policy_metrics(tmp_path: Path) -> None:
    bundle = tmp_path / "rotation"
    bundle.mkdir()
    artifacts = ["manifest.json", "summary.json", "comparison_rows.json", "scorecard.json"]
    _write_json(
        bundle / "manifest.json",
        _manifest(
            experiment_kind="rotation_suppression",
            baseline_name="current_policy",
            variant_name="soft_suppression",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle / "summary.json",
        {
            "metadata": {},
            "policies": {
                "current": {"bucket_level_pnl": 0.04, "trade_count": 5},
                "soft_suppression": {"bucket_level_pnl": "0.08", "trade_count": 4},
            },
            "opportunity_kill_rate": 0.2,
            "avoid_loss_rate": 0.6,
        },
    )
    _write_json(bundle / "comparison_rows.json", {"rows": []})
    _write_json(
        bundle / "scorecard.json",
        {
            "key_metrics": {
                "current_bucket_level_pnl": 0.04,
                "soft_suppression_bucket_level_pnl": 0.08,
                "opportunity_kill_rate": 0.2,
                "avoid_loss_rate": 0.6,
            }
        },
    )

    with pytest.raises(ValueError, match="policies.soft_suppression.bucket_level_pnl must be numeric"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_numeric_strings_in_allocator_metrics(tmp_path: Path) -> None:
    bundle = tmp_path / "allocator"
    bundle.mkdir()
    artifacts = ["manifest.json", "summary.json", "comparison_rows.json", "scorecard.json"]
    _write_json(
        bundle / "manifest.json",
        _manifest(
            experiment_kind="allocator_friction",
            baseline_name="current_policy",
            variant_name="allocator_variant",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle / "summary.json",
        {
            "variants": {
                "current_allocator": {
                    "allocation_summary": {"accepted_allocations": "4"},
                    "frictions": {"base": {"net_bucket_pnl": 0.08, "cost_drag": 0.02, "trade_count": 4}},
                }
            }
        },
    )
    _write_json(bundle / "comparison_rows.json", {"rows": []})
    _write_json(
        bundle / "scorecard.json",
        {
            "key_metrics": {
                "best_base_net_bucket_pnl": 0.08,
                "best_stressed_net_bucket_pnl": 0.05,
                "current_allocator_base_cost_drag": 0.02,
            }
        },
    )

    with pytest.raises(ValueError, match="allocation_summary.accepted_allocations must be a non-negative integer"):
        promotion.load_backtest_bundle(bundle)


def test_metric_snapshot_rejects_mutated_allocator_missing_current_variant(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "allocator_compare"
    bundle_dir.mkdir()
    artifacts = ["manifest.json", "summary.json", "comparison_rows.json", "scorecard.json"]
    _write_json(
        bundle_dir / "manifest.json",
        _manifest(
            experiment_kind="allocator_friction",
            baseline_name="current_policy",
            variant_name="allocator_variant",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle_dir / "summary.json",
        {
            "variants": {
                "current_allocator": {
                    "allocation_summary": {"accepted_allocations": 4},
                    "frictions": {"base": {"net_bucket_pnl": 0.08, "cost_drag": 0.02, "trade_count": 4}},
                }
            }
        },
    )
    _write_json(bundle_dir / "comparison_rows.json", {"rows": []})
    _write_json(
        bundle_dir / "scorecard.json",
        {
            "key_metrics": {
                "best_base_net_bucket_pnl": 0.08,
                "best_stressed_net_bucket_pnl": 0.05,
                "current_allocator_base_cost_drag": 0.02,
            }
        },
    )
    bundle = promotion.load_backtest_bundle(bundle_dir)
    bundle.artifacts["summary.json"]["variants"] = {
        "legacy_allocator": {
            "allocation_summary": {"accepted_allocations": 4},
            "frictions": {"base": {"net_bucket_pnl": 0.08, "cost_drag": 0.02, "trade_count": 4}},
        }
    }

    with pytest.raises(ValueError, match="summary.json.variants.current_allocator must be an object"):
        promotion._metric_snapshot(bundle)


def test_load_backtest_bundle_rejects_numeric_strings_in_engine_metrics(tmp_path: Path) -> None:
    bundle = tmp_path / "engine"
    bundle.mkdir()
    artifacts = ["manifest.json", "summary.json", "scorecard.json"]
    _write_json(
        bundle / "manifest.json",
        _manifest(
            experiment_kind="engine_filter_ablation",
            baseline_name="current_policy",
            variant_name="engine_variant",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle / "summary.json",
        {
            "variants": {
                "engine_variant": {
                    "funnel": {},
                    "filter_counts": {},
                    "performance": {},
                }
            }
        },
    )
    _write_json(
        bundle / "scorecard.json",
        {"key_metrics": {"best_bucket_level_pnl": 0.08, "best_variant_accepted_allocations": "4"}},
    )

    with pytest.raises(ValueError, match="scorecard.json.key_metrics.best_variant_accepted_allocations must be a non-negative integer"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_noncanonical_engine_variant_keys(tmp_path: Path) -> None:
    bundle = tmp_path / "engine_variant_key"
    bundle.mkdir()
    artifacts = ["manifest.json", "summary.json", "scorecard.json"]
    _write_json(
        bundle / "manifest.json",
        _manifest(
            experiment_kind="engine_filter_ablation",
            baseline_name="current_policy",
            variant_name="engine_variant",
            artifacts=artifacts,
        ),
    )
    _write_json(
        bundle / "summary.json",
        {
            "variants": {
                " engine_variant ": {
                    "funnel": {},
                    "filter_counts": {},
                    "performance": {},
                }
            }
        },
    )
    _write_json(
        bundle / "scorecard.json",
        {"key_metrics": {"best_bucket_level_pnl": 0.08, "best_variant_accepted_allocations": 4}},
    )

    with pytest.raises(ValueError, match="summary.json.variants key must be canonical"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_non_string_experiment_kind(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["experiment_kind"] = 123
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.experiment_kind must be a string"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_boolean_manifest_snapshot_count(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_count"] = True
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.snapshot_count must be a non-negative integer"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_non_object_manifest_sample_period(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_period"] = "2026-01"
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.sample_period must be an object"):
        promotion.load_backtest_bundle(bundle)

def test_compare_backtest_bundles_rejects_string_runtime_fields_plan(tmp_path: Path) -> None:
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.75,
        parameter_stability_score=0.7,
        worst_window_return=0.01,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
    )
    variant_bundle = _write_walk_forward_bundle(
        tmp_path / "variant",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
        runtime_fields=["regime", "allocator_decision_reason"],
        rollback_target="baseline_walk_forward",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
    )
    summary_path = variant_bundle / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["runtime_observability"]["runtime_fields"] = "regime"
    _write_json(summary_path, payload)

    with pytest.raises(ValueError, match="runtime_observability.runtime_fields must be a list of strings"):
        promotion.compare_backtest_bundles(baseline_bundle=baseline_bundle, variant_bundle=variant_bundle)

def test_load_backtest_bundle_rejects_numeric_strings_in_walk_forward_windows(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
    )
    windows_path = bundle / "windows.json"
    payload = json.loads(windows_path.read_text(encoding="utf-8"))
    payload["rows"][0]["out_of_sample"]["scorecard"]["total_return"] = "0.08"
    _write_json(windows_path, payload)

    with pytest.raises(ValueError, match=r"windows.json.rows\[0\].out_of_sample.scorecard.total_return must be numeric"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_numeric_strings_in_parameter_stability_summary(tmp_path: Path) -> None:
    bundle = _write_walk_forward_bundle(
        tmp_path / "bundle",
        baseline_name="current_policy",
        variant_name="candidate_walk_forward",
        out_of_sample_total_return=0.08,
        positive_window_ratio=0.9,
        parameter_stability_score=0.9,
        worst_window_return=0.02,
    )
    summary_path = bundle / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["parameter_stability"]["parameter_stability_score"] = "0.9"
    _write_json(summary_path, payload)

    with pytest.raises(
        ValueError,
        match=r"parameter_stability\.parameter_stability_score must be a bounded ratio strict number",
    ):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_string_full_market_audit_trade_count(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    audit_path = bundle / "audit.json"
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["audit"]["trade_count"] = "5"
    _write_json(audit_path, payload)

    with pytest.raises(ValueError, match="audit.json.audit.trade_count must be a non-negative integer"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_string_full_market_breakdown_net_pnl(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    breakdowns_path = bundle / "breakdowns.json"
    payload = json.loads(breakdowns_path.read_text(encoding="utf-8"))
    payload["breakdowns"]["by_year"][0]["net_pnl"] = "0.10"
    _write_json(breakdowns_path, payload)

    with pytest.raises(ValueError, match=r"breakdowns.json.breakdowns.by_year\[0\].net_pnl must be numeric"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_non_finite_full_market_summary_numbers(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    summary_path = bundle / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["summary"]["sharpe"] = float("inf")
    _write_json(summary_path, payload)

    with pytest.raises(ValueError, match="summary.json.summary.sharpe must be finite"):
        promotion.load_backtest_bundle(bundle)


def test_load_backtest_bundle_rejects_unsafe_audit_rejection_reason_identifier(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    audit_path = bundle / "audit.json"
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["audit"]["rejection_reasons"] = {"open risk limit reached": 1}
    _write_json(audit_path, payload)

    with pytest.raises(ValueError, match="audit.json.audit.rejection_reasons key must be a safe identifier"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_reversed_manifest_sample_period(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_period"] = {"start": "2026-02-01T00:00:00+00:00", "end": "2026-01-01T00:00:00+00:00"}
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.sample_period start must be before end"):
        promotion.load_backtest_bundle(bundle)

def test_load_backtest_bundle_rejects_naive_manifest_sample_period(tmp_path: Path) -> None:
    bundle = _write_full_market_bundle(
        tmp_path / "bundle",
        baseline_name="current_system",
        variant_name="candidate_policy",
        total_return=0.10,
        max_drawdown=-0.10,
        sharpe=1.00,
        cost_drag=0.020,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_period"] = {"start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00"}
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest.json.sample_period.start must be timezone-aware"):
        promotion.load_backtest_bundle(bundle)


def _valid_execution_preview() -> dict[str, object]:
    return {
        "schema_version": "execution_preview.v1",
        "orders": [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 0.01,
                "notional": 600.0,
                "price": 60000.0,
                "stop_price": None,
                "limit_price": 60000.0,
                "reduce_only": False,
                "close_position": False,
                "time_in_force": "GTX",
                "post_only": True,
            },
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "order_type": "STOP_MARKET",
                "quantity": None,
                "notional": None,
                "price": None,
                "stop_price": 58000.0,
                "limit_price": None,
                "reduce_only": True,
                "close_position": True,
                "time_in_force": None,
                "post_only": False,
            },
        ],
        "unsupported": [],
    }


def test_validate_execution_preview_payload_accepts_runtime_replay_payload() -> None:
    report = promotion.validate_execution_preview_payload(_valid_execution_preview())

    assert report == {"valid": True, "reason_codes": []}


@pytest.mark.parametrize(
    ("mutator", "reason_code"),
    [
        (lambda payload: payload["orders"][0].update({"quantity": "0.01"}), "quantity_not_strict_number"),
        (lambda payload: payload["orders"][0].update({"quantity": True}), "quantity_not_strict_number"),
        (lambda payload: payload["orders"][0].update({"quantity": float("inf")}), "quantity_not_finite"),
        (lambda payload: payload["orders"][0].update({"symbol": " BTCUSDT"}), "symbol_not_canonical"),
        (lambda payload: payload["orders"][0].update({"side": "LONG"}), "side_unsupported"),
        (lambda payload: payload["orders"][0].update({"order_type": "ICEBERG"}), "order_type_unsupported"),
        (lambda payload: payload["orders"][0].update({"reduce_only": 0}), "reduce_only_not_bool"),
        (lambda payload: payload["orders"][0].update({"post_only": 1}), "post_only_not_bool"),
        (lambda payload: payload["orders"][0].update({"time_in_force": " GTX"}), "time_in_force_not_canonical"),
        (lambda payload: payload.update({"unsupported": [{"reason_code": "price protection missing"}]}), "unsupported_reason_code_invalid"),
        (lambda payload: payload.update({"unsupported": [{"reason_code": "missing_exchange_metadata"}]}), "unsupported_orders_present"),
        (lambda payload: payload["orders"][0].update({"limit_price": None}), "limit_price_required_for_limit"),
        (lambda payload: payload["orders"][0].update({"post_only": False}), "post_only_required_for_gtx"),
        (lambda payload: payload["orders"][1].update({"stop_price": None}), "stop_price_required_for_stop_market"),
        (lambda payload: payload["orders"][1].update({"quantity": 0.01}), "quantity_must_be_absent_for_close_position"),
    ],
)
def test_validate_execution_preview_payload_fails_closed_on_malformed_payloads(mutator, reason_code) -> None:
    payload = _valid_execution_preview()
    mutator(payload)

    report = promotion.validate_execution_preview_payload(payload)

    assert report["valid"] is False
    assert reason_code in report["reason_codes"]
