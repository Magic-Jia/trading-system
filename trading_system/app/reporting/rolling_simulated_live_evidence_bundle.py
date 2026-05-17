from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "rolling_simulated_live_evidence_bundle.v1"
FILENAME = "rolling_simulated_live_evidence_bundle.json"
SOURCE_MODE_SIMULATED_LIVE_LOCAL = "simulated_live_local"

REQUIRED_COMPONENTS = (
    "daily_quality_gate",
    "rolling_tca_durability",
    "l2_longitudinal_replay_calibration",
    "cross_source_parity",
    "venue_rulebook_catalog_freshness",
    "execution_race_evidence",
)
OPTIONAL_COMPONENTS = ("derivatives_risk",)
COMPONENT_ORDER = (*REQUIRED_COMPONENTS, *OPTIONAL_COMPONENTS)
KNOWN_STATUSES = {"pass", "review", "hold"}
PASS_DECISIONS = {"pass", "passed", "accepted", "durable", "pass_for_continued_paper"}
REVIEW_DECISIONS = {"review", "accepted_with_review", "hold_for_review", "insufficient"}
HOLD_DECISIONS = {"hold", "reject", "rejected", "reject_live_promotion", "failed"}

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_NUMERIC_FIELD_HINTS = (
    "age",
    "amount",
    "balance",
    "bps",
    "count",
    "equity",
    "fee",
    "latency",
    "limit",
    "ms",
    "notional",
    "p95",
    "p99",
    "price",
    "qty",
    "quantity",
    "ratio",
    "rate",
    "sample",
    "seconds",
    "size",
    "slippage",
    "value",
)


def _is_exact_string(value: Any) -> bool:
    return type(value) is str


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


def _require_safe_identifier(value: Any, field_path: str) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        raise ValueError(f"{field_path} must be non-empty")
    if value != value.strip() or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path} must be a safe identifier")
    return value


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _load_json_artifact(path: Path, component: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{component} artifact cannot be read") from exc
    try:
        payload = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{component} artifact JSON is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{component} artifact must be a JSON object")
    return payload, {
        "path": str(path),
        "bytes": len(raw_bytes),
        "sha256": _sha256_bytes(raw_bytes),
    }


def _strict_json_payload(value: Any, field_path: str) -> Any:
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key, child in value.items():
            if not _is_exact_string(key):
                raise ValueError(f"{field_path}.<key> must be a string")
            if not key.strip():
                raise ValueError(f"{field_path}.<key> must be non-empty")
            if key != key.strip():
                raise ValueError(f"{field_path}.{key} must be canonical")
            child_path = f"{field_path}.{key}"
            key_lower = key.lower()
            if key_lower.endswith("_at") or key_lower.endswith("_time") or key_lower.endswith("_timestamp"):
                if not _is_exact_string(child):
                    raise ValueError(f"{child_path} must be a string")
            elif any(hint in key_lower for hint in _NUMERIC_FIELD_HINTS):
                _require_number(child, child_path)
            payload[key] = _strict_json_payload(child, child_path)
        return payload
    if isinstance(value, list):
        return [_strict_json_payload(child, f"{field_path}[{index}]") for index, child in enumerate(value)]
    if isinstance(value, bool) or value is None or _is_exact_string(value):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field_path} must be finite")
        return number
    raise ValueError(f"{field_path} must be JSON-serializable")


def _require_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _reason_codes(payload: Mapping[str, Any], component: str) -> list[str]:
    raw_reasons = payload.get("reason_codes", payload.get("reasons", []))
    if raw_reasons is None:
        return []
    if not isinstance(raw_reasons, list):
        raise ValueError(f"{component}.reason_codes must be a list")
    reasons: list[str] = []
    for index, raw_reason in enumerate(raw_reasons):
        if not _is_exact_string(raw_reason):
            raise ValueError(f"{component}.reason_codes[{index}] must be a string")
        if raw_reason != raw_reason.strip() or _REASON_CODE_RE.fullmatch(raw_reason) is None:
            raise ValueError(f"{component}.reason_codes[{index}] must be canonical")
        reasons.append(raw_reason)
    return reasons


def _component_status(payload: Mapping[str, Any], component: str) -> str:
    raw_status = payload.get("status")
    if raw_status is not None:
        if not _is_exact_string(raw_status):
            raise ValueError(f"{component} status must be a string")
        if raw_status not in KNOWN_STATUSES:
            raise ValueError(f"{component} status is unknown")
        return raw_status
    raw_decision = payload.get("decision")
    if not _is_exact_string(raw_decision):
        raise ValueError(f"{component} decision must be a string")
    if raw_decision in PASS_DECISIONS:
        return "pass"
    if raw_decision in REVIEW_DECISIONS:
        return "review"
    if raw_decision in HOLD_DECISIONS:
        return "hold"
    raise ValueError(f"{component} decision is unknown")


def _component_identity(payload: Mapping[str, Any], component: str, source: Mapping[str, Any]) -> str:
    for field in ("artifact_id", "bundle_id", "report_id", "run_id"):
        value = payload.get(field)
        if value is not None:
            return _require_safe_identifier(value, f"{component}.{field}")
    schema_version = payload.get("schema_version")
    if not _is_exact_string(schema_version):
        raise ValueError(f"{component}.schema_version must be a string")
    return f"{component}:{schema_version}:{source['sha256']}"


