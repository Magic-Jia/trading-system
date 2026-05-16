from __future__ import annotations

import json
from pathlib import Path

from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.scheduled_live_sim_generation import main, run_scheduled_generation


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _evidence_manifest() -> dict[str, object]:
    return {
        "bundle_id": "paper-live-sim-20260516",
        "generated_at": "2026-05-16T10:00:10Z",
        "max_evidence_age_seconds": 300,
        "evidence_source": {
            "type": "paper_runtime_logs",
            "run_id": "paper-run-20260516",
            "exported_at": "2026-05-16T10:00:10Z",
        },
        "lineage": {
            "strategy_id": "trend_breakout_v2",
            "code_version": "abc123",
            "config_hash": "f" * 64,
            "data_snapshot_id": "snapshot-20260516",
        },
        "stages": [
            {
                "stage": "signal",
                "event_id": "evt-001",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:00Z",
                "observed_at": "2026-05-16T10:00:01Z",
                "payload": {"symbol": "BTCUSDT", "side": "long", "score": 0.73},
            },
            {
                "stage": "order_intent",
                "event_id": "evt-002",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:01Z",
                "observed_at": "2026-05-16T10:00:02Z",
                "payload": {"client_order_id": "paper-order-1", "quantity": 0.01, "limit_price": 65000.0},
            },
            {
                "stage": "risk_check",
                "event_id": "evt-003",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:02Z",
                "observed_at": "2026-05-16T10:00:03Z",
                "payload": {"passed": True, "max_notional": 1000.0, "notional": 650.0},
            },
            {
                "stage": "submit",
                "event_id": "evt-004",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:03Z",
                "observed_at": "2026-05-16T10:00:04Z",
                "payload": {"client_order_id": "paper-order-1", "simulator_order_id": "sim-1"},
            },
            {
                "stage": "ack",
                "event_id": "evt-005",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:04Z",
                "observed_at": "2026-05-16T10:00:05Z",
                "payload": {"simulator_order_id": "sim-1", "acknowledged": True},
            },
            {
                "stage": "fill",
                "event_id": "evt-006",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:05Z",
                "observed_at": "2026-05-16T10:00:06Z",
                "payload": {"fill_id": "fill-1", "filled_quantity": 0.01, "fill_price": 65001.0},
            },
            {
                "stage": "position_reconcile",
                "event_id": "evt-007",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:06Z",
                "observed_at": "2026-05-16T10:00:07Z",
                "payload": {
                    "reconciled": True,
                    "expected_position_qty": 0.01,
                    "actual_position_qty": 0.01,
                    "unreconciled_quantity": 0.0,
                },
            },
            {
                "stage": "paper_snapshot",
                "event_id": "evt-008",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:07Z",
                "observed_at": "2026-05-16T10:00:08Z",
                "payload": {"equity": 10000.0, "position_qty": 0.01},
            },
            {
                "stage": "shadow_snapshot",
                "event_id": "evt-009",
                "correlation_id": "paper-order-1",
                "as_of": "2026-05-16T10:00:08Z",
                "observed_at": "2026-05-16T10:00:09Z",
                "payload": {"equity": 10000.0, "position_qty": 0.01},
            },
        ],
    }


def _calibration_records() -> list[dict[str, object]]:
    base = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "intended_limit_price": 100.0,
        "signal_at": "2026-05-16T10:00:00Z",
        "decision_at": "2026-05-16T10:00:01Z",
        "submitted_at": "2026-05-16T10:00:02Z",
        "exchange_ack_at": "2026-05-16T10:00:03Z",
        "first_fill_at": "2026-05-16T10:00:04Z",
        "last_fill_at": "2026-05-16T10:00:05Z",
        "requested_qty": 1.0,
        "filled_qty": 1.0,
        "filled_notional": 100.0,
        "status": "filled",
        "maker_taker": "maker",
        "slippage_bps": 2.0,
        "adverse_selection_bps": 1.0,
        "fees": 0.01,
        "funding": 0.0,
    }
    taker = dict(base, symbol="ETHUSDT", maker_taker="taker", slippage_bps=3.0)
    partial = dict(base, symbol="SOLUSDT", status="partially_filled", filled_qty=0.5, maker_taker="maker")
    rejected = dict(
        base,
        symbol="BNBUSDT",
        first_fill_at=None,
        last_fill_at=None,
        filled_qty=0.0,
        filled_notional=None,
        status="rejected",
        cancel_ack_at="2026-05-16T10:00:05Z",
        cancel_reason="post_only_reject",
        slippage_bps=None,
        adverse_selection_bps=None,
        fees=0.0,
    )
    return [base, taker, partial, rejected]


