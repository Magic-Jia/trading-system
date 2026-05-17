from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.reporting.longitudinal_live_sim_trend_report import (
    build_longitudinal_live_sim_trend_report,
    write_longitudinal_live_sim_trend_report,
)


def _daily_payload(report_date: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "report_date": report_date,
        "generated_at": f"{report_date}T23:30:00Z",
        "daily_quality_gate": {
            "decision": "pass_for_continued_paper",
            "reasons": [],
            "checks": {
                "paper_shadow_material_drift_absent": True,
                "reconciliation_passed": True,
                "execution_chain_present": True,
                "tca_slippage_within_threshold": True,
                "latency_distribution_stable": True,
                "data_freshness_met": True,
                "sufficient_sample_size": True,
            },
        },
        "tca": {
            "sample_size": 48,
            "p95_slippage_bps": 3.0,
            "max_p95_slippage_bps": 5.0,
            "checks": {"all_metrics_within_tolerance": True, "evidence_fresh": True},
        },
        "drift": {
            "paper_live_shadow_drift_bps": 0.4,
            "max_abs_drift_bps": 2.0,
            "checks": {"paper_live_shadow_material_drift_absent": True},
        },
        "reconciliation": {
            "checks": {
                "execution_event_chain_met": True,
                "order_position_reconciliation_met": True,
            }
        },
        "latency": {"p95_ms": 115.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25},
        "slippage": {"p95_bps": 3.0, "max_p95_bps": 5.0},
        "fill_quality": {"fill_rate": 0.92, "min_fill_rate": 0.80, "partial_fill_rate": 0.08, "max_partial_fill_rate": 0.25},
        "freshness": {"max_age_seconds": 3600.0, "oldest_age_seconds": 300.0},
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


def test_longitudinal_report_passes_and_summarizes_multi_day_simulated_live_trends() -> None:
    report = build_longitudinal_live_sim_trend_report(
        daily_reports=[
            _daily_payload("2026-05-14", latency={"p95_ms": 100.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25}),
            _daily_payload(
                "2026-05-15",
                tca={"sample_size": 52, "p95_slippage_bps": 3.2, "max_p95_slippage_bps": 5.0},
                latency={"p95_ms": 104.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25},
                fill_quality={"fill_rate": 0.93, "min_fill_rate": 0.80, "partial_fill_rate": 0.07, "max_partial_fill_rate": 0.25},
            ),
            _daily_payload(
                "2026-05-16",
                tca={"sample_size": 55, "p95_slippage_bps": 3.4, "max_p95_slippage_bps": 5.0},
                latency={"p95_ms": 108.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25},
                fill_quality={"fill_rate": 0.94, "min_fill_rate": 0.80, "partial_fill_rate": 0.06, "max_partial_fill_rate": 0.25},
            ),
        ],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
    )

    assert report["schema_version"] == "longitudinal_live_sim_trend_report.v1"
    assert report["mode"] == "simulated_live"
    assert report["decision"] == "pass_for_continued_paper"
    assert report["reasons"] == []
    assert report["checks"]["all_expected_days_present"] is True
    assert report["checks"]["all_days_well_formed"] is True
    assert report["checks"]["no_regressions_detected"] is True
    assert report["trend_checks"]["latency_p95_ms"]["direction"] == "up"
    assert report["trend_checks"]["latency_p95_ms"]["delta"] == 8.0
    assert report["trend_checks"]["fill_rate"]["direction"] == "up"
    assert report["trend_checks"]["tca_p95_slippage_bps"]["last"] == 3.4
    assert [day["report_date"] for day in report["days"]] == ["2026-05-14", "2026-05-15", "2026-05-16"]


def test_longitudinal_report_rejects_missing_stale_and_malformed_days() -> None:
    malformed = _daily_payload("2026-05-16", freshness={"max_age_seconds": 3600.0, "oldest_age_seconds": 7200.0})
    malformed["tca"] = {"sample_size": "55", "p95_slippage_bps": 3.4, "max_p95_slippage_bps": 5.0}

    report = build_longitudinal_live_sim_trend_report(
        daily_reports=[
            _daily_payload("2026-05-14"),
            malformed,
        ],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
    )

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == ["missing_day", "stale_day", "malformed_day"]
    assert report["checks"]["all_expected_days_present"] is False
    assert report["checks"]["all_days_fresh"] is False
    assert report["checks"]["all_days_well_formed"] is False
    assert report["missing_dates"] == ["2026-05-15"]
    assert report["days"][-1]["status"] == "reject"
    assert "tca.sample_size_not_int" in report["days"][-1]["malformed_inputs"]
    assert "freshness_stale" in report["days"][-1]["reasons"]


def test_longitudinal_report_identifies_regressions_across_daily_evidence() -> None:
    report = build_longitudinal_live_sim_trend_report(
        daily_reports=[
            _daily_payload("2026-05-14", latency={"p95_ms": 100.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25}),
            _daily_payload(
                "2026-05-15",
                tca={"sample_size": 50, "p95_slippage_bps": 4.0, "max_p95_slippage_bps": 5.0},
                latency={"p95_ms": 120.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25},
                fill_quality={"fill_rate": 0.86, "min_fill_rate": 0.80, "partial_fill_rate": 0.18, "max_partial_fill_rate": 0.25},
            ),
            _daily_payload(
                "2026-05-16",
                tca={"sample_size": 50, "p95_slippage_bps": 4.8, "max_p95_slippage_bps": 5.0},
                latency={"p95_ms": 135.0, "baseline_p95_ms": 100.0, "max_p95_shift_pct": 0.25},
                fill_quality={"fill_rate": 0.82, "min_fill_rate": 0.80, "partial_fill_rate": 0.23, "max_partial_fill_rate": 0.25},
            ),
        ],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
        regression_thresholds={
            "latency_p95_ms": 25.0,
            "tca_p95_slippage_bps": 1.5,
            "fill_rate": 0.08,
            "partial_fill_rate": 0.10,
        },
    )

    assert report["decision"] == "hold_for_review"
    assert report["reasons"] == [
        "latency_regression",
        "slippage_regression",
        "fill_quality_regression",
    ]
    assert report["checks"]["no_regressions_detected"] is False
    assert report["trend_checks"]["latency_p95_ms"]["regressed"] is True
    assert report["trend_checks"]["tca_p95_slippage_bps"]["regressed"] is True
    assert report["trend_checks"]["fill_rate"]["regressed"] is True
    assert report["trend_checks"]["partial_fill_rate"]["regressed"] is True


def test_longitudinal_report_surfaces_rolling_tca_durability_reason_from_daily_gate() -> None:
    failing_day = _daily_payload("2026-05-16")
    failing_day["daily_quality_gate"] = {
        "decision": "reject_live_promotion",
        "reasons": ["rolling_tca_durability_failed", "bucket_regression"],
        "checks": {
            "paper_shadow_material_drift_absent": True,
            "reconciliation_passed": True,
            "execution_chain_present": True,
            "tca_slippage_within_threshold": True,
            "latency_distribution_stable": True,
            "data_freshness_met": True,
            "sufficient_sample_size": True,
            "rolling_tca_durability_passed": False,
            "rolling_tca_bucket_samples_sufficient": True,
        },
    }

    report = build_longitudinal_live_sim_trend_report(
        daily_reports=[
            _daily_payload("2026-05-14"),
            _daily_payload("2026-05-15"),
            failing_day,
        ],
        start_date="2026-05-14",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
    )

    assert report["decision"] == "reject_live_promotion"
    assert report["reasons"] == [
        "daily_quality_gate_rejected",
        "rolling_tca_durability_failed",
        "bucket_regression",
    ]
    assert report["days"][-1]["reasons"] == [
        "daily_quality_gate_rejected",
        "rolling_tca_durability_failed",
        "bucket_regression",
    ]
    assert report["checks"]["rolling_tca_durability_stable"] is False


def test_writes_longitudinal_report_as_machine_readable_json(tmp_path: Path) -> None:
    output_path = tmp_path / "optimization" / "longitudinal_live_sim_trend_report.json"

    payload = write_longitudinal_live_sim_trend_report(
        output_path,
        daily_reports=[_daily_payload("2026-05-16")],
        start_date="2026-05-16",
        end_date="2026-05-16",
        generated_at="2026-05-16T23:40:00Z",
    )

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
