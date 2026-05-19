from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import (
    build_rolling_simulated_live_evidence_bundle,
    write_rolling_simulated_live_evidence_bundle,
)


GENERATED_AT = "2026-05-16T23:55:00Z"


def _component(
    schema_version: str,
    *,
    generated_at: str = "2026-05-16T23:40:00Z",
    decision: str = "pass_for_continued_paper",
    artifact_id: str,
    reasons: list[str] | None = None,
    status: str | None = None,
    checks: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "decision": decision,
        "artifact_id": artifact_id,
        "reasons": reasons or [],
        "checks": checks or {"well_formed": True},
    }
    if status is not None:
        payload["status"] = status
    return payload


def _passing_components() -> dict[str, dict[str, object]]:
    return {
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
            status="pass",
            decision="accepted",
        ),
        "cross_source_parity": _component(
            "cross_source_parity_report.v1",
            artifact_id="parity-20260516",
            status="pass",
            decision="accepted",
        ),
        "venue_rulebook_catalog_freshness": _component(
            "venue_rulebook_catalog_freshness.v1",
            artifact_id="venue-freshness-20260516",
            status="pass",
            decision="accepted",
        ),
        "execution_race_evidence": _component(
            "execution_race_evidence.v1",
            artifact_id="race-evidence-20260516",
            status="pass",
            decision="accepted",
        ),
    }


def test_builds_canonical_rolling_simulated_live_bundle_from_loaded_mappings() -> None:
    components = _passing_components()
    components["derivatives_risk"] = _component(
        "derivatives_risk_evidence.v1",
        artifact_id="derivatives-risk-20260516",
        status="review",
        decision="accepted_with_review",
        reasons=["derivatives_crowding_watch"],
    )

    bundle = build_rolling_simulated_live_evidence_bundle(
        components=components,
        generated_at=GENERATED_AT,
        max_artifact_age_seconds=3600,
    )

    assert bundle["schema_version"] == "rolling_simulated_live_evidence_bundle.v1"
    assert bundle["generated_at"] == GENERATED_AT
    assert bundle["decision"] == "review"
    assert bundle["reason_codes"] == ["derivatives_crowding_watch"]
    assert bundle["checks"] == {
        "all_required_components_present": True,
        "all_components_well_formed": True,
        "all_components_fresh": True,
        "component_identities_unique": True,
        "all_component_statuses_known": True,
    }
    assert [component["component"] for component in bundle["components"]] == [
        "daily_quality_gate",
        "rolling_tca_durability",
        "l2_longitudinal_replay_calibration",
        "cross_source_parity",
        "venue_rulebook_catalog_freshness",
        "execution_race_evidence",
        "derivatives_risk",
    ]
    assert bundle["components"][0]["status"] == "pass"
    assert bundle["components"][0]["source"]["identity"] == "daily-quality-20260516"
    assert bundle["components"][0]["source"]["schema_version"] == "daily_quality_gate_report.v1"
    assert len(bundle["components"][0]["source"]["sha256"]) == 64


