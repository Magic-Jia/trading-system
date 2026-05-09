from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import BJ



def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                raw = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} line {line_number} must be valid JSON") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path.name} line {line_number} must be a JSON object")
            rows.append(raw)
    return rows



def _float_or_zero(value: Any, *, field_name: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _runtime_position_rows(runtime_positions: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if runtime_positions is None:
        return out
    if not isinstance(runtime_positions, dict):
        raise ValueError("runtime_positions must be an object")
    for symbol, raw in runtime_positions.items():
        if not isinstance(raw, dict):
            raise ValueError("runtime position rows must be objects")
        status = (_optional_str(raw.get("status"), field_name="runtime_position.status") or "").upper()
        qty = _float_or_zero(raw.get("qty"), field_name="runtime_position.qty")
        if status in {"OPEN", "PENDING"} and qty > 0:
            normalized = (_optional_str(raw.get("symbol"), field_name="runtime_position.symbol") or symbol).upper()
            out[normalized] = {
                "status": status,
                "qty": qty,
                "unrealized_pnl": round(_float_or_zero(raw.get("unrealized_pnl"), field_name="runtime_position.unrealized_pnl"), 4),
            }
    return out


def _recorded_at_bj(value: str | None) -> str:
    if value:
        return value
    return datetime.now(BJ).isoformat()


def _blank_bucket() -> dict[str, Any]:
    return {
        "trade_outcome_count": 0,
        "open_count": 0,
        "position_not_tracked_count": 0,
        "unrealized_pnl_total": 0.0,
    }



def _latest_rows_by_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_key: dict[str, tuple[str, int]] = {}
    for idx, row in enumerate(rows):
        symbol = (_optional_str(row.get("symbol"), field_name="trade_outcome.symbol") or "").upper()
        if not symbol:
            continue
        ts = (
            _optional_str(row.get("updated_at_bj"), field_name="trade_outcome.updated_at_bj")
            or _optional_str(row.get("recorded_at_bj"), field_name="trade_outcome.recorded_at_bj")
            or _optional_str(row.get("opened_at_bj"), field_name="trade_outcome.opened_at_bj")
            or ""
        )
        key = (ts, idx)
        if symbol not in latest_key or key >= latest_key[symbol]:
            latest[symbol] = row
            latest_key[symbol] = key
    return list(latest.values())


def _group_breakdown(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = _optional_str(row.get(key), field_name=f"trade_outcome.{key}") or ""
        if not name:
            continue
        bucket = grouped.setdefault(name, _blank_bucket())
        bucket["trade_outcome_count"] += 1
        if row.get("outcome_status") == "OPEN":
            bucket["open_count"] += 1
        if row.get("outcome_status") == "POSITION_NOT_TRACKED":
            bucket["position_not_tracked_count"] += 1
        bucket["unrealized_pnl_total"] = round(bucket["unrealized_pnl_total"] + _float_or_zero(row.get("unrealized_pnl"), field_name="trade_outcome.unrealized_pnl"), 10)
    for bucket in grouped.values():
        bucket["unrealized_pnl_total"] = round(float(bucket["unrealized_pnl_total"]), 4)
    return grouped


def _trade_outcome_status(row: dict[str, Any], field: str) -> str | None:
    return _optional_str(row.get(field), field_name=f"trade_outcome.{field}")


def write_daily_metrics_and_health_report(
    *,
    trade_outcomes_path: Path,
    signal_facts_path: Path,
    daily_metrics_path: Path,
    health_report_path: Path,
    recorded_at_bj: str | None = None,
    runtime_positions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_rows = _jsonl(trade_outcomes_path)
    rows = _latest_rows_by_symbol(raw_rows)
    signal_fact_count = len(_jsonl(signal_facts_path))
    raw_trade_outcome_count = len(raw_rows)
    trade_outcome_count = len(rows)
    execution_status_counts = Counter(
        status for row in rows if (status := _trade_outcome_status(row, "execution_status"))
    )
    outcome_status_counts = Counter(
        status for row in rows if (status := _trade_outcome_status(row, "outcome_status"))
    )
    open_count = sum(1 for row in rows if _trade_outcome_status(row, "outcome_status") == "OPEN")
    not_executed_count = sum(1 for row in rows if _trade_outcome_status(row, "outcome_status") == "NOT_EXECUTED")
    position_not_tracked_count = sum(1 for row in rows if _trade_outcome_status(row, "outcome_status") == "POSITION_NOT_TRACKED")
    unrealized_pnl_total = round(sum(_float_or_zero(row.get("unrealized_pnl"), field_name="trade_outcome.unrealized_pnl") for row in rows), 4)
    current_positions = _runtime_position_rows(runtime_positions)
    scope = "current_runtime_latest_by_symbol"
    if current_positions:
        scope = "current_runtime_positions"
        open_count = len(current_positions)
        unrealized_pnl_total = round(sum(_float_or_zero(row.get("unrealized_pnl"), field_name="trade_outcome.unrealized_pnl") for row in current_positions.values()), 4)
        position_not_tracked_count = 0
    recorded_at = _recorded_at_bj(recorded_at_bj)

    daily_metrics = {
        "recorded_at_bj": recorded_at,
        "scope": scope,
        "raw_trade_outcome_count": raw_trade_outcome_count,
        "signal_fact_count": signal_fact_count,
        "trade_outcome_count": trade_outcome_count,
        "execution_status_counts": dict(sorted(execution_status_counts.items())),
        "outcome_status_counts": dict(sorted(outcome_status_counts.items())),
        "open_count": open_count,
        "not_executed_count": not_executed_count,
        "position_not_tracked_count": position_not_tracked_count,
        "unrealized_pnl_total": unrealized_pnl_total,
        "by_engine": _group_breakdown(rows, "engine"),
        "by_setup_type": _group_breakdown(rows, "setup_type"),
        "by_regime": _group_breakdown(rows, "regime_label"),
        "current_positions": current_positions,
    }

    warnings: list[dict[str, Any]] = []
    if position_not_tracked_count:
        warnings.append(
            {
                "code": "position_not_tracked",
                "count": position_not_tracked_count,
                "message": f"{position_not_tracked_count} filled outcomes do not currently map to an active runtime position",
            }
        )

    health_report = {
        "recorded_at_bj": recorded_at,
        "scope": scope,
        "raw_trade_outcome_count": raw_trade_outcome_count,
        "status": "warn" if warnings else "ok",
        "signal_fact_count": signal_fact_count,
        "trade_outcome_count": trade_outcome_count,
        "warnings": warnings,
    }

    daily_metrics_path.parent.mkdir(parents=True, exist_ok=True)
    daily_metrics_path.write_text(json.dumps(daily_metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    health_report_path.parent.mkdir(parents=True, exist_ok=True)
    health_report_path.write_text(json.dumps(health_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "daily_metrics_path": str(daily_metrics_path),
        "health_report_path": str(health_report_path),
        "trade_outcome_count": trade_outcome_count,
        "signal_fact_count": signal_fact_count,
        "open_count": open_count,
        "position_not_tracked_count": position_not_tracked_count,
        "warning_count": len(warnings),
    }