def _normalize_component(
    component: str,
    raw_value: Mapping[str, Any] | str | Path,
    *,
    generated_at: datetime,
    max_artifact_age_seconds: float,
) -> dict[str, Any]:
    if isinstance(raw_value, (str, Path)):
        payload, source = _load_json_artifact(Path(raw_value), component)
    elif isinstance(raw_value, Mapping):
        payload = dict(raw_value)
        source = {"sha256": _sha256_bytes(_canonical_json_bytes(payload))}
    else:
        raise ValueError(f"{component} must be a mapping or local JSON artifact path")

    _strict_json_payload(payload, component)
    schema_version = payload.get("schema_version")
    if not _is_exact_string(schema_version) or not schema_version.strip() or schema_version != schema_version.strip():
        raise ValueError(f"{component}.schema_version must be canonical")
    component_generated_at = _parse_canonical_timestamp(payload.get("generated_at"), f"{component}.generated_at")
    if component_generated_at > generated_at:
        raise ValueError(f"{component} artifact generated_at must not be after bundle generated_at")
    if (generated_at - component_generated_at).total_seconds() > max_artifact_age_seconds:
        raise ValueError(f"{component} artifact is stale")

    status = _component_status(payload, component)
    reasons = _reason_codes(payload, component)
    identity = _component_identity(payload, component, source)
    return {
        "component": component,
        "status": status,
        "generated_at": payload["generated_at"],
        "reason_codes": reasons,
        "source": {
            **source,
            "identity": identity,
            "schema_version": schema_version,
        },
    }


def build_rolling_simulated_live_evidence_bundle(
    *,
    components: Mapping[str, Mapping[str, Any] | str | Path],
    generated_at: str | None = None,
    max_artifact_age_seconds: int | float = 86_400,
    source_mode: str = SOURCE_MODE_SIMULATED_LIVE_LOCAL,
) -> dict[str, Any]:
    if not isinstance(components, Mapping):
        raise ValueError("components must be an object")
    unknown_components = sorted(set(components) - set(COMPONENT_ORDER))
    if unknown_components:
        raise ValueError("unknown components: " + ", ".join(unknown_components))
    missing_components = [component for component in REQUIRED_COMPONENTS if component not in components]
    if missing_components:
        raise ValueError("missing required components: " + ", ".join(missing_components))

    bundle_generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    parsed_generated_at = _parse_canonical_timestamp(bundle_generated_at, "generated_at")
    max_age = _require_number(max_artifact_age_seconds, "max_artifact_age_seconds")
    if max_age <= 0.0:
        raise ValueError("max_artifact_age_seconds must be positive")
    if source_mode != SOURCE_MODE_SIMULATED_LIVE_LOCAL:
        raise ValueError("source_mode must be simulated_live_local")

    normalized = [
        _normalize_component(
            component,
            components[component],
            generated_at=parsed_generated_at,
            max_artifact_age_seconds=max_age,
        )
        for component in COMPONENT_ORDER
        if component in components
    ]

    seen_identities: set[str] = set()
    for component in normalized:
        identity = component["source"]["identity"]
        if identity in seen_identities:
            raise ValueError(f"duplicate component identity: {identity}")
        seen_identities.add(identity)

    statuses = [component["status"] for component in normalized]
    if "hold" in statuses:
        decision = "hold"
    elif "review" in statuses:
        decision = "review"
    else:
        decision = "pass"

    reason_codes = sorted({reason for component in normalized for reason in component["reason_codes"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "source_mode": source_mode,
        "generated_at": bundle_generated_at,
        "decision": decision,
        "reason_codes": reason_codes,
        "checks": {
            "all_required_components_present": True,
            "all_components_well_formed": True,
            "all_components_fresh": True,
            "component_identities_unique": True,
            "all_component_statuses_known": True,
        },
        "components": normalized,
    }


def write_rolling_simulated_live_evidence_bundle(output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    payload = build_rolling_simulated_live_evidence_bundle(**kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _components_from_cli(values: list[str]) -> dict[str, Path]:
    components: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError("--component must use component_name=/local/path.json")
        if name in components:
            raise ValueError(f"duplicate component argument: {name}")
        components[name] = Path(path)
    return components


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a rolling simulated-live evidence bundle from local JSON artifacts")
    parser.add_argument("--component", action="append", required=True, help="component_name=/local/path.json")
    parser.add_argument("--output", required=True, help="Output JSON bundle path")
    parser.add_argument("--generated-at", default=None, help="Canonical UTC generation timestamp")
    parser.add_argument("--max-artifact-age-seconds", type=float, default=86_400)
    args = parser.parse_args()

    payload = write_rolling_simulated_live_evidence_bundle(
        args.output,
        components=_components_from_cli(args.component),
        generated_at=args.generated_at,
        max_artifact_age_seconds=args.max_artifact_age_seconds,
    )
    print(
        "ROLLING_SIMULATED_LIVE_EVIDENCE_BUNDLE_JSON",
        json.dumps(
            {
                "output": args.output,
                "decision": payload["decision"],
                "reason_codes": payload["reason_codes"],
                "component_count": len(payload["components"]),
            },
            sort_keys=True,
        ),
    )


__all__ = [
    "COMPONENT_ORDER",
    "FILENAME",
    "OPTIONAL_COMPONENTS",
    "REQUIRED_COMPONENTS",
    "SCHEMA_VERSION",
    "SOURCE_MODE_SIMULATED_LIVE_LOCAL",
    "build_rolling_simulated_live_evidence_bundle",
    "write_rolling_simulated_live_evidence_bundle",
]
