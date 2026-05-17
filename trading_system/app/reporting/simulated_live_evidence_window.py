from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from trading_system.app.reporting.rolling_simulated_live_evidence_bundle import (
    REQUIRED_COMPONENTS,
)


SCHEMA_VERSION = "simulated_live_evidence_window.v1"
FILENAME = "simulated_live_evidence_window.json"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def _is_exact_string(value: Any) -> bool:
    return type(value) is str


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _load_json_artifact(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError("bundle artifact cannot be read") from exc
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("bundle artifact JSON is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("bundle artifact must be a JSON object")
    return payload, {
        "path": str(path),
        "bytes": len(raw_bytes),
        "sha256": _sha256_bytes(raw_bytes),
    }


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_canonical_timestamp(value: Any, field_path: str) -> datetime:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field_path} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _require_day(value: Any, field_path: str) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if _DAY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path} must be YYYY-MM-DD")
    return value


def _require_safe_identifier(value: Any, field_path: str) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        raise ValueError(f"{field_path} must be non-empty")
    if value != value.strip() or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path} must be a safe identifier")
    return value


def _reason_codes(value: Any, field_path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_path} must be a list")
    reasons: list[str] = []
    for index, reason in enumerate(value):
        if not _is_exact_string(reason):
            raise ValueError(f"{field_path}[{index}] must be a string")
        if reason != reason.strip() or _REASON_CODE_RE.fullmatch(reason) is None:
            raise ValueError(f"{field_path}[{index}] must be canonical")
        reasons.append(reason)
    return reasons


