from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.reporting.daily_quality_gate_report import build_daily_quality_gate_report
from trading_system.app.reporting.promotion_readiness_scorecard import (
    COMPONENT_NAMES,
    build_promotion_readiness_scorecard,
    write_promotion_readiness_scorecard,
)


def _component(
    *,
    as_of: str = "2026-05-16T09:00:00Z",
    coverage_score: float = 0.95,
    sample_count: int = 60,
    duration_hours: float | None = None,
    status: str = "pass",
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of": as_of,
        "coverage_score": coverage_score,
        "sample_count": sample_count,
        "status": status,
        "reason_codes": reason_codes or [],
    }
    if duration_hours is not None:
        payload["duration_hours"] = duration_hours
    return payload


def _passing_evidence() -> dict[str, object]:
    evidence = {name: _component() for name in COMPONENT_NAMES}
    evidence["live_sim_durability"] = _component(duration_hours=96.0)
    evidence["venue_rulebook_coverage"] = _component(as_of="2026-05-15T10:00:00Z", sample_count=8)
    evidence["cross_source_parity"] = {
        **_component(sample_count=40),
        "max_parity_drift_bps": 1.25,
    }
    return evidence


def test_promotion_readiness_scorecard_passes_with_complete_rolling_evidence() -> None:
    scorecard = build_promotion_readiness_scorecard(
        _passing_evidence(),
        generated_at="2026-05-16T10:00:00Z",
    )

    assert scorecard["schema_version"] == "promotion_readiness_scorecard.v1"
    assert scorecard["mode"] == "simulated_live"
    assert scorecard["generated_at"] == "2026-05-16T10:00:00Z"
    assert scorecard["decision"] == "pass"
    assert scorecard["blockers"] == []
    assert scorecard["scores"]["promotion_readiness"] == 95.0
    assert set(scorecard["scores"]) == {*COMPONENT_NAMES, "promotion_readiness"}
    assert scorecard["component_gates"]["live_sim_durability"]["gate"] == "pass"
    assert scorecard["component_gates"]["cross_source_parity"]["gate"] == "pass"


def test_promotion_readiness_scorecard_holds_for_duration_sample_rulebook_and_parity_gaps() -> None:
    evidence = _passing_evidence()
    evidence["execution_realism"] = _component(sample_count=12)
    evidence["venue_rulebook_coverage"] = _component(as_of="2026-05-01T10:00:00Z", sample_count=8)
    evidence["cross_source_parity"] = {
        **_component(sample_count=40),
        "max_parity_drift_bps": 4.5,
    }
    evidence["live_sim_durability"] = _component(duration_hours=24.0)

    scorecard = build_promotion_readiness_scorecard(
        evidence,
        generated_at="2026-05-16T10:00:00Z",
    )

    assert scorecard["decision"] == "hold"
    assert scorecard["blockers"] == [
        {
            "component": "execution_realism",
            "reason_code": "insufficient_samples",
            "severity": "hold",
            "detail": "sample_count 12 < required 30",
        },
        {
            "component": "venue_rulebook_coverage",
            "reason_code": "stale_rulebook",
            "severity": "hold",
            "detail": "rulebook age 1296000s > allowed 604800s",
        },
        {
            "component": "cross_source_parity",
            "reason_code": "parity_drift",
            "severity": "hold",
            "detail": "max_parity_drift_bps 4.5 > allowed 2.0",
        },
        {
            "component": "live_sim_durability",
            "reason_code": "insufficient_duration",
            "severity": "hold",
            "detail": "duration_hours 24.0 < required 72.0",
        },
    ]
    assert scorecard["component_gates"]["execution_realism"]["gate"] == "hold"
    assert scorecard["scores"]["execution_realism"] == 38.0
    assert scorecard["scores"]["promotion_readiness"] < 80.0


def test_promotion_readiness_scorecard_rejects_missing_malformed_and_hard_hold_evidence() -> None:
    evidence = _passing_evidence()
    evidence.pop("data_quality")
    evidence["derivatives_risk"] = _component(status="hold", reason_codes=["derivatives_risk_hold"])
    evidence["execution_realism"] = _component(status="hold", reason_codes=["race_condition_hold"])
    evidence["cross_source_parity"] = {"as_of": "2026-05-16T09:00:00Z", "coverage_score": "0.9"}

    scorecard = build_promotion_readiness_scorecard(
        evidence,
        generated_at="2026-05-16T10:00:00Z",
    )

    assert scorecard["decision"] == "reject"
    assert scorecard["scores"]["data_quality"] == 0.0
    assert scorecard["scores"]["cross_source_parity"] == 0.0
    assert {
        "component": "data_quality",
        "reason_code": "missing_component",
        "severity": "reject",
        "detail": "component evidence is missing",
    } in scorecard["blockers"]
    assert {
        "component": "cross_source_parity",
        "reason_code": "malformed_evidence",
        "severity": "reject",
        "detail": "coverage_score must be numeric",
    } in scorecard["blockers"]
    assert {
        "component": "execution_realism",
        "reason_code": "race_condition_hold",
        "severity": "reject",
        "detail": "component reported race_condition_hold",
    } in scorecard["blockers"]
    assert {
        "component": "derivatives_risk",
        "reason_code": "derivatives_risk_hold",
        "severity": "reject",
        "detail": "component reported derivatives_risk_hold",
    } in scorecard["blockers"]


def test_promotion_readiness_scorecard_rejects_future_as_of_ordering() -> None:
    evidence = _passing_evidence()
    evidence["data_quality"] = _component(as_of="2026-05-16T10:00:01Z")

    scorecard = build_promotion_readiness_scorecard(
        evidence,
        generated_at="2026-05-16T10:00:00Z",
    )

    assert scorecard["decision"] == "reject"
    assert scorecard["component_gates"]["data_quality"]["gate"] == "reject"
    assert scorecard["blockers"][0]["reason_code"] == "timestamp_ordering"
    assert scorecard["scores"]["data_quality"] == 0.0


def test_daily_quality_gate_surfaces_promotion_readiness_hold() -> None:
    scorecard = build_promotion_readiness_scorecard(
        {
            **_passing_evidence(),
            "live_sim_durability": _component(duration_hours=24.0),
        },
        generated_at="2026-05-16T10:00:00Z",
    )

    report = build_daily_quality_gate_report(
        evidence_bundle={"verified": True, "manifest_present": True},
        drift={"checks": {"paper_live_shadow_material_drift_absent": True}},
        reconciliation={"checks": {"execution_event_chain_met": True, "order_position_reconciliation_met": True}},
        tca={"sample_size": 42, "p95_slippage_bps": 4.0, "max_p95_slippage_bps": 5.0},
        promotion_readiness=scorecard,
        generated_at="2026-05-16T10:00:00Z",
    )

    assert report["decision"] == "hold_for_review"
    assert report["reasons"] == ["promotion_readiness_hold"]
    assert report["checks"]["promotion_readiness_passed"] is False
    assert report["inputs"]["promotion_readiness"]["decision"] == "hold"
    assert report["inputs"]["promotion_readiness"]["score"] == scorecard["scores"]["promotion_readiness"]


def test_writes_promotion_readiness_scorecard(tmp_path: Path) -> None:
    path = tmp_path / "promotion_readiness_scorecard.json"

    payload = write_promotion_readiness_scorecard(
        path,
        evidence=_passing_evidence(),
        generated_at="2026-05-16T10:00:00Z",
    )

    assert json.loads(path.read_text(encoding="utf-8")) == payload
