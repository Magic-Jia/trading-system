from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.longitudinal_promotion_decision_archive import (
    build_longitudinal_promotion_decision_archive,
    write_longitudinal_promotion_decision_archive,
)


def _decision(
    *,
    generated_at: str = "2026-05-17T00:30:00Z",
    decision: str = "candidate_for_paper_promotion",
    blocking_reasons: list[str] | None = None,
    source_mode: dict[str, object] | None = None,
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "promotion_gate_decision.v1",
        "generated_at": generated_at,
        "decision": decision,
        "blocking_reasons": blocking_reasons or [],
        "checks": {
            "simulated_live_evidence_window": {"status": "pass", "blocking_reasons": [], "errors": [], "warnings": []},
            "promotion_readiness_scorecard_trend": {"status": "pass", "blocking_reasons": [], "errors": [], "warnings": []},
            "calibration": {
                "status": "pass",
                "blocking_reasons": [],
                "artifact_count": 1,
                "artifacts": [],
                "assumptions_file_mutation": "forbidden",
                "strategy_config_mutation": "forbidden",
            },
            "identity_continuity": {"non_monotonic_or_duplicate_inputs_present": False, "warnings": []},
        },
        "included_artifact_identities": [
            {
                "artifact_type": "simulated_live_evidence_window",
                "schema_version": "simulated_live_evidence_window.v1",
                "generated_at": "2026-05-17T00:05:00Z",
                "source": {"sha256": "window-sha"},
                "decision": "pass",
            },
            {
                "artifact_type": "promotion_readiness_scorecard_trend",
                "schema_version": "promotion_readiness_scorecard_trend.v1",
                "generated_at": "2026-05-17T00:10:00Z",
                "source": {"sha256": "trend-sha"},
                "decision": "pass",
                "mode": "simulated_live",
            },
        ],
        "human_review_required": decision != "candidate_for_paper_promotion",
        "source_mode": source_mode
        or {
            "mode": "simulated_live",
            "side_effect_boundary": "offline_local_filesystem_only",
            "real_orders": "forbidden",
            "testnet_orders": "forbidden",
            "exchange_api_calls": "forbidden",
            "credential_use": "forbidden",
        },
        "provenance": provenance
        or {
            "input_artifact_count": 3,
            "decision_policy": "fail_closed",
            "promotion_scope": "paper_promotion_candidate_only",
        },
    }


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_empty_archive_holds_fail_closed() -> None:
    archive = build_longitudinal_promotion_decision_archive([], generated_at="2026-05-18T00:00:00Z")

    assert archive["schema_version"] == "longitudinal_promotion_decision_archive.v1"
    assert archive["generated_at"] == "2026-05-18T00:00:00Z"
    assert archive["decision"] == "hold"
    assert archive["latest_decision"] is None
    assert archive["decisions"] == []
    assert archive["counts_by_decision"] == {}
    assert archive["reason_codes"] == ["empty_decision_archive"]


def test_rejects_replay_and_malformed_decisions_as_promotion_evidence(tmp_path: Path) -> None:
    replay = _decision(source_mode={**_decision()["source_mode"], "mode": "replay"})  # type: ignore[arg-type]
    malformed = {"schema_version": "promotion_gate_decision.v1", "decision": "candidate_for_paper_promotion"}

    replay_path = _write_json(tmp_path / "replay.json", replay)
    malformed_path = _write_json(tmp_path / "malformed.json", malformed)

    archive = build_longitudinal_promotion_decision_archive(
        [replay_path, malformed_path],
        generated_at="2026-05-18T00:00:00Z",
    )

    assert archive["decision"] == "reject"
    assert archive["decisions"] == []
    assert "source_mode_invalid" in archive["reason_codes"]
    assert "generated_at_invalid" in archive["reason_codes"]
    assert archive["rejected_sources"][0]["path"].endswith("replay.json")
    assert archive["rejected_sources"][1]["path"].endswith("malformed.json")