def _bundle_payload(raw_value: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(raw_value, (str, Path)):
        return _load_json_artifact(Path(raw_value))
    if isinstance(raw_value, Mapping):
        payload = dict(raw_value)
        return payload, {"sha256": _sha256_bytes(_canonical_json_bytes(payload))}
    raise ValueError("bundle must be a mapping or local JSON artifact path")


def _normalize_bundle(raw_value: Mapping[str, Any] | str | Path, index: int) -> tuple[dict[str, Any], dict[str, datetime]]:
    payload, source = _bundle_payload(raw_value)
    field = f"bundles[{index}]"
    if payload.get("schema_version") != "rolling_simulated_live_evidence_bundle.v1":
        raise ValueError(f"{field}.schema_version must be rolling_simulated_live_evidence_bundle.v1")

    session_id = _require_safe_identifier(payload.get("session_id"), f"{field}.session_id")
    day = _require_day(payload.get("day"), f"{field}.day")
    observed_at = _parse_canonical_timestamp(payload.get("observed_at"), f"{field}.observed_at")
    evaluated_at = _parse_canonical_timestamp(payload.get("evaluated_at"), f"{field}.evaluated_at")
    generated_at = _parse_canonical_timestamp(payload.get("generated_at"), f"{field}.generated_at")

    decision = payload.get("decision")
    if not _is_exact_string(decision):
        raise ValueError(f"{field}.decision must be a string")
    if decision not in {"pass", "review", "hold"}:
        raise ValueError(f"{field}.decision is unknown")
    reasons = _reason_codes(payload.get("reason_codes"), f"{field}.reason_codes")

    components = payload.get("components")
    if not isinstance(components, list):
        raise ValueError(f"{field}.components must be a list")
    component_names: set[str] = set()
    component_failures: list[dict[str, Any]] = []
    for component_index, raw_component in enumerate(components):
        if not isinstance(raw_component, Mapping):
            raise ValueError(f"{field}.components[{component_index}] must be an object")
        name = _require_safe_identifier(raw_component.get("component"), f"{field}.components[{component_index}].component")
        if name in component_names:
            raise ValueError(f"{field}.components duplicate component: {name}")
        component_names.add(name)
        status = raw_component.get("status")
        if not _is_exact_string(status):
            raise ValueError(f"{field}.components[{component_index}].status must be a string")
        if status in {"hold", "reject"}:
            component_failures.append(
                {
                    "component": name,
                    "status": status,
                    "reason_codes": _reason_codes(
                        raw_component.get("reason_codes"),
                        f"{field}.components[{component_index}].reason_codes",
                    ),
                }
            )
        elif status not in {"pass", "review"}:
            raise ValueError(f"{field}.components[{component_index}].status is unknown")

    missing_components = [component for component in REQUIRED_COMPONENTS if component not in component_names]
    return (
        {
            "session_id": session_id,
            "day": day,
            "observed_at": payload["observed_at"],
            "evaluated_at": payload["evaluated_at"],
            "generated_at": payload["generated_at"],
            "decision": decision,
            "reason_codes": reasons,
            "missing_components": missing_components,
            "component_failures": component_failures,
            "source": source,
        },
        {
            "observed_at": observed_at,
            "evaluated_at": evaluated_at,
            "generated_at": generated_at,
        },
    )


def _add_duplicate_reason(values: list[str], reason: str, reasons: set[str]) -> bool:
    unique = len(set(values)) == len(values)
    if not unique:
        reasons.add(reason)
    return unique


def _is_strictly_increasing(values: list[datetime]) -> bool:
    return all(previous < current for previous, current in zip(values, values[1:]))


def build_simulated_live_evidence_window_report(
    bundles: list[Mapping[str, Any] | str | Path],
    *,
    generated_at: str | None = None,
    min_distinct_sessions: int = 3,
) -> dict[str, Any]:
    if not isinstance(bundles, list):
        raise ValueError("bundles must be a list")
    if isinstance(min_distinct_sessions, bool) or not isinstance(min_distinct_sessions, int):
        raise ValueError("min_distinct_sessions must be an integer")
    if min_distinct_sessions <= 0:
        raise ValueError("min_distinct_sessions must be positive")

    report_generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _parse_canonical_timestamp(report_generated_at, "generated_at")

    normalized: list[dict[str, Any]] = []
    parsed: list[dict[str, datetime]] = []
    reasons: set[str] = set()
    for index, raw_bundle in enumerate(bundles):
        try:
            bundle, timestamps = _normalize_bundle(raw_bundle, index)
        except ValueError as exc:
            bundle = {
                "session_id": f"malformed-{index}",
                "day": "unknown",
                "observed_at": None,
                "evaluated_at": None,
                "generated_at": None,
                "decision": "hold",
                "reason_codes": ["malformed_bundle_timestamp"],
                "missing_components": list(REQUIRED_COMPONENTS),
                "component_failures": [],
                "parse_error": str(exc),
                "source": {},
            }
            timestamps = {}
            reasons.add("malformed_bundle_timestamp")
        normalized.append(bundle)
        parsed.append(timestamps)

    session_ids = [bundle["session_id"] for bundle in normalized]
    days = [bundle["day"] for bundle in normalized]
    observed = [bundle["observed_at"] for bundle in normalized if bundle["observed_at"] is not None]
    evaluated = [bundle["evaluated_at"] for bundle in normalized if bundle["evaluated_at"] is not None]
    distinct_sessions = len(set(session_ids))
    distinct_days = len(set(days) - {"unknown"})
    minimum_met = distinct_sessions >= min_distinct_sessions and distinct_days >= min_distinct_sessions
    if not minimum_met:
        reasons.add("insufficient_distinct_sessions")

    sessions_unique = _add_duplicate_reason(session_ids, "duplicate_session_identity", reasons)
    days_unique = _add_duplicate_reason(days, "duplicate_day_identity", reasons)
    observed_unique = _add_duplicate_reason(observed, "duplicate_observed_at", reasons)
    evaluated_unique = _add_duplicate_reason(evaluated, "duplicate_evaluated_at", reasons)

    generated_values = [timestamps["generated_at"] for timestamps in parsed if "generated_at" in timestamps]
    as_of_values = [timestamps["evaluated_at"] for timestamps in parsed if "evaluated_at" in timestamps]
    generated_monotonic = len(generated_values) == len(normalized) and _is_strictly_increasing(generated_values)
    as_of_monotonic = len(as_of_values) == len(normalized) and _is_strictly_increasing(as_of_values)
    if not generated_monotonic:
        reasons.add("non_monotonic_generated_at")
    if not as_of_monotonic:
        reasons.add("non_monotonic_as_of")

    all_bundles_pass = True
    all_components_present = True
    for bundle in normalized:
        if bundle["decision"] != "pass":
            all_bundles_pass = False
            reasons.add(f"bundle_decision_{bundle['decision']}")
        reasons.update(bundle["reason_codes"])
        if bundle["missing_components"]:
            all_components_present = False
            reasons.add("missing_bundle_component")
        for failure in bundle["component_failures"]:
            reasons.update(failure["reason_codes"])
            if failure["status"] == "hold":
                reasons.add("bundle_component_hold")
            elif failure["status"] == "reject":
                reasons.add("bundle_component_reject")

    decision = "hold" if reasons else "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": report_generated_at,
        "decision": decision,
        "reason_codes": sorted(reasons),
        "checks": {
            "bundle_count": len(normalized),
            "distinct_days": distinct_days,
            "distinct_sessions": distinct_sessions,
            "minimum_distinct_sessions_met": minimum_met,
            "session_identities_unique": sessions_unique,
            "observed_timestamps_unique": observed_unique,
            "evaluated_timestamps_unique": evaluated_unique,
            "generated_at_monotonic": generated_monotonic,
            "as_of_monotonic": as_of_monotonic,
            "all_bundles_pass": all_bundles_pass,
            "all_required_bundle_components_present": all_components_present,
        },
        "bundles": normalized,
    }


def write_simulated_live_evidence_window_report(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_simulated_live_evidence_window_report(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a multi-day simulated-live evidence window from local bundles")
    parser.add_argument("--bundle", action="append", required=True, help="Local rolling bundle JSON path")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    parser.add_argument("--min-distinct-sessions", type=int, default=3)
    args = parser.parse_args()

    payload = write_simulated_live_evidence_window_report(
        args.output,
        bundles=[Path(path) for path in args.bundle],
        generated_at=args.generated_at,
        min_distinct_sessions=args.min_distinct_sessions,
    )
    print(
        "SIMULATED_LIVE_EVIDENCE_WINDOW_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "reason_codes": payload["reason_codes"],
                "bundle_count": payload["checks"]["bundle_count"],
                "distinct_sessions": payload["checks"]["distinct_sessions"],
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "FILENAME",
    "SCHEMA_VERSION",
    "build_simulated_live_evidence_window_report",
    "write_simulated_live_evidence_window_report",
]
