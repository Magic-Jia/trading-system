from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest import cli
import trading_system.app.backtest.promotion as promotion


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
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
        "parameter_stability": {"parameter_stability_score": parameter_stability_score},
    }
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
        },
    )
    return root


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
    }
    assert gate["why"] == []



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
