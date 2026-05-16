from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.reporting.daily_quality_gate_report import build_daily_quality_gate_report
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
