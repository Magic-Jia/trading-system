from __future__ import annotations

import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.execution.calibration import load_calibration_records
from trading_system.app.runtime_paths import build_runtime_paths

CALIBRATION_RECORDS_NAME = "passive_order_calibration_records.jsonl"
CALIBRATION_UNAVAILABLE_NAME = "calibration_records_unavailable.json"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")
_IDENTITY_FIELDS = ("intent_id", "order_id", "position_id", "symbol", "side")
_REQUIRED_STAGES = ("signal", "order_intent", "risk_check", "submit", "exchange_ack", "position_reconcile")
_CANONICAL_STAGE_STATUSES = {
    "signal": {"accepted"},
    "order_intent": {"created"},
    "risk_check": {"passed"},
    "submit": {"submitted"},
    "exchange_ack": {"acknowledged"},
    "fill": {"filled", "partially_filled", "partial"},
    "cancel": {"cancelled", "canceled", "expired", "rejected"},
    "position_reconcile": {"reconciled"},
}
_NUMERIC_FIELDS = {
    "quantity",
    "qty",
    "price",
    "limit_price",
    "ref_price",
    "fee",
    "fees",
    "funding",
    "filled_qty",
    "filled_notional",
    "executed_qty",
    "executedQty",
    "avg_price",
    "avgPrice",
}


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"{path.name}:{line_number} must contain a JSON object")
            rows.extend(_event_rows_from_object(dict(row), source=f"{path.name}:{line_number}"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} must contain valid JSONL") from exc
    return rows


def _event_rows_from_object(row: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
    event_chain = row.get("event_chain")
    if event_chain is None:
        return [row]
    if not isinstance(event_chain, list):
        raise ValueError(f"{source}.event_chain must be a list")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(event_chain):
        if not isinstance(item, Mapping):
            raise ValueError(f"{source}.event_chain[{index}] must contain a JSON object")
        rows.append(dict(item))
    return rows


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_timestamp(row: Mapping[str, Any], field: str) -> datetime:
    value = row.get(field)
    if type(value) is not str or not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _number(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    if positive and parsed <= 0.0:
        raise ValueError(f"{field} must be positive")
    if non_negative and parsed < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _optional_number(row: Mapping[str, Any], *fields: str, positive: bool = False, non_negative: bool = False) -> float | None:
    for field in fields:
        if field not in row or row[field] is None or row[field] == "":
            continue
        return _number(row[field], field, positive=positive, non_negative=non_negative)
    return None


def _require_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be a canonical string")
    return value


def _canonical_symbol(value: str) -> str:
    symbol = value.upper()
    if _SYMBOL_RE.fullmatch(symbol) is None:
        raise ValueError("symbol must be an uppercase symbol")
    return symbol


def _canonical_side(value: str) -> str:
    normalized = value.lower()
    if normalized in {"buy", "long"}:
        return "buy"
    if normalized in {"sell", "short"}:
        return "sell"
    raise ValueError("side must be buy or sell")


def _maker_taker(row: Mapping[str, Any]) -> str:
    value = row.get("maker_taker")
    if value is None:
        value = row.get("liquidity")
    if type(value) is not str or value not in {"maker", "taker"}:
        raise ValueError("maker_taker must be maker or taker")
    return value


def _stage(row: Mapping[str, Any]) -> str | None:
    value = row.get("stage")
    if type(value) is not str:
        return None
    return value


def _status(row: Mapping[str, Any]) -> str:
    value = row.get("status")
    if type(value) is not str:
        raise ValueError("status must be canonical")
    return value.lower()


def _validate_numeric_fields(row: Mapping[str, Any]) -> None:
    for field in _NUMERIC_FIELDS:
        if field in row and row[field] is not None and row[field] != "":
            _number(row[field], field)


def _chain_key(row: Mapping[str, Any]) -> str:
    value = row.get("intent_id") or row.get("order_id")
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("intent_id must be a canonical string")
    return value


def _identity_value(row: Mapping[str, Any], field: str) -> str:
    if field == "symbol":
        return _canonical_symbol(_require_string(row, field))
    if field == "side":
        return _canonical_side(_require_string(row, field))
    return _require_string(row, field)


def _validate_identity(rows: list[Mapping[str, Any]]) -> dict[str, str]:
    baseline: dict[str, str] = {}
    for row in rows:
        for field in _IDENTITY_FIELDS:
            value = _identity_value(row, field)
            previous = baseline.setdefault(field, value)
            if value != previous:
                raise ValueError(f"identity mismatch for {field}")
    return baseline


def _validate_stage_status(stage: str, row: Mapping[str, Any]) -> None:
    allowed = _CANONICAL_STAGE_STATUSES.get(stage)
    if allowed is None:
        raise ValueError(f"unknown lifecycle stage {stage}")
    status = _status(row)
    if status not in allowed:
        raise ValueError(f"{stage} status must be canonical")


def _selected_fill_price(fill_rows: list[Mapping[str, Any]]) -> float | None:
    notionals = [_optional_number(row, "filled_notional") for row in fill_rows]
    quantities = [_optional_number(row, "filled_qty", "quantity", "qty", "executed_qty", "executedQty") for row in fill_rows]
    total_notional = sum(value for value in notionals if value is not None)
    total_qty = sum(value for value in quantities if value is not None)
    if total_notional > 0.0 and total_qty > 0.0:
        return total_notional / total_qty
    for row in fill_rows:
        price = _optional_number(row, "price", "avg_price", "avgPrice", positive=True)
        if price is not None:
            return price
    return None


def _slippage_bps(*, side: str, fill_price: float | None, ref_price: float | None) -> float | None:
    if fill_price is None or ref_price is None or ref_price <= 0.0:
        return None
    if side == "buy":
        return ((fill_price - ref_price) / ref_price) * 10000.0
    return ((ref_price - fill_price) / ref_price) * 10000.0


def _ledger_index(rows: list[Mapping[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    by_order_id: dict[str, Mapping[str, Any]] = {}
    by_trade_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
        for field, index, label in (
            ("order_id", by_order_id, "order_id"),
            ("exchange_order_id", by_order_id, "order_id"),
            ("trade_id", by_trade_id, "trade_id"),
            ("fill_id", by_trade_id, "trade_id"),
        ):
            value = result.get(field) if isinstance(result, Mapping) else None
            if value is None:
                value = row.get(field)
            if value is None:
                continue
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"{label} must be a canonical string")
            if value in index:
                raise ValueError(f"duplicate {label}")
            index[value] = row
    return by_order_id, by_trade_id


def _validate_ledger_identity(record: Mapping[str, Any], ledger_by_order_id: Mapping[str, Mapping[str, Any]], ledger_by_trade_id: Mapping[str, Mapping[str, Any]]) -> None:
    order_id = record.get("order_id")
    trade_id = record.get("trade_id")
    if isinstance(order_id, str) and ledger_by_order_id and order_id not in ledger_by_order_id:
        raise ValueError("identity mismatch for order_id")
    if isinstance(trade_id, str) and ledger_by_trade_id and trade_id not in ledger_by_trade_id:
        raise ValueError("identity mismatch for trade_id")


def _record_from_chain(
    rows: list[Mapping[str, Any]],
    *,
    seen_order_ids: set[str],
    seen_trade_ids: set[str],
    ledger_by_order_id: Mapping[str, Mapping[str, Any]],
    ledger_by_trade_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        raise ValueError("lifecycle chain is empty")
    previous: datetime | None = None
    stages: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        _validate_numeric_fields(row)
        stage = _stage(row)
        if stage is None:
            continue
        timestamp = _parse_timestamp(row, "occurred_at")
        if previous is not None and timestamp < previous:
            raise ValueError("lifecycle timestamps must be monotonic")
        previous = timestamp
        _validate_stage_status(stage, row)
        if stage != "fill" and stage in stages:
            raise ValueError(f"duplicate lifecycle stage {stage}")
        stages.setdefault(stage, []).append(row)

    for stage in _REQUIRED_STAGES:
        if stage not in stages:
            raise ValueError(f"missing lifecycle stage {stage}")
    if "fill" not in stages and "cancel" not in stages:
        raise ValueError("missing lifecycle stage fill")

    identity = _validate_identity([row for row in rows if _stage(row) in stages])
    order_id = identity.get("order_id")
    if order_id in seen_order_ids:
        raise ValueError("duplicate order_id")
    seen_order_ids.add(order_id)
    fill_rows = stages.get("fill", [])
    fill_trade_ids = {_identity_value(row, "trade_id") for row in fill_rows if "trade_id" in row}
    if len(fill_trade_ids) > 1:
        trade_id = None
    else:
        trade_id = next(iter(fill_trade_ids), None)
    if trade_id is not None:
        if trade_id in seen_trade_ids:
            raise ValueError("duplicate trade_id")
        seen_trade_ids.add(trade_id)

    submit = stages["submit"][0]
    ack = stages["exchange_ack"][0]
    signal = stages["signal"][0]
    intent = stages["order_intent"][0]
    reconcile = stages["position_reconcile"][0]
    cancel = stages.get("cancel", [None])[0]
    side = identity["side"]
    requested_qty = _optional_number(intent, "quantity", "qty", positive=True)
    if requested_qty is None:
        requested_qty = _optional_number(submit, "quantity", "qty", positive=True)
    if requested_qty is None:
        raise ValueError("quantity must be numeric")
    intended_limit_price = _optional_number(submit, "price", "limit_price", positive=True)
    if intended_limit_price is None:
        intended_limit_price = _optional_number(intent, "price", "limit_price", positive=True)
    if intended_limit_price is None:
        raise ValueError("price must be numeric")

    filled_qty = 0.0
    filled_notional = 0.0
    for fill in fill_rows:
        fill_qty = _optional_number(fill, "filled_qty", "quantity", "qty", "executed_qty", "executedQty", non_negative=True)
        fill_price = _optional_number(fill, "price", "avg_price", "avgPrice", positive=True)
        fill_notional = _optional_number(fill, "filled_notional", non_negative=True)
        if fill_qty is None:
            raise ValueError("fill quantity must be numeric")
        if fill_qty <= 0.0:
            raise ValueError("fill quantity must be positive")
        if fill_notional is None:
            if fill_price is None:
                raise ValueError("fill price must be numeric")
            fill_notional = fill_qty * fill_price
        filled_qty += fill_qty
        filled_notional += fill_notional
    if filled_qty > requested_qty:
        raise ValueError("filled quantity cannot exceed requested quantity")

    terminal_status = _status(reconcile)
    if fill_rows:
        terminal_status = "filled" if math.isclose(filled_qty, requested_qty, rel_tol=1e-9, abs_tol=1e-12) else "partially_filled"
    elif cancel is not None:
        terminal_status = _status(cancel)

    maker_taker = _maker_taker(fill_rows[-1] if fill_rows else ack)
    fees = sum(_optional_number(row, "fee", "fees", non_negative=True) or 0.0 for row in fill_rows)
    if not fill_rows:
        fees = _optional_number(cancel or ack, "fee", "fees", non_negative=True) or 0.0
    funding = sum(_optional_number(row, "funding") or 0.0 for row in fill_rows)
    ref_price = _optional_number(fill_rows[-1], "ref_price", positive=True) if fill_rows else None
    if ref_price is None:
        ref_price = _optional_number(ack, "ref_price", positive=True)
    fill_price = _selected_fill_price(fill_rows)

    record: dict[str, Any] = {
        "symbol": identity["symbol"],
        "side": side,
        "intended_limit_price": intended_limit_price,
        "signal_at": _timestamp_string(signal),
        "decision_at": _timestamp_string(intent),
        "submitted_at": _timestamp_string(submit),
        "exchange_ack_at": _timestamp_string(ack),
        "first_fill_at": _timestamp_string(fill_rows[0]) if fill_rows else None,
        "last_fill_at": _timestamp_string(fill_rows[-1]) if fill_rows else None,
        "cancel_ack_at": _timestamp_string(cancel) if cancel is not None else None,
        "requested_qty": requested_qty,
        "requested_notional": requested_qty * intended_limit_price,
        "filled_qty": filled_qty if fill_rows else 0.0,
        "filled_notional": filled_notional if fill_rows else None,
        "status": terminal_status,
        "maker_taker": maker_taker,
        "fees": fees,
        "funding": funding,
        "slippage_bps": _slippage_bps(side=side, fill_price=fill_price, ref_price=ref_price),
        "ref_price": ref_price,
        "cancel_reason": _cancel_reason(cancel) if cancel is not None else None,
        "latency_ms": (_parse_timestamp(ack, "occurred_at") - _parse_timestamp(submit, "occurred_at")).total_seconds() * 1000.0,
        "setup_type": _setup_type(signal, intent, submit),
        "order_id": order_id,
        "trade_id": trade_id,
    }
    _validate_ledger_identity(record, ledger_by_order_id, ledger_by_trade_id)
    return {key: value for key, value in record.items() if value is not None and key not in {"order_id", "trade_id"}}


def _timestamp_string(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    value = row.get("occurred_at")
    if type(value) is not str:
        return None
    return value


def _cancel_reason(row: Mapping[str, Any]) -> str:
    value = row.get("cancel_reason") or row.get("reason") or _status(row)
    if type(value) is not str or not value:
        return "cancelled"
    normalized = value.strip().lower().replace("-", "_")
    if not normalized or not re.fullmatch(r"[a-z0-9_]+", normalized):
        raise ValueError("cancel_reason must be canonical")
    return normalized


def _setup_type(*rows: Mapping[str, Any]) -> str | None:
    for row in rows:
        value = row.get("setup_type") or row.get("engine")
        if value is None:
            continue
        if type(value) is not str or not re.fullmatch(r"[A-Z0-9_]+", value):
            raise ValueError("setup_type must be canonical")
        return value
    return None


def _canonical_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = []
    for row in rows:
        if _stage(row) is None:
            continue
        canonical.append(row)
    return canonical


def build_passive_order_calibration_records(
    execution_events: list[dict[str, Any]],
    *,
    ledger_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    canonical_events = _canonical_event_rows(execution_events)
    if not canonical_events:
        return []
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in canonical_events:
        grouped.setdefault(_chain_key(row), []).append(row)
    ledger_by_order_id, ledger_by_trade_id = _ledger_index(ledger_events or [])
    seen_order_ids: set[str] = set()
    seen_trade_ids: set[str] = set()
    records = [
        _record_from_chain(
            rows,
            seen_order_ids=seen_order_ids,
            seen_trade_ids=seen_trade_ids,
            ledger_by_order_id=ledger_by_order_id,
            ledger_by_trade_id=ledger_by_trade_id,
        )
        for _, rows in sorted(grouped.items())
    ]
    return records


def _write_jsonl_atomic(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if not rows:
        tmp.write_text("", encoding="utf-8")
    else:
        tmp.write_text("\n".join(json.dumps(dict(row), sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    tmp.replace(path)


def generate_execution_calibration_records(
    *,
    execution_log_file: str | Path,
    output_file: str | Path,
    paper_ledger_file: str | Path | None = None,
    unavailable_marker_file: str | Path | None = None,
) -> dict[str, Any]:
    execution_path = Path(execution_log_file)
    output_path = Path(output_file)
    ledger_rows = _read_jsonl_objects(Path(paper_ledger_file)) if paper_ledger_file is not None else []
    records = build_passive_order_calibration_records(_read_jsonl_objects(execution_path), ledger_events=ledger_rows)
    _write_jsonl_atomic(output_path, records)
    load_calibration_records(output_path)
    marker_path = Path(unavailable_marker_file) if unavailable_marker_file is not None else None
    if records and marker_path is not None and marker_path.exists():
        marker_path.unlink()
    return {
        "schema_version": "generate_execution_calibration_records_result.v1",
        "status": "ok",
        "record_count": len(records),
        "execution_log_file": str(execution_path),
        "paper_ledger_file": str(paper_ledger_file) if paper_ledger_file is not None else None,
        "output_file": str(output_path),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate passive order calibration records from simulated-live execution lifecycle logs."
    )
    parser.add_argument("--execution-log-file")
    parser.add_argument("--paper-ledger-file")
    parser.add_argument("--output-file")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.execution_log_file or args.output_file:
        if not args.execution_log_file or not args.output_file:
            raise ValueError("--execution-log-file and --output-file must be provided together")
        execution_log_file = Path(args.execution_log_file)
        paper_ledger_file = Path(args.paper_ledger_file) if args.paper_ledger_file else None
        output_file = Path(args.output_file)
        unavailable_marker_file = output_file.parent / CALIBRATION_UNAVAILABLE_NAME
    else:
        paths = build_runtime_paths(args.mode, runtime_root=args.runtime_root, runtime_env=args.runtime_env)
        execution_log_file = paths.execution_log_file
        paper_ledger_file = paths.paper_ledger_file
        output_file = paths.optimization_dir / CALIBRATION_RECORDS_NAME
        unavailable_marker_file = paths.optimization_dir / CALIBRATION_UNAVAILABLE_NAME

    result = generate_execution_calibration_records(
        execution_log_file=execution_log_file,
        paper_ledger_file=paper_ledger_file,
        output_file=output_file,
        unavailable_marker_file=unavailable_marker_file,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
