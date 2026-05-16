from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from trading_system.app.runtime.ledger_reconciliation import (
    build_ledger_reconciliation_evidence,
    reconciliation_runtime_safety_events,
    write_ledger_reconciliation_evidence,
)


def _snapshot(ts: str = "2026-05-16T10:00:00Z") -> dict:
    return {
        "schema_version": "exchange_reconciliation_snapshot.v1",
        "captured_at": ts,
        "max_evidence_age_seconds": 120,
        "exchange_account_state": "known",
        "account": {
            "equity": 1000.0,
            "available_balance": 900.0,
            "futures_wallet_balance": 1000.0,
        },
        "orders": [
            {
                "order_id": "ord-1",
                "client_order_id": "intent-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "status": "FILLED",
                "qty": 0.1,
                "price": 100.0,
                "updated_at": ts,
            }
        ],
        "trades": [
            {
                "trade_id": "trade-1",
                "order_id": "ord-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.1,
                "price": 100.0,
                "executed_at": ts,
            }
        ],
        "positions": [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.1,
                "entry_price": 100.0,
                "updated_at": ts,
            }
        ],
    }


def _ledger_event(ts: str = "2026-05-16T10:00:00Z") -> dict:
    return {
        "event_type": "paper_fill",
        "recorded_at": ts,
        "intent_id": "intent-1",
        "symbol": "BTCUSDT",
        "order": {
            "intent_id": "intent-1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "qty": 0.1,
            "entry_price": 100.0,
        },
        "result": {
            "order_id": "ord-1",
            "trade_id": "trade-1",
            "status": "FILLED",
            "qty": 0.1,
            "price": 100.0,
        },
        "position_update": {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0},
        "replay_result": {"status": "FILLED", "intent_id": "intent-1"},
    }


def _event_chain() -> list[dict]:
    base = {
        "intent_id": "intent-1",
        "order_id": "ord-1",
        "trade_id": "trade-1",
        "position_id": "pos-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "quantity": 0.1,
        "price": 100.0,
    }
    stages = [
        ("signal", "accepted", "2026-05-16T10:00:00Z"),
        ("order_intent", "created", "2026-05-16T10:00:01Z"),
        ("risk_check", "passed", "2026-05-16T10:00:02Z"),
        ("submit", "submitted", "2026-05-16T10:00:03Z"),
        ("exchange_ack", "acknowledged", "2026-05-16T10:00:04Z"),
        ("fill", "filled", "2026-05-16T10:00:05Z"),
        ("position_reconcile", "reconciled", "2026-05-16T10:00:06Z"),
    ]
    return [{**base, "stage": stage, "status": status, "occurred_at": ts} for stage, status, ts in stages]


def _reconciliation_manifest() -> dict:
    return {
        "evidence_source": {
            "type": "offline_fixture",
            "run_id": "reconcile-1",
            "exported_at": "2026-05-16T10:00:30Z",
        },
        "evaluated_at": "2026-05-16T10:00:30Z",
        "event_chain": _event_chain(),
        "ledger_events": [_ledger_event()],
        "order_snapshot": _snapshot()["orders"],
        "trade_snapshot": _snapshot()["trades"],
        "position_snapshot": [
            {
                **_snapshot()["positions"][0],
                "position_id": "pos-1",
                "order_id": "ord-1",
                "intent_id": "intent-1",
            }
        ],
        "account_snapshot": _snapshot()["account"],
        "snapshot_metadata": {
            "captured_at": "2026-05-16T10:00:00Z",
            "max_evidence_age_seconds": 120,
            "exchange_account_state": "known",
        },
    }


