from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.real_local_simulated_live_evidence_chain import (
    build_real_local_simulated_live_evidence_chain_checkpoint,
    write_real_local_simulated_live_evidence_chain_checkpoint,
)


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _evidence_window(*, decision: str = "pass", reason_codes: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "simulated_live_evidence_window.v1",
        "generated_at": "2026-05-16T00:05:00Z",
        "decision": decision,
        "reason_codes": reason_codes or [],
        "checks": {
            "bundle_count": 3,
            "distinct_days": 3,
            "distinct_sessions": 3,
            "minimum_distinct_sessions_met": True,
            "session_identities_unique": True,
            "observed_timestamps_unique": True,
            "evaluated_timestamps_unique": True,
            "generated_at_monotonic": True,
            "as_of_monotonic": True,
            "all_bundles_pass": True,
            "all_required_bundle_components_present": True,
        },
        "bundles": [
            {"session_id": "sim-live-20260514", "day": "2026-05-14", "decision": "pass"},
            {"session_id": "sim-live-20260515", "day": "2026-05-15", "decision": "pass"},
            {"session_id": "sim-live-20260516", "day": "2026-05-16", "decision": "pass"},
        ],
    }


def _scorecard_trend(*, decision: str = "pass", reasons: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "promotion_readiness_scorecard_trend.v1",
        "mode": "simulated_live",
        "generated_at": "2026-05-16T00:06:00Z",
        "decision": decision,
        "reasons": reasons or [],
        "checks": {
            "sample_window_sufficient": True,
            "scorecards_well_formed": True,
            "generated_at_monotonic": True,
            "scorecard_identities_unique": True,
            "score_deterioration_within_threshold": True,
            "repeated_blockers_absent": True,
        },
        "sample_window": {"observed_count": 3, "required_count": 3},
        "score_trend": {"first": 90.0, "latest": 92.0, "delta": 2.0, "deteriorated": False},
    }


def _calibration_recommendation(*, decision: str = "no_change") -> dict[str, object]:
    return {
        "schema_version": "calibration_assumption_update_recommendation.v1",
        "generated_at": "2026-05-16T00:07:00Z",
        "decision": decision,
        "recommended_assumption_updates": [] if decision == "no_change" else [
            {
                "field": "expected_slippage_bps",
                "current": 2.0,
                "recommended": 3.0,
                "observed": 3.0,
                "reason_codes": ["observed_slippage_above_current_assumption"],
            }
        ],
        "rationale": {
            "reason_codes": ["observed_calibration_within_current_assumptions"]
            if decision == "no_change"
            else ["review_required_for_assumption_update"],
            "notes": ["This artifact is review input only."],
        },
        "assumptions_file_mutation": "forbidden",
        "side_effect_boundary": "offline_local_only",
    }


def test_builds_happy_real_local_chain_checkpoint_from_local_artifacts(tmp_path: Path) -> None:
    window = _write_json(tmp_path / "window.json", _evidence_window())
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend())
    calibration = _write_json(tmp_path / "calibration.json", _calibration_recommendation())

    checkpoint = build_real_local_simulated_live_evidence_chain_checkpoint(
        evidence_window_path=window,
        scorecard_trend_path=trend,
        calibration_recommendation_path=calibration,
        generated_at="2026-05-16T00:10:00Z",
    )

    assert checkpoint["schema_version"] == "real_local_simulated_live_evidence_chain_checkpoint.v1"
    assert checkpoint["source_mode"] == "simulated_live_local"
    assert checkpoint["source_mode"] != "replay"
    assert checkpoint["final_chain_decision"] == "pass"
    assert checkpoint["final_reason_codes"] == []
    assert checkpoint["evidence_window"]["decision"] == "pass"
    assert checkpoint["scorecard_trend"]["decision"] == "pass"
    assert checkpoint["calibration"]["recommendation_decision"] == "no_change"
    assert checkpoint["calibration"]["human_review_summary"]["required"] is False
    assert checkpoint["input_artifact_paths"] == {
        "calibration_recommendation": str(calibration),
        "evidence_window": str(window),
        "scorecard_trend": str(trend),
    }
    assert checkpoint["lineage"]["required_artifacts_present"] is True
    assert len(checkpoint["lineage"]["artifacts"]["evidence_window"]["sha256"]) == 64
    assert checkpoint["side_effect_boundary"] == "offline_local_filesystem_only"


