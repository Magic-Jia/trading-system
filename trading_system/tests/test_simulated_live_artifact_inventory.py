from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.simulated_live_artifact_inventory import (
    ROLLING_BUNDLE_COMPONENT_ARTIFACTS,
    build_simulated_live_artifact_inventory_report,
    write_simulated_live_artifact_inventory_report,
)


GENERATED_AT = "2026-05-17T01:05:00Z"


def _write_artifact(path: Path, *, schema_version: str, generated_at: str = "2026-05-17T01:00:00Z") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "generated_at": generated_at,
                "decision": "pass",
                "artifact_id": path.stem,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _populate_all_required_artifacts(root: Path) -> None:
    for artifact in ROLLING_BUNDLE_COMPONENT_ARTIFACTS:
        _write_artifact(root / artifact["path"], schema_version=str(artifact["schema_version"]))
    _write_artifact(
        root / "rolling_simulated_live_evidence_bundle.json",
        schema_version="rolling_simulated_live_evidence_bundle.v1",
    )
    _write_artifact(root / "simulated_live_evidence_window.json", schema_version="simulated_live_evidence_window.v1")
    _write_artifact(
        root / "promotion_readiness_scorecard_trend.json",
        schema_version="promotion_readiness_scorecard_trend.v1",
    )
    _write_artifact(
        root / "real_local_simulated_live_evidence_chain_checkpoint.json",
        schema_version="real_local_simulated_live_evidence_chain_checkpoint.v1",
    )
    _write_artifact(root / "promotion_gate_decision.json", schema_version="promotion_gate_decision.v1")


def test_inventory_passes_when_all_phase9_required_artifacts_are_present(tmp_path: Path) -> None:
    _populate_all_required_artifacts(tmp_path)

    report = build_simulated_live_artifact_inventory_report(tmp_path, generated_at=GENERATED_AT)

    assert report["schema_version"] == "simulated_live_artifact_inventory.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["source_mode"] == "simulated_live_local"
    assert report["decision"] == "pass"
    assert report["reason_codes"] == []
    assert report["missing_artifacts"] == []
    assert {artifact["artifact"] for artifact in report["required_artifacts"]} == {
        artifact["artifact"] for artifact in report["present_artifacts"]
    }
    assert report["checks"] == {
        "runtime_directory_safe": True,
        "all_required_artifacts_present": True,
        "all_required_artifacts_well_formed": True,
    }
    assert report["side_effect_boundary"] == {
        "real_orders": "forbidden",
        "testnet_orders": "forbidden",
        "exchange_api_calls": "forbidden",
        "credential_use": "forbidden",
    }


def test_inventory_holds_when_required_artifacts_are_missing(tmp_path: Path) -> None:
    _write_artifact(tmp_path / "daily_quality_gate_report.json", schema_version="daily_quality_gate_report.v1")

    report = build_simulated_live_artifact_inventory_report(tmp_path, generated_at=GENERATED_AT)

    assert report["decision"] == "hold"
    assert "missing_required_artifacts" in report["reason_codes"]
    assert "runtime_directory_missing_required_phase9_artifacts" in report["reason_codes"]
    missing_names = {artifact["artifact"] for artifact in report["missing_artifacts"]}
    assert "rolling_tca_durability" in missing_names
    assert "promotion_gate_decision" in missing_names
    assert "daily_quality_gate" not in missing_names
    assert report["checks"]["all_required_artifacts_present"] is False


def test_inventory_rejects_malformed_and_unsafe_artifact_paths(tmp_path: Path) -> None:
    root = tmp_path / "optimization"
    root.mkdir()
    _populate_all_required_artifacts(root)
    (root / "promotion_gate_decision.json").write_text("{not-json", encoding="utf-8")

    report = build_simulated_live_artifact_inventory_report(
        root,
        generated_at=GENERATED_AT,
        extra_required_artifacts=[
            {
                "artifact": "unsafe_escape",
                "path": "../unsafe.json",
                "cadence_stage": "promotion_gate",
                "schema_version": "unsafe.v1",
            }
        ],
    )

    assert report["decision"] == "reject"
    assert "malformed_required_artifact" in report["reason_codes"]
    assert "unsafe_required_artifact_path" in report["reason_codes"]
    assert any(artifact["artifact"] == "promotion_gate_decision" for artifact in report["malformed_artifacts"])
    assert any(artifact["artifact"] == "unsafe_escape" for artifact in report["missing_artifacts"])
    assert report["checks"]["runtime_directory_safe"] is True
    assert report["checks"]["all_required_artifacts_well_formed"] is False


def test_inventory_rejects_unsafe_runtime_directory_and_noncanonical_generated_at(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="generated_at must be a canonical UTC timestamp"):
        build_simulated_live_artifact_inventory_report(tmp_path, generated_at="2026-05-17T01:05:00+00:00")

    report = build_simulated_live_artifact_inventory_report(
        tmp_path / "missing" / ".." / "optimization",
        generated_at=GENERATED_AT,
    )

    assert report["decision"] == "reject"
    assert "unsafe_runtime_directory" in report["reason_codes"]
    assert report["checks"]["runtime_directory_safe"] is False


def test_write_report_and_cli_scan_default_runtime_directory(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    optimization_dir = runtime_root / "paper" / "paper" / "optimization"
    _populate_all_required_artifacts(optimization_dir)
    output = tmp_path / "inventory.json"

    payload = write_simulated_live_artifact_inventory_report(
        output,
        optimization_dir,
        generated_at=GENERATED_AT,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == payload

    cli_output = tmp_path / "cli-inventory.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_simulated_live_artifact_inventory",
            "--runtime-root",
            str(runtime_root),
            "--output",
            str(cli_output),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(cli_output.read_text(encoding="utf-8"))["decision"] == "pass"
    assert re.search(r"SIMULATED_LIVE_ARTIFACT_INVENTORY_JSON.*simulated_live_local", result.stdout)
