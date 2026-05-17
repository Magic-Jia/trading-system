from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from trading_system.app.reporting.market_coverage import (
    build_cross_source_parity_report,
    build_venue_rulebook_catalog_freshness_report,
    write_cross_source_parity_report,
    write_venue_rulebook_catalog_freshness_report,
)
from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import (
    build_rolling_simulated_live_evidence_bundle,
)


GENERATED_AT = "2026-05-17T01:05:00Z"
EVIDENCE_AT = "2026-05-17T01:00:00Z"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _base_runtime(root: Path) -> None:
    _write_json(
        root / "paper_live_sim_evidence_bundle.json",
        {
            "schema_version": "paper_live_sim_evidence_bundle.v1",
            "bundle_id": "paper-live-sim-20260517",
            "generated_at": EVIDENCE_AT,
            "evidence_source": {"type": "local_simulated_live_runtime", "run_id": "run-20260517"},
            "summary": {"trade_count": 1, "symbols": ["BTCUSDT"]},
        },
    )
    _write_json(
        root / "paper_live_sim_evidence_manifest.json",
        {
            "schema_version": "paper_live_sim_evidence_bundle.v1",
            "bundle_id": "paper-live-sim-20260517",
            "generated_at": EVIDENCE_AT,
        },
    )
    _write_json(
        root / "runtime_safety_gate.json",
        {
            "schema_version": "runtime_safety_gate_input.v1",
            "generated_at": EVIDENCE_AT,
            "decision": "pass",
            "checks": {"local_runtime_only": True},
        },
    )
    _write_json(
        root / "paper_live_shadow_drift_contract.json",
        {
            "schema_version": "paper_live_shadow_drift_contract.v1",
            "generated_at": EVIDENCE_AT,
            "decision": "pass",
            "checks": {"paper_live_shadow_drift_contract_schema_valid": True},
        },
    )


def test_market_coverage_reports_hold_when_independent_sources_and_catalog_are_unavailable(tmp_path: Path) -> None:
    _base_runtime(tmp_path)

    parity = build_cross_source_parity_report(tmp_path, generated_at=GENERATED_AT)
    freshness = build_venue_rulebook_catalog_freshness_report(tmp_path, generated_at=GENERATED_AT)

    assert parity["schema_version"] == "cross_source_parity_report.v1"
    assert parity["status"] == "hold"
    assert parity["decision"] == "hold"
    assert "independent_source_unavailable" in parity["reason_codes"]
    assert parity["checks"]["independent_source_available"] is False
    assert parity["side_effect_boundary"]["exchange_api_calls"] == "forbidden"

    assert freshness["schema_version"] == "venue_rulebook_catalog_freshness.v1"
    assert freshness["status"] == "hold"
    assert freshness["decision"] == "hold"
    assert "venue_rulebook_catalog_unavailable" in freshness["reason_codes"]
    assert freshness["checks"]["venue_rulebook_catalog_available"] is False
    assert freshness["side_effect_boundary"]["credential_use"] == "forbidden"


