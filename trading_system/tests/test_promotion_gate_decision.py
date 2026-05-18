from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.promotion_gate_decision import (
    build_promotion_gate_decision_report,
    write_promotion_gate_decision_report,
)


def _window(*, decision: str = "pass", reason_codes: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "simulated_live_evidence_window.v1",
        "generated_at": "2026-05-17T00:05:00Z",
        "decision": decision,
        "reason_codes": reason_codes or [],
        "checks": {
            "minimum_distinct_sessions_met": True,
            "session_identities_unique": True,
            "generated_at_monotonic": True,
            "as_of_monotonic": True,
            "all_bundles_pass": decision == "pass",
            "all_required_bundle_components_present": True,
        },
        "bundles": [
            {"session_id": "sim-live-20260514", "day": "2026-05-14", "generated_at": "2026-05-14T23:59:00Z"},
            {"session_id": "sim-live-20260515", "day": "2026-05-15", "generated_at": "2026-05-15T23:59:00Z"},
            {"session_id": "sim-live-20260516", "day": "2026-05-16", "generated_at": "2026-05-16T23:59:00Z"},
        ],
    }


def _trend(*, decision: str = "pass", reasons: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "promotion_readiness_scorecard_trend.v1",
        "mode": "simulated_live",
        "generated_at": "2026-05-17T00:10:00Z",
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
        "scorecards": [
            {"identity": "scorecard-1", "generated_at": "2026-05-15T10:00:00Z", "decision": "pass", "score": 90.0},
            {"identity": "scorecard-2", "generated_at": "2026-05-16T10:00:00Z", "decision": "pass", "score": 91.0},
        ],
    }


def _calibration_feedback(*, decision: str = "ready") -> dict[str, object]:
    return {
        "schema_version": "calibration_feedback_artifact.v1",
        "generated_at": "2026-05-17T00:15:00Z",
        "decision": decision,
        "checks": {"sample_count_met": True, "evidence_fresh": True},
        "reasons": [],
        "components": [{"component": "tca_report", "identity": "tca-20260517", "schema_version": "tca_calibration_report.v1"}],
        "side_effect_boundary": "offline_local_only",
        "strategy_config_mutation": "forbidden",
    }


def _assumption_recommendation(*, decision: str = "no_change") -> dict[str, object]:
    return {
        "schema_version": "calibration_assumption_update_recommendation.v1",
        "generated_at": "2026-05-17T00:20:00Z",
        "decision": decision,
        "source": {
            "artifact_id": "calibration-feedback-20260517",
            "schema_version": "calibration_feedback_artifact.v1",
            "generated_at": "2026-05-17T00:15:00Z",
            "decision": "ready",
        },
        "recommended_assumption_updates": (
            [
                {
                    "field": "expected_slippage_bps",
                    "current": 2.0,
                    "recommended": 3.0,
                    "observed": 3.0,
                    "reason_codes": ["observed_slippage_above_current_assumption"],
                }
            ]
            if decision == "review"
            else []
        ),
        "rationale": {
            "reason_codes": (
                ["review_required_for_assumption_update"]
                if decision == "review"
                else ["observed_calibration_within_current_assumptions"]
            )
        },
        "assumptions_file_mutation": "forbidden",
        "side_effect_boundary": "offline_local_only",
    }


def _professional_evidence_chain(
    *,
    execution_realism_status: str = "pass",
    sample_count: int | None = None,
    maker_fill_probability: float | None = None,
    taker_slippage_p95_bps: float | None = None,
) -> dict[str, object]:
    reasons = [] if execution_realism_status == "pass" else ["execution_calibration_unavailable", "execution_log_missing"]
    execution_realism: dict[str, object] = {
        "status": execution_realism_status,
        "coverage_score": 1.0 if execution_realism_status == "pass" else 0.0,
        "sample_count": sample_count if sample_count is not None else (12 if execution_realism_status == "pass" else 0),
        "reason_codes": reasons,
    }
    if maker_fill_probability is not None:
        execution_realism["maker_fill_probability"] = maker_fill_probability
    if taker_slippage_p95_bps is not None:
        execution_realism["taker_slippage_bps"] = {"median": taker_slippage_p95_bps / 2, "p95": taker_slippage_p95_bps}
    return {
        "schema_version": "backtest_evidence_chain.v1",
        "generated_at": "2026-05-17T00:25:00Z",
        "summary": {
            "decision": "pass" if execution_realism_status == "pass" else "hold",
            "component_statuses": {
                "historical_backtest": "pass",
                "exit_path_replay": "pass",
                "walk_forward_oos": "pass",
                "cost_sensitivity": "pass",
                "execution_realism": execution_realism_status,
                "data_quality": "pass",
            },
        },
        "execution_realism": execution_realism,
    }


