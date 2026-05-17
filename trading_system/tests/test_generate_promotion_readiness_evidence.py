from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.promotion_readiness_scorecard import build_promotion_readiness_scorecard
from trading_system.generate_promotion_readiness_evidence import (
    build_promotion_readiness_evidence,
    write_promotion_readiness_evidence,
)


GENERATED_AT = "2026-05-16T12:00:00Z"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _paper_live_bundle() -> dict[str, object]:
    return {
        "schema_version": "paper_live_sim_evidence_bundle.v1",
        "generated_at": "2026-05-16T11:45:00Z",
        "checks": {
            "paper_live_sim_evidence_complete": True,
            "paper_live_sim_schema_valid": True,
            "paper_live_sim_freshness_valid": True,
            "paper_live_sim_reconciled": True,
        },
        "summary": {
            "stage_count": 9,
            "first_as_of": "2026-05-12T11:45:00Z",
            "last_as_of": "2026-05-16T11:45:00Z",
        },
        "reasons": [],
    }


def _daily_quality_gate() -> dict[str, object]:
    return {
        "schema_version": "daily_quality_gate_report.v1",
        "generated_at": "2026-05-16T11:50:00Z",
        "decision": "pass_for_continued_paper",
        "reasons": [],
        "checks": {
            "evidence_bundle_verified": True,
            "evidence_bundle_manifest_present": True,
            "paper_live_shadow_drift_contract_schema_valid": True,
            "paper_shadow_material_drift_absent": True,
            "execution_chain_present": True,
            "reconciliation_passed": True,
            "sufficient_sample_size": True,
            "tca_slippage_within_threshold": True,
            "rolling_tca_durability_passed": True,
            "venue_rulebook_catalog_present": True,
            "venue_rulebook_schema_valid": True,
            "venue_rulebook_freshness_valid": True,
            "exchange_filters_covered": True,
        },
        "inputs": {
            "tca": {
                "sample_size": 64,
                "p95_slippage_bps": 1.2,
                "max_p95_slippage_bps": 5.0,
            }
        },
    }


def _rolling_tca() -> dict[str, object]:
    return {
        "schema_version": "rolling_tca_durability_report.v1",
        "mode": "simulated_live",
        "generated_at": "2026-05-16T11:50:00Z",
        "decision": "durable",
        "reasons": [],
        "canonical_dates": ["2026-05-14", "2026-05-15", "2026-05-16"],
        "checks": {
            "all_expected_dates_present": True,
            "no_stale_dates": True,
            "all_records_well_formed": True,
            "all_bucket_fields_known": True,
            "all_bucket_windows_sufficiently_sampled": True,
            "no_threshold_breaches": True,
        },
        "windows": [
            {
                "window": "3d",
                "metrics": {
                    "sample_count": 64,
                    "slippage_bps": {"p95": 1.2},
                    "latency_ms": {"p95": 250.0},
                },
            }
        ],
    }


def _drift_contract() -> dict[str, object]:
    return {
        "schema_version": "paper_live_shadow_drift_contract.v1",
        "mode": "offline_simulated",
        "fail_closed": True,
        "generated_at": "2026-05-16T11:50:00Z",
        "checks": {
            "paper_live_shadow_drift_contract_schema_valid": True,
            "paper_live_shadow_material_drift_absent": True,
            "material_drift_absent": True,
            "offline_simulated_evidence_only": True,
            "fail_closed": True,
        },
        "comparisons": {
            "paper_vs_shadow": {
                "fill_rate_delta": 0.001,
                "slippage_bps_delta": 0.4,
                "latency_ms_delta": 25.0,
                "net_pnl_delta": 0.0,
            }
        },
        "reasons": [],
    }


def _runtime_safety_gate() -> dict[str, object]:
    return {
        "schema_version": "runtime_safety_gate_input.v1",
        "evidence_source": {"type": "paper_runtime_logs", "run_id": "local-paper-run"},
        "checks": {
            "execution_event_chain_met": True,
            "order_position_reconciliation_met": True,
            "kill_switch_dry_run_met": True,
            "runtime_safety_artifact_schema_valid": True,
        },
        "summary": {"event_count": 8},
        "reasons": [],
    }


def _calibration_rows(count: int = 64) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        minute = index % 60
        rows.append(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "intended_limit_price": 100.0,
                "signal_at": f"2026-05-16T10:{minute:02d}:00Z",
                "decision_at": f"2026-05-16T10:{minute:02d}:01Z",
                "submitted_at": f"2026-05-16T10:{minute:02d}:02Z",
                "exchange_ack_at": f"2026-05-16T10:{minute:02d}:03Z",
                "first_fill_at": f"2026-05-16T10:{minute:02d}:04Z",
                "last_fill_at": f"2026-05-16T10:{minute:02d}:05Z",
                "requested_qty": 1.0,
                "filled_qty": 1.0,
                "filled_notional": 100.0,
                "status": "filled",
                "maker_taker": "maker",
                "slippage_bps": 1.0,
                "adverse_selection_bps": 0.5,
                "fees": 0.01,
                "funding": 0.0,
            }
        )
    return rows


