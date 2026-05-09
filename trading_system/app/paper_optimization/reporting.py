from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _optional_int(payload: dict[str, Any], field_name: str, *, default: int) -> int:
    value = payload.get(field_name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"daily_metrics.{field_name} must be an integer")
    return value


def build_optimization_summary(
    *,
    signal_facts_path: Path,
    trade_outcomes_path: Path,
    daily_metrics_path: Path,
    health_report_path: Path,
    recommendations_path: Path | None = None,
    promotion_decision_path: Path | None = None,
) -> dict[str, Any]:
    daily_metrics = _read_json(daily_metrics_path) if daily_metrics_path.exists() else {}
    health_report = _read_json(health_report_path) if health_report_path.exists() else {}
    recommendations = _read_json(recommendations_path) if recommendations_path is not None and recommendations_path.exists() else {}
    promotion_decision = _read_json(promotion_decision_path) if promotion_decision_path is not None and promotion_decision_path.exists() else {}

    signal_fact_count = _optional_int(
        daily_metrics,
        "signal_fact_count",
        default=_count_jsonl(signal_facts_path),
    )
    trade_outcome_count = _optional_int(
        daily_metrics,
        "trade_outcome_count",
        default=_count_jsonl(trade_outcomes_path),
    )
    warning_count = len(health_report.get("warnings") or []) if isinstance(health_report.get("warnings"), list) else 0
    recommendation_count = len(recommendations.get("recommendations") or []) if isinstance(recommendations.get("recommendations"), list) else 0
    optimization_alerts = list(recommendations.get("alerts") or []) if isinstance(recommendations.get("alerts"), list) else []

    return {
        "signal_fact_count": signal_fact_count,
        "trade_outcome_count": trade_outcome_count,
        "last_metrics_at": daily_metrics.get("recorded_at_bj"),
        "last_recommendation_at": recommendations.get("recorded_at_bj"),
        "health_status": health_report.get("status"),
        "warning_count": warning_count,
        "recommendation_count": recommendation_count,
        "optimization_alert_count": len(optimization_alerts),
        "optimization_alerts": optimization_alerts,
        "promotion_status": promotion_decision.get("status"),
        "promotion_decision": promotion_decision.get("decision"),
    }


__all__ = ["build_optimization_summary"]
