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


def _write_full_market_bundle(
    root: Path,
    *,
    baseline_name: str,
    variant_name: str,
    total_return: float,
    max_drawdown: float,
    sharpe: float,
    cost_drag: float,
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
    runtime_fields: list[str] | None = None,
    rollback_target: str | None = None,
    rollback_trigger: str | None = None,
    observation_window: str | None = None,
    multiple_testing_correction: dict[str, object] | None = None,
    include_multiple_testing_correction: bool = True,
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
    if runtime_fields:
        summary_payload["runtime_observability"] = {"runtime_fields": runtime_fields}
    if rollback_target and (rollback_trigger or observation_window):
        summary_payload["rollback_plan"] = {
            "rollback_target": rollback_target,
            "rollback_trigger": rollback_trigger,
            "observation_window": observation_window,
        }
    _write_json(
        root / "manifest.json",
        _manifest(
            experiment_kind="walk_forward_validation",
            baseline_name=baseline_name,
            variant_name=variant_name,
            artifacts=artifacts,
        ),
    )
    _write_json(root / "summary.json", summary_payload)
    _write_json(
        root / "windows.json",
        {
            "metadata": {"baseline_name": baseline_name, "variant_name": variant_name},
            "rows": [
                {
                    "window_index": 1,
                    "out_of_sample": {"scorecard": {"total_return": out_of_sample_total_return, "trade_count": 2}},
                },
                {
                    "window_index": 2,
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
        "has_attribution_or_funnel_explanation": True,
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


def test_compare_backtest_bundles_promotes_walk_forward_when_all_checks_pass(tmp_path: Path) -> None:
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
        "has_attribution_or_funnel_explanation": True,
        "has_runtime_observability_plan": True,
        "has_rollback_plan": True,
        "has_parameter_stability_surface": True,
        "rejects_isolated_spike_optimum": True,
    }
    assert gate["why"] == []


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
    baseline_bundle = _write_walk_forward_bundle(
        tmp_path / "baseline",
        baseline_name="current_policy",
        variant_name="baseline_walk_forward",
        out_of_sample_total_return=0.03,
        positive_window_ratio=0.85,
        parameter_stability_score=0.9,
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
        out_of_sample_total_return=0.05,
        positive_window_ratio=0.7,
        parameter_stability_score=0.75,
        worst_window_return=0.01,
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
