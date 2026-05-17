from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import REQUIRED_COMPONENTS
from trading_system.app.reporting.simulated_live_artifact_inventory import ROLLING_BUNDLE_COMPONENT_ARTIFACTS
from trading_system.generate_simulated_live_cadence_runner import (
    RUNTIME_CALIBRATION_FEEDBACK_NAME,
    RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME,
    run_simulated_live_cadence,
)


GENERATED_AT = "2026-05-16T23:55:00Z"
COMPONENT_FILENAMES = {spec["artifact"]: spec["path"] for spec in ROLLING_BUNDLE_COMPONENT_ARTIFACTS}


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _component(
    schema_version: str,
    *,
    artifact_id: str,
    generated_at: str = "2026-05-16T23:40:00Z",
    decision: str = "accepted",
    status: str = "pass",
    reasons: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "artifact_id": artifact_id,
        "decision": decision,
        "status": status,
        "reason_codes": reasons or [],
        "checks": {"well_formed": True},
    }


def _scorecard_component(
    *,
    as_of: str,
    coverage_score: float = 0.95,
    sample_count: int = 60,
    duration_hours: float | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of": as_of,
        "coverage_score": coverage_score,
        "sample_count": sample_count,
        "status": "pass",
        "reason_codes": [],
    }
    if duration_hours is not None:
        payload["duration_hours"] = duration_hours
    return payload


def _write_complete_runtime(runtime_dir: Path) -> None:
    components = {
        "daily_quality_gate": _component(
            "daily_quality_gate_report.v1",
            artifact_id="daily-quality-20260516",
            decision="pass_for_continued_paper",
        ),
        "rolling_tca_durability": _component(
            "rolling_tca_durability_report.v1",
            artifact_id="rolling-tca-20260516",
            decision="durable",
        ),
        "l2_longitudinal_replay_calibration": _component(
            "l2_longitudinal_replay_calibration.v1",
            artifact_id="l2-replay-20260516",
        ),
        "cross_source_parity": _component(
            "cross_source_parity_report.v1",
            artifact_id="parity-20260516",
        ),
        "venue_rulebook_catalog_freshness": _component(
            "venue_rulebook_catalog_freshness.v1",
            artifact_id="venue-freshness-20260516",
        ),
        "execution_race_evidence": _component(
            "execution_race_evidence.v1",
            artifact_id="race-evidence-20260516",
        ),
    }
    for component, payload in components.items():
        _write_json(runtime_dir / COMPONENT_FILENAMES[component], payload)

    scorecard_evidence = {
        "data_quality": _scorecard_component(as_of="2026-05-16T22:00:00Z"),
        "execution_realism": _scorecard_component(as_of="2026-05-16T22:00:00Z"),
        "venue_rulebook_coverage": _scorecard_component(as_of="2026-05-15T23:00:00Z", sample_count=8),
        "derivatives_risk": _scorecard_component(as_of="2026-05-16T22:00:00Z"),
        "cross_source_parity": {
            **_scorecard_component(as_of="2026-05-16T22:00:00Z", sample_count=40),
            "max_parity_drift_bps": 1.25,
        },
        "live_sim_durability": _scorecard_component(as_of="2026-05-16T22:00:00Z", duration_hours=96.0),
    }
    _write_json(runtime_dir / RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME, scorecard_evidence)
    _write_json(
        runtime_dir / RUNTIME_CALIBRATION_FEEDBACK_NAME,
        {
            "schema_version": "calibration_feedback_artifact.v1",
            "generated_at": "2026-05-16T23:45:00Z",
            "decision": "ready",
            "reasons": [],
            "side_effect_boundary": "offline_local_only",
            "strategy_config_mutation": "forbidden",
        },
    )


