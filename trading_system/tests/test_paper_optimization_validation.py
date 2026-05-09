from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.paper_optimization.validation import run_paper_optimization_validation


def test_run_paper_optimization_validation_writes_artifacts_and_updates_promotion_decision(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
                "snapshot_count": 11,
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps(
            {
                "recorded_at_bj": "2026-04-24T12:05:00+08:00",
                "recommendations": [
                    {
                        "id": "lower-total-risk-budget",
                        "overlay_ops": [
                            {
                                "env": "TRADING_MAX_TOTAL_RISK_PCT",
                                "op": "multiply",
                                "factor": 0.8,
                                "default": 0.03,
                                "minimum": 0.005,
                                "precision": 4,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    captured_configs: list[dict[str, object]] = []

    def fake_backtest_cli_main(args: list[str]) -> int:
        config_path = Path(args[args.index("--config") + 1])
        output_dir = Path(args[args.index("--output-dir") + 1])
        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        captured_configs.append(config_payload)
        bundle_dir = output_dir / (
            f"walk_forward_validation__{config_payload['baseline_name']}__{config_payload['variant_name']}"
        )
        bundle_dir.mkdir(parents=True, exist_ok=True)
        return 0

    def fake_compare_backtest_bundles(*, baseline_bundle, variant_bundle):
        return {
            "promotion_gate": {
                "experiment_kind": "walk_forward_validation",
                "baseline_bundle": str(baseline_bundle),
                "variant_bundle": str(variant_bundle),
                "decision": "candidate_for_promotion",
                "checks": {"has_cost_adjusted_edge": True},
                "metric_deltas": {"total_return": 0.04},
                "why": [],
            },
            "decision_summary": {
                "experiment_kind": "walk_forward_validation",
                "baseline_bundle": str(baseline_bundle),
                "variant_bundle": str(variant_bundle),
                "decision": "candidate_for_promotion",
                "summary": "validation bundles beat baseline",
                "why": [],
                "artifacts": ["promotion_gate.json", "decision_summary.json"],
            },
        }

    monkeypatch.setattr(
        "trading_system.app.paper_optimization.validation.backtest_cli.main",
        fake_backtest_cli_main,
    )
    monkeypatch.setattr(
        "trading_system.app.paper_optimization.validation.compare_backtest_bundles",
        fake_compare_backtest_bundles,
    )

    payload = run_paper_optimization_validation(
        recommendations_path=recommendations_path,
        promotion_decision_path=promotion_decision_path,
        optimization_dir=optimization_dir,
        repo_root=repo_root,
        baseline_env={"TRADING_MAX_TOTAL_RISK_PCT": "0.03"},
        recorded_at_bj="2026-04-24T12:10:00+08:00",
    )

    validation_dir = optimization_dir / "validation"
    configs_dir = validation_dir / "configs"
    comparison_dir = validation_dir / "comparison"

    assert payload["status"] == "candidate_for_promotion"
    assert payload["decision"] == "candidate_for_promotion"
    assert payload["summary"] == "validation bundles beat baseline"
    assert len(captured_configs) == 2
    assert captured_configs[0]["dataset_root"] == str(dataset_root)
    assert captured_configs[1]["dataset_root"] == str(dataset_root)
    assert (configs_dir / "baseline_config.json").exists()
    assert (configs_dir / "variant_config.json").exists()
    assert captured_configs[0]["metadata"]["runtime_env_overrides"] == {}
    assert captured_configs[1]["metadata"]["runtime_env_overrides"] == {"TRADING_MAX_TOTAL_RISK_PCT": "0.024"}
    assert (comparison_dir / "promotion_gate.json").exists()
    assert (comparison_dir / "decision_summary.json").exists()

    written = json.loads(promotion_decision_path.read_text(encoding="utf-8"))
    assert written["decision"] == "candidate_for_promotion"
    assert written["baseline_bundle"].endswith("paper_opt_baseline")
    assert written["variant_bundle"].endswith("paper_opt_candidate")

def test_run_paper_optimization_validation_rejects_non_string_baseline_env(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(json.dumps({"recommendations": []}), encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="baseline_env.TRADING_MAX_TOTAL_RISK_PCT must be a string"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={"TRADING_MAX_TOTAL_RISK_PCT": 0.03},
            recorded_at_bj="2026-04-24T12:10:00+08:00",
        )

def test_run_paper_optimization_validation_rejects_non_string_recommendation_ids(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps({"recommendations": [{"id": 123, "overlay_ops": []}]}),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="recommendations.id must be a string"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={},
            recorded_at_bj="2026-04-24T12:10:00+08:00",
        )

def test_run_paper_optimization_validation_rejects_non_string_dataset_timestamps(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": 123,
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps({"recommendations": [{"id": "lower-risk", "overlay_ops": []}]}),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="import_manifest.start_timestamp must be a string"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={},
            recorded_at_bj="2026-04-24T12:10:00+08:00",
        )

def test_run_paper_optimization_validation_rejects_non_list_recommendations(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(json.dumps({"recommendations": "not-a-list"}), encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="recommendations must be a list"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={},
            recorded_at_bj="2026-04-24T12:10:00+08:00",
        )

def test_run_paper_optimization_validation_rejects_non_mapping_env_overrides(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps({"recommendations": [{"id": "bad-env", "overlay_ops": []}]}),
        encoding="utf-8",
    )

    def fake_materialize_env_overrides(*_args, **_kwargs):
        return [("TRADING_MAX_TOTAL_RISK_PCT", "0.02")]

    monkeypatch.setattr(
        "trading_system.app.paper_optimization.validation.materialize_env_overrides",
        fake_materialize_env_overrides,
    )

    import pytest

    with pytest.raises(ValueError, match="runtime_env_overrides must be an object"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={},
            recorded_at_bj="2026-04-24T12:10:00+08:00",
        )

def test_run_paper_optimization_validation_rejects_non_string_recorded_at(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    optimization_dir = tmp_path / "runtime" / "paper" / "prod" / "optimization"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    dataset_root = repo_root / "data" / "imported-datasets" / "dataset-a"
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "start_timestamp": "2026-03-10T00:00:00Z",
                "end_timestamp": "2026-03-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
        json.dumps({"recommendations": [{"id": "lower-risk", "overlay_ops": []}]}),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="recorded_at_bj must be a string"):
        run_paper_optimization_validation(
            recommendations_path=recommendations_path,
            promotion_decision_path=promotion_decision_path,
            optimization_dir=optimization_dir,
            repo_root=repo_root,
            baseline_env={},
            recorded_at_bj=123,
        )

def test_resolve_validation_dataset_root_rejects_invalid_dataset_root_type(tmp_path: Path) -> None:
    from trading_system.app.paper_optimization.validation import resolve_validation_dataset_root

    import pytest

    with pytest.raises(ValueError, match="dataset_root must be a path string"):
        resolve_validation_dataset_root(dataset_root=123, repo_root=tmp_path)

def test_resolve_validation_dataset_root_rejects_invalid_repo_root_type() -> None:
    from trading_system.app.paper_optimization.validation import resolve_validation_dataset_root

    import pytest

    with pytest.raises(ValueError, match="repo_root must be a path"):
        resolve_validation_dataset_root(repo_root=123)