def _write_complete_runtime(runtime_dir: Path) -> None:
    _write_json(runtime_dir / "paper_live_sim_evidence_bundle.json", _paper_live_bundle())
    _write_json(runtime_dir / "daily_quality_gate_report.json", _daily_quality_gate())
    _write_json(runtime_dir / "rolling_tca_durability_report.json", _rolling_tca())
    _write_json(runtime_dir / "paper_live_shadow_drift_contract.json", _drift_contract())
    _write_json(runtime_dir / "runtime_safety_gate.json", _runtime_safety_gate())
    _write_jsonl(runtime_dir / "passive_order_calibration_records.jsonl", _calibration_rows())


def test_builds_candidate_quality_promotion_readiness_evidence_from_complete_runtime(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "optimization"
    _write_complete_runtime(runtime_dir)

    evidence = build_promotion_readiness_evidence(runtime_dir, generated_at=GENERATED_AT)
    scorecard = build_promotion_readiness_scorecard(evidence, generated_at=GENERATED_AT)

    assert evidence["schema_version"] == "promotion_readiness_evidence.v1"
    assert evidence["source_mode"] == "simulated_live_local"
    assert evidence["generated_at"] == GENERATED_AT
    assert evidence["data_quality"]["status"] == "pass"
    assert evidence["execution_realism"]["sample_count"] >= 30
    assert evidence["venue_rulebook_coverage"]["coverage_score"] >= 0.8
    assert evidence["derivatives_risk"]["reason_codes"] == []
    assert evidence["cross_source_parity"]["max_parity_drift_bps"] == 0.4
    assert evidence["live_sim_durability"]["duration_hours"] == 96.0
    assert scorecard["decision"] == "pass"
    assert scorecard["scores"]["promotion_readiness"] >= 85.0
    assert set(evidence["sources"]) == {
        "paper_live_sim_evidence_bundle",
        "daily_quality_gate_report",
        "rolling_tca_durability_report",
        "paper_live_shadow_drift_contract",
        "runtime_safety_gate",
        "passive_order_calibration_records",
    }
    for source in evidence["sources"].values():
        assert source["sha256"]
        assert source["provenance"]["source_mode"] == "simulated_live_local"


def test_sparse_runtime_emits_hold_evidence_without_fabricating_missing_sections(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "optimization"
    _write_json(
        runtime_dir / "runtime_safety_gate.json",
        {
            "schema_version": "runtime_safety_gate_input.v1",
            "checks": {"execution_event_chain_met": False, "order_position_reconciliation_met": False},
            "reasons": ["execution_chain_missing"],
        },
    )

    evidence = build_promotion_readiness_evidence(runtime_dir, generated_at=GENERATED_AT)
    scorecard = build_promotion_readiness_scorecard(evidence, generated_at=GENERATED_AT)

    assert evidence["source_mode"] == "simulated_live_local"
    assert evidence["summary"]["decision"] == "hold"
    assert scorecard["decision"] == "hold"
    assert scorecard["scores"]["promotion_readiness"] < 50.0
    assert evidence["data_quality"]["status"] == "hold"
    assert evidence["data_quality"]["reason_codes"] == ["source_missing:daily_quality_gate_report"]
    assert evidence["execution_realism"]["reason_codes"] == ["execution_chain_missing", "source_missing:passive_order_calibration_records"]
    assert evidence["venue_rulebook_coverage"]["reason_codes"] == ["source_missing:venue_rulebook_catalog_freshness"]
    assert evidence["cross_source_parity"]["reason_codes"] == ["source_missing:paper_live_shadow_drift_contract"]
    assert evidence["live_sim_durability"]["reason_codes"] == ["source_missing:paper_live_sim_evidence_bundle"]
    assert set(evidence["sources"]) == {"runtime_safety_gate"}
    assert "daily_quality_gate_report" in evidence["missing_sources"]
    assert "paper_live_shadow_drift_contract" in evidence["missing_sources"]


def test_writes_promotion_readiness_evidence_and_cli(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "optimization"
    _write_complete_runtime(runtime_dir)

    payload = write_promotion_readiness_evidence(runtime_dir, generated_at=GENERATED_AT)
    output_path = runtime_dir / "promotion_readiness_evidence.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload

    output_path.unlink()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_promotion_readiness_evidence",
            "--runtime-optimization-dir",
            str(runtime_dir),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert output_path.exists()
    assert "PROMOTION_READINESS_EVIDENCE_JSON" in completed.stdout
