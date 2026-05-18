from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trading_system.app.backtest.evidence_chain import build_backtest_evidence_chain
from trading_system.app.backtest.professional_reports import (
    build_cost_sensitivity_report,
    build_walk_forward_oos_report,
    write_cost_sensitivity_report,
    write_professional_backtest_evidence,
    write_walk_forward_oos_report,
)


GENERATED_AT = "2026-05-16T12:00:00Z"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_minimal_backtest_bundle(bundle_dir: Path) -> None:
    _write_json(
        bundle_dir / "manifest.json",
        {
            "bundle_name": "full_market_baseline__baseline__candidate",
            "snapshot_count": 24,
            "artifacts": ["manifest.json", "summary.json", "audit.json", "exit_path_replay.json"],
        },
    )
    _write_json(
        bundle_dir / "summary.json",
        {"summary": {"trade_count": 24, "total_return": 0.08, "max_drawdown": -0.04}},
    )
    _write_json(bundle_dir / "audit.json", {"audit": {"trade_count": 24}})
    _write_json(bundle_dir / "exit_path_replay.json", {"exit_path_replay": {"trade_count": 24, "replayed_count": 24}})


def _write_walk_forward_bundle(bundle_dir: Path, *, window_count: int = 2, trade_count: int = 4) -> None:
    _write_json(
        bundle_dir / "manifest.json",
        {
            "bundle_name": "walk_forward_validation__current_policy__rolling_walk_forward",
            "snapshot_count": 8,
            "artifacts": ["manifest.json", "summary.json", "windows.json", "scorecard.json"],
        },
    )
    _write_json(
        bundle_dir / "summary.json",
        {
            "metadata": {"window_count": window_count},
            "robustness_summary": {
                "out_of_sample_scorecard": {"trade_count": trade_count, "total_return": 0.04},
                "performance_dispersion": {"window_count": window_count, "positive_window_ratio": 1.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.75},
        },
    )
    _write_json(bundle_dir / "windows.json", {"rows": [{"window_index": index + 1} for index in range(window_count)]})
    _write_json(
        bundle_dir / "scorecard.json",
        {
            "key_metrics": {
                "window_count": window_count,
                "out_of_sample_total_return": 0.04,
                "positive_window_ratio": 1.0,
                "parameter_stability_score": 0.75,
            },
            "multiple_testing_correction": {"adjusted_pass": True},
        },
    )


def _write_allocator_friction_bundle(bundle_dir: Path) -> None:
    frictions = {
        "low": {"net_bucket_pnl": 0.06, "cost_drag": 0.01},
        "base": {"net_bucket_pnl": 0.04, "cost_drag": 0.02},
        "stressed": {"net_bucket_pnl": 0.02, "cost_drag": 0.04},
    }
    _write_json(
        bundle_dir / "manifest.json",
        {
            "bundle_name": "allocator_friction__current_policy__friction_scan",
            "snapshot_count": 8,
            "artifacts": ["manifest.json", "summary.json", "comparison_rows.json", "scorecard.json"],
        },
    )
    _write_json(
        bundle_dir / "summary.json",
        {"variants": {"current_allocator": {"frictions": frictions}}},
    )
    _write_json(bundle_dir / "comparison_rows.json", {"rows": [{"friction_scenario": name} for name in frictions]})
    _write_json(
        bundle_dir / "scorecard.json",
        {
            "key_metrics": {
                "current_allocator_base_net_bucket_pnl": 0.04,
                "current_allocator_base_cost_drag": 0.02,
                "best_stressed_net_bucket_pnl": 0.02,
            },
            "multiple_testing_correction": {"adjusted_pass": True},
        },
    )


def test_build_walk_forward_oos_report_passes_from_existing_bundle_payload(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "walk_forward"
    _write_walk_forward_bundle(bundle_dir)

    report = build_walk_forward_oos_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["schema_version"] == "walk_forward_oos_report.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["summary"]["decision"] == "pass"
    assert report["summary"]["out_of_sample_scorecard"]["trade_count"] == 4
    assert report["summary"]["window_count"] == 2
    assert report["summary"]["positive_window_ratio"] == 1.0
    assert report["summary"]["parameter_stability_score"] == 0.75
    assert report["reason_codes"] == []


def test_build_walk_forward_oos_report_holds_fail_closed_on_zero_windows(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "walk_forward"
    _write_walk_forward_bundle(bundle_dir, window_count=0, trade_count=0)

    report = build_walk_forward_oos_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["summary"]["decision"] == "hold"
    assert "walk_forward_zero_windows" in report["reason_codes"]
    assert "walk_forward_oos_trade_count_zero" in report["reason_codes"]


def test_build_walk_forward_oos_report_holds_fail_closed_on_malformed_source(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "walk_forward"
    _write_walk_forward_bundle(bundle_dir)
    (bundle_dir / "summary.json").write_text("{not-json", encoding="utf-8")

    report = build_walk_forward_oos_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["summary"]["decision"] == "hold"
    assert "walk_forward_summary_malformed:JSONDecodeError" in report["reason_codes"]


def test_build_walk_forward_oos_report_holds_on_failed_multiple_testing(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "walk_forward"
    _write_walk_forward_bundle(bundle_dir)
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    scorecard["multiple_testing_correction"]["adjusted_pass"] = False
    _write_json(bundle_dir / "scorecard.json", scorecard)

    report = build_walk_forward_oos_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["summary"]["decision"] == "hold"
    assert "walk_forward_multiple_testing_adjusted_pass_false" in report["reason_codes"]


def test_build_cost_sensitivity_report_passes_from_existing_bundle_payload(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "allocator_friction"
    _write_allocator_friction_bundle(bundle_dir)

    report = build_cost_sensitivity_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["schema_version"] == "cost_sensitivity_report.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["summary"]["decision"] == "pass"
    assert report["summary"]["scenario_count"] == 3
    assert report["summary"]["base_net_pnl"] == 0.04
    assert report["summary"]["stressed_net_pnl"] == 0.02
    assert report["summary"]["worst_case_total_return"] == 0.02
    assert report["reason_codes"] == []


def test_build_cost_sensitivity_report_holds_fail_closed_on_missing_stressed_scenario(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "allocator_friction"
    _write_allocator_friction_bundle(bundle_dir)
    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    del summary["variants"]["current_allocator"]["frictions"]["stressed"]
    _write_json(bundle_dir / "summary.json", summary)

    report = build_cost_sensitivity_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["summary"]["decision"] == "hold"
    assert "cost_sensitivity_stressed_scenario_missing" in report["reason_codes"]


def test_build_cost_sensitivity_report_holds_fail_closed_on_zero_scenarios(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "allocator_friction"
    _write_allocator_friction_bundle(bundle_dir)
    _write_json(bundle_dir / "summary.json", {"variants": {"current_allocator": {"frictions": {}}}})
    _write_json(bundle_dir / "comparison_rows.json", {"rows": []})

    report = build_cost_sensitivity_report(bundle_dir, generated_at=GENERATED_AT)

    assert report["summary"]["decision"] == "hold"
    assert report["summary"]["scenario_count"] == 0
    assert "cost_sensitivity_zero_scenarios" in report["reason_codes"]


def test_report_writers_use_canonical_json(tmp_path: Path) -> None:
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)

    walk_forward_report = write_walk_forward_oos_report(
        walk_forward_dir,
        tmp_path / "walk_forward_oos_report.json",
        generated_at=GENERATED_AT,
    )
    cost_report = write_cost_sensitivity_report(
        allocator_dir,
        tmp_path / "cost_sensitivity_report.json",
        generated_at=GENERATED_AT,
    )

    assert json.loads((tmp_path / "walk_forward_oos_report.json").read_text(encoding="utf-8")) == walk_forward_report
    assert (tmp_path / "walk_forward_oos_report.json").read_text(encoding="utf-8").endswith("\n")
    assert json.loads((tmp_path / "cost_sensitivity_report.json").read_text(encoding="utf-8")) == cost_report
    assert (tmp_path / "cost_sensitivity_report.json").read_text(encoding="utf-8").endswith("\n")


def test_write_professional_backtest_evidence_outputs_chain_consuming_both_reports(tmp_path: Path) -> None:
    backtest_dir = tmp_path / "backtest"
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    output_dir = tmp_path / "evidence"
    _write_minimal_backtest_bundle(backtest_dir)
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)

    outputs = write_professional_backtest_evidence(
        backtest_bundle_dir=backtest_dir,
        walk_forward_bundle_dir=walk_forward_dir,
        allocator_friction_bundle_dir=allocator_dir,
        output_dir=output_dir,
        generated_at=GENERATED_AT,
    )

    evidence = build_backtest_evidence_chain(
        backtest_dir,
        walk_forward_report_path=outputs["walk_forward_report_path"],
        cost_sensitivity_report_path=outputs["cost_sensitivity_report_path"],
        generated_at=GENERATED_AT,
    )
    assert outputs["evidence_chain"]["summary"]["decision"] == "pass"
    assert evidence["summary"]["decision"] == "pass"
    assert evidence["walk_forward_oos"]["status"] == "pass"
    assert evidence["cost_sensitivity"]["status"] == "pass"
    assert evidence["execution_realism"]["status"] == "pass"
    assert Path(outputs["evidence_chain_path"]).name == "backtest_evidence_chain.json"


def test_write_professional_backtest_evidence_surfaces_execution_calibration_summary_metrics(tmp_path: Path) -> None:
    backtest_dir = tmp_path / "backtest"
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    output_dir = tmp_path / "evidence"
    calibration_summary = tmp_path / "passive_order_calibration_summary.json"
    _write_minimal_backtest_bundle(backtest_dir)
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)
    _write_json(
        calibration_summary,
        {
            "schema_version": "passive_order_calibration_summary.v1",
            "overall": {"attempt_count": 21, "fill_rate": 0.71},
            "passive_maker": {"attempt_count": 13, "fill_rate": 0.61},
            "taker_slippage": {"sample_count": 8, "median_slippage_bps": 3.9, "p95_slippage_bps": 6.8},
        },
    )

    outputs = write_professional_backtest_evidence(
        backtest_bundle_dir=backtest_dir,
        walk_forward_bundle_dir=walk_forward_dir,
        allocator_friction_bundle_dir=allocator_dir,
        output_dir=output_dir,
        execution_calibration_summary_path=calibration_summary,
        generated_at=GENERATED_AT,
    )

    evidence = outputs["evidence_chain"]
    assert outputs["execution_calibration_summary_path"] == str(calibration_summary)
    assert evidence["summary"]["decision"] == "pass"
    assert evidence["execution_realism"]["sample_count"] == 21
    assert evidence["execution_realism"]["maker_fill_probability"] == 0.61
    assert evidence["execution_realism"]["taker_slippage_bps"] == {"median": 3.9, "p95": 6.8}
    assert evidence["sources"]["execution_calibration_summary"]["path"] == str(calibration_summary)


def test_write_professional_backtest_evidence_holds_on_execution_calibration_unavailable_marker(tmp_path: Path) -> None:
    backtest_dir = tmp_path / "backtest"
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    output_dir = tmp_path / "evidence"
    marker_path = tmp_path / "calibration_records_unavailable.json"
    _write_minimal_backtest_bundle(backtest_dir)
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)
    _write_json(
        marker_path,
        {
            "schema_version": "calibration_records_unavailable.v1",
            "status": "unavailable",
            "generated_at": GENERATED_AT,
            "reason_codes": ["execution_log_missing", "no_canonical_execution_events"],
            "canonical_event_count": 0,
            "record_count": 0,
            "decision_policy": "fail_closed",
        },
    )

    outputs = write_professional_backtest_evidence(
        backtest_bundle_dir=backtest_dir,
        walk_forward_bundle_dir=walk_forward_dir,
        allocator_friction_bundle_dir=allocator_dir,
        output_dir=output_dir,
        execution_calibration_unavailable_path=marker_path,
        generated_at=GENERATED_AT,
    )

    evidence = outputs["evidence_chain"]
    assert outputs["execution_calibration_unavailable_path"] == str(marker_path)
    assert evidence["summary"]["decision"] == "hold"
    assert evidence["summary"]["component_statuses"]["execution_realism"] == "hold"
    assert evidence["execution_realism"]["status"] == "hold"
    assert evidence["execution_realism"]["sample_count"] == 0
    assert evidence["execution_realism"]["coverage_score"] == 0.0
    assert "execution_calibration_unavailable" in evidence["execution_realism"]["reason_codes"]
    assert "execution_log_missing" in evidence["execution_realism"]["reason_codes"]
    assert evidence["sources"]["execution_calibration_unavailable"]["path"] == str(marker_path)


def test_backtest_cli_writes_professional_evidence_chain_from_existing_bundles(tmp_path: Path) -> None:
    backtest_dir = tmp_path / "backtest"
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    output_dir = tmp_path / "evidence"
    _write_minimal_backtest_bundle(backtest_dir)
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "write-professional-evidence",
            "--backtest-bundle-dir",
            str(backtest_dir),
            "--walk-forward-bundle-dir",
            str(walk_forward_dir),
            "--allocator-friction-bundle-dir",
            str(allocator_dir),
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

    assert (output_dir / "walk_forward_oos_report.json").is_file()
    assert (output_dir / "cost_sensitivity_report.json").is_file()
    evidence_chain = json.loads((output_dir / "backtest_evidence_chain.json").read_text(encoding="utf-8"))
    assert evidence_chain["summary"]["decision"] == "pass"
    assert evidence_chain["walk_forward_oos"]["status"] == "pass"
    assert evidence_chain["cost_sensitivity"]["status"] == "pass"
    assert evidence_chain["execution_realism"]["status"] == "pass"
    assert str(output_dir / "backtest_evidence_chain.json") in completed.stdout


def test_backtest_cli_writes_hold_evidence_chain_from_execution_calibration_unavailable_marker(tmp_path: Path) -> None:
    backtest_dir = tmp_path / "backtest"
    walk_forward_dir = tmp_path / "walk_forward"
    allocator_dir = tmp_path / "allocator_friction"
    output_dir = tmp_path / "evidence"
    marker_path = tmp_path / "calibration_records_unavailable.json"
    _write_minimal_backtest_bundle(backtest_dir)
    _write_walk_forward_bundle(walk_forward_dir)
    _write_allocator_friction_bundle(allocator_dir)
    _write_json(
        marker_path,
        {
            "schema_version": "calibration_records_unavailable.v1",
            "status": "unavailable",
            "generated_at": GENERATED_AT,
            "reason_codes": ["no_canonical_execution_events"],
            "canonical_event_count": 0,
            "record_count": 0,
            "decision_policy": "fail_closed",
        },
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "write-professional-evidence",
            "--backtest-bundle-dir",
            str(backtest_dir),
            "--walk-forward-bundle-dir",
            str(walk_forward_dir),
            "--allocator-friction-bundle-dir",
            str(allocator_dir),
            "--output-dir",
            str(output_dir),
            "--execution-calibration-unavailable-path",
            str(marker_path),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    evidence_chain = json.loads((output_dir / "backtest_evidence_chain.json").read_text(encoding="utf-8"))
    assert evidence_chain["summary"]["decision"] == "hold"
    assert evidence_chain["summary"]["component_statuses"]["execution_realism"] == "hold"
    assert "no_canonical_execution_events" in evidence_chain["execution_realism"]["reason_codes"]