def _assumptions() -> dict[str, object]:
    return {
        "expected_slippage_bps": 2.0,
        "expected_fill_probability": 0.75,
        "expected_maker_rate": 0.75,
        "expected_taker_rate": 0.25,
        "expected_ack_latency_ms": 1000.0,
        "expected_fill_latency_ms": 1000.0,
        "expected_cancel_latency_ms": 3000.0,
        "expected_partial_fill_rate": 0.25,
        "expected_adverse_selection_bps": 1.0,
        "expected_fee_funding_bps": 1.0,
        "expected_reject_reason_rates": {"post_only_reject": 0.25},
    }


def _drift_contract() -> dict[str, object]:
    return {
        "schema_version": "paper_live_shadow_drift_contract.v1",
        "mode": "offline_simulated",
        "fail_closed": True,
        "checks": {
            "paper_live_shadow_drift_contract_schema_valid": True,
            "paper_live_shadow_material_drift_absent": True,
        },
    }


def _reconciliation() -> dict[str, object]:
    return {
        "schema_version": "runtime_safety_gate_input.v1",
        "checks": {
            "execution_event_chain_met": True,
            "order_position_reconciliation_met": True,
        },
    }


def _write_inputs(paths) -> None:
    opt = paths.optimization_dir
    _write_json(opt / "paper_live_sim_evidence_manifest.json", _evidence_manifest())
    _write_jsonl(opt / "passive_order_calibration_records.jsonl", _calibration_records())
    _write_json(opt / "tca_assumptions.json", _assumptions())
    _write_json(opt / "paper_live_shadow_drift_contract.json", _drift_contract())
    _write_json(opt / "runtime_safety_gate.json", _reconciliation())


def test_scheduled_generation_writes_deterministic_simulated_live_artifacts(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="paper")
    _write_inputs(paths)

    result = run_scheduled_generation(
        mode="paper",
        runtime_root=tmp_path / "runtime",
        runtime_env="paper",
        generated_at="2026-05-16T10:00:10Z",
        max_evidence_age_seconds=300,
        min_tca_samples=4,
        max_p95_slippage_bps=5.0,
    )

    evidence = json.loads((paths.optimization_dir / "paper_live_sim_evidence_bundle.json").read_text())
    calibration = json.loads((paths.optimization_dir / "passive_order_calibration_summary.json").read_text())
    tca = json.loads((paths.optimization_dir / "tca_calibration_report.json").read_text())
    gate = json.loads((paths.optimization_dir / "daily_quality_gate_report.json").read_text())

    assert result["status"] == "ok"
    assert result["generated_artifacts"] == {
        "paper_live_sim_evidence_bundle": str(paths.optimization_dir / "paper_live_sim_evidence_bundle.json"),
        "passive_order_calibration_summary": str(paths.optimization_dir / "passive_order_calibration_summary.json"),
        "tca_calibration_report": str(paths.optimization_dir / "tca_calibration_report.json"),
        "daily_quality_gate_report": str(paths.optimization_dir / "daily_quality_gate_report.json"),
    }
    assert evidence["schema_version"] == "paper_live_sim_evidence_bundle.v1"
    assert calibration["schema_version"] == "passive_order_calibration_summary.v1"
    assert tca["decision"] == "pass"
    assert gate["decision"] == "pass_for_continued_paper"
    assert gate["inputs"]["tca"]["p95_slippage_bps"] == 3.0


def test_scheduled_generation_cli_fails_closed_for_missing_required_inputs(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="paper")
    paths.optimization_dir.mkdir(parents=True)
    _write_json(paths.optimization_dir / "paper_live_sim_evidence_manifest.json", _evidence_manifest())

    exit_code = main(
        [
            "--mode",
            "paper",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--runtime-env",
            "paper",
            "--generated-at",
            "2026-05-16T10:00:10Z",
        ]
    )

    assert exit_code == 1
    failure = json.loads((paths.optimization_dir / "scheduled_live_sim_generation_error.json").read_text())
    assert failure["status"] == "fail_closed"
    assert failure["error_type"] == "FileNotFoundError"
    assert "passive_order_calibration_records.jsonl" in failure["error_message"]


def test_paper_cron_runs_scheduled_generation_after_cycle() -> None:
    script = Path("deploy/cron/trading-system-paper-cron.sh").read_text(encoding="utf-8")

    assert "python -m trading_system.scheduled_live_sim_generation" in script
    assert "--mode \"${MODE}\"" in script
    assert "--runtime-env \"${RUNTIME_ENV}\"" in script
