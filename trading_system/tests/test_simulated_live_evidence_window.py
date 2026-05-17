from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.simulated_live_evidence_window import (
    build_simulated_live_evidence_window_report,
    write_simulated_live_evidence_window_report,
)


def _component(component: str, *, status: str = "pass", reason_codes: list[str] | None = None) -> dict[str, object]:
    return {
        "component": component,
        "status": status,
        "generated_at": "2026-05-16T23:40:00Z",
        "reason_codes": reason_codes or [],
        "source": {
            "identity": f"{component}-20260516",
            "schema_version": f"{component}.v1",
            "sha256": "0" * 64,
        },
    }


def _bundle(
    day: str,
    *,
    session_id: str | None = None,
    generated_at: str | None = None,
    observed_at: str | None = None,
    evaluated_at: str | None = None,
    decision: str = "pass",
    reason_codes: list[str] | None = None,
    components: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    suffix = day.replace("-", "")
    return {
        "schema_version": "rolling_simulated_live_evidence_bundle.v1",
        "session_id": session_id or f"sim-live-{suffix}",
        "day": day,
        "observed_at": observed_at or f"{day}T23:50:00Z",
        "evaluated_at": evaluated_at or f"{day}T23:55:00Z",
        "generated_at": generated_at or f"{day}T23:59:00Z",
        "decision": decision,
        "reason_codes": reason_codes or [],
        "checks": {"all_required_components_present": True},
        "components": components
        or [
            _component("daily_quality_gate"),
            _component("rolling_tca_durability"),
            _component("l2_longitudinal_replay_calibration"),
            _component("cross_source_parity"),
            _component("venue_rulebook_catalog_freshness"),
            _component("execution_race_evidence"),
        ],
    }


def test_builds_passing_multi_day_simulated_live_evidence_window() -> None:
    report = build_simulated_live_evidence_window_report(
        [
            _bundle("2026-05-14"),
            _bundle("2026-05-15"),
            _bundle("2026-05-16"),
        ],
        generated_at="2026-05-17T00:05:00Z",
        min_distinct_sessions=3,
    )

    assert report["schema_version"] == "simulated_live_evidence_window.v1"
    assert report["generated_at"] == "2026-05-17T00:05:00Z"
    assert report["decision"] == "pass"
    assert report["reason_codes"] == []
    assert report["checks"] == {
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
    }
    assert [entry["session_id"] for entry in report["bundles"]] == [
        "sim-live-20260514",
        "sim-live-20260515",
        "sim-live-20260516",
    ]


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (
            lambda bundles: bundles[1].__setitem__("session_id", bundles[0]["session_id"]),
            "duplicate_session_identity",
        ),
        (
            lambda bundles: bundles[2].__setitem__("day", bundles[0]["day"]),
            "duplicate_day_identity",
        ),
        (
            lambda bundles: bundles[2].__setitem__("observed_at", bundles[0]["observed_at"]),
            "duplicate_observed_at",
        ),
        (
            lambda bundles: bundles[2].__setitem__("evaluated_at", bundles[0]["evaluated_at"]),
            "duplicate_evaluated_at",
        ),
        (
            lambda bundles: bundles[2].__setitem__("generated_at", "2026-05-15T23:58:00Z"),
            "non_monotonic_generated_at",
        ),
        (
            lambda bundles: bundles[2].__setitem__("evaluated_at", "2026-05-15T23:54:00Z"),
            "non_monotonic_as_of",
        ),
        (
            lambda bundles: bundles[1].__setitem__("generated_at", "2026-05-15T23:59:00+00:00"),
            "malformed_bundle_timestamp",
        ),
    ],
)
def test_window_fails_closed_for_continuity_breaks(mutation: object, reason_code: str) -> None:
    bundles = [_bundle("2026-05-14"), _bundle("2026-05-15"), _bundle("2026-05-16")]
    mutation(bundles)  # type: ignore[operator]

    report = build_simulated_live_evidence_window_report(
        bundles,
        generated_at="2026-05-17T00:05:00Z",
        min_distinct_sessions=3,
    )

    assert report["decision"] == "hold"
    assert reason_code in report["reason_codes"]


