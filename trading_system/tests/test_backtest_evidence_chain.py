from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.backtest.evidence_chain import build_backtest_evidence_chain, write_backtest_evidence_chain


GENERATED_AT = "2026-05-16T12:00:00Z"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_minimal_backtest_bundle(bundle_dir: Path) -> None:
    _write_json(
        bundle_dir / "manifest.json",
        {
            "bundle_name": "full_market_baseline__baseline__candidate",
            "snapshot_count": 48,
            "artifacts": [
                "manifest.json",
                "summary.json",
                "audit.json",
                "exit_path_replay.json",
            ],
        },
    )
    _write_json(
        bundle_dir / "summary.json",
        {
            "summary": {
                "trade_count": 48,
                "total_return": 0.08,
                "max_drawdown": -0.04,
                "cost_breakdown": {"fees": 0.01, "slippage": 0.005, "funding": 0.0},
            }
        },
    )
    _write_json(
        bundle_dir / "audit.json",
        {
            "audit": {
                "trade_count": 48,
                "accepted_count": 48,
                "rejection_count": 0,
                "rejection_reasons": {},
            }
        },
    )
    _write_json(
        bundle_dir / "exit_path_replay.json",
        {
            "exit_path_replay": {
                "trade_count": 48,
                "replayed_count": 48,
                "reason_codes": [],
            }
        },
    )


def _write_walk_forward_report(path: Path) -> None:
    _write_json(
        path,
        {
            "summary": {
                "decision": "pass",
                "out_of_sample_scorecard": {"trade_count": 32, "total_return": 0.03},
            },
            "reason_codes": [],
        },
    )


def _write_cost_sensitivity_report(path: Path) -> None:
    _write_json(
        path,
        {
            "summary": {
                "decision": "pass",
                "scenario_count": 3,
                "worst_case_total_return": 0.01,
            },
            "reason_codes": [],
        },
    )