def test_builds_from_local_artifact_paths_and_records_file_identity(tmp_path: Path) -> None:
    components = _passing_components()
    paths: dict[str, Path] = {}
    for component, payload in components.items():
        path = tmp_path / f"{component}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        paths[component] = path

    output = tmp_path / "rolling_simulated_live_evidence_bundle.json"
    bundle = write_rolling_simulated_live_evidence_bundle(
        output,
        components=paths,
        generated_at=GENERATED_AT,
        max_artifact_age_seconds=3600,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == bundle
    assert bundle["decision"] == "pass"
    assert bundle["components"][0]["source"]["path"].endswith("daily_quality_gate.json")
    assert bundle["components"][0]["source"]["bytes"] > 0


def test_rolling_tca_bucket_dimension_values_are_allowed_as_strings() -> None:
    components = _passing_components()
    components["rolling_tca_durability"]["windows"] = [
        {
            "window": "1d",
            "buckets": [
                {
                    "bucket": {"dimension": "global", "value": "all"},
                    "decision": "insufficient",
                    "metrics": {"sample_count": 1, "fill_rate": 1.0},
                    "reasons": ["insufficient_bucket_sample_size"],
                },
                {
                    "bucket": {"dimension": "symbol", "value": "BTCUSDT"},
                    "decision": "insufficient",
                    "metrics": {"sample_count": 1, "fill_rate": 1.0},
                    "reasons": ["insufficient_bucket_sample_size"],
                },
            ],
        }
    ]
    components["rolling_tca_durability"]["decision"] = "insufficient"
    components["rolling_tca_durability"]["reasons"] = ["insufficient_bucket_sample_size"]

    bundle = build_rolling_simulated_live_evidence_bundle(
        components=components,
        generated_at=GENERATED_AT,
        max_artifact_age_seconds=3600,
    )

    assert bundle["decision"] == "review"
    assert bundle["reason_codes"] == ["insufficient_bucket_samples"]


def test_component_check_booleans_with_numeric_words_are_allowed() -> None:
    components = _passing_components()
    components["daily_quality_gate"]["checks"] = {
        "latency_distribution_stable": True,
        "sufficient_sample_size": True,
        "well_formed": True,
    }
    components["daily_quality_gate"]["inputs"] = {
        "latency": {
            "baseline_p95_ms": None,
            "current_p95_ms": None,
            "latency_distribution_stable": True,
            "max_p95_shift_pct": None,
        },
        "runtime_safety_gate": {"generated_at": None},
        "tca": {"sample_size": 0, "sufficient_sample_size": False},
    }

    bundle = build_rolling_simulated_live_evidence_bundle(
        components=components,
        generated_at=GENERATED_AT,
        max_artifact_age_seconds=3600,
    )

    assert bundle["decision"] == "pass"


@pytest.mark.parametrize(
    ("component", "mutation", "message"),
    [
        (
            "daily_quality_gate",
            lambda payload: payload.update({"generated_at": "2026-05-16T23:40:00+00:00"}),
            "daily_quality_gate.generated_at must be a canonical UTC timestamp",
        ),
        (
            "daily_quality_gate",
            lambda payload: payload.update({"status": "maybe"}),
            "daily_quality_gate status is unknown",
        ),
        (
            "rolling_tca_durability",
            lambda payload: payload.update({"generated_at": "2026-05-15T23:40:00Z"}),
            "rolling_tca_durability artifact is stale",
        ),
        (
            "cross_source_parity",
            lambda payload: payload.update({"max_mid_bps_diff": True}),
            "cross_source_parity.max_mid_bps_diff must be numeric, not boolean",
        ),
        (
            "execution_race_evidence",
            lambda payload: payload.update({"latency_ms": math.inf}),
            "execution_race_evidence.latency_ms must be finite",
        ),
        (
            "venue_rulebook_catalog_freshness",
            lambda payload: payload.update({"reason_codes": ["not canonical"]}),
            "venue_rulebook_catalog_freshness.reason_codes[0] must be canonical",
        ),
    ],
)
def test_bundle_fails_closed_for_untrusted_component_payloads(
    component: str,
    mutation: object,
    message: str,
) -> None:
    components = _passing_components()
    mutation(components[component])  # type: ignore[operator]

    with pytest.raises(ValueError, match=re.escape(message)):
        build_rolling_simulated_live_evidence_bundle(
            components=components,
            generated_at=GENERATED_AT,
            max_artifact_age_seconds=3600,
        )


def test_bundle_fails_closed_for_missing_required_component() -> None:
    components = _passing_components()
    del components["execution_race_evidence"]

    with pytest.raises(ValueError, match="missing required components: execution_race_evidence"):
        build_rolling_simulated_live_evidence_bundle(
            components=components,
            generated_at=GENERATED_AT,
            max_artifact_age_seconds=3600,
        )


def test_bundle_fails_closed_for_duplicate_component_identity() -> None:
    components = _passing_components()
    components["cross_source_parity"]["artifact_id"] = components["daily_quality_gate"]["artifact_id"]

    with pytest.raises(ValueError, match="duplicate component identity: daily-quality-20260516"):
        build_rolling_simulated_live_evidence_bundle(
            components=components,
            generated_at=GENERATED_AT,
            max_artifact_age_seconds=3600,
        )


def test_bundle_fails_closed_for_malformed_json_artifact(tmp_path: Path) -> None:
    components: dict[str, object] = _passing_components()
    bad_path = tmp_path / "daily_quality_gate.json"
    bad_path.write_text('{"schema_version": "daily_quality_gate_report.v1"', encoding="utf-8")
    components["daily_quality_gate"] = bad_path

    with pytest.raises(ValueError, match="daily_quality_gate artifact JSON is malformed"):
        build_rolling_simulated_live_evidence_bundle(
            components=components,
            generated_at=GENERATED_AT,
            max_artifact_age_seconds=3600,
        )


def test_generate_rolling_simulated_live_bundle_cli_writes_artifact(tmp_path: Path) -> None:
    args = [
        sys.executable,
        "-m",
        "trading_system.generate_rolling_simulated_live_evidence_bundle",
        "--output",
        str(tmp_path / "bundle.json"),
        "--generated-at",
        GENERATED_AT,
        "--max-artifact-age-seconds",
        "3600",
    ]
    for component, payload in _passing_components().items():
        path = tmp_path / f"{component}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        args.extend(["--component", f"{component}={path}"])

    result = subprocess.run(
        args,
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads((tmp_path / "bundle.json").read_text(encoding="utf-8"))
    assert payload["decision"] == "pass"
    assert "ROLLING_SIMULATED_LIVE_EVIDENCE_BUNDLE_JSON" in result.stdout