def test_builds_candidate_promotion_gate_decision_from_passing_artifacts() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(),
        promotion_readiness_scorecard_trend=_trend(),
        calibration_artifacts=[_calibration_feedback(), _assumption_recommendation()],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["schema_version"] == "promotion_gate_decision.v1"
    assert report["generated_at"] == "2026-05-17T00:30:00Z"
    assert report["decision"] == "candidate_for_paper_promotion"
    assert report["blocking_reasons"] == []
    assert report["human_review_required"] is False
    assert report["checks"]["simulated_live_evidence_window"]["status"] == "pass"
    assert report["checks"]["promotion_readiness_scorecard_trend"]["status"] == "pass"
    assert report["checks"]["calibration"]["status"] == "pass"
    assert report["source_mode"] == {
        "mode": "simulated_live",
        "side_effect_boundary": "offline_local_filesystem_only",
        "real_orders": "forbidden",
        "testnet_orders": "forbidden",
        "exchange_api_calls": "forbidden",
        "credential_use": "forbidden",
    }
    assert [item["schema_version"] for item in report["included_artifact_identities"]] == [
        "simulated_live_evidence_window.v1",
        "promotion_readiness_scorecard_trend.v1",
        "calibration_feedback_artifact.v1",
        "calibration_assumption_update_recommendation.v1",
    ]


def test_gate_holds_when_any_input_holds_without_reject() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(),
        promotion_readiness_scorecard_trend=_trend(decision="hold", reasons=["score_deterioration"]),
        calibration_artifacts=[_calibration_feedback()],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "hold"
    assert "promotion_readiness_scorecard_trend:score_deterioration" in report["blocking_reasons"]
    assert report["human_review_required"] is True


def test_gate_holds_when_professional_evidence_chain_execution_realism_holds() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(),
        promotion_readiness_scorecard_trend=_trend(),
        calibration_artifacts=[_calibration_feedback()],
        professional_evidence_chain=_professional_evidence_chain(execution_realism_status="hold"),
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "hold"
    assert report["checks"]["professional_evidence_chain"]["status"] == "hold"
    assert "professional_evidence_chain:execution_realism_hold" in report["blocking_reasons"]
    assert "professional_evidence_chain:execution_realism:execution_log_missing" in report["blocking_reasons"]
    assert report["human_review_required"] is True


def test_gate_holds_when_execution_realism_metrics_fail_professional_thresholds() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(),
        promotion_readiness_scorecard_trend=_trend(),
        calibration_artifacts=[_calibration_feedback()],
        professional_evidence_chain=_professional_evidence_chain(
            execution_realism_status="pass",
            sample_count=9,
            maker_fill_probability=0.42,
            taker_slippage_p95_bps=12.5,
        ),
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "hold"
    assert report["checks"]["professional_evidence_chain"]["status"] == "hold"
    assert "professional_evidence_chain:execution_realism_sample_count_below_floor" in report["blocking_reasons"]
    assert "professional_evidence_chain:maker_fill_probability_below_floor" in report["blocking_reasons"]
    assert "professional_evidence_chain:taker_slippage_p95_above_ceiling" in report["blocking_reasons"]
    assert report["checks"]["professional_evidence_chain"]["thresholds"] == {
        "min_execution_samples": 10,
        "min_maker_fill_probability": 0.5,
        "max_taker_slippage_p95_bps": 10.0,
    }
    assert report["human_review_required"] is True


