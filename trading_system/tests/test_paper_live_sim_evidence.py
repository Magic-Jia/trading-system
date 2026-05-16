from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from trading_system.app.runtime.paper_live_sim_evidence import (
    FILENAME,
    SCHEMA_VERSION,
    build_paper_live_sim_evidence_bundle,
    validate_paper_live_sim_evidence_bundle,
    write_paper_live_sim_evidence_bundle,
)


def _write_minimal_readiness_bundle(root: Path) -> None:
    trade = {
        "trade_id": "t1",
        "symbol": "BTCUSDT",
        "side": "long",
        "setup_type": "BREAKOUT_CONTINUATION",
        "net_pnl": 100.0,
        "gross_pnl": 125.0,
        "fee_paid": 10.0,
        "slippage_paid": 10.0,
        "funding_paid": 5.0,
        "quantity": 0.01,
        "notional": 650.0,
        "entry_price": 65000.0,
        "exit_price": 65125.0,
        "fill_quality": "evidence_backed",
        "execution_price_source": "trade_print",
        "exit_fill_quality": "evidence_backed",
        "exit_price_source": "trade_print",
        "simulated_exit_reason": "take_profit",
    }
    (root / "trades.json").write_text(json.dumps({"trades": [trade]}), encoding="utf-8")
    (root / "exit_path_replay.json").write_text(json.dumps({"trades": [{"trade_id": "t1"}]}), encoding="utf-8")


def _valid_manifest() -> dict:
    return {
        "bundle_id": "paper-live-sim-1",
        "generated_at": "2026-05-16T10:00:10Z",
        "max_evidence_age_seconds": 300,
        "evidence_source": {
            "type": "paper_runtime_logs",
            "run_id": "paper-run-1",
            "exported_at": "2026-05-16T10:00:10Z",
        },
        "lineage": {
            "strategy_id": "trend_breakout_v2",
            "code_version": "abc123",
            "config_hash": "f" * 64,
            "data_snapshot_id": "snapshot-1",
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


def test_builds_canonical_paper_live_sim_evidence_bundle(tmp_path: Path) -> None:
    bundle = build_paper_live_sim_evidence_bundle(_valid_manifest())

    assert bundle["schema_version"] == SCHEMA_VERSION
    assert bundle["bundle_id"] == "paper-live-sim-1"
    assert bundle["evidence_source"]["type"] == "paper_runtime_logs"
    assert bundle["checks"] == {
        "paper_live_sim_evidence_complete": True,
        "paper_live_sim_schema_valid": True,
        "paper_live_sim_freshness_valid": True,
        "paper_live_sim_reconciled": True,
    }
    assert bundle["summary"]["stage_count"] == 9
    assert bundle["summary"]["first_as_of"] == "2026-05-16T10:00:00Z"
    assert bundle["summary"]["last_as_of"] == "2026-05-16T10:00:08Z"
    assert bundle["reasons"] == []

    path = write_paper_live_sim_evidence_bundle(_valid_manifest(), tmp_path)
    assert path == tmp_path / FILENAME
    assert validate_paper_live_sim_evidence_bundle(json.loads(path.read_text())) == bundle


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest["stages"].pop(5), "missing paper-live sim evidence stage: fill"),
        (
            lambda manifest: manifest["stages"][1].__setitem__("event_id", "evt-001"),
            "duplicate paper-live sim evidence event_id: evt-001",
        ),
        (
            lambda manifest: manifest["stages"][2].__setitem__("as_of", "2026-05-16T10:00:01Z"),
            "stages[2].as_of must be strictly monotonic",
        ),
        (
            lambda manifest: manifest["stages"][0].__setitem__("as_of", "2026-05-16T09:54:59Z"),
            "stages[0].as_of is stale",
        ),
        (
            lambda manifest: manifest["stages"][0].__setitem__("as_of", "2026-05-16T10:00:11Z"),
            "stages[0].as_of must not be after generated_at",
        ),
        (
            lambda manifest: manifest["stages"][0]["payload"].__setitem__("score", "0.73"),
            "stages[0].payload.score must be numeric",
        ),
        (
            lambda manifest: manifest["stages"][0]["payload"].__setitem__("score", True),
            "stages[0].payload.score must be numeric, not boolean",
        ),
        (
            lambda manifest: manifest["stages"][0]["payload"].__setitem__("score", math.inf),
            "stages[0].payload.score must be finite",
        ),
        (
            lambda manifest: manifest.__setitem__("evidence_source", {"type": "paper runtime"}),
            "evidence_source type must be a safe identifier",
        ),
        (
            lambda manifest: manifest["stages"][6]["payload"].__setitem__("reconciled", False),
            "final position reconcile must be reconciled",
        ),
        (
            lambda manifest: manifest["stages"][6]["payload"].__setitem__("unreconciled_quantity", 0.01),
            "final position reconcile must have zero unreconciled_quantity",
        ),
    ],
)
def test_rejects_malformed_paper_live_sim_evidence_bundle(mutate, message: str) -> None:
    manifest = _valid_manifest()
    mutate(manifest)

    with pytest.raises(ValueError, match=re.escape(message)):
        build_paper_live_sim_evidence_bundle(manifest)


def test_live_readiness_report_surfaces_paper_live_sim_evidence(tmp_path: Path) -> None:
    from trading_system.app.backtest.live_readiness import write_live_readiness_smoke_report

    source = tmp_path / "source"
    source.mkdir()
    _write_minimal_readiness_bundle(source)
    write_paper_live_sim_evidence_bundle(_valid_manifest(), source)

    report = write_live_readiness_smoke_report(
        source,
        tmp_path / "out",
        max_setup_trade_share=None,
        max_symbol_trade_share=None,
        max_setup_net_abs_share=None,
        max_symbol_net_abs_share=None,
        max_setup_loss_abs_share=None,
        max_symbol_loss_abs_share=None,
    )

    assert report["paper_live_sim_evidence"]["checks"]["paper_live_sim_schema_valid"] is True
    assert report["promotion_gate"]["checks"]["paper_live_sim_evidence_complete"] is True
    assert "paper_live_sim_evidence_invalid" not in report["promotion_gate"]["reasons"]


def test_live_readiness_rejects_malformed_paper_live_sim_evidence(tmp_path: Path) -> None:
    from trading_system.app.backtest.live_readiness import write_live_readiness_smoke_report

    source = tmp_path / "source"
    source.mkdir()
    _write_minimal_readiness_bundle(source)
    invalid = _valid_manifest()
    invalid["stages"][6]["payload"]["reconciled"] = False
    (source / FILENAME).write_text(json.dumps(invalid), encoding="utf-8")

    report = write_live_readiness_smoke_report(
        source,
        tmp_path / "out",
        max_setup_trade_share=None,
        max_symbol_trade_share=None,
        max_setup_net_abs_share=None,
        max_symbol_net_abs_share=None,
        max_setup_loss_abs_share=None,
        max_symbol_loss_abs_share=None,
    )

    assert report["paper_live_sim_evidence"]["checks"]["paper_live_sim_schema_valid"] is False
    assert report["paper_live_sim_evidence"]["parse_error"] == "final position reconcile must be reconciled"
    assert "paper_live_sim_evidence_invalid" in report["promotion_gate"]["reasons"]
