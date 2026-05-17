from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from trading_system.app.reporting.execution_stream_producers import (
    build_execution_race_evidence,
    build_l2_longitudinal_replay_calibration,
    write_execution_race_evidence,
    write_l2_longitudinal_replay_calibration,
)
from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import (
    build_rolling_simulated_live_evidence_bundle,
)


GENERATED_AT = "2026-05-16T10:01:00Z"


def _stage(
    name: str,
    index: int,
    *,
    correlation_id: str = "paper-order-1",
    event_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    second = index
    return {
        "stage": name,
        "event_id": event_id or f"evt-{index:03d}",
        "correlation_id": correlation_id,
        "as_of": f"2026-05-16T10:00:{second:02d}Z",
        "observed_at": f"2026-05-16T10:00:{second:02d}.500000Z",
        "payload": payload or {},
    }


def _paper_bundle(*, stages: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "paper_live_sim_evidence_bundle.v1",
        "bundle_id": "paper-live-sim-1",
        "generated_at": "2026-05-16T10:00:30Z",
        "source_mode": "simulated_live_local",
        "stages": stages
        or [
            _stage("signal", 0, payload={"symbol": "BTCUSDT", "score": 0.72}),
            _stage("order_intent", 1, payload={"client_order_id": "paper-order-1", "quantity": 0.01}),
            _stage("risk_check", 2, payload={"passed": True, "notional": 650.0}),
            _stage("submit", 3, payload={"client_order_id": "paper-order-1", "simulator_order_id": "sim-1"}),
            _stage("ack", 4, payload={"acknowledged": True, "simulator_order_id": "sim-1"}),
            _stage("fill", 5, payload={"fill_id": "fill-1", "filled_quantity": 0.01, "fill_price": 65001.0}),
            _stage(
                "position_reconcile",
                6,
                payload={
                    "reconciled": True,
                    "expected_position_qty": 0.01,
                    "actual_position_qty": 0.01,
                    "unreconciled_quantity": 0.0,
                },
            ),
            _stage("paper_snapshot", 7, payload={"equity": 10000.0, "position_qty": 0.01}),
            _stage("shadow_snapshot", 8, payload={"equity": 10000.0, "position_qty": 0.01}),
        ],
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _component(
    schema_version: str,
    *,
    artifact_id: str,
    generated_at: str = GENERATED_AT,
    status: str = "pass",
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "artifact_id": artifact_id,
        "generated_at": generated_at,
        "status": status,
        "decision": "accepted" if status == "pass" else "hold",
        "reason_codes": reason_codes or [],
    }


def _passing_bundle_components(race: Path, l2: Path) -> dict[str, object]:
    return {
        "daily_quality_gate": _component("daily_quality_gate_report.v1", artifact_id="daily-quality"),
        "rolling_tca_durability": _component("rolling_tca_durability_report.v1", artifact_id="rolling-tca"),
        "l2_longitudinal_replay_calibration": l2,
        "cross_source_parity": _component("cross_source_parity_report.v1", artifact_id="cross-source-parity"),
        "venue_rulebook_catalog_freshness": _component(
            "venue_rulebook_catalog_freshness.v1",
            artifact_id="venue-rulebook",
        ),
        "execution_race_evidence": race,
    }


def test_l2_producer_holds_when_depth_evidence_is_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    _write_json(source, _paper_bundle())

    report = build_l2_longitudinal_replay_calibration(source, generated_at=GENERATED_AT)

    assert report["schema_version"] == "l2_longitudinal_replay_calibration.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["source_mode"] == "simulated_live_local"
    assert report["status"] == "hold"
    assert report["decision"] == "hold"
    assert report["reason_codes"] == ["l2_depth_evidence_unavailable"]
    assert report["checks"]["l2_depth_evidence_present"] is False
    assert report["replay_metrics"] is None
    assert report["source"]["path"] == str(source)
    assert len(report["source"]["sha256"]) == 64
    assert report["provenance"]["decision_policy"] == "fail_closed"


def test_execution_race_producer_passes_ordered_local_stage_fixture(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    _write_json(source, _paper_bundle())

    report = build_execution_race_evidence(source, generated_at=GENERATED_AT)

    assert report["schema_version"] == "execution_race_evidence.v1"
    assert report["status"] == "pass"
    assert report["decision"] == "accepted"
    assert report["reason_codes"] == []
    assert report["checks"] == {
        "ordering_evidence_complete": True,
        "correlation_ids_consistent": True,
        "event_ids_unique": True,
        "order_stages_monotonic": True,
        "terminal_state_coherent": True,
    }
    assert report["correlations"] == [
        {
            "correlation_id": "paper-order-1",
            "first_stage": "signal",
            "last_stage": "position_reconcile",
            "terminal_state": "filled_reconciled",
            "stage_count": 7,
        }
    ]


def test_execution_race_producer_passes_multiple_ordered_correlations(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    stages = []
    for order_index, correlation_id in enumerate(("paper-order-1", "paper-order-2")):
        base = order_index * 10
        stages.extend(
            [
                _stage("signal", base, correlation_id=correlation_id, payload={"symbol": "BTCUSDT", "score": 0.72}),
                _stage(
                    "order_intent",
                    base + 1,
                    correlation_id=correlation_id,
                    payload={"client_order_id": correlation_id, "quantity": 0.01},
                ),
                _stage("risk_check", base + 2, correlation_id=correlation_id, payload={"passed": True}),
                _stage("submit", base + 3, correlation_id=correlation_id, payload={"client_order_id": correlation_id}),
                _stage("ack", base + 4, correlation_id=correlation_id, payload={"acknowledged": True}),
                _stage(
                    "fill",
                    base + 5,
                    correlation_id=correlation_id,
                    payload={"filled_quantity": 0.01, "fill_price": 65001.0},
                ),
                _stage(
                    "position_reconcile",
                    base + 6,
                    correlation_id=correlation_id,
                    payload={"reconciled": True, "unreconciled_quantity": 0.0},
                ),
            ]
        )
    _write_json(source, _paper_bundle(stages=stages))

    report = build_execution_race_evidence(source, generated_at=GENERATED_AT)

    assert report["status"] == "pass"
    assert report["checks"]["correlation_ids_consistent"] is True
    assert [item["correlation_id"] for item in report["correlations"]] == ["paper-order-1", "paper-order-2"]


def test_execution_race_producer_holds_for_missing_malformed_duplicate_and_conflicting_events(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    stages = [
        _stage("signal", 0),
        _stage("order_intent", 1, event_id="evt-dup", payload={"client_order_id": "paper-order-1"}),
        _stage("risk_check", 2, event_id="evt-dup", payload={"passed": "yes"}),
        _stage("submit", 3, payload={"client_order_id": "paper-order-1"}),
        _stage("fill", 4, payload={"filled_quantity": 0.01, "fill_price": 65001.0}),
        _stage(
            "position_reconcile",
            5,
            payload={"reconciled": False, "unreconciled_quantity": 0.01},
        ),
    ]
    _write_json(source, _paper_bundle(stages=stages))

    report = build_execution_race_evidence(source, generated_at=GENERATED_AT)

    assert report["status"] == "hold"
    assert report["decision"] == "hold"
    assert report["reason_codes"] == [
        "duplicate_order_event",
        "malformed_order_event",
        "missing_order_ack",
        "ordering_evidence_incomplete",
        "terminal_state_conflict",
    ]
    assert report["checks"]["ordering_evidence_complete"] is False
    assert report["checks"]["event_ids_unique"] is False
    assert report["checks"]["terminal_state_coherent"] is False


def test_execution_race_producer_holds_for_out_of_order_events(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    stages = list(_paper_bundle()["stages"])  # type: ignore[index]
    stages[3], stages[4] = stages[4], stages[3]
    _write_json(source, _paper_bundle(stages=stages))

    report = build_execution_race_evidence(source, generated_at=GENERATED_AT)

    assert report["status"] == "hold"
    assert "order_stage_out_of_order" in report["reason_codes"]
    assert report["checks"]["order_stages_monotonic"] is False


def test_producers_write_artifacts_and_rolling_bundle_consumes_hold_status(tmp_path: Path) -> None:
    source = tmp_path / "paper_live_sim_evidence_bundle.json"
    _write_json(source, _paper_bundle())
    race_path = tmp_path / "execution_race_evidence.json"
    l2_path = tmp_path / "l2_longitudinal_replay_calibration.json"

    write_execution_race_evidence(race_path, source, generated_at=GENERATED_AT)
    write_l2_longitudinal_replay_calibration(l2_path, source, generated_at=GENERATED_AT)

    bundle = build_rolling_simulated_live_evidence_bundle(
        components=_passing_bundle_components(race_path, l2_path),
        generated_at="2026-05-16T10:02:00Z",
        max_artifact_age_seconds=3600,
    )

    assert bundle["decision"] == "hold"
    assert bundle["reason_codes"] == ["l2_depth_evidence_unavailable"]
    assert {component["component"]: component["status"] for component in bundle["components"]}[
        "execution_race_evidence"
    ] == "pass"
    assert {component["component"]: component["status"] for component in bundle["components"]}[
        "l2_longitudinal_replay_calibration"
    ] == "hold"


def test_execution_stream_producer_clis_write_default_runtime_artifacts(tmp_path: Path) -> None:
    optimization_dir = tmp_path / "runtime" / "paper" / "paper" / "optimization"
    source = optimization_dir / "paper_live_sim_evidence_bundle.json"
    _write_json(source, _paper_bundle())

    for module_name, output_name, marker in [
        (
            "trading_system.generate_execution_race_evidence",
            "execution_race_evidence.json",
            "EXECUTION_RACE_EVIDENCE_JSON",
        ),
        (
            "trading_system.generate_l2_longitudinal_replay_calibration",
            "l2_longitudinal_replay_calibration.json",
            "L2_LONGITUDINAL_REPLAY_CALIBRATION_JSON",
        ),
    ]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                module_name,
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--generated-at",
                GENERATED_AT,
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        )

        assert (optimization_dir / output_name).exists()
        assert re.search(marker + r".*simulated_live_local", result.stdout)
