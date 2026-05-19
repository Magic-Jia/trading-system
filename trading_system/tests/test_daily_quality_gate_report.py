from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.reporting.daily_quality_gate_report import (
    build_daily_quality_gate_report,
    build_quality_gate_alert_hold_workflow,
    write_quality_gate_alert_hold_workflow,
)
from trading_system.reporting.testnet_daily_report import build_report_payload, render_markdown


def _passing_inputs() -> dict[str, object]:
    return {
        "evidence_bundle": {"verified": True, "manifest_present": True},
        "drift": {
            "checks": {
                "paper_live_shadow_material_drift_absent": True,
                "paper_live_shadow_drift_contract_schema_valid": True,
            }
        },
        "reconciliation": {
            "checks": {
                "execution_event_chain_met": True,
                "order_position_reconciliation_met": True,
            }
        },
        "tca": {
            "sample_size": 42,
            "p95_slippage_bps": 4.0,
            "max_p95_slippage_bps": 5.0,
        },
        "latency": {
            "current_p95_ms": 120.0,
            "baseline_p95_ms": 100.0,
            "max_p95_shift_pct": 0.25,
        },
        "freshness": {
            "max_age_seconds": 3600,
            "items": {
                "evidence_bundle": {"age_seconds": 60},
                "runtime_reconciliation": {"age_seconds": 120},
            },
        },
        "min_sample_size": 30,
        "generated_at": "2026-05-16T10:00:00Z",
    }


def test_daily_quality_gate_passes_when_all_simulated_live_evidence_is_within_limits() -> None:
    report = build_daily_quality_gate_report(**_passing_inputs())

    assert report["schema_version"] == "daily_quality_gate_report.v1"
    assert report["mode"] == "simulated_live"
    assert report["decision"] == "pass_for_continued_paper"
    assert report["reasons"] == []
    assert report["checks"]["tca_slippage_within_threshold"] is True
    assert report["checks"]["sufficient_sample_size"] is True


def test_daily_quality_gate_rejects_when_rolling_tca_durability_rejects() -> None:
    inputs = _passing_inputs()
    inputs["rolling_tca_durability"] = {
        "schema_version": "rolling_tca_durability_report.v1",
        "decision": "reject",
        "reasons": ["bucket_regression"],
        "checks": {
            "rolling_tca_durable": False,
            "sufficient_bucket_samples": True,
        },
    }

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == ["rolling_tca_durability_failed", "bucket_regression"]
    assert report["checks"]["rolling_tca_durability_passed"] is False
    assert report["checks"]["rolling_tca_bucket_samples_sufficient"] is True
    assert report["inputs"]["rolling_tca_durability"]["decision"] == "reject"


def test_daily_quality_gate_holds_when_rolling_tca_bucket_samples_are_insufficient() -> None:
    inputs = _passing_inputs()
    inputs["rolling_tca_durability"] = {
        "schema_version": "rolling_tca_durability_report.v1",
        "decision": "hold",
        "reasons": ["insufficient_bucket_samples"],
        "checks": {
            "rolling_tca_durable": True,
            "sufficient_bucket_samples": False,
        },
    }

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "hold_for_review"
    assert report["reasons"] == ["insufficient_bucket_samples"]
    assert report["checks"]["rolling_tca_durability_passed"] is True
    assert report["checks"]["rolling_tca_bucket_samples_sufficient"] is False


def test_daily_quality_gate_holds_for_producer_native_insufficient_rolling_tca_evidence() -> None:
    inputs = _passing_inputs()
    inputs["rolling_tca_durability"] = {
        "schema_version": "rolling_tca_durability_report.v1",
        "decision": "insufficient",
        "reason_codes": ["stale_dates", "insufficient_bucket_samples"],
        "checks": {
            "all_bucket_fields_known": True,
            "all_bucket_windows_sufficiently_sampled": False,
            "all_expected_dates_present": True,
            "all_records_well_formed": True,
            "no_stale_dates": False,
            "no_threshold_breaches": True,
        },
    }

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "hold_for_review"
    assert report["malformed_inputs"] == []
    assert "malformed_evidence" not in report["reasons"]
    assert "data_freshness_violation" in report["reasons"]
    assert "insufficient_bucket_samples" in report["reasons"]
    assert report["checks"]["rolling_tca_durability_passed"] is False
    assert report["checks"]["rolling_tca_bucket_samples_sufficient"] is False