def test_window_fails_closed_when_minimum_distinct_sessions_not_met() -> None:
    report = build_simulated_live_evidence_window_report(
        [_bundle("2026-05-15"), _bundle("2026-05-16")],
        generated_at="2026-05-17T00:05:00Z",
        min_distinct_sessions=3,
    )

    assert report["decision"] == "hold"
    assert "insufficient_distinct_sessions" in report["reason_codes"]
    assert report["checks"]["minimum_distinct_sessions_met"] is False


def test_window_propagates_missing_and_hold_bundle_components() -> None:
    held_bundle = _bundle(
        "2026-05-15",
        decision="hold",
        reason_codes=["race_condition_hold"],
        components=[
            _component("daily_quality_gate"),
            _component("rolling_tca_durability"),
            _component("l2_longitudinal_replay_calibration"),
            _component("cross_source_parity"),
            _component("venue_rulebook_catalog_freshness"),
            _component("execution_race_evidence", status="hold", reason_codes=["race_condition_hold"]),
        ],
    )
    missing_component_bundle = _bundle("2026-05-16")
    missing_component_bundle["components"] = [_component("daily_quality_gate")]

    report = build_simulated_live_evidence_window_report(
        [_bundle("2026-05-14"), held_bundle, missing_component_bundle],
        generated_at="2026-05-17T00:05:00Z",
        min_distinct_sessions=3,
    )

    assert report["decision"] == "hold"
    assert "bundle_decision_hold" in report["reason_codes"]
    assert "race_condition_hold" in report["reason_codes"]
    assert "missing_bundle_component" in report["reason_codes"]
    assert report["checks"]["all_bundles_pass"] is False
    assert report["checks"]["all_required_bundle_components_present"] is False
    assert report["bundles"][1]["component_failures"] == [
        {
            "component": "execution_race_evidence",
            "status": "hold",
            "reason_codes": ["race_condition_hold"],
        }
    ]


def test_write_window_report_from_local_bundle_paths(tmp_path: Path) -> None:
    paths = []
    for day in ("2026-05-14", "2026-05-15", "2026-05-16"):
        path = tmp_path / f"{day}.json"
        path.write_text(json.dumps(_bundle(day)), encoding="utf-8")
        paths.append(path)

    output = tmp_path / "simulated_live_evidence_window.json"
    report = write_simulated_live_evidence_window_report(
        output,
        bundles=paths,
        generated_at="2026-05-17T00:05:00Z",
        min_distinct_sessions=3,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["decision"] == "pass"
    assert report["bundles"][0]["source"]["path"].endswith("2026-05-14.json")
    assert len(report["bundles"][0]["source"]["sha256"]) == 64


def test_generate_window_cli_writes_fail_closed_report(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    third = tmp_path / "third.json"
    first.write_text(json.dumps(_bundle("2026-05-14")), encoding="utf-8")
    second.write_text(json.dumps(_bundle("2026-05-15")), encoding="utf-8")
    third.write_text(json.dumps(_bundle("2026-05-16", generated_at="2026-05-15T23:59:00Z")), encoding="utf-8")
    output = tmp_path / "window.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_simulated_live_evidence_window",
            "--output",
            str(output),
            "--generated-at",
            "2026-05-17T00:05:00Z",
            "--min-distinct-sessions",
            "3",
            "--bundle",
            str(first),
            "--bundle",
            str(second),
            "--bundle",
            str(third),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["decision"] == "hold"
    assert "non_monotonic_generated_at" in report["reason_codes"]
    assert re.search(r"SIMULATED_LIVE_EVIDENCE_WINDOW_JSON.*\"decision\": \"hold\"", result.stdout)
