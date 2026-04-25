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
        for line in handle:
            payload = line.strip()
            if not payload:
                continue
            try:
                raw = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                rows.append(raw)
    return rows



def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0



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



def _group_breakdown(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get(key) or "")
        if not name:
            continue
        bucket = grouped.setdefault(name, _blank_bucket())
        bucket["trade_outcome_count"] += 1
        if row.get("outcome_status") == "OPEN":
            bucket["open_count"] += 1
        if row.get("outcome_status") == "POSITION_NOT_TRACKED":
            bucket["position_not_tracked_count"] += 1
        bucket["unrealized_pnl_total"] = round(bucket["unrealized_pnl_total"] + _float_or_zero(row.get("unrealized_pnl")), 10)
    for bucket in grouped.values():
        bucket["unrealized_pnl_total"] = round(float(bucket["unrealized_pnl_total"]), 4)
    return grouped



def write_daily_metrics_and_health_report(
    *,
    trade_outcomes_path: Path,
    signal_facts_path: Path,
    daily_metrics_path: Path,
    health_report_path: Path,
    recorded_at_bj: str | None = None,
) -> dict[str, Any]:
    rows = _jsonl(trade_outcomes_path)
    signal_fact_count = len(_jsonl(signal_facts_path))
    trade_outcome_count = len(rows)
    execution_status_counts = Counter(str(row.get("execution_status") or "") for row in rows if row.get("execution_status"))
    outcome_status_counts = Counter(str(row.get("outcome_status") or "") for row in rows if row.get("outcome_status"))
    open_count = sum(1 for row in rows if row.get("outcome_status") == "OPEN")
    not_executed_count = sum(1 for row in rows if row.get("outcome_status") == "NOT_EXECUTED")
    position_not_tracked_count = sum(1 for row in rows if row.get("outcome_status") == "POSITION_NOT_TRACKED")
    unrealized_pnl_total = round(sum(_float_or_zero(row.get("unrealized_pnl")) for row in rows), 4)
    recorded_at = _recorded_at_bj(recorded_at_bj)

    daily_metrics = {
        "recorded_at_bj": recorded_at,
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