def test_daily_quality_gate_fails_closed_for_malformed_rolling_tca_durability() -> None:
    inputs = _passing_inputs()
    inputs["rolling_tca_durability"] = {
        "schema_version": "rolling_tca_durability_report.v1",
        "decision": "pass",
        "reasons": ["bucket_regression"],
        "checks": {
            "rolling_tca_durable": True,
            "sufficient_bucket_samples": True,
        },
    }

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == ["malformed_evidence"]
    assert "rolling_tca_durability.reasons_present_for_pass" in report["malformed_inputs"]


def test_daily_quality_gate_rejects_hard_failures_and_preserves_reason_taxonomy() -> None:
    inputs = _passing_inputs()
    inputs["drift"] = {"checks": {"paper_live_shadow_material_drift_absent": False}}
    inputs["reconciliation"] = {
        "checks": {
            "execution_event_chain_met": False,
            "order_position_reconciliation_met": False,
        }
    }
    inputs["tca"] = {"sample_size": 42, "p95_slippage_bps": 8.0, "max_p95_slippage_bps": 5.0}

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == [
        "paper_shadow_material_drift",
        "tca_slippage_exceeds_threshold",
        "execution_chain_missing",
        "reconcile_failed",
    ]


def test_daily_quality_gate_holds_for_review_on_soft_daily_evidence_gaps() -> None:
    inputs = _passing_inputs()
    inputs["tca"] = {"sample_size": 12, "p95_slippage_bps": 3.0, "max_p95_slippage_bps": 5.0}
    inputs["latency"] = {"current_p95_ms": 150.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25}
    inputs["freshness"] = {"max_age_seconds": 3600, "items": {"evidence_bundle": {"age_seconds": 7200}}}

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "hold_for_review"
    assert report["reasons"] == [
        "latency_distribution_shift",
        "data_freshness_violation",
        "insufficient_sample_size",
    ]


def test_daily_quality_gate_rejects_malformed_evidence() -> None:
    inputs = _passing_inputs()
    inputs["evidence_bundle"] = {"verified": "yes"}

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == ["malformed_evidence"]
    assert report["malformed_inputs"] == ["evidence_bundle.verified_not_bool"]


def test_daily_quality_gate_fails_closed_for_unverified_bundle_or_invalid_drift_contract() -> None:
    inputs = _passing_inputs()
    inputs["evidence_bundle"] = {"verified": False, "manifest_present": True}
    inputs["drift"] = {
        "checks": {
            "paper_live_shadow_material_drift_absent": True,
            "paper_live_shadow_drift_contract_schema_valid": False,
        }
    }

    report = build_daily_quality_gate_report(**inputs)

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == ["malformed_evidence"]
    assert report["checks"]["evidence_bundle_verified"] is False
    assert report["checks"]["paper_live_shadow_drift_contract_schema_valid"] is False


def test_testnet_daily_report_surfaces_daily_quality_gate_decision(tmp_path: Path) -> None:
    bucket = tmp_path / "data" / "runtime" / "testnet" / "prod"
    optimization = bucket / "optimization"
    optimization.mkdir(parents=True)
    (bucket / "latest.json").write_text(json.dumps({"status": "ok", "mode": "testnet"}), encoding="utf-8")
    (bucket / "account_snapshot.json").write_text(json.dumps({"positions": []}), encoding="utf-8")
    (bucket / "runtime_state.json").write_text(json.dumps({"positions": {}, "active_orders": {}}), encoding="utf-8")
    (optimization / "daily_quality_gate_report.json").write_text(
        json.dumps({"decision": "hold_for_review", "reasons": ["latency_distribution_shift"]}),
        encoding="utf-8",
    )

    payload = build_report_payload(bucket=bucket, report_date="2026-05-16")

    assert payload["daily_quality_gate_report"]["decision"] == "hold_for_review"
    markdown = render_markdown(payload)
    assert "daily_quality_gate：hold_for_review" in markdown
    assert "latency_distribution_shift" in markdown


