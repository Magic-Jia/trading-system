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


def test_generate_execution_calibration_records_rejects_duplicate_trade_identity(tmp_path: Path) -> None:
    execution_log = tmp_path / "execution_log.jsonl"
    output = tmp_path / "passive_order_calibration_records.jsonl"
    _write_jsonl(execution_log, _event_chain() + _event_chain(intent_id="intent-2", order_id="order-2"))

    with pytest.raises(ValueError, match="duplicate trade_id"):
        generate_execution_calibration_records(execution_log_file=execution_log, output_file=output)


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
