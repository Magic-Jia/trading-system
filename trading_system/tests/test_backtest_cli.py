from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest import cli


FIXTURES = Path(__file__).parent / "fixtures" / "backtest"
GENERATED_AT = "2026-05-18T05:00:00Z"


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