def test_quality_gate_alert_hold_workflow_opens_hold_with_reason_lifetimes() -> None:
    gate = build_daily_quality_gate_report(**_passing_inputs())
    gate["decision"] = "hold_for_review"
    gate["reasons"] = ["latency_distribution_shift", "insufficient_sample_size"]

    workflow = build_quality_gate_alert_hold_workflow(
        gate,
        generated_at="2026-05-16T11:00:00Z",
        previous_workflow={
            "active_reasons": [
                {
                    "code": "latency_distribution_shift",
                    "first_seen": "2026-05-15T11:00:00Z",
                    "last_seen": "2026-05-15T11:00:00Z",
                    "status": "active",
                }
            ]
        },
    )

    assert workflow["schema_version"] == "quality_gate_alert_hold_workflow.v1"
    assert workflow["mode"] == "simulated_live"
    assert workflow["hold"]["status"] == "active"
    assert workflow["hold"]["decision"] == "hold_for_review"
    assert workflow["hold"]["escalation_level"] == "warning"
    assert workflow["hold"]["acknowledgement_required"] is True
    assert workflow["hold"]["acknowledgement"] == {"status": "required"}
    assert workflow["hold"]["release_conditions"] == [
        "next_daily_quality_gate_decision_pass_for_continued_paper",
        "all_active_reason_codes_absent",
        "no_unresolved_reject_live_promotion",
        "acknowledgement_recorded_for_current_hold",
    ]
    assert workflow["active_reasons"] == [
        {
            "code": "latency_distribution_shift",
            "severity": "warning",
            "category": "quality_gate",
            "first_seen": "2026-05-15T11:00:00Z",
            "last_seen": "2026-05-16T11:00:00Z",
            "status": "active",
        },
        {
            "code": "insufficient_sample_size",
            "severity": "warning",
            "category": "quality_gate",
            "first_seen": "2026-05-16T11:00:00Z",
            "last_seen": "2026-05-16T11:00:00Z",
            "status": "active",
        },
    ]
    assert workflow["alerts"][0]["code"] == "quality_gate_hold_for_review"


def test_quality_gate_alert_hold_workflow_fails_closed_for_reject_until_acknowledged() -> None:
    gate = build_daily_quality_gate_report(**_passing_inputs())
    gate["decision"] = "pass_for_continued_paper"
    gate["reasons"] = []

    workflow = build_quality_gate_alert_hold_workflow(
        gate,
        generated_at="2026-05-16T11:00:00Z",
        previous_workflow={
            "hold": {"decision": "reject_live_promotion"},
            "active_reasons": [
                {
                    "code": "paper_shadow_material_drift",
                    "first_seen": "2026-05-15T11:00:00Z",
                    "last_seen": "2026-05-15T11:00:00Z",
                    "status": "active",
                }
            ],
        },
    )

    assert workflow["hold"]["status"] == "blocked"
    assert workflow["hold"]["decision"] == "reject_live_promotion"
    assert workflow["hold"]["escalation_level"] == "critical"
    assert workflow["active_reasons"][0]["code"] == "unresolved_reject_live_promotion"
    assert workflow["active_reasons"][0]["first_seen"] == "2026-05-16T11:00:00Z"
    assert workflow["alerts"][0]["code"] == "quality_gate_reject_live_promotion"


def test_quality_gate_alert_hold_workflow_releases_after_acknowledged_pass(tmp_path: Path) -> None:
    gate = build_daily_quality_gate_report(**_passing_inputs())

    workflow = write_quality_gate_alert_hold_workflow(
        tmp_path / "quality_gate_alert_hold_workflow.json",
        gate,
        generated_at="2026-05-16T11:00:00Z",
        previous_workflow={
            "hold": {"decision": "reject_live_promotion"},
            "active_reasons": [
                {
                    "code": "paper_shadow_material_drift",
                    "first_seen": "2026-05-15T11:00:00Z",
                    "last_seen": "2026-05-15T11:00:00Z",
                    "status": "active",
                }
            ],
        },
        acknowledgement={
            "acknowledged_by": "ops",
            "acknowledged_at": "2026-05-16T10:55:00Z",
            "reason_codes": ["paper_shadow_material_drift"],
        },
    )

    assert workflow["hold"]["status"] == "released"
    assert workflow["hold"]["decision"] == "pass_for_continued_paper"
    assert workflow["hold"]["acknowledgement"] == {
        "status": "recorded",
        "acknowledged_by": "ops",
        "acknowledged_at": "2026-05-16T10:55:00Z",
        "reason_codes": ["paper_shadow_material_drift"],
    }
    assert workflow["active_reasons"] == []
    assert workflow["resolved_reasons"][0]["code"] == "paper_shadow_material_drift"
    assert json.loads((tmp_path / "quality_gate_alert_hold_workflow.json").read_text(encoding="utf-8")) == workflow