def test_builds_fail_closed_reconciliation_evidence_for_matching_local_snapshots(tmp_path: Path) -> None:
    evidence = build_ledger_reconciliation_evidence(_reconciliation_manifest())

    assert evidence["schema_version"] == "ledger_exchange_reconciliation.v1"
    assert evidence["checks"] == {
        "ledger_exchange_reconciliation_met": True,
        "event_chain_complete_met": True,
        "event_chain_canonical_met": True,
        "event_chain_monotonic_met": True,
        "event_chain_identity_consistent_met": True,
        "event_chain_final_state_reconciled_met": True,
        "snapshots_present_met": True,
        "order_ids_known_unique_met": True,
        "trade_ids_known_unique_met": True,
        "positions_match_met": True,
        "account_balances_match_met": True,
        "timestamps_canonical_met": True,
        "numerics_finite_met": True,
        "evidence_fresh_met": True,
        "exchange_account_state_resolved_met": True,
    }
    assert evidence["reasons"] == []
    assert evidence["summary"]["ledger_event_count"] == 1

    events = reconciliation_runtime_safety_events(evidence)
    assert events == [
        {"type": "execution_event_chain", "passed": True},
        {"type": "order_position_reconciliation", "passed": True},
        {"type": "live_trade_ledger", "passed": True},
        {"type": "runtime_fail_closed", "passed": True},
    ]

    output = write_ledger_reconciliation_evidence(evidence, tmp_path)
    assert output == tmp_path / "ledger_exchange_reconciliation.json"
    assert json.loads(output.read_text()) == evidence


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda manifest: manifest.pop("order_snapshot"), "missing_order_snapshot"),
        (
            lambda manifest: manifest["order_snapshot"].append(dict(manifest["order_snapshot"][0])),
            "duplicate_order_id",
        ),
        (lambda manifest: manifest["trade_snapshot"][0].pop("trade_id"), "unknown_trade_id"),
        (lambda manifest: manifest["position_snapshot"].clear(), "position_snapshot_mismatch"),
        (lambda manifest: manifest["account_snapshot"].__setitem__("equity", 999.0), "account_snapshot_mismatch"),
        (
            lambda manifest: manifest["order_snapshot"][0].__setitem__("updated_at", "2026-05-16T10:00:00+00:00"),
            "noncanonical_timestamp",
        ),
        (lambda manifest: manifest["trade_snapshot"][0].__setitem__("qty", "0.1"), "numeric_string"),
        (lambda manifest: manifest["position_snapshot"][0].__setitem__("qty", math.inf), "nonfinite_numeric"),
        (lambda manifest: manifest["event_chain"].pop(2), "event_chain_missing_stage"),
        (lambda manifest: manifest["event_chain"][0].__setitem__("status", "Accepted"), "event_chain_noncanonical_status"),
        (
            lambda manifest: manifest["event_chain"][3].__setitem__("occurred_at", "2026-05-16T10:00:01Z"),
            "event_chain_nonmonotonic_timestamp",
        ),
        (lambda manifest: manifest["event_chain"][4].__setitem__("order_id", "ord-2"), "event_chain_identity_mismatch"),
        (lambda manifest: manifest["event_chain"][5].__setitem__("quantity", True), "bool_numeric"),
        (lambda manifest: manifest["event_chain"][5].__setitem__("price", math.nan), "nonfinite_numeric"),
        (
            lambda manifest: manifest["event_chain"][-1].__setitem__("status", "pending"),
            "event_chain_unreconciled_final_state",
        ),
        (
            lambda manifest: manifest["snapshot_metadata"].__setitem__("captured_at", "2026-05-16T10:00:31Z"),
            "future_evidence",
        ),
        (
            lambda manifest: manifest["snapshot_metadata"].__setitem__("captured_at", "2026-05-16T09:57:00Z"),
            "stale_evidence",
        ),
        (
            lambda manifest: manifest["snapshot_metadata"].__setitem__("exchange_account_state", "unknown"),
            "exchange_account_state_unresolved",
        ),
    ],
)
def test_reconciliation_fails_closed_for_invalid_snapshot_contracts(mutation, reason: str) -> None:
    manifest = _reconciliation_manifest()
    mutation(manifest)

    evidence = build_ledger_reconciliation_evidence(manifest)

    assert evidence["checks"]["ledger_exchange_reconciliation_met"] is False
    assert reason in evidence["reasons"]
    assert reconciliation_runtime_safety_events(evidence) == [
        {"type": "execution_event_chain", "passed": False},
        {"type": "order_position_reconciliation", "passed": False},
        {"type": "live_trade_ledger", "passed": False},
        {"type": "runtime_fail_closed", "passed": True},
    ]
