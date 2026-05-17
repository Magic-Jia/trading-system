from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "promotion_readiness_scorecard_trend.v1"
INPUT_SCHEMA_VERSION = "promotion_readiness_scorecard.v1"
MODE = "simulated_live"
PASS_DECISION = "pass"
HOLD_DECISION = "hold"
REJECT_DECISION = "reject"

DEFAULT_MIN_SAMPLE_COUNT = 2
DEFAULT_MAX_SCORE_DETERIORATION = 5.0
DEFAULT_REPEATED_BLOCKER_MIN_COUNT = 2

REASON_ORDER = (
    "insufficient_sample_window",
    "malformed_scorecard",
    "timestamp_ordering",
    "duplicate_scorecard",
    "score_deterioration",
    "repeated_blocker",
)

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if _parse_timestamp(value) is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return value


def _parse_timestamp(value: Any) -> datetime | None:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_number(value: Any, field: str) -> float:
    parsed = _number(value)
    if parsed is None or parsed < 0.0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in REASON_ORDER if reason in reasons]


def _load_scorecard(value: Mapping[str, Any] | str | Path, malformed_inputs: list[str]) -> Mapping[str, Any]:
    if isinstance(value, (str, Path)):
        path = Path(value)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            malformed_inputs.append("scorecard_file_unreadable")
            return {}
        if not isinstance(loaded, Mapping):
            malformed_inputs.append("scorecard_not_object")
            return {}
        return loaded
    if not isinstance(value, Mapping):
        malformed_inputs.append("scorecard_not_object")
        return {}
    return value


def _scorecard_identity(raw: Mapping[str, Any], generated_at: str | None) -> str | None:
    scorecard_id = raw.get("scorecard_id")
    if isinstance(scorecard_id, str) and scorecard_id:
        return scorecard_id
    return generated_at


def _validate_scorecard(raw: Mapping[str, Any]) -> dict[str, Any]:
    malformed: list[str] = []
    schema_version = raw.get("schema_version")
    if schema_version != INPUT_SCHEMA_VERSION:
        malformed.append("schema_version_invalid")
    mode = raw.get("mode")
    if mode != MODE:
        malformed.append("mode_invalid")

    generated_at_raw = raw.get("generated_at")
    parsed_generated_at = _parse_timestamp(generated_at_raw)
    if parsed_generated_at is None:
        malformed.append("generated_at_invalid")
    generated_at = generated_at_raw if isinstance(generated_at_raw, str) else None

    decision = raw.get("decision")
    if decision not in {PASS_DECISION, "review", HOLD_DECISION, REJECT_DECISION}:
        malformed.append("decision_invalid")

    scores = raw.get("scores")
    if not isinstance(scores, Mapping):
        malformed.append("scores_not_object")
        score = None
    else:
        score = _number(scores.get("promotion_readiness"))
        if score is None:
            malformed.append("scores.promotion_readiness_not_finite_number")

    blockers = raw.get("blockers", [])
    normalized_blockers: list[dict[str, str]] = []
    if not isinstance(blockers, list):
        malformed.append("blockers_not_list")
    else:
        for index, blocker in enumerate(blockers):
            if not isinstance(blocker, Mapping):
                malformed.append(f"blockers[{index}]_not_object")
                continue
            component = blocker.get("component")
            reason_code = blocker.get("reason_code")
            severity = blocker.get("severity")
            if not isinstance(component, str) or not component:
                malformed.append(f"blockers[{index}].component_invalid")
                continue
            if not isinstance(reason_code, str) or not reason_code:
                malformed.append(f"blockers[{index}].reason_code_invalid")
                continue
            if severity not in {HOLD_DECISION, REJECT_DECISION}:
                malformed.append(f"blockers[{index}].severity_invalid")
                continue
            normalized_blockers.append(
                {
                    "component": component,
                    "reason_code": reason_code,
                    "severity": severity,
                }
            )

    return {
        "generated_at": generated_at,
        "parsed_generated_at": parsed_generated_at,
        "identity": _scorecard_identity(raw, generated_at),
        "decision": decision if isinstance(decision, str) else None,
        "score": score,
        "blockers": normalized_blockers,
        "malformed_inputs": malformed,
    }


