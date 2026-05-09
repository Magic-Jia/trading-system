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


def _optional_list(payload: dict[str, Any], field_name: str, *, source_name: str) -> list[Any]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{source_name}.{field_name} must be a list")
    return value


def _optional_str(payload: dict[str, Any], field_name: str, *, source_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{source_name}.{field_name} must be a string")
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
    warnings = _optional_list(health_report, "warnings", source_name="health_report")
    recommendation_items = _optional_list(recommendations, "recommendations", source_name="recommendations")
    optimization_alerts = _optional_list(recommendations, "alerts", source_name="recommendations")
    warning_count = len(warnings)
    recommendation_count = len(recommendation_items)

    return {
        "signal_fact_count": signal_fact_count,
        "trade_outcome_count": trade_outcome_count,
        "last_metrics_at": _optional_str(daily_metrics, "recorded_at_bj", source_name="daily_metrics"),
        "last_recommendation_at": _optional_str(recommendations, "recorded_at_bj", source_name="recommendations"),
        "health_status": _optional_str(health_report, "status", source_name="health_report"),
        "warning_count": warning_count,
        "recommendation_count": recommendation_count,
        "optimization_alert_count": len(optimization_alerts),
        "optimization_alerts": optimization_alerts,
        "promotion_status": _optional_str(promotion_decision, "status", source_name="promotion_decision"),
        "promotion_decision": _optional_str(promotion_decision, "decision", source_name="promotion_decision"),
    }


__all__ = ["build_optimization_summary"]
