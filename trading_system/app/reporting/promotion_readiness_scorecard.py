from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "promotion_readiness_scorecard.v1"
MODE = "simulated_live"
PASS_DECISION = "pass"
REVIEW_DECISION = "review"
HOLD_DECISION = "hold"
REJECT_DECISION = "reject"

COMPONENT_NAMES = (
    "data_quality",
    "execution_realism",
    "venue_rulebook_coverage",
    "derivatives_risk",
    "cross_source_parity",
    "live_sim_durability",
)

DEFAULT_THRESHOLDS = {
    "min_component_score": 80.0,
    "min_overall_score": 85.0,
    "min_sample_count": 30,
    "min_live_sim_duration_hours": 72.0,
    "max_rulebook_age_seconds": 7 * 24 * 60 * 60,
    "max_parity_drift_bps": 2.0,
}

REASON_SEVERITY = {
    "insufficient_duration": "hold",
    "insufficient_samples": "hold",
    "stale_rulebook": "hold",
    "parity_drift": "hold",
    "race_condition_hold": "reject",
    "derivatives_risk_hold": "reject",
    "missing_component": "reject",
    "malformed_evidence": "reject",
    "timestamp_ordering": "reject",
}

REASON_ORDER = (
    "missing_component",
    "malformed_evidence",
    "timestamp_ordering",
    "insufficient_samples",
    "stale_rulebook",
    "parity_drift",
    "insufficient_duration",
    "race_condition_hold",
    "derivatives_risk_hold",
)

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_timestamp(value, "generated_at")
    return value


def _parse_timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return parsed.astimezone(UTC)