def test_summarizes_repeated_blockers_across_decisions(tmp_path: Path) -> None:
    first = _decision(
        generated_at="2026-05-17T00:30:00Z",
        decision="hold",
        blocking_reasons=["calibration:missing_calibration_artifact", "trend:score_deterioration"],
    )
    second = _decision(
        generated_at="2026-05-18T00:30:00Z",
        decision="hold",
        blocking_reasons=["calibration:missing_calibration_artifact"],
    )

    archive = build_longitudinal_promotion_decision_archive(
        [_write_json(tmp_path / "day-1.json", first), _write_json(tmp_path / "day-2.json", second)],
        generated_at="2026-05-19T00:00:00Z",
    )

    assert archive["decision"] == "hold"
    assert archive["counts_by_decision"] == {"hold": 2}
    assert archive["repeated_blockers"] == [
        {
            "reason": "calibration:missing_calibration_artifact",
            "count": 2,
            "first_seen_at": "2026-05-17T00:30:00Z",
            "latest_seen_at": "2026-05-18T00:30:00Z",
        }
    ]


def test_candidate_latest_summary_and_source_hashes_are_deterministic(tmp_path: Path) -> None:
    hold = _write_json(tmp_path / "2026-05-17.json", _decision(generated_at="2026-05-17T00:30:00Z", decision="hold", blocking_reasons=["trend:score_deterioration"]))
    candidate = _write_json(tmp_path / "2026-05-18.json", _decision(generated_at="2026-05-18T00:30:00Z"))

    archive = build_longitudinal_promotion_decision_archive(
        [candidate, hold],
        generated_at="2026-05-19T00:00:00Z",
    )

    assert archive["decision"] == "candidate_for_paper_promotion"
    assert archive["latest_decision"] == {
        "identity": archive["decisions"][1]["identity"],
        "generated_at": "2026-05-18T00:30:00Z",
        "decision": "candidate_for_paper_promotion",
        "blocking_reasons": [],
        "source_sha256": archive["decisions"][1]["source_sha256"],
        "source_path": str(candidate),
    }
    assert archive["first_decision_at"] == "2026-05-17T00:30:00Z"
    assert archive["latest_decision_at"] == "2026-05-18T00:30:00Z"
    assert archive["counts_by_decision"] == {"hold": 1, "candidate_for_paper_promotion": 1}
    assert [row["generated_at"] for row in archive["decisions"]] == [
        "2026-05-17T00:30:00Z",
        "2026-05-18T00:30:00Z",
    ]
    assert re.fullmatch(r"[0-9a-f]{64}", archive["source_artifacts"][0]["sha256"])


def test_duplicate_decision_identity_rejection(tmp_path: Path) -> None:
    first = _write_json(tmp_path / "a.json", _decision())
    duplicate = _write_json(tmp_path / "b.json", _decision())

    archive = build_longitudinal_promotion_decision_archive(
        [first, duplicate],
        generated_at="2026-05-18T00:00:00Z",
    )

    assert archive["decision"] == "reject"
    assert archive["decisions"] == []
    assert archive["reason_codes"] == ["duplicate_decision_identity"]


def test_write_report_and_cli_accept_paths_or_directory(tmp_path: Path) -> None:
    input_dir = tmp_path / "decisions"
    input_dir.mkdir()
    _write_json(input_dir / "day-1.json", _decision(generated_at="2026-05-17T00:30:00Z", decision="hold", blocking_reasons=["trend:score_deterioration"]))
    _write_json(input_dir / "day-2.json", _decision(generated_at="2026-05-18T00:30:00Z"))
    output = tmp_path / "archive.json"

    payload = write_longitudinal_promotion_decision_archive(
        output,
        input_dir=input_dir,
        generated_at="2026-05-19T00:00:00Z",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert payload["latest_decision"]["decision"] == "candidate_for_paper_promotion"

    cli_output = tmp_path / "cli-archive.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_longitudinal_promotion_decision_archive",
            "--input-dir",
            str(input_dir),
            "--output",
            str(cli_output),
            "--generated-at",
            "2026-05-19T00:00:00Z",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(cli_output.read_text(encoding="utf-8"))["decision"] == "candidate_for_paper_promotion"
    assert re.search(r"LONGITUDINAL_PROMOTION_DECISION_ARCHIVE_JSON.*candidate_for_paper_promotion", result.stdout)