def test_missing_required_artifact_fails_closed(tmp_path: Path) -> None:
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend())

    checkpoint = build_real_local_simulated_live_evidence_chain_checkpoint(
        evidence_window_path=tmp_path / "missing-window.json",
        scorecard_trend_path=trend,
        generated_at="2026-05-16T00:10:00Z",
    )

    assert checkpoint["final_chain_decision"] == "reject"
    assert "missing_required_artifact" in checkpoint["final_reason_codes"]
    assert checkpoint["lineage"]["required_artifacts_present"] is False
    assert checkpoint["evidence_window"]["decision"] == "reject"
    assert checkpoint["evidence_window"]["reason_codes"] == ["missing_required_artifact"]


def test_duplicate_or_non_monotonic_window_input_is_surfaced(tmp_path: Path) -> None:
    window = _write_json(
        tmp_path / "window.json",
        _evidence_window(
            decision="hold",
            reason_codes=["duplicate_session_identity", "non_monotonic_generated_at"],
        ),
    )
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend())

    checkpoint = build_real_local_simulated_live_evidence_chain_checkpoint(
        evidence_window_path=window,
        scorecard_trend_path=trend,
        generated_at="2026-05-16T00:10:00Z",
    )

    assert checkpoint["final_chain_decision"] == "hold"
    assert "evidence_window_hold" in checkpoint["final_reason_codes"]
    assert "duplicate_session_identity" in checkpoint["final_reason_codes"]
    assert "non_monotonic_generated_at" in checkpoint["final_reason_codes"]
    assert "duplicate_session_identity" in checkpoint["evidence_window"]["reason_codes"]
    assert "non_monotonic_generated_at" in checkpoint["evidence_window"]["reason_codes"]
    assert checkpoint["evidence_window"]["checks"]["generated_at_monotonic"] is True


def test_stale_required_artifact_fails_closed(tmp_path: Path) -> None:
    window = _write_json(tmp_path / "window.json", _evidence_window())
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend())

    checkpoint = build_real_local_simulated_live_evidence_chain_checkpoint(
        evidence_window_path=window,
        scorecard_trend_path=trend,
        generated_at="2026-05-18T00:10:00Z",
        max_required_artifact_age_seconds=60,
    )

    assert checkpoint["final_chain_decision"] == "reject"
    assert "stale_required_artifact" in checkpoint["final_reason_codes"]
    assert checkpoint["lineage"]["artifacts"]["evidence_window"]["error"] == "stale_required_artifact"


def test_cli_writes_machine_readable_local_chain_checkpoint(tmp_path: Path) -> None:
    window = _write_json(tmp_path / "window.json", _evidence_window())
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend(decision="hold", reasons=["repeated_blocker"]))
    output = tmp_path / "checkpoint.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_real_local_simulated_live_evidence_chain",
            "--evidence-window",
            str(window),
            "--scorecard-trend",
            str(trend),
            "--output",
            str(output),
            "--generated-at",
            "2026-05-16T00:10:00Z",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    checkpoint = json.loads(output.read_text(encoding="utf-8"))
    assert checkpoint["source_mode"] == "simulated_live_local"
    assert checkpoint["final_chain_decision"] == "hold"
    assert checkpoint["scorecard_trend"]["reason_codes"] == ["repeated_blocker"]
    assert re.search(r"REAL_LOCAL_SIMULATED_LIVE_EVIDENCE_CHAIN_JSON.*\"decision\": \"hold\"", result.stdout)


def test_writes_checkpoint_without_mutating_inputs(tmp_path: Path) -> None:
    window = _write_json(tmp_path / "window.json", _evidence_window())
    trend = _write_json(tmp_path / "trend.json", _scorecard_trend())
    before = {path: path.read_text(encoding="utf-8") for path in (window, trend)}

    payload = write_real_local_simulated_live_evidence_chain_checkpoint(
        tmp_path / "checkpoint.json",
        evidence_window_path=window,
        scorecard_trend_path=trend,
        generated_at="2026-05-16T00:10:00Z",
    )

    assert json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8")) == payload
    assert {path: path.read_text(encoding="utf-8") for path in (window, trend)} == before