def _number(value: Any, field: str) -> tuple[float | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{field} must be numeric"
    number = float(value)
    if not math.isfinite(number):
        return None, f"{field} must be finite"
    return number, None


def _optional_number(value: Any, field: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    return _number(value, field)


def _non_negative_int(value: Any, field: str) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{field} must be an integer"
    if value < 0:
        return None, f"{field} must be non-negative"
    return value, None


def _thresholds(overrides: Mapping[str, Any] | None) -> dict[str, float | int]:
    if overrides is None:
        return dict(DEFAULT_THRESHOLDS)
    if not isinstance(overrides, Mapping):
        raise ValueError("thresholds must be an object")
    unknown = sorted(set(overrides) - set(DEFAULT_THRESHOLDS))
    if unknown:
        raise ValueError("unknown promotion readiness threshold: " + ", ".join(unknown))
    values = dict(DEFAULT_THRESHOLDS)
    for key, raw_value in overrides.items():
        if key == "min_sample_count":
            parsed, error = _non_negative_int(raw_value, f"thresholds.{key}")
        else:
            parsed, error = _number(raw_value, f"thresholds.{key}")
            if parsed is not None and parsed < 0.0:
                error = f"thresholds.{key} must be non-negative"
        if error is not None or parsed is None:
            raise ValueError(error or f"thresholds.{key} is invalid")
        values[key] = parsed
    return values


def _blocker(component: str, reason_code: str, detail: str) -> dict[str, str]:
    return {
        "component": component,
        "reason_code": reason_code,
        "severity": REASON_SEVERITY[reason_code],
        "detail": detail,
    }


def _ordered_blockers(blockers: list[dict[str, str]]) -> list[dict[str, str]]:
    component_order = {component: index for index, component in enumerate(COMPONENT_NAMES)}
    reason_order = {reason: index for index, reason in enumerate(REASON_ORDER)}
    return sorted(
        blockers,
        key=lambda item: (
            component_order.get(item["component"], len(component_order)),
            reason_order.get(item["reason_code"], len(reason_order)),
        ),
    )


def _score_from_coverage(coverage: float) -> float:
    return round(max(0.0, min(1.0, coverage)) * 100.0, 1)


def _component_gate(
    component: str,
    raw: Any,
    *,
    generated_at: datetime,
    thresholds: Mapping[str, float | int],
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    if not isinstance(raw, Mapping):
        return {
            "gate": REJECT_DECISION,
            "score": 0.0,
            "as_of": None,
            "sample_count": None,
            "blockers": [_blocker(component, "missing_component", "component evidence is missing")],
        }

    as_of_raw = raw.get("as_of")
    try:
        as_of = _parse_timestamp(as_of_raw, f"{component}.as_of")
    except ValueError as exc:
        return {
            "gate": REJECT_DECISION,
            "score": 0.0,
            "as_of": as_of_raw if isinstance(as_of_raw, str) else None,
            "sample_count": None,
            "blockers": [_blocker(component, "malformed_evidence", str(exc).replace(f"{component}.", ""))],
        }
    if as_of > generated_at:
        blockers.append(_blocker(component, "timestamp_ordering", "as_of must be at or before generated_at"))

    coverage, coverage_error = _number(raw.get("coverage_score"), "coverage_score")
    if coverage_error is not None or coverage is None:
        blockers.append(_blocker(component, "malformed_evidence", coverage_error or "coverage_score is invalid"))
    sample_count, sample_error = _non_negative_int(raw.get("sample_count"), "sample_count")
    if sample_error is not None or sample_count is None:
        blockers.append(_blocker(component, "malformed_evidence", sample_error or "sample_count is invalid"))

    score = _score_from_coverage(coverage) if coverage is not None and not any(
        blocker["severity"] == "reject" for blocker in blockers
    ) else 0.0

    if component != "venue_rulebook_coverage" and sample_count is not None and sample_count < int(thresholds["min_sample_count"]):
        blockers.append(
            _blocker(
                component,
                "insufficient_samples",
                f"sample_count {sample_count} < required {int(thresholds['min_sample_count'])}",
            )
        )
        score = min(score, round((sample_count / float(thresholds["min_sample_count"])) * 95.0, 1))

    duration_hours, duration_error = _optional_number(raw.get("duration_hours"), "duration_hours")
    if duration_error is not None:
        blockers.append(_blocker(component, "malformed_evidence", duration_error))
        score = 0.0
    if component == "live_sim_durability":
        if duration_hours is None:
            blockers.append(_blocker(component, "insufficient_duration", "duration_hours is missing"))
        elif duration_hours < float(thresholds["min_live_sim_duration_hours"]):
            blockers.append(
                _blocker(
                    component,
                    "insufficient_duration",
                    f"duration_hours {duration_hours:.1f} < required {float(thresholds['min_live_sim_duration_hours']):.1f}",
                )
            )
            score = min(score, round((duration_hours / float(thresholds["min_live_sim_duration_hours"])) * 95.0, 1))

    if component == "venue_rulebook_coverage":
        age_seconds = int((generated_at - as_of).total_seconds())
        if age_seconds > int(thresholds["max_rulebook_age_seconds"]):
            blockers.append(
                _blocker(
                    component,
                    "stale_rulebook",
                    f"rulebook age {age_seconds}s > allowed {int(thresholds['max_rulebook_age_seconds'])}s",
                )
            )

    if component == "cross_source_parity":
        drift, drift_error = _optional_number(raw.get("max_parity_drift_bps"), "max_parity_drift_bps")
        if drift_error is not None:
            blockers.append(_blocker(component, "malformed_evidence", drift_error))
            score = 0.0
        elif drift is not None and drift > float(thresholds["max_parity_drift_bps"]):
            blockers.append(
                _blocker(
                    component,
                    "parity_drift",
                    f"max_parity_drift_bps {drift:g} > allowed {float(thresholds['max_parity_drift_bps']):.1f}",
                )
            )

    reason_codes = raw.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        blockers.append(_blocker(component, "malformed_evidence", "reason_codes must be a list"))
        score = 0.0
    else:
        for reason in reason_codes:
            if reason in {"race_condition_hold", "derivatives_risk_hold"}:
                blockers.append(_blocker(component, reason, f"component reported {reason}"))
                score = 0.0
            elif not isinstance(reason, str):
                blockers.append(_blocker(component, "malformed_evidence", "reason_codes entries must be strings"))
                score = 0.0

    if any(blocker["severity"] == "reject" for blocker in blockers):
        gate = REJECT_DECISION
        score = 0.0
    elif any(blocker["severity"] == "hold" for blocker in blockers):
        gate = HOLD_DECISION
    elif score < float(thresholds["min_component_score"]):
        gate = REVIEW_DECISION
    else:
        gate = PASS_DECISION

    result: dict[str, Any] = {
        "gate": gate,
        "score": score,
        "as_of": as_of_raw,
        "sample_count": sample_count,
        "blockers": _ordered_blockers(blockers),
    }
    if duration_hours is not None:
        result["duration_hours"] = duration_hours
    return result


def build_promotion_readiness_scorecard(
    evidence: Mapping[str, Any],
    *,
    generated_at: str | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be an object")
    evaluated_at = _generated_at(generated_at)
    evaluated_time = _parse_timestamp(evaluated_at, "generated_at")
    parsed_thresholds = _thresholds(thresholds)
    component_gates = {
        component: _component_gate(
            component,
            evidence.get(component),
            generated_at=evaluated_time,
            thresholds=parsed_thresholds,
        )
        for component in COMPONENT_NAMES
    }
    scores = {component: component_gates[component]["score"] for component in COMPONENT_NAMES}
    scores["promotion_readiness"] = round(sum(scores.values()) / len(COMPONENT_NAMES), 1)
    blockers = _ordered_blockers(
        [blocker for component in COMPONENT_NAMES for blocker in component_gates[component]["blockers"]]
    )
    gates = [component_gates[component]["gate"] for component in COMPONENT_NAMES]
    if any(gate == REJECT_DECISION for gate in gates):
        decision = REJECT_DECISION
    elif any(gate == HOLD_DECISION for gate in gates):
        decision = HOLD_DECISION
    elif any(gate == REVIEW_DECISION for gate in gates) or scores["promotion_readiness"] < float(parsed_thresholds["min_overall_score"]):
        decision = REVIEW_DECISION
    else:
        decision = PASS_DECISION
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": evaluated_at,
        "decision": decision,
        "scores": scores,
        "component_gates": component_gates,
        "blockers": blockers,
        "thresholds": parsed_thresholds,
        "caveats": [
            "Simulated-live evidence only; this scorecard performs no real-money or exchange side effects.",
            "Unknown, missing, malformed, or future-dated component evidence fails closed.",
        ],
    }


def write_promotion_readiness_scorecard(
    output_path: str | Path,
    *,
    evidence: Mapping[str, Any],
    generated_at: str | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_promotion_readiness_scorecard(evidence, generated_at=generated_at, thresholds=thresholds)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "COMPONENT_NAMES",
    "HOLD_DECISION",
    "PASS_DECISION",
    "REJECT_DECISION",
    "REVIEW_DECISION",
    "build_promotion_readiness_scorecard",
    "write_promotion_readiness_scorecard",
]
