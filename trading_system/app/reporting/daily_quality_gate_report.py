from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "daily_quality_gate_report.v1"
ALERT_HOLD_WORKFLOW_SCHEMA_VERSION = "quality_gate_alert_hold_workflow.v1"
MODE = "simulated_live"
PASS_DECISION = "pass_for_continued_paper"
HOLD_DECISION = "hold_for_review"
REJECT_DECISION = "reject_live_promotion"
UNRESOLVED_REJECT_REASON = "unresolved_reject_live_promotion"

HARD_REASONS = (
    "paper_shadow_material_drift",
    "tca_slippage_exceeds_threshold",
    "rolling_tca_durability_failed",
    "bucket_regression",
    "promotion_readiness_reject",
    "execution_chain_missing",
    "reconcile_failed",
    "malformed_evidence",
)
SOFT_REASONS = (
    "latency_distribution_shift",
    "data_freshness_violation",
    "calibration_records_unavailable",
    "insufficient_sample_size",
    "insufficient_bucket_samples",
    "promotion_readiness_hold",
    "promotion_readiness_review",
)
REASON_ORDER = (
    "paper_shadow_material_drift",
    "tca_slippage_exceeds_threshold",
    "rolling_tca_durability_failed",
    "bucket_regression",
    "promotion_readiness_reject",
    "execution_chain_missing",
    "reconcile_failed",
    "latency_distribution_shift",
    "data_freshness_violation",
    "calibration_records_unavailable",
    "insufficient_sample_size",
    "insufficient_bucket_samples",
    "promotion_readiness_hold",
    "promotion_readiness_review",
    "malformed_evidence",
)
WORKFLOW_REASON_ORDER = (*REASON_ORDER, UNRESOLVED_REJECT_REASON)
REASON_SEVERITY = {
    **{reason: "critical" for reason in HARD_REASONS},
    **{reason: "warning" for reason in SOFT_REASONS},
    UNRESOLVED_REJECT_REASON: "critical",
}
WORKFLOW_RELEASE_CONDITIONS = [
    "next_daily_quality_gate_decision_pass_for_continued_paper",
    "all_active_reason_codes_absent",
    "no_unresolved_reject_live_promotion",
    "acknowledgement_recorded_for_current_hold",
]


