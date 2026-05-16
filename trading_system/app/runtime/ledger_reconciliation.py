from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "ledger_exchange_reconciliation.v1"
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_NUMERIC_FIELDS = {
    "equity",
    "available_balance",
    "futures_wallet_balance",
    "qty",
    "quantity",
    "price",
    "entry_price",
    "notional",
    "fee",
}
_TIMESTAMP_FIELDS = {
    "captured_at",
    "recorded_at",
    "updated_at",
    "executed_at",
    "created_at",
    "as_of",
}


def _is_canonical_utc_timestamp(value: str) -> bool:
    if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _is_canonical_utc_timestamp(value):
        return None
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _as_list(manifest: Mapping[str, Any], key: str, reasons: list[str]) -> list[Any]:
    value = manifest.get(key)
    if value is None:
        _append_reason(reasons, f"missing_{key}")
        return []
    if not isinstance(value, list):
        _append_reason(reasons, f"{key}_not_list")
        return []
    return value


def _as_mapping(manifest: Mapping[str, Any], key: str, reasons: list[str]) -> dict[str, Any]:
    value = manifest.get(key)
    if value is None:
        _append_reason(reasons, f"missing_{key}")
        return {}
    if not isinstance(value, Mapping):
        _append_reason(reasons, f"{key}_not_object")
        return {}
    return dict(value)