def test_gate_rejects_when_any_input_rejects() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(decision="hold", reason_codes=["non_monotonic_generated_at"]),
        promotion_readiness_scorecard_trend=_trend(decision="reject", reasons=["malformed_scorecard"]),
        calibration_artifacts=[_calibration_feedback()],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "reject"
    assert "promotion_readiness_scorecard_trend:malformed_scorecard" in report["blocking_reasons"]
    assert "simulated_live_evidence_window:non_monotonic_generated_at" in report["blocking_reasons"]
    assert report["checks"]["identity_continuity"]["non_monotonic_or_duplicate_inputs_present"] is True


def test_gate_rejects_malformed_and_missing_artifacts_from_paths(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    missing = tmp_path / "missing.json"

    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=malformed,
        promotion_readiness_scorecard_trend=missing,
        calibration_artifacts=[],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "reject"
    assert "simulated_live_evidence_window:malformed_artifact" in report["blocking_reasons"]
    assert "promotion_readiness_scorecard_trend:missing_artifact" in report["blocking_reasons"]
    assert report["human_review_required"] is True


def test_calibration_assumption_update_recommendation_requires_human_review() -> None:
    report = build_promotion_gate_decision_report(
        simulated_live_evidence_window=_window(),
        promotion_readiness_scorecard_trend=_trend(),
        calibration_artifacts=[_assumption_recommendation(decision="review")],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert report["decision"] == "hold"
    assert report["human_review_required"] is True
    assert "calibration:review_required_for_assumption_update" in report["blocking_reasons"]
    assert report["checks"]["calibration"]["assumptions_file_mutation"] == "forbidden"


def test_write_report_and_cli_emit_machine_readable_decision(tmp_path: Path) -> None:
    window_path = tmp_path / "window.json"
    trend_path = tmp_path / "trend.json"
    calibration_path = tmp_path / "calibration.json"
    output = tmp_path / "promotion_gate_decision.json"
    window_path.write_text(json.dumps(_window()), encoding="utf-8")
    trend_path.write_text(json.dumps(_trend()), encoding="utf-8")
    calibration_path.write_text(json.dumps(_calibration_feedback()), encoding="utf-8")

    payload = write_promotion_gate_decision_report(
        output,
        simulated_live_evidence_window=window_path,
        promotion_readiness_scorecard_trend=trend_path,
        calibration_artifacts=[calibration_path],
        generated_at="2026-05-17T00:30:00Z",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert payload["included_artifact_identities"][0]["source"]["path"].endswith("window.json")

    cli_output = tmp_path / "cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_promotion_gate_decision",
            "--simulated-live-evidence-window",
            str(window_path),
            "--promotion-readiness-scorecard-trend",
            str(trend_path),
            "--calibration-artifact",
            str(calibration_path),
            "--output",
            str(cli_output),
            "--generated-at",
            "2026-05-17T00:30:00Z",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(cli_output.read_text(encoding="utf-8"))["decision"] == "candidate_for_paper_promotion"
    assert re.search(r"PROMOTION_GATE_DECISION_JSON.*candidate_for_paper_promotion", result.stdout)


def test_cli_holds_on_professional_evidence_chain_execution_realism_hold(tmp_path: Path) -> None:
    window_path = tmp_path / "window.json"
    trend_path = tmp_path / "trend.json"
    calibration_path = tmp_path / "calibration.json"
    professional_evidence_path = tmp_path / "backtest_evidence_chain.json"
    output = tmp_path / "promotion_gate_decision.json"
    window_path.write_text(json.dumps(_window()), encoding="utf-8")
    trend_path.write_text(json.dumps(_trend()), encoding="utf-8")
    calibration_path.write_text(json.dumps(_calibration_feedback()), encoding="utf-8")
    professional_evidence_path.write_text(
        json.dumps(_professional_evidence_chain(execution_realism_status="hold")), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_promotion_gate_decision",
            "--simulated-live-evidence-window",
            str(window_path),
            "--promotion-readiness-scorecard-trend",
            str(trend_path),
            "--calibration-artifact",
            str(calibration_path),
            "--professional-evidence-chain",
            str(professional_evidence_path),
            "--output",
            str(output),
            "--generated-at",
            "2026-05-17T00:30:00Z",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "hold"
    assert "professional_evidence_chain:execution_realism_hold" in payload["blocking_reasons"]
    assert re.search(r"PROMOTION_GATE_DECISION_JSON.*hold", result.stdout)
