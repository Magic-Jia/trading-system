from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "longitudinal_live_sim_trend_report.v1"
MODE = "simulated_live"
PASS_DECISION = "pass_for_continued_paper"
HOLD_DECISION = "hold_for_review"
REJECT_DECISION = "reject_live_promotion"

REASON_ORDER = (
    "missing_day",
    "stale_day",
    "malformed_day",
    "daily_quality_gate_rejected",
    "daily_quality_gate_held",
    "paper_shadow_material_drift",
    "tca_calibration_failed",
    "rolling_tca_durability_failed",
    "bucket_regression",
    "reconciliation_failed",
    "latency_regression",
    "slippage_regression",
    "fill_quality_regression",
    "freshness_regression",
)
DAY_REASON_ORDER = (
    "stale_day",
    "freshness_stale",
    "malformed_day",
    "daily_quality_gate_rejected",
    "daily_quality_gate_held",
    "paper_shadow_material_drift",
    "tca_calibration_failed",
    "rolling_tca_durability_failed",
    "bucket_regression",
    "insufficient_bucket_samples",
    "reconciliation_failed",
    "latency_regression",
    "slippage_regression",
    "fill_quality_regression",
)

HARD_REASONS = {
    "missing_day",
    "stale_day",
    "malformed_day",
    "daily_quality_gate_rejected",
    "paper_shadow_material_drift",
    "tca_calibration_failed",
    "rolling_tca_durability_failed",
    "bucket_regression",
    "reconciliation_failed",
}