def test_quality_gate_alert_hold_workflow_does_not_clear_reject_with_unrelated_acknowledgement() -> None:
    gate = build_daily_quality_gate_report(**_passing_inputs())

    workflow = build_quality_gate_alert_hold_workflow(
        gate,
        generated_at="2026-05-16T11:00:00Z",
        previous_workflow={
            "hold": {"decision": "reject_live_promotion"},
            "active_reasons": [
                {
                    "code": "paper_shadow_material_drift",
                    "first_seen": "2026-05-15T11:00:00Z",
                    "last_seen": "2026-05-15T11:00:00Z",
                    "status": "active",
                }
            ],
        },
        acknowledgement={
            "acknowledged_by": "ops",
            "acknowledged_at": "2026-05-16T10:55:00Z",
            "reason_codes": ["insufficient_sample_size"],
        },
    )

    assert workflow["hold"]["status"] == "blocked"
    assert workflow["hold"]["decision"] == "reject_live_promotion"
    assert workflow["active_reasons"][0]["code"] == "unresolved_reject_live_promotion"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda gate: gate.pop("reasons"), "daily quality gate reasons must be present"),
        (lambda gate: gate.__setitem__("reasons", []), "failing daily quality gate reasons must be non-empty"),
        (lambda gate: gate.__setitem__("generated_at", "2026-05-16T09:00:00Z"), "daily quality gate evidence is stale"),
    ],
)
def test_quality_gate_alert_hold_workflow_rejects_missing_reasons_and_stale_evidence(mutate, match) -> None:
    gate = build_daily_quality_gate_report(**_passing_inputs())
    gate["decision"] = "hold_for_review"
    gate["reasons"] = ["latency_distribution_shift"]
    mutate(gate)

    with pytest.raises(ValueError, match=match):
        build_quality_gate_alert_hold_workflow(
            gate,
            generated_at="2026-05-16T11:00:00Z",
            max_gate_age_seconds=3600,
        )


def test_testnet_daily_report_surfaces_quality_gate_alert_hold_workflow(tmp_path: Path) -> None:
    bucket = tmp_path / "data" / "runtime" / "testnet" / "prod"
    optimization = bucket / "optimization"
    optimization.mkdir(parents=True)
    (bucket / "latest.json").write_text(json.dumps({"status": "ok", "mode": "testnet"}), encoding="utf-8")
    (bucket / "account_snapshot.json").write_text(json.dumps({"positions": []}), encoding="utf-8")
    (bucket / "runtime_state.json").write_text(json.dumps({"positions": {}, "active_orders": {}}), encoding="utf-8")
    (optimization / "quality_gate_alert_hold_workflow.json").write_text(
        json.dumps(
            {
                "hold": {
                    "status": "blocked",
                    "decision": "reject_live_promotion",
                    "escalation_level": "critical",
                    "acknowledgement_required": True,
                },
                "active_reasons": [{"code": "paper_shadow_material_drift"}],
            }
        ),
        encoding="utf-8",
    )

    payload = build_report_payload(bucket=bucket, report_date="2026-05-16")

    assert payload["quality_gate_alert_hold_workflow"]["hold"]["status"] == "blocked"
    markdown = render_markdown(payload)
    assert "quality_gate_hold：blocked escalation=critical acknowledgement_required=True" in markdown
    assert "quality_gate_hold_reasons：paper_shadow_material_drift" in markdown
