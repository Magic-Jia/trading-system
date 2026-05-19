from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from trading_system.app.backtest import cli
from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import OrderIntent
from trading_system.run_cycle import _execution_sample_collection_health


FIXTURES = Path(__file__).parent / "fixtures" / "backtest"
GENERATED_AT = "2026-05-18T05:00:00Z"


def _sample_order() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-btc-long",
        signal_id="signal-btc-long",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
    )


def _write_professional_config(path: Path, *, dataset_root: Path, experiment_kind: str) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "experiment_kind": experiment_kind,
                "sample_windows": [
                    {
                        "name": "smoke_window",
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-12T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "universe": {
                    "listing_age_days": 30,
                    "min_quote_volume_usdt_24h": {"spot": 1000000.0, "futures": 1000000.0},
                    "require_complete_funding": True,
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100000.0,
                    "risk_per_trade": 0.02,
                    "max_open_risk": 0.03,
                },
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0},
                    "funding_mode": "historical_series",
                },
                "baseline_name": "current_system",
                "variant_name": f"diagnostic_{experiment_kind}",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_professional_evidence_pipeline_writes_hold_diagnostic_when_dataset_generation_fails(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    backtest_config = configs_dir / "backtest.json"
    walk_forward_config = configs_dir / "walk_forward.json"
    allocator_config = configs_dir / "allocator.json"
    _write_professional_config(backtest_config, dataset_root=dataset_root, experiment_kind="full_market_baseline")
    _write_professional_config(walk_forward_config, dataset_root=dataset_root, experiment_kind="walk_forward_validation")
    _write_professional_config(allocator_config, dataset_root=dataset_root, experiment_kind="allocator_friction")
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(backtest_config),
            "--walk-forward-config",
            str(walk_forward_config),
            "--allocator-friction-config",
            str(allocator_config),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    evidence_chain_path = output_dir / "professional_evidence" / "backtest_evidence_chain.json"
    assert evidence_chain_path.exists()
    evidence_chain = json.loads(evidence_chain_path.read_text(encoding="utf-8"))
    assert evidence_chain["schema_version"] == "backtest_evidence_chain.v1"
    assert evidence_chain["summary"]["decision"] == "hold"
    assert evidence_chain["historical_backtest"]["status"] == "hold"
    assert "dataset_missing_lifecycle_status" in evidence_chain["historical_backtest"]["reason_codes"]
    assert "pipeline_generation_failed" in evidence_chain["summary"]["reason_codes"]
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "hold"
    assert manifest["professional_evidence"]["evidence_chain_path"] == str(evidence_chain_path)
    assert manifest["professional_evidence"]["generation_failed"] is True


def test_run_professional_evidence_pipeline_preflights_multiple_legacy_dataset_gaps(tmp_path: Path) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif source_path.name == "market_context.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for symbol_context in payload["symbols"].values():
                symbol_context.pop("futures_context", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    backtest_config = configs_dir / "backtest.json"
    walk_forward_config = configs_dir / "walk_forward.json"
    allocator_config = configs_dir / "allocator.json"
    _write_professional_config(backtest_config, dataset_root=dataset_root, experiment_kind="full_market_baseline")
    _write_professional_config(walk_forward_config, dataset_root=dataset_root, experiment_kind="walk_forward_validation")
    _write_professional_config(allocator_config, dataset_root=dataset_root, experiment_kind="allocator_friction")
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(backtest_config),
            "--walk-forward-config",
            str(walk_forward_config),
            "--allocator-friction-config",
            str(allocator_config),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    evidence_chain = json.loads(
        (output_dir / "professional_evidence" / "backtest_evidence_chain.json").read_text(encoding="utf-8")
    )
    assert evidence_chain["summary"]["decision"] == "hold"
    assert "dataset_missing_lifecycle_status" in evidence_chain["summary"]["reason_codes"]
    assert "dataset_missing_futures_context" in evidence_chain["summary"]["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in evidence_chain["summary"]["reason_codes"]
    preflight = evidence_chain["generation_failure"]["preflight"]
    assert preflight["dataset_root"] == str(dataset_root)
    assert preflight["snapshot_count"] > 0
    assert preflight["missing_lifecycle_status"]["row_count"] > 0
    assert preflight["missing_lifecycle_status"]["snapshot_count"] > 0
    assert preflight["missing_lifecycle_status"]["examples"][0]["path"].endswith("instrument_snapshot.json")
    assert preflight["missing_futures_context"]["symbol_count"] > 0
    assert preflight["missing_futures_context"]["snapshot_count"] > 0
    assert preflight["missing_futures_context"]["examples"][0]["path"].endswith("market_context.json")


def test_run_professional_evidence_pipeline_writes_bundles_reports_and_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    pipeline_manifest_path = output_dir / "professional_evidence_pipeline_manifest.json"
    assert pipeline_manifest_path.exists()
    manifest = json.loads(pipeline_manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "professional_evidence_pipeline.v1"
    assert manifest["generated_at"] == GENERATED_AT
    assert manifest["decision"] in {"pass", "hold"}
    assert manifest["bundles"]["backtest"].endswith("full_market_baseline__current_system__auditable_baseline")
    assert manifest["bundles"]["walk_forward"].endswith("walk_forward_validation__current_policy__rolling_walk_forward")
    assert manifest["bundles"]["allocator_friction"].endswith("allocator_friction__current_policy__allocator_fee_drag")

    evidence_outputs = manifest["professional_evidence"]
    evidence_chain_path = Path(evidence_outputs["evidence_chain_path"])
    assert evidence_chain_path == output_dir / "professional_evidence" / "backtest_evidence_chain.json"
    assert evidence_chain_path.exists()
    assert Path(evidence_outputs["walk_forward_report_path"]).exists()
    assert Path(evidence_outputs["cost_sensitivity_report_path"]).exists()

    evidence_chain = json.loads(evidence_chain_path.read_text(encoding="utf-8"))
    assert evidence_chain["schema_version"] == "backtest_evidence_chain.v1"
    assert evidence_chain["generated_at"] == GENERATED_AT
    assert evidence_chain["summary"]["decision"] == manifest["decision"]


def test_run_professional_evidence_pipeline_writes_promotion_gate_report_and_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"
    gate_inputs = tmp_path / "gate-inputs"
    window_path = gate_inputs / "simulated_live_evidence_window.json"
    trend_path = gate_inputs / "promotion_readiness_scorecard_trend.json"
    calibration_path = gate_inputs / "calibration_feedback.json"
    gate_inputs.mkdir(parents=True)
    window_path.write_text(
        json.dumps(
            {
                "schema_version": "simulated_live_evidence_window.v1",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reason_codes": [],
                "checks": {
                    "minimum_distinct_sessions_met": True,
                    "session_identities_unique": True,
                    "generated_at_monotonic": True,
                    "as_of_monotonic": True,
                    "all_bundles_pass": True,
                    "all_required_bundle_components_present": True,
                },
                "bundles": [
                    {"session_id": "s1", "day": "2026-05-15", "generated_at": "2026-05-15T00:00:00Z"},
                    {"session_id": "s2", "day": "2026-05-16", "generated_at": "2026-05-16T00:00:00Z"},
                    {"session_id": "s3", "day": "2026-05-17", "generated_at": "2026-05-17T00:00:00Z"},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    trend_path.write_text(
        json.dumps(
            {
                "schema_version": "promotion_readiness_scorecard_trend.v1",
                "mode": "simulated_live",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reasons": [],
                "checks": {
                    "sample_window_sufficient": True,
                    "scorecards_well_formed": True,
                    "generated_at_monotonic": True,
                    "scorecard_identities_unique": True,
                    "score_deterioration_within_threshold": True,
                    "repeated_blockers_absent": True,
                },
                "scorecards": [
                    {"identity": "scorecard-1", "generated_at": "2026-05-16T00:00:00Z", "decision": "pass", "score": 90.0},
                    {"identity": "scorecard-2", "generated_at": "2026-05-17T00:00:00Z", "decision": "pass", "score": 91.0},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    calibration_path.write_text(
        json.dumps(
            {
                "schema_version": "calibration_feedback_artifact.v1",
                "generated_at": GENERATED_AT,
                "decision": "ready",
                "checks": {"sample_count_met": True, "evidence_fresh": True},
                "reasons": [],
                "components": [
                    {"component": "tca_report", "identity": "tca-20260518", "schema_version": "tca_calibration_report.v1"}
                ],
                "side_effect_boundary": "offline_local_only",
                "strategy_config_mutation": "forbidden",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--simulated-live-evidence-window",
            str(window_path),
            "--promotion-readiness-scorecard-trend",
            str(trend_path),
            "--calibration-artifact",
            str(calibration_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    gate_path = Path(manifest["promotion_gate"]["decision_report_path"])
    assert gate_path == output_dir / "promotion_gate_decision.json"
    assert gate_path.exists()
    gate_report = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate_report["schema_version"] == "promotion_gate_decision.v1"
    assert gate_report["checks"]["professional_evidence_chain"]["status"] in {"pass", "hold"}
    assert gate_report["checks"]["professional_evidence_chain"]["execution_realism"]["status"] in {"pass", "hold"}
    assert manifest["promotion_gate"]["decision"] == gate_report["decision"]
    assert manifest["promotion_gate"]["professional_evidence_chain_path"] == manifest["professional_evidence"]["evidence_chain_path"]


def test_run_professional_evidence_pipeline_passes_execution_realism_from_non_empty_paper_samples(tmp_path: Path) -> None:
    runtime_paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        state_file=runtime_paths.state_file,
        execution=replace(DEFAULT_CONFIG.execution, mode="paper", environment="research"),
    )
    executor = OrderExecutor(config, mode="paper")
    result = executor.execute(_sample_order(), RuntimeStateV2.empty())
    health = _execution_sample_collection_health(runtime_paths, {"candidate_count": 1, "allocation_count": 1})
    health_path = runtime_paths.bucket_dir / "execution_sample_collection_health.json"
    health_path.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_paths.latest_summary_file.write_text(
        json.dumps(
            {
                "status": "ok",
                "mode": "paper",
                "runtime_env": "research",
                "candidate_count": 1,
                "allocation_count": 1,
                "execution_sample_collection_health_file": str(health_path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--runtime-summary-path",
            str(runtime_paths.latest_summary_file),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert result["result"] == "FILLED"
    assert health["status"] == "available"
    assert exit_code == 0
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    evidence_chain = json.loads(Path(manifest["professional_evidence"]["evidence_chain_path"]).read_text(encoding="utf-8"))
    assert manifest["professional_evidence"]["runtime_summary_path"] == str(runtime_paths.latest_summary_file)
    assert manifest["professional_evidence"]["execution_sample_collection_health_path"] == str(health_path)
    assert evidence_chain["execution_realism"]["status"] == "pass"
    assert evidence_chain["execution_realism"]["sample_count"] == 1
    assert evidence_chain["execution_realism"]["reason_codes"] == []
    assert evidence_chain["summary"]["component_statuses"]["execution_realism"] == "pass"


def test_run_professional_evidence_pipeline_rejects_partial_promotion_gate_inputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"
    window_path = tmp_path / "simulated_live_evidence_window.json"
    window_path.write_text(
        json.dumps(
            {
                "schema_version": "simulated_live_evidence_window.v1",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reason_codes": [],
                "checks": {},
                "bundles": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--simulated-live-evidence-window",
            str(window_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 1
    assert not (output_dir / "professional_evidence_pipeline_manifest.json").exists()
    assert not (output_dir / "promotion_gate_decision.json").exists()


def test_run_professional_evidence_pipeline_rejects_mismatched_config_kind(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 1
    assert not (output_dir / "professional_evidence_pipeline_manifest.json").exists()