DEFAULT_REGRESSION_THRESHOLDS = {
    "latency_p95_ms": 250.0,
    "tca_p95_slippage_bps": 2.0,
    "paper_live_shadow_drift_bps": 1.0,
    "freshness_oldest_age_seconds": 900.0,
    "fill_rate": 0.10,
    "partial_fill_rate": 0.10,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today_generated_at(value: str | None) -> str:
    if value is not None:
        return value
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_date(value: Any, field: str, malformed: list[str]) -> date | None:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        malformed.append(f"{field}_invalid")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        malformed.append(f"{field}_invalid")
        return None


def _date_range(start: date, end: date) -> list[str]:
    if end < start:
        return []
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _mapping(value: Any, field: str, malformed: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        malformed.append(f"{field}_not_object")
        return {}
    return value


def _optional_mapping(value: Any, field: str, malformed: list[str]) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _mapping(value, field, malformed)


def _bool(value: Any, field: str, malformed: list[str]) -> bool | None:
    if not isinstance(value, bool):
        malformed.append(f"{field}_not_bool")
        return None
    return value


def _number(value: Any, field: str, malformed: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        malformed.append(f"{field}_not_finite_number")
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        malformed.append(f"{field}_not_finite_number")
        return None
    return parsed


def _non_negative_number(value: Any, field: str, malformed: list[str]) -> float | None:
    parsed = _number(value, field, malformed)
    if parsed is None:
        return None
    if parsed < 0.0:
        malformed.append(f"{field}_negative")
        return None
    return parsed


def _non_negative_int(value: Any, field: str, malformed: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        malformed.append(f"{field}_not_int")
        return None
    if value < 0:
        malformed.append(f"{field}_negative")
        return None
    return value


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in REASON_ORDER if reason in reasons]


def _ordered_day_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in DAY_REASON_ORDER if reason in reasons]


def _metric_direction(delta: float | None) -> str:
    if delta is None or math.isclose(delta, 0.0, abs_tol=1e-12):
        return "flat"
    return "up" if delta > 0.0 else "down"


def _load_daily_report(value: Mapping[str, Any] | str | Path, malformed: list[str]) -> Mapping[str, Any]:
    if isinstance(value, (str, Path)):
        path = Path(value)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            malformed.append("daily_report_file_unreadable")
            return {}
        return _mapping(loaded, "daily_report", malformed)
    return _mapping(value, "daily_report", malformed)


def _validate_daily_report(raw_day: Mapping[str, Any]) -> dict[str, Any]:
    malformed: list[str] = []
    day_date = _parse_date(raw_day.get("report_date"), "report_date", malformed)
    reasons: set[str] = set()

    quality_gate = _mapping(raw_day.get("daily_quality_gate"), "daily_quality_gate", malformed)
    gate_decision = quality_gate.get("decision")
    if gate_decision not in {PASS_DECISION, HOLD_DECISION, REJECT_DECISION}:
        malformed.append("daily_quality_gate.decision_invalid")
    if gate_decision == REJECT_DECISION:
        reasons.add("daily_quality_gate_rejected")
    elif gate_decision == HOLD_DECISION:
        reasons.add("daily_quality_gate_held")
    gate_reasons = quality_gate.get("reasons", [])
    if not isinstance(gate_reasons, list):
        malformed.append("daily_quality_gate.reasons_not_list")
    else:
        for index, reason in enumerate(gate_reasons):
            if not isinstance(reason, str):
                malformed.append(f"daily_quality_gate.reasons[{index}]_not_string")
                continue
            if reason in {
                "rolling_tca_durability_failed",
                "bucket_regression",
                "insufficient_bucket_samples",
            }:
                reasons.add(reason)

    gate_checks = _optional_mapping(quality_gate.get("checks"), "daily_quality_gate.checks", malformed)
    gate_drift_absent = gate_checks.get("paper_shadow_material_drift_absent")
    if gate_drift_absent is not None and not isinstance(gate_drift_absent, bool):
        malformed.append("daily_quality_gate.checks.paper_shadow_material_drift_absent_not_bool")
    if gate_drift_absent is False:
        reasons.add("paper_shadow_material_drift")

    tca = _mapping(raw_day.get("tca"), "tca", malformed)
    sample_size = _non_negative_int(tca.get("sample_size"), "tca.sample_size", malformed)
    tca_p95 = _non_negative_number(tca.get("p95_slippage_bps"), "tca.p95_slippage_bps", malformed)
    tca_max = _non_negative_number(tca.get("max_p95_slippage_bps"), "tca.max_p95_slippage_bps", malformed)
    tca_checks = _optional_mapping(tca.get("checks"), "tca.checks", malformed)
    all_metrics_within_tolerance = tca_checks.get("all_metrics_within_tolerance")
    if all_metrics_within_tolerance is not None and not isinstance(all_metrics_within_tolerance, bool):
        malformed.append("tca.checks.all_metrics_within_tolerance_not_bool")
    evidence_fresh = tca_checks.get("evidence_fresh")
    if evidence_fresh is not None and not isinstance(evidence_fresh, bool):
        malformed.append("tca.checks.evidence_fresh_not_bool")
    if tca_p95 is not None and tca_max is not None and tca_p95 > tca_max:
        reasons.add("tca_calibration_failed")
    if all_metrics_within_tolerance is False or evidence_fresh is False:
        reasons.add("tca_calibration_failed")

    drift = _mapping(raw_day.get("drift"), "drift", malformed)
    drift_bps = _non_negative_number(drift.get("paper_live_shadow_drift_bps"), "drift.paper_live_shadow_drift_bps", malformed)
    max_drift_bps = _non_negative_number(drift.get("max_abs_drift_bps"), "drift.max_abs_drift_bps", malformed)
    drift_checks = _optional_mapping(drift.get("checks"), "drift.checks", malformed)
    drift_absent = drift_checks.get("paper_live_shadow_material_drift_absent")
    if drift_absent is not None and not isinstance(drift_absent, bool):
        malformed.append("drift.checks.paper_live_shadow_material_drift_absent_not_bool")
    if drift_absent is False or (drift_bps is not None and max_drift_bps is not None and drift_bps > max_drift_bps):
        reasons.add("paper_shadow_material_drift")

    reconciliation = _mapping(raw_day.get("reconciliation"), "reconciliation", malformed)
    reconciliation_checks = _mapping(reconciliation.get("checks"), "reconciliation.checks", malformed)
    execution_chain_met = _bool(
        reconciliation_checks.get("execution_event_chain_met"),
        "reconciliation.checks.execution_event_chain_met",
        malformed,
    )
    reconciliation_met = _bool(
        reconciliation_checks.get("order_position_reconciliation_met"),
        "reconciliation.checks.order_position_reconciliation_met",
        malformed,
    )
    if execution_chain_met is False or reconciliation_met is False:
        reasons.add("reconciliation_failed")

    latency = _mapping(raw_day.get("latency"), "latency", malformed)
    latency_p95 = _non_negative_number(latency.get("p95_ms"), "latency.p95_ms", malformed)
    latency_baseline = _non_negative_number(latency.get("baseline_p95_ms"), "latency.baseline_p95_ms", malformed)
    latency_max_shift = _non_negative_number(latency.get("max_p95_shift_pct"), "latency.max_p95_shift_pct", malformed)
    if latency_baseline == 0.0:
        malformed.append("latency.baseline_p95_ms_zero")
    if (
        latency_p95 is not None
        and latency_baseline is not None
        and latency_baseline > 0.0
        and latency_max_shift is not None
        and ((latency_p95 - latency_baseline) / latency_baseline) > latency_max_shift
    ):
        reasons.add("latency_regression")

    slippage = _mapping(raw_day.get("slippage"), "slippage", malformed)
    slippage_p95 = _non_negative_number(slippage.get("p95_bps"), "slippage.p95_bps", malformed)
    slippage_max = _non_negative_number(slippage.get("max_p95_bps"), "slippage.max_p95_bps", malformed)
    if slippage_p95 is not None and slippage_max is not None and slippage_p95 > slippage_max:
        reasons.add("slippage_regression")

    fill_quality = _mapping(raw_day.get("fill_quality"), "fill_quality", malformed)
    fill_rate = _non_negative_number(fill_quality.get("fill_rate"), "fill_quality.fill_rate", malformed)
    min_fill_rate = _non_negative_number(fill_quality.get("min_fill_rate"), "fill_quality.min_fill_rate", malformed)
    partial_fill_rate = _non_negative_number(fill_quality.get("partial_fill_rate"), "fill_quality.partial_fill_rate", malformed)
    max_partial_fill_rate = _non_negative_number(
        fill_quality.get("max_partial_fill_rate"),
        "fill_quality.max_partial_fill_rate",
        malformed,
    )
    for field, value in (
        ("fill_quality.fill_rate", fill_rate),
        ("fill_quality.min_fill_rate", min_fill_rate),
        ("fill_quality.partial_fill_rate", partial_fill_rate),
        ("fill_quality.max_partial_fill_rate", max_partial_fill_rate),
    ):
        if value is not None and value > 1.0:
            malformed.append(f"{field}_above_one")
    if fill_rate is not None and min_fill_rate is not None and fill_rate < min_fill_rate:
        reasons.add("fill_quality_regression")
    if partial_fill_rate is not None and max_partial_fill_rate is not None and partial_fill_rate > max_partial_fill_rate:
        reasons.add("fill_quality_regression")

    freshness = _mapping(raw_day.get("freshness"), "freshness", malformed)
    max_age = _non_negative_number(freshness.get("max_age_seconds"), "freshness.max_age_seconds", malformed)
    oldest_age = _non_negative_number(freshness.get("oldest_age_seconds"), "freshness.oldest_age_seconds", malformed)
    if max_age is not None and oldest_age is not None and oldest_age > max_age:
        reasons.add("stale_day")
        reasons.add("freshness_stale")

    if malformed:
        reasons.add("malformed_day")

    status = "pass"
    ordered = _ordered_day_reasons(reasons)
    if any(reason in HARD_REASONS for reason in ordered):
        status = "reject"
    elif ordered:
        status = "hold"

    return {
        "report_date": day_date.isoformat() if day_date is not None else raw_day.get("report_date"),
        "status": status,
        "reasons": ordered,
        "malformed_inputs": malformed,
        "metrics": {
            "tca_sample_size": sample_size,
            "tca_p95_slippage_bps": tca_p95,
            "paper_live_shadow_drift_bps": drift_bps,
            "latency_p95_ms": latency_p95,
            "slippage_p95_bps": slippage_p95,
            "fill_rate": fill_rate,
            "partial_fill_rate": partial_fill_rate,
            "freshness_oldest_age_seconds": oldest_age,
        },
    }


def _trend_check(
    values: Sequence[float | None],
    *,
    threshold: float,
    regression_direction: str,
) -> dict[str, Any]:
    numeric_values = [value for value in values if value is not None]
    first = numeric_values[0] if numeric_values else None
    last = numeric_values[-1] if numeric_values else None
    delta = None if first is None or last is None else last - first
    direction = _metric_direction(delta)
    regressed = False
    if delta is not None:
        if regression_direction == "up":
            regressed = delta > threshold
        elif regression_direction == "down":
            regressed = -delta > threshold
    return {
        "values": list(values),
        "first": first,
        "last": last,
        "delta": delta,
        "direction": direction,
        "regressed": regressed,
        "regression_threshold": threshold,
    }


def _trend_checks(days: list[dict[str, Any]], thresholds: Mapping[str, float]) -> dict[str, Any]:
    metrics = [day["metrics"] for day in days]
    return {
        "latency_p95_ms": _trend_check(
            [metric["latency_p95_ms"] for metric in metrics],
            threshold=thresholds["latency_p95_ms"],
            regression_direction="up",
        ),
        "tca_p95_slippage_bps": _trend_check(
            [metric["tca_p95_slippage_bps"] for metric in metrics],
            threshold=thresholds["tca_p95_slippage_bps"],
            regression_direction="up",
        ),
        "paper_live_shadow_drift_bps": _trend_check(
            [metric["paper_live_shadow_drift_bps"] for metric in metrics],
            threshold=thresholds["paper_live_shadow_drift_bps"],
            regression_direction="up",
        ),
        "freshness_oldest_age_seconds": _trend_check(
            [metric["freshness_oldest_age_seconds"] for metric in metrics],
            threshold=thresholds["freshness_oldest_age_seconds"],
            regression_direction="up",
        ),
        "fill_rate": _trend_check(
            [metric["fill_rate"] for metric in metrics],
            threshold=thresholds["fill_rate"],
            regression_direction="down",
        ),
        "partial_fill_rate": _trend_check(
            [metric["partial_fill_rate"] for metric in metrics],
            threshold=thresholds["partial_fill_rate"],
            regression_direction="up",
        ),
    }


def _thresholds(overrides: Mapping[str, Any] | None) -> dict[str, float]:
    thresholds = dict(DEFAULT_REGRESSION_THRESHOLDS)
    if overrides is None:
        return thresholds
    for key, value in overrides.items():
        if key not in thresholds:
            raise ValueError(f"unknown regression threshold: {key}")
        parsed = _number(value, f"regression_thresholds.{key}", [])
        if parsed is None or parsed < 0.0:
            raise ValueError(f"regression threshold {key} must be finite and non-negative")
        thresholds[key] = parsed
    return thresholds


def build_longitudinal_live_sim_trend_report(
    *,
    daily_reports: Sequence[Mapping[str, Any] | str | Path],
    start_date: str,
    end_date: str,
    generated_at: str | None = None,
    regression_thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    range_malformed: list[str] = []
    parsed_start = _parse_date(start_date, "start_date", range_malformed)
    parsed_end = _parse_date(end_date, "end_date", range_malformed)
    if parsed_start is None or parsed_end is None:
        raise ValueError("start_date and end_date must be ISO dates")
    if parsed_end < parsed_start:
        raise ValueError("end_date must be on or after start_date")

    thresholds = _thresholds(regression_thresholds)
    expected_dates = _date_range(parsed_start, parsed_end)
    load_malformed: list[str] = []
    parsed_days = [_validate_daily_report(_load_daily_report(day, load_malformed)) for day in daily_reports]

    days_by_date: dict[str, dict[str, Any]] = {}
    duplicate_dates: set[str] = set()
    out_of_range_dates: set[str] = set()
    unplaced_malformed_inputs: list[str] = []
    for day in parsed_days:
        report_date = day["report_date"]
        if not isinstance(report_date, str) or report_date not in expected_dates:
            if isinstance(report_date, str):
                out_of_range_dates.add(report_date)
            unplaced_malformed_inputs.extend(day["malformed_inputs"])
            continue
        if report_date in days_by_date:
            duplicate_dates.add(report_date)
            day["malformed_inputs"].append("report_date_duplicate")
            day["reasons"] = _ordered_day_reasons(set(day["reasons"]) | {"malformed_day"})
            day["status"] = "reject"
        days_by_date[report_date] = day

    if duplicate_dates:
        for report_date in duplicate_dates:
            days_by_date[report_date]["malformed_inputs"].append("report_date_duplicate")
            days_by_date[report_date]["reasons"] = _ordered_day_reasons(
                set(days_by_date[report_date]["reasons"]) | {"malformed_day"}
            )
            days_by_date[report_date]["status"] = "reject"

    missing_dates = [report_date for report_date in expected_dates if report_date not in days_by_date]
    ordered_days = [days_by_date[report_date] for report_date in expected_dates if report_date in days_by_date]
    trends = _trend_checks(ordered_days, thresholds)

    reasons: set[str] = set()
    if missing_dates:
        reasons.add("missing_day")
    if load_malformed:
        reasons.add("malformed_day")
    if out_of_range_dates or unplaced_malformed_inputs:
        reasons.add("malformed_day")
    for day in ordered_days:
        reasons.update(day["reasons"])

    if trends["latency_p95_ms"]["regressed"]:
        reasons.add("latency_regression")
    if trends["tca_p95_slippage_bps"]["regressed"]:
        reasons.add("slippage_regression")
    if trends["fill_rate"]["regressed"] or trends["partial_fill_rate"]["regressed"]:
        reasons.add("fill_quality_regression")
    if trends["freshness_oldest_age_seconds"]["regressed"] and "stale_day" not in reasons:
        reasons.add("freshness_regression")
    if trends["paper_live_shadow_drift_bps"]["regressed"]:
        reasons.add("paper_shadow_material_drift")

    ordered_reasons = _ordered_reasons(reasons)
    if any(reason in HARD_REASONS for reason in ordered_reasons):
        decision = REJECT_DECISION
    elif ordered_reasons:
        decision = HOLD_DECISION
    else:
        decision = PASS_DECISION

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": _today_generated_at(generated_at),
        "start_date": start_date,
        "end_date": end_date,
        "decision": decision,
        "reasons": ordered_reasons,
        "checks": {
            "all_expected_days_present": not missing_dates,
            "all_days_well_formed": (
                not any("malformed_day" in day["reasons"] for day in ordered_days)
                and not load_malformed
                and not out_of_range_dates
                and not unplaced_malformed_inputs
            ),
            "all_days_fresh": not any("stale_day" in day["reasons"] for day in ordered_days),
            "all_daily_quality_gates_passed": not any(
                reason in day["reasons"] for day in ordered_days for reason in ("daily_quality_gate_rejected", "daily_quality_gate_held")
            ),
            "tca_calibration_stable": "tca_calibration_failed" not in ordered_reasons and not trends["tca_p95_slippage_bps"]["regressed"],
            "rolling_tca_durability_stable": (
                "rolling_tca_durability_failed" not in ordered_reasons
                and "bucket_regression" not in ordered_reasons
                and "insufficient_bucket_samples" not in ordered_reasons
            ),
            "paper_live_shadow_drift_stable": "paper_shadow_material_drift" not in ordered_reasons,
            "reconciliation_stable": "reconciliation_failed" not in ordered_reasons,
            "latency_stable": "latency_regression" not in ordered_reasons,
            "slippage_stable": "slippage_regression" not in ordered_reasons,
            "fill_quality_stable": "fill_quality_regression" not in ordered_reasons,
            "freshness_stable": "freshness_regression" not in ordered_reasons and "stale_day" not in ordered_reasons,
            "no_regressions_detected": not any(
                trends[name]["regressed"]
                for name in (
                    "latency_p95_ms",
                    "tca_p95_slippage_bps",
                    "paper_live_shadow_drift_bps",
                    "freshness_oldest_age_seconds",
                    "fill_rate",
                    "partial_fill_rate",
                )
            ),
        },
        "missing_dates": missing_dates,
        "malformed_inputs": load_malformed + unplaced_malformed_inputs,
        "out_of_range_dates": sorted(out_of_range_dates),
        "trend_checks": trends,
        "days": ordered_days,
    }


def write_longitudinal_live_sim_trend_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_longitudinal_live_sim_trend_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "HOLD_DECISION",
    "PASS_DECISION",
    "REJECT_DECISION",
    "build_longitudinal_live_sim_trend_report",
    "write_longitudinal_live_sim_trend_report",
]