def _mapping(value: Any, field: str, malformed: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        malformed.append(f"{field}_not_object")
        return {}
    return value


def _optional_mapping(value: Any, field: str, malformed: list[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _mapping(value, field, malformed)


def _bool_field(
    payload: Mapping[str, Any],
    field: str,
    malformed: list[str],
    *,
    error_field: str | None = None,
) -> bool | None:
    value = payload.get(field)
    if not isinstance(value, bool):
        malformed.append(f"{error_field or field}_not_bool")
        return None
    return value


def _number(value: Any, field: str, malformed: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        malformed.append(f"{field}_not_finite_number")
        return None
    return float(value)


def _non_negative_number(value: Any, field: str, malformed: list[str]) -> float | None:
    number = _number(value, field, malformed)
    if number is None:
        return None
    if number < 0.0:
        malformed.append(f"{field}_negative")
        return None
    return number


def _non_negative_int(value: Any, field: str, malformed: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        malformed.append(f"{field}_not_int")
        return None
    if value < 0:
        malformed.append(f"{field}_negative")
        return None
    return value


def _generated_at(value: str | None) -> str:
    if value is not None:
        return value
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.endswith("Z"):
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return parsed.astimezone(UTC)


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in REASON_ORDER if reason in reasons]


def _ordered_workflow_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in WORKFLOW_REASON_ORDER if reason in reasons]


def _strict_reason_code(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if value not in REASON_SEVERITY:
        raise ValueError(f"{field} is unknown")
    return value


def _previous_reason_first_seen(previous_workflow: Mapping[str, Any] | None) -> dict[str, str]:
    if previous_workflow is None:
        return {}
    if not isinstance(previous_workflow, Mapping):
        raise ValueError("previous_workflow must be an object")
    raw_reasons = previous_workflow.get("active_reasons", [])
    if not isinstance(raw_reasons, list):
        raise ValueError("previous_workflow.active_reasons must be a list")
    first_seen: dict[str, str] = {}
    for index, raw_reason in enumerate(raw_reasons):
        if not isinstance(raw_reason, Mapping):
            raise ValueError(f"previous_workflow.active_reasons[{index}] must be an object")
        code = _strict_reason_code(raw_reason.get("code"), f"previous_workflow.active_reasons[{index}].code")
        first_seen_value = raw_reason.get("first_seen")
        _parse_utc_timestamp(first_seen_value, f"previous_workflow.active_reasons[{index}].first_seen")
        first_seen.setdefault(code, first_seen_value)
    return first_seen


def _previous_decision(previous_workflow: Mapping[str, Any] | None) -> str | None:
    if not previous_workflow:
        return None
    hold = previous_workflow.get("hold")
    if not isinstance(hold, Mapping):
        return None
    decision = hold.get("decision")
    return decision if isinstance(decision, str) else None


def _canonical_acknowledgement(acknowledgement: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if acknowledgement is None:
        return None
    if not isinstance(acknowledgement, Mapping):
        raise ValueError("acknowledgement must be an object")
    unknown_fields = sorted(set(acknowledgement) - {"acknowledged_by", "acknowledged_at", "reason_codes"})
    if unknown_fields:
        raise ValueError("unknown acknowledgement field: " + ", ".join(unknown_fields))
    acknowledged_by = acknowledgement.get("acknowledged_by")
    if not isinstance(acknowledged_by, str) or not acknowledged_by.strip() or acknowledged_by != acknowledged_by.strip():
        raise ValueError("acknowledgement.acknowledged_by must be canonical")
    acknowledged_at = acknowledgement.get("acknowledged_at")
    _parse_utc_timestamp(acknowledged_at, "acknowledgement.acknowledged_at")
    reason_codes = acknowledgement.get("reason_codes")
    if not isinstance(reason_codes, list) or not reason_codes:
        raise ValueError("acknowledgement.reason_codes must be a non-empty list")
    canonical_reasons = [
        _strict_reason_code(reason, f"acknowledgement.reason_codes[{index}]")
        for index, reason in enumerate(reason_codes)
    ]
    return {
        "status": "recorded",
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": acknowledged_at,
        "reason_codes": canonical_reasons,
    }


def _validate_daily_gate_for_workflow(
    daily_quality_gate: Mapping[str, Any],
    *,
    generated_at: str,
    max_gate_age_seconds: int,
) -> tuple[str, list[str]]:
    if not isinstance(daily_quality_gate, Mapping):
        raise ValueError("daily quality gate must be an object")
    if daily_quality_gate.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"daily quality gate schema_version must be {SCHEMA_VERSION}")
    if daily_quality_gate.get("mode") != MODE:
        raise ValueError(f"daily quality gate mode must be {MODE}")
    decision = daily_quality_gate.get("decision")
    if decision not in {PASS_DECISION, HOLD_DECISION, REJECT_DECISION}:
        raise ValueError("daily quality gate decision is invalid")
    gate_generated_at = _parse_utc_timestamp(daily_quality_gate.get("generated_at"), "daily quality gate generated_at")
    workflow_generated_at = _parse_utc_timestamp(generated_at, "generated_at")
    if gate_generated_at > workflow_generated_at:
        raise ValueError("daily quality gate generated_at must not be in the future")
    if (workflow_generated_at - gate_generated_at).total_seconds() > max_gate_age_seconds:
        raise ValueError("daily quality gate evidence is stale")
    if "reasons" not in daily_quality_gate:
        raise ValueError("daily quality gate reasons must be present")
    raw_reasons = daily_quality_gate["reasons"]
    if not isinstance(raw_reasons, list):
        raise ValueError("daily quality gate reasons must be a list")
    reasons = [
        _strict_reason_code(reason, f"daily quality gate reasons[{index}]")
        for index, reason in enumerate(raw_reasons)
    ]
    if decision != PASS_DECISION and not reasons:
        raise ValueError("failing daily quality gate reasons must be non-empty")
    if decision == PASS_DECISION and reasons:
        raise ValueError("passing daily quality gate reasons must be empty")
    malformed_inputs = daily_quality_gate.get("malformed_inputs", [])
    if malformed_inputs and not isinstance(malformed_inputs, list):
        raise ValueError("daily quality gate malformed_inputs must be a list")
    return decision, reasons


def build_quality_gate_alert_hold_workflow(
    daily_quality_gate: Mapping[str, Any],
    *,
    generated_at: str | None = None,
    previous_workflow: Mapping[str, Any] | None = None,
    acknowledgement: Mapping[str, Any] | None = None,
    max_gate_age_seconds: int = 86400,
) -> dict[str, Any]:
    workflow_generated_at = _generated_at(generated_at)
    if isinstance(max_gate_age_seconds, bool) or not isinstance(max_gate_age_seconds, int) or max_gate_age_seconds < 0:
        raise ValueError("max_gate_age_seconds must be a non-negative integer")
    decision, gate_reasons = _validate_daily_gate_for_workflow(
        daily_quality_gate,
        generated_at=workflow_generated_at,
        max_gate_age_seconds=max_gate_age_seconds,
    )
    previous_first_seen = _previous_reason_first_seen(previous_workflow)
    ack = _canonical_acknowledgement(acknowledgement)
    previous_reason_codes = set(previous_first_seen)
    ack_reason_codes = set(ack["reason_codes"]) if ack is not None else set()
    prior_reject_unacknowledged = (
        _previous_decision(previous_workflow) == REJECT_DECISION
        and not previous_reason_codes.issubset(ack_reason_codes)
    )

    active_reason_codes = set(gate_reasons)
    unresolved_reject = decision == PASS_DECISION and prior_reject_unacknowledged
    if unresolved_reject:
        decision = REJECT_DECISION
        active_reason_codes.add(UNRESOLVED_REJECT_REASON)

    ordered_active_codes = _ordered_workflow_reasons(active_reason_codes)
    active_reasons = [
        {
            "code": code,
            "severity": REASON_SEVERITY[code],
            "category": "quality_gate",
            "first_seen": previous_first_seen.get(code, workflow_generated_at),
            "last_seen": workflow_generated_at,
            "status": "active",
        }
        for code in ordered_active_codes
    ]
    resolved_reasons = [
        {
            "code": code,
            "severity": REASON_SEVERITY[code],
            "category": "quality_gate",
            "first_seen": first_seen,
            "last_seen": workflow_generated_at,
            "status": "resolved",
            "resolved_at": workflow_generated_at,
        }
        for code, first_seen in previous_first_seen.items()
        if code not in active_reason_codes
    ]

    if decision == REJECT_DECISION:
        hold_status = "blocked"
        escalation_level = "critical"
        alert_code = "quality_gate_reject_live_promotion"
        acknowledgement_required = True
    elif decision == HOLD_DECISION:
        hold_status = "active"
        escalation_level = "warning"
        alert_code = "quality_gate_hold_for_review"
        acknowledgement_required = True
    else:
        hold_status = "released"
        escalation_level = "none"
        alert_code = "quality_gate_released"
        acknowledgement_required = False

    acknowledgement_status = ack or ({"status": "required"} if acknowledgement_required else {"status": "not_required"})
    return {
        "schema_version": ALERT_HOLD_WORKFLOW_SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": workflow_generated_at,
        "source_gate": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": daily_quality_gate["generated_at"],
            "decision": daily_quality_gate["decision"],
        },
        "hold": {
            "status": hold_status,
            "decision": decision,
            "reason_codes": ordered_active_codes,
            "escalation_level": escalation_level,
            "acknowledgement_required": acknowledgement_required,
            "acknowledgement": acknowledgement_status,
            "release_conditions": WORKFLOW_RELEASE_CONDITIONS,
        },
        "active_reasons": active_reasons,
        "resolved_reasons": resolved_reasons,
        "alerts": [
            {
                "code": alert_code,
                "severity": escalation_level,
                "status": "open" if acknowledgement_required else "closed",
                "reason_codes": ordered_active_codes,
                "requires_acknowledgement": acknowledgement_required,
                "created_at": workflow_generated_at,
            }
        ],
    }


def _evidence_bundle_checks(evidence_bundle: Any, malformed: list[str]) -> dict[str, bool]:
    bundle = _mapping(evidence_bundle, "evidence_bundle", malformed)
    verified = _bool_field(bundle, "verified", malformed, error_field="evidence_bundle.verified")
    manifest_present = bundle.get("manifest_present")
    if manifest_present is not None and not isinstance(manifest_present, bool):
        malformed.append("evidence_bundle.manifest_present_not_bool")
    return {
        "evidence_bundle_verified": verified is True,
        "evidence_bundle_manifest_present": manifest_present is True,
    }


def _drift_checks(drift: Any, malformed: list[str]) -> dict[str, bool]:
    payload = _mapping(drift, "drift", malformed)
    checks = _mapping(payload.get("checks"), "drift.checks", malformed)
    schema_valid = checks.get("paper_live_shadow_drift_contract_schema_valid")
    material_absent = checks.get("paper_live_shadow_material_drift_absent")
    if schema_valid is not None and not isinstance(schema_valid, bool):
        malformed.append("drift.checks.paper_live_shadow_drift_contract_schema_valid_not_bool")
    if not isinstance(material_absent, bool):
        malformed.append("drift.checks.paper_live_shadow_material_drift_absent_not_bool")
    return {
        "paper_live_shadow_drift_contract_schema_valid": schema_valid is not False,
        "paper_shadow_material_drift_absent": material_absent is True,
    }


def _reconciliation_checks(reconciliation: Any, malformed: list[str]) -> dict[str, bool]:
    payload = _mapping(reconciliation, "reconciliation", malformed)
    checks = _mapping(payload.get("checks"), "reconciliation.checks", malformed)
    execution_chain_met = checks.get("execution_event_chain_met")
    reconcile_met = checks.get("order_position_reconciliation_met")
    if not isinstance(execution_chain_met, bool):
        malformed.append("reconciliation.checks.execution_event_chain_met_not_bool")
    if not isinstance(reconcile_met, bool):
        malformed.append("reconciliation.checks.order_position_reconciliation_met_not_bool")
    return {
        "execution_chain_present": execution_chain_met is True,
        "reconciliation_passed": reconcile_met is True,
    }


def _tca_checks(tca: Any, min_sample_size: Any, malformed: list[str]) -> dict[str, bool | float | int | None]:
    payload = _mapping(tca, "tca", malformed)
    sample_size = _non_negative_int(payload.get("sample_size"), "tca.sample_size", malformed)
    min_samples = _non_negative_int(min_sample_size, "min_sample_size", malformed)
    p95_slippage = _non_negative_number(payload.get("p95_slippage_bps"), "tca.p95_slippage_bps", malformed)
    max_p95_slippage = _non_negative_number(
        payload.get("max_p95_slippage_bps"),
        "tca.max_p95_slippage_bps",
        malformed,
    )
    availability_reason = payload.get("availability_reason")
    calibration_records_available = True
    if availability_reason is not None:
        if availability_reason != "calibration_records_unavailable":
            malformed.append("tca.availability_reason_invalid")
        else:
            calibration_records_available = False
    return {
        "availability_reason": availability_reason if availability_reason == "calibration_records_unavailable" else None,
        "calibration_records_available": calibration_records_available,
        "sample_size": sample_size,
        "min_sample_size": min_samples,
        "sufficient_sample_size": sample_size is not None and min_samples is not None and sample_size >= min_samples,
        "p95_slippage_bps": p95_slippage,
        "max_p95_slippage_bps": max_p95_slippage,
        "tca_slippage_within_threshold": (
            p95_slippage is not None and max_p95_slippage is not None and p95_slippage <= max_p95_slippage
        ),
    }


def _latency_checks(latency: Any, malformed: list[str]) -> dict[str, bool | float | None]:
    payload = _optional_mapping(latency, "latency", malformed)
    if not payload:
        return {
            "current_p95_ms": None,
            "baseline_p95_ms": None,
            "max_p95_shift_pct": None,
            "latency_distribution_stable": True,
        }
    current = _non_negative_number(payload.get("current_p95_ms"), "latency.current_p95_ms", malformed)
    baseline = _non_negative_number(payload.get("baseline_p95_ms"), "latency.baseline_p95_ms", malformed)
    max_shift = _non_negative_number(payload.get("max_p95_shift_pct"), "latency.max_p95_shift_pct", malformed)
    if baseline == 0.0:
        malformed.append("latency.baseline_p95_ms_zero")
    shifted = (
        current is not None
        and baseline is not None
        and baseline > 0.0
        and max_shift is not None
        and ((current - baseline) / baseline) > max_shift
    )
    return {
        "current_p95_ms": current,
        "baseline_p95_ms": baseline,
        "max_p95_shift_pct": max_shift,
        "latency_distribution_stable": not shifted,
    }


def _freshness_checks(freshness: Any, malformed: list[str]) -> dict[str, Any]:
    payload = _optional_mapping(freshness, "freshness", malformed)
    if not payload:
        return {"max_age_seconds": None, "items": {}, "data_freshness_met": True}
    max_age = _non_negative_number(payload.get("max_age_seconds"), "freshness.max_age_seconds", malformed)
    items = _mapping(payload.get("items"), "freshness.items", malformed)
    item_results: dict[str, dict[str, Any]] = {}
    freshness_met = max_age is not None
    for name, raw_item in items.items():
        if not isinstance(name, str) or not name:
            malformed.append("freshness.items.key_invalid")
            freshness_met = False
            continue
        item = _mapping(raw_item, f"freshness.items.{name}", malformed)
        age = _non_negative_number(item.get("age_seconds"), f"freshness.items.{name}.age_seconds", malformed)
        item_fresh = age is not None and max_age is not None and age <= max_age
        item_result: dict[str, Any] = {"age_seconds": age, "fresh": item_fresh}
        reason = item.get("reason")
        if reason is not None:
            if not isinstance(reason, str) or not reason:
                malformed.append(f"freshness.items.{name}.reason_invalid")
                freshness_met = False
            else:
                item_result["reason"] = reason
                item_fresh = False
                item_result["fresh"] = False
        freshness_met = freshness_met and item_fresh
        item_results[name] = item_result
    return {"max_age_seconds": max_age, "items": item_results, "data_freshness_met": freshness_met}


def _rolling_tca_durability_checks(
    rolling_tca_durability: Any,
    malformed: list[str],
) -> dict[str, Any]:
    if rolling_tca_durability is None:
        return {
            "present": False,
            "decision": None,
            "reasons": [],
            "rolling_tca_durability_passed": True,
            "rolling_tca_bucket_samples_sufficient": True,
        }

    malformed_start = len(malformed)
    payload = _mapping(rolling_tca_durability, "rolling_tca_durability", malformed)
    schema_version = payload.get("schema_version")
    if schema_version != "rolling_tca_durability_report.v1":
        malformed.append("rolling_tca_durability.schema_version_invalid")

    raw_decision = payload.get("decision")
    decision_map = {
        "pass": "pass",
        "hold": "hold",
        "reject": "reject",
        "durable": "pass",
        "insufficient": "hold",
        "rejected": "reject",
    }
    decision = decision_map.get(raw_decision) if isinstance(raw_decision, str) else None
    if decision is None:
        malformed.append("rolling_tca_durability.decision_invalid")

    raw_reason_field = "reason_codes" if "reason_codes" in payload else "reasons"
    raw_reasons = payload.get(raw_reason_field)
    reasons: list[str] = []
    reason_map = {
        "rolling_tca_durability_failed": "rolling_tca_durability_failed",
        "bucket_regression": "bucket_regression",
        "insufficient_bucket_samples": "insufficient_bucket_samples",
        "insufficient_bucket_sample_size": "insufficient_bucket_samples",
        "stale_dates": "data_freshness_violation",
        "rolling_slippage_exceeds_threshold": "rolling_tca_durability_failed",
        "bucket_latency_regression": "bucket_regression",
        "maker_taker_mix_shift": "bucket_regression",
    }
    if not isinstance(raw_reasons, list):
        malformed.append(f"rolling_tca_durability.{raw_reason_field}_not_list")
    else:
        for index, reason in enumerate(raw_reasons):
            mapped = reason_map.get(reason) if isinstance(reason, str) else None
            if mapped is None:
                malformed.append(f"rolling_tca_durability.{raw_reason_field}[{index}]_invalid")
                continue
            reasons.append(mapped)

    if decision != "reject" and "rolling_tca_durability_failed" in reasons:
        malformed.append("rolling_tca_durability.rolling_tca_durability_failed_reason_decision_mismatch")
    if decision != "reject" and "bucket_regression" in reasons:
        malformed.append("rolling_tca_durability.bucket_regression_reason_decision_mismatch")
    if decision != "hold" and "insufficient_bucket_samples" in reasons:
        malformed.append("rolling_tca_durability.insufficient_bucket_samples_reason_decision_mismatch")
    if decision == "pass" and reasons:
        malformed.append("rolling_tca_durability.reasons_present_for_pass")
    if decision in {"hold", "reject"} and not reasons:
        malformed.append("rolling_tca_durability.reasons_missing_for_non_pass")

    checks = _mapping(payload.get("checks"), "rolling_tca_durability.checks", malformed)
    durable = checks.get("rolling_tca_durable")
    if durable is None and raw_decision in {"durable", "insufficient", "rejected"}:
        durable = raw_decision == "durable"
    elif not isinstance(durable, bool):
        malformed.append("rolling_tca_durability.checks.rolling_tca_durable_not_bool")

    sufficient_buckets = checks.get("sufficient_bucket_samples")
    if sufficient_buckets is None:
        sufficient_buckets = checks.get("all_bucket_windows_sufficiently_sampled")
    if sufficient_buckets is None and raw_decision in {"durable", "insufficient", "rejected"}:
        sufficient_buckets = raw_decision != "insufficient"
    elif not isinstance(sufficient_buckets, bool):
        malformed.append("rolling_tca_durability.checks.sufficient_bucket_samples_not_bool")

    if decision == "reject" and "rolling_tca_durability_failed" not in reasons:
        reasons.append("rolling_tca_durability_failed")
    if durable is False and "rolling_tca_durability_failed" not in reasons and decision == "reject":
        reasons.append("rolling_tca_durability_failed")
    if sufficient_buckets is False and "insufficient_bucket_samples" not in reasons:
        reasons.append("insufficient_bucket_samples")

    if len(malformed) > malformed_start:
        return {
            "present": True,
            "decision": decision,
            "reasons": [],
            "rolling_tca_durability_passed": False,
            "rolling_tca_bucket_samples_sufficient": False,
        }

    return {
        "present": True,
        "decision": decision,
        "reasons": _ordered_reasons(set(reasons)),
        "rolling_tca_durability_passed": decision != "reject" and durable is not False,
        "rolling_tca_bucket_samples_sufficient": sufficient_buckets is True,
    }


def _promotion_readiness_checks(promotion_readiness: Any, malformed: list[str]) -> dict[str, Any]:
    if promotion_readiness is None:
        return {
            "present": False,
            "decision": None,
            "score": None,
            "reasons": [],
            "promotion_readiness_passed": True,
        }
    malformed_start = len(malformed)
    payload = _mapping(promotion_readiness, "promotion_readiness", malformed)
    if payload.get("schema_version") != "promotion_readiness_scorecard.v1":
        malformed.append("promotion_readiness.schema_version_invalid")
    decision = payload.get("decision")
    if decision not in {"pass", "review", "hold", "reject"}:
        malformed.append("promotion_readiness.decision_invalid")
    scores = _mapping(payload.get("scores"), "promotion_readiness.scores", malformed)
    score = _non_negative_number(
        scores.get("promotion_readiness"),
        "promotion_readiness.scores.promotion_readiness",
        malformed,
    )
    if score is not None and score > 100.0:
        malformed.append("promotion_readiness.scores.promotion_readiness_above_100")
    reason_map = {
        "review": "promotion_readiness_review",
        "hold": "promotion_readiness_hold",
        "reject": "promotion_readiness_reject",
    }
    reasons = [reason_map[decision]] if decision in reason_map else []
    if len(malformed) > malformed_start:
        return {
            "present": True,
            "decision": decision if isinstance(decision, str) else None,
            "score": score,
            "reasons": [],
            "promotion_readiness_passed": False,
        }
    return {
        "present": True,
        "decision": decision,
        "score": score,
        "reasons": reasons,
        "promotion_readiness_passed": decision == "pass",
    }


def build_daily_quality_gate_report(
    *,
    evidence_bundle: Mapping[str, Any],
    drift: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    tca: Mapping[str, Any],
    latency: Mapping[str, Any] | None = None,
    freshness: Mapping[str, Any] | None = None,
    rolling_tca_durability: Mapping[str, Any] | None = None,
    promotion_readiness: Mapping[str, Any] | None = None,
    min_sample_size: int = 30,
    generated_at: str | None = None,
) -> dict[str, Any]:
    malformed: list[str] = []
    bundle_checks = _evidence_bundle_checks(evidence_bundle, malformed)
    drift_result = _drift_checks(drift, malformed)
    reconciliation_result = _reconciliation_checks(reconciliation, malformed)
    tca_result = _tca_checks(tca, min_sample_size, malformed)
    latency_result = _latency_checks(latency, malformed)
    freshness_result = _freshness_checks(freshness, malformed)
    rolling_tca_result = _rolling_tca_durability_checks(rolling_tca_durability, malformed)
    promotion_readiness_result = _promotion_readiness_checks(promotion_readiness, malformed)

    reasons: set[str] = set()
    if malformed:
        reasons.add("malformed_evidence")
    if not bundle_checks["evidence_bundle_verified"] or not bundle_checks["evidence_bundle_manifest_present"]:
        reasons.add("malformed_evidence")
    if not drift_result["paper_live_shadow_drift_contract_schema_valid"]:
        reasons.add("malformed_evidence")
    if drift_result["paper_shadow_material_drift_absent"] is False:
        reasons.add("paper_shadow_material_drift")
    if tca_result["tca_slippage_within_threshold"] is False:
        reasons.add("tca_slippage_exceeds_threshold")
    if reconciliation_result["execution_chain_present"] is False:
        reasons.add("execution_chain_missing")
    if reconciliation_result["reconciliation_passed"] is False:
        reasons.add("reconcile_failed")
    if latency_result["latency_distribution_stable"] is False:
        reasons.add("latency_distribution_shift")
    if freshness_result["data_freshness_met"] is False:
        reasons.add("data_freshness_violation")
    if tca_result["calibration_records_available"] is False:
        reasons.add("calibration_records_unavailable")
    if tca_result["sufficient_sample_size"] is False:
        reasons.add("insufficient_sample_size")
    if rolling_tca_result["present"]:
        reasons.update(rolling_tca_result["reasons"])
    if promotion_readiness_result["present"]:
        reasons.update(promotion_readiness_result["reasons"])

    ordered_reasons = _ordered_reasons(reasons)
    if any(reason in HARD_REASONS for reason in ordered_reasons):
        decision = REJECT_DECISION
    elif any(reason in SOFT_REASONS for reason in ordered_reasons):
        decision = HOLD_DECISION
    else:
        decision = PASS_DECISION

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": _generated_at(generated_at),
        "decision": decision,
        "reasons": ordered_reasons,
        "malformed_inputs": malformed,
        "checks": {
            **bundle_checks,
            **drift_result,
            **reconciliation_result,
            "tca_slippage_within_threshold": bool(tca_result["tca_slippage_within_threshold"]),
            "calibration_records_available": bool(tca_result["calibration_records_available"]),
            "latency_distribution_stable": bool(latency_result["latency_distribution_stable"]),
            "data_freshness_met": bool(freshness_result["data_freshness_met"]),
            "sufficient_sample_size": bool(tca_result["sufficient_sample_size"]),
            "rolling_tca_durability_passed": bool(rolling_tca_result["rolling_tca_durability_passed"]),
            "rolling_tca_bucket_samples_sufficient": bool(
                rolling_tca_result["rolling_tca_bucket_samples_sufficient"]
            ),
            "promotion_readiness_passed": bool(promotion_readiness_result["promotion_readiness_passed"]),
        },
        "inputs": {
            "tca": tca_result,
            "latency": latency_result,
            "freshness": freshness_result,
            **({"rolling_tca_durability": rolling_tca_result} if rolling_tca_result["present"] else {}),
            **({"promotion_readiness": promotion_readiness_result} if promotion_readiness_result["present"] else {}),
        },
    }


def write_daily_quality_gate_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_daily_quality_gate_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def write_quality_gate_alert_hold_workflow(
    output_path: str | Path,
    daily_quality_gate: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_quality_gate_alert_hold_workflow(daily_quality_gate, **kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "HOLD_DECISION",
    "PASS_DECISION",
    "REJECT_DECISION",
    "build_quality_gate_alert_hold_workflow",
    "build_daily_quality_gate_report",
    "write_quality_gate_alert_hold_workflow",
    "write_daily_quality_gate_report",
]
