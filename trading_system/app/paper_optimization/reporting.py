from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .promotion import validate_decision_audit_evidence

POSITIVE_PROMOTION_DECISIONS = {"promote"}


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


def _promotion_audit_summary(promotion_decision: dict[str, Any]) -> dict[str, Any]:
    decision = _optional_str(promotion_decision, "decision", source_name="promotion_decision")
    if decision not in POSITIVE_PROMOTION_DECISIONS:
        return {
            "promotion_entry_reason": None,
            "promotion_exit_reason": None,
            "promotion_as_of_inputs": [],
        }
    recorded_at_bj = _optional_str(promotion_decision, "recorded_at_bj", source_name="promotion_decision")
    audit_evidence = promotion_decision.get("decision_audit_evidence")
    if audit_evidence is None:
        raise ValueError("promotion_decision.decision_audit_evidence is required for positive decisions")
    if recorded_at_bj is None and isinstance(audit_evidence, dict):
        recorded_at_bj = audit_evidence.get("decision_recorded_at_bj")
    if recorded_at_bj is None:
        raise ValueError("promotion_decision.recorded_at_bj is required for positive decisions")
    validated = validate_decision_audit_evidence(
        audit_evidence,
        decision=decision,
        decision_recorded_at_bj=recorded_at_bj,
        field_name="promotion_decision.decision_audit_evidence",
    )
    return {
        "promotion_entry_reason": validated["entry_reason"],
        "promotion_exit_reason": validated["exit_reason"],
        "promotion_as_of_inputs": validated["as_of_inputs"],
    }




def _promotion_decision_audit_fields(promotion_decision: dict[str, Any]) -> dict[str, Any]:
    status = promotion_decision.get("status")
    decision = promotion_decision.get("decision")
    positive = status == "promote" or decision == "promote"
    evidence = promotion_decision.get("decision_audit_evidence")
    if evidence is None:
        if positive:
            raise ValueError("promotion_decision.decision_audit_evidence is required for positive decisions")
        return {
            "promotion_entry_reason": None,
            "promotion_exit_reason": None,
            "promotion_as_of_inputs": [],
        }
    if not isinstance(evidence, dict):
        raise ValueError("promotion_decision.decision_audit_evidence must be an object")
    entry_reason = evidence.get("entry_reason")
    if entry_reason is not None and not isinstance(entry_reason, str):
        raise ValueError("promotion_decision.decision_audit_evidence.entry_reason must be a string")
    exit_reason = evidence.get("exit_reason")
    if exit_reason is not None and not isinstance(exit_reason, str):
        raise ValueError("promotion_decision.decision_audit_evidence.exit_reason must be a string")
    as_of_inputs = evidence.get("as_of_inputs", [])
    if not isinstance(as_of_inputs, list):
        raise ValueError("promotion_decision.decision_audit_evidence.as_of_inputs must be a list")
    return {
        "promotion_entry_reason": entry_reason,
        "promotion_exit_reason": exit_reason,
        "promotion_as_of_inputs": as_of_inputs,
    }

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
    audit_summary = _promotion_audit_summary(promotion_decision)

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
        **audit_summary,
    }


__all__ = ["build_optimization_summary"]