def test_cadence_runner_writes_fail_closed_result_for_missing_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"

    result = run_simulated_live_cadence(
        runtime_optimization_dir=tmp_path / "runtime",
        output_dir=output_dir,
        generated_at=GENERATED_AT,
    )

    persisted = json.loads((output_dir / "simulated_live_cadence_result.json").read_text(encoding="utf-8"))
    assert persisted == result
    assert result["decision"] == "hold"
    assert result["status"] == "fail_closed"
    assert result["missing_required_artifacts"] == [
        f"{component}:{COMPONENT_FILENAMES[component]}" for component in REQUIRED_COMPONENTS
    ] + [f"promotion_readiness_evidence:{RUNTIME_PROMOTION_READINESS_EVIDENCE_NAME}"]
    assert result["steps"]["rolling_simulated_live_evidence_bundle"]["status"] == "skipped"
    assert result["artifacts"] == {
        "simulated_live_cadence_result": {
            "path": str(output_dir / "simulated_live_cadence_result.json"),
            "sha256": result["artifacts"]["simulated_live_cadence_result"]["sha256"],
            "provenance": {
                "source": "offline_local_filesystem_only",
                "generated_by": "run_simulated_live_cadence",
                "hash_scope": "payload_before_self_reference",
            },
        }
    }
    assert not (output_dir / "rolling_simulated_live_evidence_bundle.json").exists()


def test_cadence_runner_rejects_replay_provenance_as_local_simulated_live(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _write_complete_runtime(runtime_dir)
    _write_json(
        runtime_dir / "rolling_simulated_live_evidence_bundle.json",
        {
            "schema_version": "rolling_simulated_live_evidence_bundle.v1",
            "source_mode": "replay",
            "generated_at": "2026-05-16T23:40:00Z",
            "replay_lineage": {"replay_source_id": "replay-1"},
        },
    )

    result = run_simulated_live_cadence(
        runtime_optimization_dir=runtime_dir,
        output_dir=tmp_path / "out",
        generated_at=GENERATED_AT,
    )

    assert result["decision"] == "hold"
    assert result["status"] == "fail_closed"
    assert "replay_provenance_not_local_simulated_live:rolling_simulated_live_evidence_bundle.json" in result["blocking_reasons"]
    assert result["steps"]["rolling_simulated_live_evidence_bundle"]["status"] == "skipped"
    assert "promotion_gate_decision" not in result["artifacts"]


def test_cadence_runner_generates_successful_chain_from_runtime_fixture(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    output_dir = tmp_path / "out"
    _write_complete_runtime(runtime_dir)

    result = run_simulated_live_cadence(
        runtime_optimization_dir=runtime_dir,
        output_dir=output_dir,
        generated_at=GENERATED_AT,
    )

    assert result["schema_version"] == "simulated_live_cadence_result.v1"
    assert result["status"] == "completed"
    assert result["decision"] == "candidate_for_paper_promotion"
    assert result["blocking_reasons"] == []
    assert result["missing_required_artifacts"] == []
    assert list(result["steps"]) == [
        "rolling_simulated_live_evidence_bundle",
        "simulated_live_evidence_window",
        "promotion_readiness_scorecard",
        "promotion_readiness_scorecard_trend",
        "real_local_simulated_live_evidence_chain_checkpoint",
        "promotion_gate_decision",
    ]
    assert all(step["status"] == "generated" for step in result["steps"].values())
    for artifact_name, artifact in result["artifacts"].items():
        path = Path(artifact["path"])
        assert path.exists(), artifact_name
        assert len(artifact["sha256"]) == 64
        assert artifact["provenance"]["source"] == "offline_local_filesystem_only"
    assert json.loads((output_dir / "promotion_gate_decision.json").read_text(encoding="utf-8"))["decision"] == (
        "candidate_for_paper_promotion"
    )


def test_cadence_runner_cli_writes_generated_output_shape(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    output_dir = tmp_path / "out"
    _write_complete_runtime(runtime_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_simulated_live_cadence_runner",
            "--runtime-optimization-dir",
            str(runtime_dir),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads((output_dir / "simulated_live_cadence_result.json").read_text(encoding="utf-8"))
    assert result["generated_at"] == GENERATED_AT
    assert result["runtime_optimization_dir"] == str(runtime_dir)
    assert result["output_dir"] == str(output_dir)
    assert "SIMULATED_LIVE_CADENCE_RESULT_JSON" in completed.stdout