def _blocker_summary(scorecards: list[dict[str, Any]], repeated_blocker_min_count: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for scorecard in scorecards:
        for blocker in scorecard["blockers"]:
            key = (blocker["component"], blocker["reason_code"])
            grouped.setdefault(key, []).append(scorecard)

    repeated: list[dict[str, Any]] = []
    for (component, reason_code), matches in grouped.items():
        if len(matches) < repeated_blocker_min_count:
            continue
        generated_values = [
            match["generated_at"]
            for match in matches
            if isinstance(match["generated_at"], str)
        ]
        repeated.append(
            {
                "component": component,
                "reason_code": reason_code,
                "count": len(matches),
                "decisions": sorted({match["decision"] for match in matches if isinstance(match["decision"], str)}),
                "first_generated_at": min(generated_values) if generated_values else None,
                "latest_generated_at": max(generated_values) if generated_values else None,
            }
        )
    return sorted(repeated, key=lambda item: (item["component"], item["reason_code"]))


def build_promotion_readiness_scorecard_trend_report(
    *,
    scorecards: Sequence[Mapping[str, Any] | str | Path],
    generated_at: str | None = None,
    min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT,
    max_score_deterioration: Any = DEFAULT_MAX_SCORE_DETERIORATION,
    repeated_blocker_min_count: int = DEFAULT_REPEATED_BLOCKER_MIN_COUNT,
) -> dict[str, Any]:
    observed_required = _positive_int(min_sample_count, "min_sample_count")
    repeated_required = _positive_int(repeated_blocker_min_count, "repeated_blocker_min_count")
    deterioration_threshold = _non_negative_number(max_score_deterioration, "max_score_deterioration")

    load_malformed: list[str] = []
    parsed_scorecards = [_validate_scorecard(_load_scorecard(scorecard, load_malformed)) for scorecard in scorecards]

    reasons: set[str] = set()
    if len(parsed_scorecards) < observed_required:
        reasons.add("insufficient_sample_window")
    if load_malformed or any(scorecard["malformed_inputs"] for scorecard in parsed_scorecards):
        reasons.add("malformed_scorecard")

    generated_times = [scorecard["parsed_generated_at"] for scorecard in parsed_scorecards]
    generated_at_monotonic = True
    for previous, current in zip(generated_times, generated_times[1:]):
        if previous is None or current is None:
            continue
        if current <= previous:
            generated_at_monotonic = False
            reasons.add("timestamp_ordering")
            break

    seen_identities: set[str] = set()
    seen_timestamps: set[str] = set()
    duplicate_identities: set[str] = set()
    for scorecard in parsed_scorecards:
        generated_value = scorecard["generated_at"]
        if isinstance(generated_value, str):
            if generated_value in seen_timestamps:
                duplicate_identities.add(generated_value)
            seen_timestamps.add(generated_value)
        identity = scorecard["identity"]
        if not isinstance(identity, str):
            continue
        if identity in seen_identities:
            duplicate_identities.add(identity)
        seen_identities.add(identity)
    if duplicate_identities:
        reasons.add("duplicate_scorecard")

    numeric_scores = [scorecard["score"] for scorecard in parsed_scorecards if scorecard["score"] is not None]
    first_score = numeric_scores[0] if numeric_scores else None
    latest_score = numeric_scores[-1] if numeric_scores else None
    delta = None if first_score is None or latest_score is None else round(latest_score - first_score, 10)
    deteriorated = delta is not None and -delta > deterioration_threshold
    if deteriorated:
        reasons.add("score_deterioration")

    repeated_blockers = _blocker_summary(parsed_scorecards, repeated_required)
    if repeated_blockers:
        reasons.add("repeated_blocker")

    ordered_reasons = _ordered_reasons(reasons)
    if any(reason in {"malformed_scorecard", "timestamp_ordering", "duplicate_scorecard"} for reason in ordered_reasons):
        decision = REJECT_DECISION
    elif ordered_reasons:
        decision = HOLD_DECISION
    else:
        decision = PASS_DECISION

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "generated_at": _generated_at(generated_at),
        "decision": decision,
        "reasons": ordered_reasons,
        "checks": {
            "sample_window_sufficient": len(parsed_scorecards) >= observed_required,
            "scorecards_well_formed": "malformed_scorecard" not in ordered_reasons,
            "generated_at_monotonic": generated_at_monotonic,
            "scorecard_identities_unique": not duplicate_identities,
            "score_deterioration_within_threshold": not deteriorated,
            "repeated_blockers_absent": not repeated_blockers,
        },
        "sample_window": {
            "observed_count": len(parsed_scorecards),
            "required_count": observed_required,
        },
        "thresholds": {
            "max_score_deterioration": deterioration_threshold,
            "repeated_blocker_min_count": repeated_required,
        },
        "score_trend": {
            "first": first_score,
            "latest": latest_score,
            "delta": delta,
            "deteriorated": deteriorated,
        },
        "duplicate_identities": sorted(duplicate_identities),
        "malformed_inputs": load_malformed,
        "repeated_blockers": repeated_blockers,
        "scorecards": [
            {
                "generated_at": scorecard["generated_at"],
                "identity": scorecard["identity"],
                "decision": scorecard["decision"],
                "score": scorecard["score"],
                **({"malformed_inputs": scorecard["malformed_inputs"]} if scorecard["malformed_inputs"] else {}),
            }
            for scorecard in parsed_scorecards
        ],
        "caveats": [
            "Simulated-live evidence only; this trend report performs no real-money or exchange side effects.",
            "Malformed scorecards, duplicate identities, and non-monotonic generated_at ordering fail closed.",
        ],
    }


def write_promotion_readiness_scorecard_trend_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_promotion_readiness_scorecard_trend_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "HOLD_DECISION",
    "PASS_DECISION",
    "REJECT_DECISION",
    "build_promotion_readiness_scorecard_trend_report",
    "write_promotion_readiness_scorecard_trend_report",
]
