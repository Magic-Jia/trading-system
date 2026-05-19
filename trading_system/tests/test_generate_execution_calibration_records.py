from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.execution.calibration import load_calibration_records
from trading_system.generate_execution_calibration_records import (
    generate_execution_calibration_records,
    main,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _event_chain(**overrides: object) -> list[dict[str, object]]:
    base = {
        "intent_id": "intent-1",
        "order_id": "order-1",
        "trade_id": "trade-1",
        "position_id": "pos-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 0.1,
        "price": 100.0,
        "ref_price": 99.5,
        "maker_taker": "maker",
        "fee": 0.01,
        "funding": 0.0,
        "setup_type": "TREND",
    }
    base.update(overrides)
    stages = [
        ("signal", "accepted", "2026-05-16T10:00:00Z"),
        ("order_intent", "created", "2026-05-16T10:00:01Z"),
        ("risk_check", "passed", "2026-05-16T10:00:02Z"),
        ("submit", "submitted", "2026-05-16T10:00:03Z"),
        ("exchange_ack", "acknowledged", "2026-05-16T10:00:04Z"),
        ("fill", "filled", "2026-05-16T10:00:05Z"),
        ("position_reconcile", "reconciled", "2026-05-16T10:00:06Z"),
    ]
    return [{**base, "stage": stage, "status": status, "occurred_at": occurred_at} for stage, status, occurred_at in stages]


def _partial_cancel_event_chain(**overrides: object) -> list[dict[str, object]]:
    rows = _event_chain(**overrides)
    rows[5].update(
        {
            "status": "partially_filled",
            "quantity": 0.04,
            "filled_qty": 0.04,
            "price": 100.0,
            "occurred_at": "2026-05-16T10:00:05Z",
        }
    )
    rows.insert(
        6,
        {
            **rows[5],
            "stage": "cancel_request",
            "status": "requested",
            "trade_id": "trade-1",
            "quantity": 0.1,
            "occurred_at": "2026-05-16T10:00:06Z",
        },
    )
    rows.insert(
        7,
        {
            **rows[5],
            "stage": "cancel_ack",
            "status": "cancelled",
            "trade_id": "trade-1",
            "quantity": 0.1,
            "cancel_reason": "user_cancel",
            "occurred_at": "2026-05-16T10:00:08Z",
        },
    )
    rows[-1] = {**rows[-1], "occurred_at": "2026-05-16T10:00:09Z"}
    return rows


def _cancel_no_fill_event_chain(**overrides: object) -> list[dict[str, object]]:
    rows = _event_chain(**overrides)
    rows.pop(5)
    rows.insert(
        5,
        {
            **rows[4],
            "stage": "cancel_request",
            "status": "requested",
            "occurred_at": "2026-05-16T10:00:06Z",
        },
    )
    rows.insert(
        6,
        {
            **rows[4],
            "stage": "cancel_ack",
            "status": "cancelled",
            "cancel_reason": "user_cancel",
            "filled_qty": 0.0,
            "occurred_at": "2026-05-16T10:00:08Z",
        },
    )
    rows[-1] = {**rows[-1], "occurred_at": "2026-05-16T10:00:09Z"}
    return rows


def _replace_event_chain(**overrides: object) -> list[dict[str, object]]:
    rows = _event_chain(**overrides)
    rows.insert(
        5,
        {
            **rows[4],
            "stage": "replace_request",
            "status": "requested",
            "price": 99.8,
            "occurred_at": "2026-05-16T10:00:05Z",
        },
    )
    rows.insert(
        6,
        {
            **rows[4],
            "stage": "replace_ack",
            "status": "acknowledged",
            "price": 99.8,
            "occurred_at": "2026-05-16T10:00:07Z",
        },
    )
    rows[7].update({"occurred_at": "2026-05-16T10:00:08Z", "price": 99.8, "filled_notional": 9.98})
    rows[8].update({"occurred_at": "2026-05-16T10:00:09Z"})
    return rows


def _late_fill_cancel_race_event_chain(**overrides: object) -> list[dict[str, object]]:
    rows = _event_chain(**overrides)
    rows[5].update(
        {
            "status": "partially_filled",
            "quantity": 0.04,
            "filled_qty": 0.04,
            "price": 100.0,
            "occurred_at": "2026-05-16T10:00:05Z",
            "event_id": "evt-fill-before-cancel",
        }
    )
    rows.insert(
        6,
        {
            **rows[5],
            "stage": "cancel_request",
            "status": "requested",
            "quantity": 0.1,
            "filled_qty": None,
            "occurred_at": "2026-05-16T10:00:06Z",
            "event_id": "evt-cancel-request",
        },
    )
    rows.insert(
        7,
        {
            **rows[5],
            "stage": "cancel_ack",
            "status": "cancelled",
            "quantity": 0.1,
            "filled_qty": None,
            "cancel_reason": "user_cancel",
            "occurred_at": "2026-05-16T10:00:08Z",
            "event_id": "evt-cancel-ack",
        },
    )
    rows.insert(
        8,
        {
            **rows[5],
            "stage": "fill",
            "status": "filled",
            "trade_id": "trade-2",
            "quantity": 0.01,
            "filled_qty": 0.01,
            "price": 100.0,
            "occurred_at": "2026-05-16T10:00:08.500000Z",
            "event_id": "evt-fill-after-cancel",
        },
    )
    rows[-1] = {**rows[-1], "occurred_at": "2026-05-16T10:00:09Z", "event_id": "evt-reconcile"}
    for index, row in enumerate(rows):
        row.setdefault("event_id", f"evt-{index}")
        row["client_order_id"] = "client-1"
        row["exchange_timestamp"] = row["occurred_at"]
    return rows


def _ledger_event() -> dict[str, object]:
    return {
        "event_type": "paper_fill",
        "recorded_at": "2026-05-16T10:00:05Z",
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
            "order_id": "order-1",
            "trade_id": "trade-1",
            "status": "FILLED",
            "qty": 0.1,
            "price": 100.0,
            "maker_taker": "maker",
            "fee": 0.01,
        },
        "position_update": {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0},
        "replay_result": {"status": "FILLED", "intent_id": "intent-1"},
    }