def test_market_coverage_reports_fail_closed_for_missing_and_malformed_runtime_inputs(tmp_path: Path) -> None:
    _base_runtime(tmp_path)
    (tmp_path / "runtime_safety_gate.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "paper_live_shadow_drift_contract.json").unlink()

    parity = build_cross_source_parity_report(tmp_path, generated_at=GENERATED_AT)
    freshness = build_venue_rulebook_catalog_freshness_report(tmp_path, generated_at=GENERATED_AT)

    assert parity["status"] == "hold"
    assert "malformed_runtime_input" in parity["reason_codes"]
    assert "runtime_input_missing" in parity["reason_codes"]
    assert any(source["name"] == "runtime_safety_gate" for source in parity["malformed_inputs"])
    assert any(source["name"] == "paper_live_shadow_drift_contract" for source in parity["missing_inputs"])

    assert freshness["status"] == "hold"
    assert "malformed_runtime_input" in freshness["reason_codes"]
    assert "runtime_input_missing" in freshness["reason_codes"]


def test_market_coverage_reports_use_local_independent_source_and_catalog_when_present(tmp_path: Path) -> None:
    _base_runtime(tmp_path)
    _write_json(
        tmp_path / "local_independent_source_snapshot.json",
        {
            "schema_version": "local_independent_source_snapshot.v1",
            "generated_at": EVIDENCE_AT,
            "source_id": "reference-feed-a",
            "observations": [{"symbol": "BTCUSDT", "mid_price": 100.0}],
        },
    )
    _write_json(
        tmp_path / "venue_rulebook_catalog.json",
        {
            "schema_version": "venue_rulebook_catalog.v1",
            "generated_at": EVIDENCE_AT,
            "effective_at": EVIDENCE_AT,
            "rulebooks": [
                {
                    "schema_version": "venue_rulebook_report.v1",
                    "venue": "binance",
                    "symbol": "BTCUSDT",
                    "product_type": "futures",
                    "rulebook_version": "local-20260517",
                    "generated_at": EVIDENCE_AT,
                    "effective_at": EVIDENCE_AT,
                    "source": "local_fixture",
                    "constraints": {
                        "price_tick_size": 0.1,
                        "quantity_step_size": 0.001,
                        "min_notional": 5.0,
                        "post_only_policy": "reject_taker",
                        "reduce_only_policy": "supported",
                    },
                }
            ],
        },
    )

    parity = build_cross_source_parity_report(tmp_path, generated_at=GENERATED_AT)
    freshness = build_venue_rulebook_catalog_freshness_report(tmp_path, generated_at=GENERATED_AT)

    assert parity["status"] == "review"
    assert parity["decision"] == "review"
    assert parity["checks"]["independent_source_available"] is True
    assert "cross_source_threshold_not_evaluable" in parity["reason_codes"]

    assert freshness["status"] == "review"
    assert freshness["decision"] == "review"
    assert freshness["checks"]["venue_rulebook_catalog_available"] is True
    assert freshness["checks"]["venue_rulebook_catalog_schema_valid"] is True


def test_market_coverage_writers_do_not_use_network_when_generating_hold_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_runtime(tmp_path)

    def _blocked_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", _blocked_socket)

    write_cross_source_parity_report(tmp_path, generated_at=GENERATED_AT)
    write_venue_rulebook_catalog_freshness_report(tmp_path, generated_at=GENERATED_AT)

    assert json.loads((tmp_path / "cross_source_parity_report.json").read_text(encoding="utf-8"))["status"] == "hold"
    assert json.loads((tmp_path / "venue_rulebook_catalog_freshness.json").read_text(encoding="utf-8"))["status"] == "hold"


def test_market_coverage_clis_write_expected_filenames(tmp_path: Path) -> None:
    _base_runtime(tmp_path)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_cross_source_parity_report",
            "--optimization-dir",
            str(tmp_path),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.generate_venue_rulebook_catalog_freshness",
            "--optimization-dir",
            str(tmp_path),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (tmp_path / "cross_source_parity_report.json").is_file()
    assert (tmp_path / "venue_rulebook_catalog_freshness.json").is_file()
    assert json.loads((tmp_path / "cross_source_parity_report.json").read_text(encoding="utf-8"))["status"] == "hold"
    assert json.loads((tmp_path / "venue_rulebook_catalog_freshness.json").read_text(encoding="utf-8"))["status"] == "hold"


def test_market_coverage_outputs_are_consumable_by_rolling_bundle(tmp_path: Path) -> None:
    _base_runtime(tmp_path)
    parity = write_cross_source_parity_report(tmp_path, generated_at=GENERATED_AT)
    freshness = write_venue_rulebook_catalog_freshness_report(tmp_path, generated_at=GENERATED_AT)

    components = {
        "daily_quality_gate": {
            "schema_version": "daily_quality_gate_report.v1",
            "generated_at": GENERATED_AT,
            "status": "pass",
            "decision": "pass",
            "artifact_id": "daily-quality",
        },
        "rolling_tca_durability": {
            "schema_version": "rolling_tca_durability_report.v1",
            "generated_at": GENERATED_AT,
            "status": "pass",
            "decision": "pass",
            "artifact_id": "rolling-tca",
        },
        "l2_longitudinal_replay_calibration": {
            "schema_version": "l2_longitudinal_replay_calibration.v1",
            "generated_at": GENERATED_AT,
            "status": "pass",
            "decision": "pass",
            "artifact_id": "l2-replay",
        },
        "cross_source_parity": parity,
        "venue_rulebook_catalog_freshness": freshness,
        "execution_race_evidence": {
            "schema_version": "execution_race_evidence.v1",
            "generated_at": GENERATED_AT,
            "status": "pass",
            "decision": "pass",
            "artifact_id": "execution-race",
        },
    }

    bundle = build_rolling_simulated_live_evidence_bundle(
        components=components,
        generated_at=GENERATED_AT,
        max_artifact_age_seconds=3600,
    )

    assert bundle["decision"] == "hold"
    assert "independent_source_unavailable" in bundle["reason_codes"]
    assert "venue_rulebook_catalog_unavailable" in bundle["reason_codes"]
