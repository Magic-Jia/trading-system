from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "daily_quality_gate_report.v1"
MODE = "simulated_live"
PASS_DECISION = "pass_for_continued_paper"
HOLD_DECISION = "hold_for_review"
REJECT_DECISION = "reject_live_promotion"

HARD_REASONS = (
    "paper_shadow_material_drift",
    "tca_slippage_exceeds_threshold",
    "execution_chain_missing",
    "reconcile_failed",
    "malformed_evidence",
)
SOFT_REASONS = (
    "latency_distribution_shift",
    "data_freshness_violation",
    "insufficient_sample_size",
)
REASON_ORDER = (
    "paper_shadow_material_drift",
    "tca_slippage_exceeds_threshold",
    "execution_chain_missing",
    "reconcile_failed",
    "latency_distribution_shift",
    "data_freshness_violation",
    "insufficient_sample_size",
    "malformed_evidence",
)


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


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in REASON_ORDER if reason in reasons]


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
    return {
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
        item_results[name] = {"age_seconds": age, "fresh": item_fresh}
        freshness_met = freshness_met and item_fresh
    return {"max_age_seconds": max_age, "items": item_results, "data_freshness_met": freshness_met}


def build_daily_quality_gate_report(
    *,
    evidence_bundle: Mapping[str, Any],
    drift: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    tca: Mapping[str, Any],
    latency: Mapping[str, Any] | None = None,
    freshness: Mapping[str, Any] | None = None,
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
    if tca_result["sufficient_sample_size"] is False:
        reasons.add("insufficient_sample_size")

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
            "latency_distribution_stable": bool(latency_result["latency_distribution_stable"]),
            "data_freshness_met": bool(freshness_result["data_freshness_met"]),
            "sufficient_sample_size": bool(tca_result["sufficient_sample_size"]),
        },
        "inputs": {
            "tca": tca_result,
            "latency": latency_result,
            "freshness": freshness_result,
        },
    }


def write_daily_quality_gate_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_daily_quality_gate_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "HOLD_DECISION",
    "PASS_DECISION",
    "REJECT_DECISION",
    "build_daily_quality_gate_report",
    "write_daily_quality_gate_report",
]