def test_generate_execution_calibration_records_writes_loader_compatible_jsonl(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    ledger = tmp_path / "paper_ledger.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _event_chain())
    _write_jsonl(ledger, [_ledger_event()])

    result = generate_execution_calibration_records(
        execution_log_file=execution_log,
        paper_ledger_file=ledger,
        output_file=output,
    )

    records = load_calibration_records(output)
    assert result["status"] == "ok"
    assert result["record_count"] == 1
    assert len(records) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["intent_id"] == "intent-1"
    assert payload["order_id"] == "order-1"
    assert payload["trade_id"] == "trade-1"
    record = records[0]
    assert record.symbol == "BTCUSDT"
    assert record.side == "buy"
    assert record.intended_limit_price == pytest.approx(100.0)
    assert record.requested_qty == pytest.approx(0.1)
    assert record.filled_qty == pytest.approx(0.1)
    assert record.filled_notional == pytest.approx(10.0)
    assert record.status == "filled"
    assert record.maker_taker == "maker"
    assert record.slippage_bps == pytest.approx(50.2512562814)


def test_generate_execution_calibration_records_captures_partial_fill_cancel_lifecycle(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _partial_cancel_event_chain())

    result = generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert result["record_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "cancelled"
    assert payload["terminal_status"] == "cancelled"
    assert payload["first_fill_at"] == "2026-05-16T10:00:05Z"
    assert payload["cancel_requested_at"] == "2026-05-16T10:00:06Z"
    assert payload["cancel_ack_at"] == "2026-05-16T10:00:08Z"
    assert payload["cancel_latency_ms"] == pytest.approx(2000.0)
    assert payload["partial_fill_before_cancel"] is True
    assert payload["filled_qty"] == pytest.approx(0.04)
    assert load_calibration_records(output)[0].terminal_status == "cancelled"


def test_generate_execution_calibration_records_captures_no_fill_cancel_lifecycle(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _cancel_no_fill_event_chain())

    result = generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert result["record_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "cancelled"
    assert payload["terminal_status"] == "cancelled"
    assert "first_fill_at" not in payload
    assert "last_fill_at" not in payload
    assert payload["filled_qty"] == pytest.approx(0.0)
    assert "filled_notional" not in payload
    assert payload["cancel_requested_at"] == "2026-05-16T10:00:06Z"
    assert payload["cancel_ack_at"] == "2026-05-16T10:00:08Z"
    assert payload["cancel_latency_ms"] == pytest.approx(2000.0)
    record = load_calibration_records(output)[0]
    assert record.filled_qty == pytest.approx(0.0)
    assert record.filled_notional is None


def test_generate_execution_calibration_records_allows_no_fill_cancel_without_fill_ledger_match(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    ledger = tmp_path / "paper_ledger.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(
        execution_log,
        _event_chain()
        + _cancel_no_fill_event_chain(
            intent_id="intent-cancel-1",
            order_id="order-cancel-1",
            trade_id="trade-cancel-1",
        ),
    )
    _write_jsonl(ledger, [_ledger_event()])

    result = generate_execution_calibration_records(
        execution_log_file=execution_log,
        paper_ledger_file=ledger,
        output_file=output,
    )

    payloads = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    cancel_payload = next(payload for payload in payloads if payload["intent_id"] == "intent-cancel-1")
    assert result["record_count"] == 2
    assert cancel_payload["status"] == "cancelled"
    assert cancel_payload["filled_qty"] == pytest.approx(0.0)
    assert "trade_id" not in cancel_payload
    assert cancel_payload["cancel_latency_ms"] == pytest.approx(2000.0)
    assert len(load_calibration_records(output)) == 2


def test_generate_execution_calibration_records_captures_replace_lifecycle(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _replace_event_chain())

    result = generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert result["record_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["replace_requested_at"] == "2026-05-16T10:00:05Z"
    assert payload["replace_ack_at"] == "2026-05-16T10:00:07Z"
    assert payload["replace_latency_ms"] == pytest.approx(2000.0)
    assert payload["terminal_status"] == "filled"
    assert payload["status"] == "filled"


def test_generate_execution_calibration_records_emits_cancel_fill_race_evidence(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _late_fill_cancel_race_event_chain())

    result = generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert result["record_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["client_order_id"] == "client-1"
    assert payload["terminal_status"] == "conflict"
    assert payload["race_condition_status"] == "hold_for_review"
    assert payload["reason_codes"] == ["fill_after_cancel_ack", "terminal_status_conflict"]
    assert payload["late_fill_quantity"] == pytest.approx(0.01)
    assert payload["late_fill_notional"] == pytest.approx(1.0)
    assert payload["filled_qty"] == pytest.approx(0.05)
    assert load_calibration_records(output)[0].race_condition_status == "hold_for_review"


def test_generate_execution_calibration_records_ignores_recommendation_only_paper_trades(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(
        execution_log,
        [
            {
                "recorded_at": "2026-05-16T10:00:00Z",
                "symbol": "BTCUSDT",
                "action": "BUY",
                "recommendation": "open_paper_position",
                "confidence": 0.72,
            }
        ],
    )

    result = generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert result["status"] == "ok"
    assert result["record_count"] == 0
    assert output.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.pop(3), "missing lifecycle stage submit"),
        (lambda rows: rows[4].update({"occurred_at": "2026-05-16T10:00:04+00:00"}), "occurred_at must be a canonical UTC timestamp"),
        (lambda rows: rows[2].update({"occurred_at": "2026-05-16T09:59:59Z"}), "lifecycle timestamps must be monotonic"),
        (lambda rows: rows[5].update({"order_id": "other-order"}), "identity mismatch"),
        (lambda rows: rows[5].update({"quantity": True}), "quantity must be numeric"),
        (lambda rows: rows[5].update({"quantity": "0.1"}), "quantity must be numeric"),
        (lambda rows: rows[5].update({"quantity": float("inf")}), "quantity must be finite"),
        (lambda rows: rows[5].update({"quantity": 0.2}), "filled quantity cannot exceed requested quantity"),
        (lambda rows: rows[5].update({"maker_taker": None}), "maker_taker must be maker or taker"),
        (lambda rows: rows[5].update({"status": "FILLED"}), "fill status must be canonical"),
        (lambda rows: rows[1].update({"event_id": "evt-dup"}) or rows[2].update({"event_id": "evt-dup"}), "duplicate lifecycle event_id"),
    ],
)
def test_generate_execution_calibration_records_fails_closed_for_malformed_lifecycle(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    rows = _event_chain()
    mutate(rows)
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, rows)

    with pytest.raises(ValueError, match=message):
        generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert not output.exists()


@pytest.mark.parametrize(
    ("rows_factory", "mutate", "message"),
    [
        (
            _partial_cancel_event_chain,
            lambda rows: rows.pop(6),
            "cancel_ack requires cancel_request",
        ),
        (
            _partial_cancel_event_chain,
            lambda rows: rows[6].update({"occurred_at": "2026-05-16T10:00:02Z"}),
            "cancel_request must be at or after exchange_ack",
        ),
        (
            _partial_cancel_event_chain,
            lambda rows: rows[7].update({"occurred_at": "2026-05-16T10:00:05.500000Z"}),
            "cancel_ack must be at or after cancel_request",
        ),
        (
            _partial_cancel_event_chain,
            lambda rows: rows[7].update({"occurred_at": "2026-05-16T10:00:02Z"}),
            "cancel_ack must be at or after exchange_ack",
        ),
        (
            _partial_cancel_event_chain,
            lambda rows: rows.insert(
                8,
                {
                    **rows[5],
                    "stage": "fill",
                    "status": "partially_filled",
                    "quantity": 0.01,
                    "occurred_at": "2026-05-16T10:00:08.500000Z",
                },
            ),
            "fill after terminal cancel",
        ),
        (
            _replace_event_chain,
            lambda rows: rows.pop(5),
            "replace_ack requires replace_request",
        ),
    ],
)
def test_generate_execution_calibration_records_fails_closed_for_malformed_cancel_replace_lifecycle(
    tmp_path: Path,
    rows_factory,
    mutate,
    message: str,
) -> None:
    rows = rows_factory()
    mutate(rows)
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, rows)

    with pytest.raises(ValueError, match=message):
        generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)

    assert not output.exists()


def test_generate_execution_calibration_records_rejects_duplicate_trade_identity(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _event_chain() + _event_chain(intent_id="intent-2", order_id="order-2"))

    with pytest.raises(ValueError, match="duplicate trade_id"):
        generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)