def _walk_contract_values(value: Any, *, reasons: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _TIMESTAMP_FIELDS:
                if not isinstance(item, str) or not _is_canonical_utc_timestamp(item):
                    _append_reason(reasons, "noncanonical_timestamp")
            if key in _NUMERIC_FIELDS:
                _validate_numeric(item, reasons)
            _walk_contract_values(item, reasons=reasons)
    elif isinstance(value, list):
        for item in value:
            _walk_contract_values(item, reasons=reasons)


def _validate_numeric(value: Any, reasons: list[str]) -> float | None:
    if isinstance(value, bool):
        _append_reason(reasons, "bool_numeric")
        return None
    if isinstance(value, str):
        _append_reason(reasons, "numeric_string")
        return None
    if not isinstance(value, (int, float)):
        _append_reason(reasons, "invalid_numeric")
        return None
    number = float(value)
    if not math.isfinite(number):
        _append_reason(reasons, "nonfinite_numeric")
        return None
    return number


def _canonical_id(row: Any, fields: tuple[str, ...], unknown_reason: str, reasons: list[str]) -> str | None:
    if not isinstance(row, Mapping):
        _append_reason(reasons, "snapshot_row_not_object")
        return None
    for field in fields:
        value = row.get(field)
        if isinstance(value, str) and value.strip() and value == value.strip():
            return value
    _append_reason(reasons, unknown_reason)
    return None


def _unique_ids(rows: list[Any], fields: tuple[str, ...], unknown_reason: str, duplicate_reason: str, reasons: list[str]) -> dict[str, Mapping[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        row_id = _canonical_id(row, fields, unknown_reason, reasons)
        if row_id is None:
            continue
        if row_id in by_id:
            _append_reason(reasons, duplicate_reason)
            continue
        by_id[row_id] = row
    return by_id


def _ledger_order_id(event: Mapping[str, Any]) -> str | None:
    result = event.get("result")
    if isinstance(result, Mapping):
        value = result.get("order_id") or result.get("exchange_order_id")
        if isinstance(value, str) and value.strip() and value == value.strip():
            return value
    value = event.get("order_id") or event.get("exchange_order_id")
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _ledger_trade_id(event: Mapping[str, Any]) -> str | None:
    result = event.get("result")
    if isinstance(result, Mapping):
        value = result.get("trade_id") or result.get("fill_id")
        if isinstance(value, str) and value.strip() and value == value.strip():
            return value
    value = event.get("trade_id") or event.get("fill_id")
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _ledger_symbol(event: Mapping[str, Any]) -> str | None:
    value = event.get("symbol")
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    order = event.get("order")
    if isinstance(order, Mapping):
        value = order.get("symbol")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _ledger_qty(event: Mapping[str, Any]) -> float | None:
    position = event.get("position_update")
    if isinstance(position, Mapping) and "qty" in position:
        return float(position["qty"]) if isinstance(position["qty"], (int, float)) and not isinstance(position["qty"], bool) else None
    result = event.get("result")
    if isinstance(result, Mapping) and "qty" in result:
        return float(result["qty"]) if isinstance(result["qty"], (int, float)) and not isinstance(result["qty"], bool) else None
    return None


def _positions_match(ledger_events: list[Any], positions: list[Any], reasons: list[str]) -> None:
    position_by_symbol: dict[str, Mapping[str, Any]] = {}
    for row in positions:
        if not isinstance(row, Mapping):
            _append_reason(reasons, "snapshot_row_not_object")
            continue
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol.strip():
            position_by_symbol[symbol.strip().upper()] = row
    for event in ledger_events:
        if not isinstance(event, Mapping):
            continue
        symbol = _ledger_symbol(event)
        qty = _ledger_qty(event)
        if symbol is None or qty is None:
            _append_reason(reasons, "position_snapshot_mismatch")
            continue
        position = position_by_symbol.get(symbol)
        position_qty = position.get("qty") if isinstance(position, Mapping) else None
        if not isinstance(position_qty, (int, float)) or isinstance(position_qty, bool):
            _append_reason(reasons, "position_snapshot_mismatch")
            continue
        if not math.isclose(float(position_qty), qty, rel_tol=1e-9, abs_tol=1e-12):
            _append_reason(reasons, "position_snapshot_mismatch")


def _account_matches_ledger(account: Mapping[str, Any], reasons: list[str]) -> None:
    equity = account.get("equity")
    futures_wallet_balance = account.get("futures_wallet_balance")
    available_balance = account.get("available_balance")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (equity, futures_wallet_balance, available_balance)):
        _append_reason(reasons, "account_snapshot_mismatch")
        return
    if float(equity) <= 0 or float(futures_wallet_balance) <= 0 or float(available_balance) < 0:
        _append_reason(reasons, "account_snapshot_mismatch")
        return
    if not math.isclose(float(equity), float(futures_wallet_balance), rel_tol=1e-9, abs_tol=1e-9):
        _append_reason(reasons, "account_snapshot_mismatch")


def build_ledger_reconciliation_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    evaluated_at = _parse_timestamp(manifest.get("evaluated_at"))
    if evaluated_at is None:
        _append_reason(reasons, "noncanonical_timestamp")

    ledger_events = _as_list(manifest, "ledger_events", reasons)
    order_snapshot = _as_list(manifest, "order_snapshot", reasons)
    trade_snapshot = _as_list(manifest, "trade_snapshot", reasons)
    position_snapshot = _as_list(manifest, "position_snapshot", reasons)
    account_snapshot = _as_mapping(manifest, "account_snapshot", reasons)
    snapshot_metadata = _as_mapping(manifest, "snapshot_metadata", reasons)

    _walk_contract_values(
        {
            "ledger_events": ledger_events,
            "order_snapshot": order_snapshot,
            "trade_snapshot": trade_snapshot,
            "position_snapshot": position_snapshot,
            "account_snapshot": account_snapshot,
            "snapshot_metadata": snapshot_metadata,
        },
        reasons=reasons,
    )

    orders_by_id = _unique_ids(order_snapshot, ("order_id", "exchange_order_id"), "unknown_order_id", "duplicate_order_id", reasons)
    trades_by_id = _unique_ids(trade_snapshot, ("trade_id", "fill_id"), "unknown_trade_id", "duplicate_trade_id", reasons)
    for event in ledger_events:
        if not isinstance(event, Mapping):
            _append_reason(reasons, "ledger_event_not_object")
            continue
        order_id = _ledger_order_id(event)
        trade_id = _ledger_trade_id(event)
        if order_id is None or order_id not in orders_by_id:
            _append_reason(reasons, "unknown_order_id")
        if trade_id is None or trade_id not in trades_by_id:
            _append_reason(reasons, "unknown_trade_id")

    _positions_match(ledger_events, position_snapshot, reasons)
    _account_matches_ledger(account_snapshot, reasons)

    captured_at = _parse_timestamp(snapshot_metadata.get("captured_at"))
    max_age = snapshot_metadata.get("max_evidence_age_seconds")
    if isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or not math.isfinite(float(max_age)) or float(max_age) < 0:
        _append_reason(reasons, "invalid_max_evidence_age")
    elif evaluated_at is not None and captured_at is not None:
        age_seconds = (evaluated_at - captured_at).total_seconds()
        if age_seconds < 0:
            _append_reason(reasons, "future_evidence")
        elif age_seconds > float(max_age):
            _append_reason(reasons, "stale_evidence")

    exchange_account_state = snapshot_metadata.get("exchange_account_state")
    if exchange_account_state != "known":
        _append_reason(reasons, "exchange_account_state_unresolved")

    checks = {
        "snapshots_present_met": not any(reason.startswith("missing_") for reason in reasons),
        "order_ids_known_unique_met": "unknown_order_id" not in reasons and "duplicate_order_id" not in reasons,
        "trade_ids_known_unique_met": "unknown_trade_id" not in reasons and "duplicate_trade_id" not in reasons,
        "positions_match_met": "position_snapshot_mismatch" not in reasons,
        "account_balances_match_met": "account_snapshot_mismatch" not in reasons,
        "timestamps_canonical_met": "noncanonical_timestamp" not in reasons,
        "numerics_finite_met": not any(reason in reasons for reason in ("bool_numeric", "numeric_string", "invalid_numeric", "nonfinite_numeric")),
        "evidence_fresh_met": not any(reason in reasons for reason in ("future_evidence", "stale_evidence", "invalid_max_evidence_age")),
        "exchange_account_state_resolved_met": "exchange_account_state_unresolved" not in reasons,
    }
    checks["ledger_exchange_reconciliation_met"] = all(checks.values())

    source = manifest.get("evidence_source")
    if not isinstance(source, Mapping):
        source = {"type": "unknown_offline_records"}

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": dict(source),
        "evaluated_at": manifest.get("evaluated_at"),
        "checks": {"ledger_exchange_reconciliation_met": checks.pop("ledger_exchange_reconciliation_met"), **checks},
        "summary": {
            "ledger_event_count": len(ledger_events),
            "order_snapshot_count": len(order_snapshot),
            "trade_snapshot_count": len(trade_snapshot),
            "position_snapshot_count": len(position_snapshot),
            "reason_count": len(reasons),
        },
        "reasons": reasons,
        "snapshots": {
            "orders": order_snapshot,
            "trades": trade_snapshot,
            "positions": position_snapshot,
            "account": account_snapshot,
            "metadata": snapshot_metadata,
        },
    }


def reconciliation_runtime_safety_events(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = evidence.get("checks")
    passed = isinstance(checks, Mapping) and checks.get("ledger_exchange_reconciliation_met") is True
    return [
        {"type": "order_position_reconciliation", "passed": passed},
        {"type": "live_trade_ledger", "passed": passed},
        {"type": "runtime_fail_closed", "passed": True},
    ]


def write_ledger_reconciliation_evidence(evidence: Mapping[str, Any], output_dir: str | Path) -> Path:
    output_path = Path(output_dir) / "ledger_exchange_reconciliation.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return output_path