def test_valid_minimal_backtest_bundle_builds_passing_evidence_chain(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    walk_forward = tmp_path / "walk_forward.json"
    cost_sensitivity = tmp_path / "cost_sensitivity.json"
    _write_minimal_backtest_bundle(bundle_dir)
    _write_walk_forward_report(walk_forward)
    _write_cost_sensitivity_report(cost_sensitivity)

    evidence = build_backtest_evidence_chain(
        bundle_dir,
        walk_forward_report_path=walk_forward,
        cost_sensitivity_report_path=cost_sensitivity,
        generated_at=GENERATED_AT,
    )

    assert evidence["schema_version"] == "backtest_evidence_chain.v1"
    assert evidence["source_mode"] == "historical_backtest_local"
    assert evidence["summary"]["decision"] == "pass"
    assert evidence["historical_backtest"]["status"] == "pass"
    assert evidence["historical_backtest"]["trade_count"] == 48
    assert evidence["exit_path_replay"]["status"] == "pass"
    assert evidence["walk_forward_oos"]["status"] == "pass"
    assert evidence["cost_sensitivity"]["status"] == "pass"
    assert evidence["execution_realism"]["status"] == "pass"
    assert evidence["data_quality"]["status"] == "pass"
    assert set(evidence["sources"]) == {
        "manifest",
        "summary",
        "audit",
        "exit_path_replay",
        "walk_forward_oos",
        "cost_sensitivity",
    }
    assert evidence["sources"]["manifest"]["sha256"]
    assert evidence["sources"]["manifest"]["provenance"]["source_mode"] == "historical_backtest_local"


def test_missing_optional_professional_reports_hold_fail_closed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    _write_minimal_backtest_bundle(bundle_dir)

    evidence = build_backtest_evidence_chain(bundle_dir, generated_at=GENERATED_AT)

    assert evidence["historical_backtest"]["status"] == "pass"
    assert evidence["walk_forward_oos"]["status"] == "hold"
    assert evidence["walk_forward_oos"]["reason_codes"] == ["source_missing:walk_forward_oos"]
    assert evidence["cost_sensitivity"]["status"] == "hold"
    assert evidence["cost_sensitivity"]["reason_codes"] == ["source_missing:cost_sensitivity"]
    assert evidence["execution_realism"]["status"] == "pass"
    assert evidence["summary"]["decision"] == "hold"


def test_execution_calibration_summary_surfaces_maker_fill_and_taker_slippage_metrics(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    walk_forward = tmp_path / "walk_forward.json"
    cost_sensitivity = tmp_path / "cost_sensitivity.json"
    calibration_summary = tmp_path / "passive_order_calibration_summary.json"
    _write_minimal_backtest_bundle(bundle_dir)
    _write_walk_forward_report(walk_forward)
    _write_cost_sensitivity_report(cost_sensitivity)
    _write_json(
        calibration_summary,
        {
            "schema_version": "passive_order_calibration_summary.v1",
            "overall": {"attempt_count": 20, "fill_rate": 0.7},
            "passive_maker": {"attempt_count": 12, "fill_rate": 0.58},
            "taker_slippage": {"sample_count": 8, "median_slippage_bps": 4.2, "p95_slippage_bps": 7.5},
        },
    )

    evidence = build_backtest_evidence_chain(
        bundle_dir,
        walk_forward_report_path=walk_forward,
        cost_sensitivity_report_path=cost_sensitivity,
        execution_calibration_summary_path=calibration_summary,
        generated_at=GENERATED_AT,
    )

    assert evidence["summary"]["decision"] == "pass"
    assert evidence["execution_realism"]["status"] == "pass"
    assert evidence["execution_realism"]["sample_count"] == 20
    assert evidence["execution_realism"]["maker_fill_probability"] == 0.58
    assert evidence["execution_realism"]["taker_slippage_bps"]["median"] == 4.2
    assert evidence["execution_realism"]["taker_slippage_bps"]["p95"] == 7.5
    assert evidence["sources"]["execution_calibration_summary"]["sha256"]


def test_execution_calibration_unavailable_marker_holds_execution_realism_fail_closed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    walk_forward = tmp_path / "walk_forward.json"
    cost_sensitivity = tmp_path / "cost_sensitivity.json"
    marker_path = tmp_path / "calibration_records_unavailable.json"
    _write_minimal_backtest_bundle(bundle_dir)
    _write_walk_forward_report(walk_forward)
    _write_cost_sensitivity_report(cost_sensitivity)
    _write_json(
        marker_path,
        {
            "schema_version": "calibration_records_unavailable.v1",
            "status": "unavailable",
            "generated_at": GENERATED_AT,
            "reason_codes": ["execution_log_missing", "no_canonical_execution_events"],
            "record_count": 0,
            "decision_policy": "fail_closed",
        },
    )

    evidence = build_backtest_evidence_chain(
        bundle_dir,
        walk_forward_report_path=walk_forward,
        cost_sensitivity_report_path=cost_sensitivity,
        execution_calibration_unavailable_path=marker_path,
        generated_at=GENERATED_AT,
    )

    assert evidence["summary"]["decision"] == "hold"
    assert evidence["summary"]["component_statuses"]["execution_realism"] == "hold"
    assert evidence["execution_realism"]["sample_count"] == 0
    assert evidence["execution_realism"]["coverage_score"] == 0.0
    assert "execution_calibration_unavailable" in evidence["execution_realism"]["reason_codes"]
    assert "execution_log_missing" in evidence["execution_realism"]["reason_codes"]
    assert evidence["sources"]["execution_calibration_unavailable"]["sha256"]


def test_execution_sample_collection_health_holds_execution_realism_fail_closed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    walk_forward = tmp_path / "walk_forward.json"
    cost_sensitivity = tmp_path / "cost_sensitivity.json"
    health_path = tmp_path / "execution_sample_collection_health.json"
    _write_minimal_backtest_bundle(bundle_dir)
    _write_walk_forward_report(walk_forward)
    _write_cost_sensitivity_report(cost_sensitivity)
    _write_json(
        health_path,
        {
            "schema_version": "execution_sample_collection_health.v1",
            "status": "unavailable",
            "generated_at": GENERATED_AT,
            "reason_codes": ["execution_ledger_intent_mismatch", "no_execution_samples"],
            "sample_count": 0,
            "decision_policy": "fail_closed",
        },
    )

    evidence = build_backtest_evidence_chain(
        bundle_dir,
        walk_forward_report_path=walk_forward,
        cost_sensitivity_report_path=cost_sensitivity,
        execution_sample_collection_health_path=health_path,
        generated_at=GENERATED_AT,
    )

    assert evidence["summary"]["decision"] == "hold"
    assert evidence["summary"]["component_statuses"]["execution_realism"] == "hold"
    assert evidence["execution_realism"]["sample_count"] == 0
    assert evidence["execution_realism"]["coverage_score"] == 0.0
    assert "execution_sample_collection_health_unavailable" in evidence["execution_realism"]["reason_codes"]
    assert "execution_ledger_intent_mismatch" in evidence["execution_realism"]["reason_codes"]
    assert evidence["sources"]["execution_sample_collection_health"]["sha256"]


def test_malformed_or_incomplete_backtest_bundle_holds_fail_closed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    _write_minimal_backtest_bundle(bundle_dir)
    (bundle_dir / "audit.json").write_text("{not-json", encoding="utf-8")

    evidence = build_backtest_evidence_chain(bundle_dir, generated_at=GENERATED_AT)

    assert evidence["historical_backtest"]["status"] == "hold"
    assert "source_malformed:audit.json:JSONDecodeError" in evidence["historical_backtest"]["reason_codes"]
    assert evidence["data_quality"]["status"] == "hold"
    assert "source_malformed:audit.json:JSONDecodeError" in evidence["data_quality"]["reason_codes"]
    assert evidence["summary"]["decision"] == "hold"


def test_write_backtest_evidence_chain_uses_canonical_json(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "backtest"
    output_path = tmp_path / "backtest_evidence_chain.json"
    _write_minimal_backtest_bundle(bundle_dir)

    payload = write_backtest_evidence_chain(bundle_dir, output_path=output_path, generated_at=GENERATED_AT)

    assert output_path.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