def test_generate_execution_calibration_records_cli_writes_unavailable_marker_when_runtime_has_no_execution_events(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    bucket = runtime_root / "paper" / "phase-three"
    optimization = bucket / "optimization"

    exit_code = main(["--mode", "paper", "--runtime-root", str(runtime_root), "--runtime-env", "phase-three"])

    output = optimization / "passive_order_calibration_records.jsonl"
    unavailable = optimization / "calibration_records_unavailable.json"
    marker = json.loads(unavailable.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == ""
    assert marker["schema_version"] == "calibration_records_unavailable.v1"
    assert marker["status"] == "unavailable"
    assert marker["reason_codes"] == ["execution_log_missing", "no_canonical_execution_events"]
    assert marker["execution_log_file"]["exists"] is False
    assert marker["record_count"] == 0


def test_generate_execution_calibration_records_cli_removes_unavailable_marker_after_valid_write(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    bucket = runtime_root / "paper" / "phase-three"
    optimization = bucket / "optimization"
    unavailable = optimization / "calibration_records_unavailable.json"
    _write_jsonl(bucket / "execution_log.jsonl", _event_chain())
    _write_jsonl(bucket / "paper_ledger.jsonl", [_ledger_event()])
    unavailable.parent.mkdir(parents=True, exist_ok=True)
    unavailable.write_text('{"reason":"calibration_records_unavailable"}\n', encoding="utf-8")

    exit_code = main(["--mode", "paper", "--runtime-root", str(runtime_root), "--runtime-env", "phase-three"])

    output = optimization / "passive_order_calibration_records.jsonl"
    assert exit_code == 0
    assert len(load_calibration_records(output)) == 1
    assert not unavailable.exists()
