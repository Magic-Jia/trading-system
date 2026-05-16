from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "paper_live_sim_evidence_bundle.v1"
FILENAME = "paper_live_sim_evidence_bundle.json"

REQUIRED_STAGES = (
    "signal",
    "order_intent",
    "risk_check",
    "submit",
    "ack",
    "fill",
    "position_reconcile",
    "paper_snapshot",
    "shadow_snapshot",
)

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_NUMERIC_FIELD_HINTS = (
    "age",
    "amount",
    "balance",
    "bps",
    "equity",
    "exposure",
    "fee",
    "latency",
    "limit",
    "margin",
    "notional",
    "pnl",
    "price",
    "qty",
    "quantity",
    "rate",
    "score",
    "seconds",
    "size",
    "slippage",
    "value",
)


def _is_exact_string(value: Any) -> bool:
    return type(value) is str


def _is_safe_identifier(value: str) -> bool:
    return _SAFE_IDENTIFIER_RE.fullmatch(value) is not None


def _is_canonical_utc_timestamp(value: str) -> bool:
    if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
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


def _require_safe_string(value: Any, field_path: str) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        raise ValueError(f"{field_path} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{field_path} must be canonical")
    if not _is_safe_identifier(value):
        raise ValueError(f"{field_path} must be a safe identifier")
    return value


def _require_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _strict_json_payload(value: Any, field_path: str) -> Any:
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key, child in value.items():
            if not _is_exact_string(key):
                raise ValueError(f"{field_path}.<key> must be a string")
            if not key.strip():
                raise ValueError(f"{field_path}.<key> must be non-empty")
            if key != key.strip():
                raise ValueError(f"{field_path}.<key> must be canonical")
            child_path = f"{field_path}.{key}"
            key_lower = key.lower()
            if any(hint in key_lower for hint in _NUMERIC_FIELD_HINTS):
                payload[key] = _require_number(child, child_path)
            else:
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


def _validate_evidence_source(raw_source: Any) -> dict[str, str]:
    if raw_source is None:
        raise ValueError("evidence_source must be present")
    if not isinstance(raw_source, Mapping):
        raise ValueError("evidence_source must be an object")
    for key in raw_source:
        if not _is_exact_string(key):
            raise ValueError("evidence_source.<key> must be a string")
        if not key.strip():
            raise ValueError("evidence_source.<key> must be non-empty")
        if key != key.strip():
            raise ValueError("evidence_source.<key> must be canonical")
    unknown_fields = sorted(set(raw_source) - {"type", "run_id", "exported_at"})
    if unknown_fields:
        raise ValueError("unknown evidence_source field: " + ", ".join(unknown_fields))
    source_type = _require_safe_string(raw_source.get("type"), "evidence_source type")
    source: dict[str, str] = {"type": source_type}
    for field in ("run_id", "exported_at"):
        value = raw_source.get(field)
        if value is None:
            continue
        if field == "exported_at":
            _parse_canonical_timestamp(value, "evidence_source exported_at")
        else:
            _require_safe_string(value, f"evidence_source {field}")
        source[field] = value
    return source


def _validate_lineage(raw_lineage: Any) -> dict[str, str]:
    if not isinstance(raw_lineage, Mapping):
        raise ValueError("lineage must be an object")
    unknown_fields = sorted(set(raw_lineage) - {"strategy_id", "code_version", "config_hash", "data_snapshot_id"})
    if unknown_fields:
        raise ValueError("unknown lineage field: " + ", ".join(unknown_fields))
    lineage = {
        "strategy_id": _require_safe_string(raw_lineage.get("strategy_id"), "lineage strategy_id"),
        "code_version": _require_safe_string(raw_lineage.get("code_version"), "lineage code_version"),
        "config_hash": _require_safe_string(raw_lineage.get("config_hash"), "lineage config_hash"),
        "data_snapshot_id": _require_safe_string(raw_lineage.get("data_snapshot_id"), "lineage data_snapshot_id"),
    }
    if not _SHA256_HEX_RE.fullmatch(lineage["config_hash"]):
        raise ValueError("lineage config_hash must be a sha256 hex digest")
    return lineage


def _validate_stage(
    raw_stage: Any,
    *,
    index: int,
    expected_stage: str,
    generated_at: datetime,
    max_evidence_age_seconds: float,
    previous_as_of: datetime | None,
    previous_observed_at: datetime | None,
) -> tuple[dict[str, Any], datetime, datetime]:
    if not isinstance(raw_stage, Mapping):
        raise ValueError(f"stages[{index}] must be an object")
    unknown_fields = sorted(set(raw_stage) - {"stage", "event_id", "correlation_id", "as_of", "observed_at", "payload"})
    if unknown_fields:
        raise ValueError(f"unknown stages[{index}] field: " + ", ".join(unknown_fields))
    stage_name = _require_safe_string(raw_stage.get("stage"), f"stages[{index}].stage")
    if stage_name != expected_stage:
        raise ValueError(f"stages[{index}].stage must be {expected_stage}")
    event_id = _require_safe_string(raw_stage.get("event_id"), f"stages[{index}].event_id")
    correlation_id = _require_safe_string(raw_stage.get("correlation_id"), f"stages[{index}].correlation_id")
    as_of = _parse_canonical_timestamp(raw_stage.get("as_of"), f"stages[{index}].as_of")
    observed_at = _parse_canonical_timestamp(raw_stage.get("observed_at"), f"stages[{index}].observed_at")
    if previous_as_of is not None and as_of <= previous_as_of:
        raise ValueError(f"stages[{index}].as_of must be strictly monotonic")
    if previous_observed_at is not None and observed_at <= previous_observed_at:
        raise ValueError(f"stages[{index}].observed_at must be strictly monotonic")
    if as_of > generated_at:
        raise ValueError(f"stages[{index}].as_of must not be after generated_at")
    if observed_at > generated_at:
        raise ValueError(f"stages[{index}].observed_at must not be after generated_at")
    if (generated_at - as_of).total_seconds() > max_evidence_age_seconds:
        raise ValueError(f"stages[{index}].as_of is stale")
    if observed_at < as_of:
        raise ValueError(f"stages[{index}].observed_at must be at or after as_of")
    payload = _strict_json_payload(raw_stage.get("payload"), f"stages[{index}].payload")
    if not isinstance(payload, Mapping):
        raise ValueError(f"stages[{index}].payload must be an object")
    return (
        {
            "stage": stage_name,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "as_of": raw_stage["as_of"],
            "observed_at": raw_stage["observed_at"],
            "payload": dict(payload),
        },
        as_of,
        observed_at,
    )


def build_paper_live_sim_evidence_bundle(manifest: Mapping[str, Any]) -> dict[str, Any]:
    unknown_fields = sorted(
        set(manifest)
        - {
            "schema_version",
            "bundle_id",
            "generated_at",
            "max_evidence_age_seconds",
            "evidence_source",
            "lineage",
            "stages",
            "checks",
            "summary",
            "reasons",
        }
    )
    if unknown_fields:
        raise ValueError("unknown paper-live sim evidence field: " + ", ".join(unknown_fields))
    schema_version = manifest.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise ValueError("schema_version must be paper_live_sim_evidence_bundle.v1")
    bundle_id = _require_safe_string(manifest.get("bundle_id"), "bundle_id")
    generated_at = _parse_canonical_timestamp(manifest.get("generated_at"), "generated_at")
    max_age = _require_number(manifest.get("max_evidence_age_seconds"), "max_evidence_age_seconds")
    if max_age <= 0.0:
        raise ValueError("max_evidence_age_seconds must be positive")
    source = _validate_evidence_source(manifest.get("evidence_source"))
    lineage = _validate_lineage(manifest.get("lineage"))
    raw_stages = manifest.get("stages")
    if not isinstance(raw_stages, list):
        raise ValueError("stages must be a list")
    if len(raw_stages) != len(REQUIRED_STAGES):
        present_stages = {
            stage.get("stage")
            for stage in raw_stages
            if isinstance(stage, Mapping) and _is_exact_string(stage.get("stage"))
        }
        for required_stage in REQUIRED_STAGES:
            if required_stage not in present_stages:
                raise ValueError(f"missing paper-live sim evidence stage: {required_stage}")
        raise ValueError("stages must contain exactly the required paper-live sim evidence stages")
    stages: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    seen_stage_names: set[str] = set()
    correlation_id: str | None = None
    previous_as_of: datetime | None = None
    previous_observed_at: datetime | None = None
    for index, (raw_stage, expected_stage) in enumerate(zip(raw_stages, REQUIRED_STAGES, strict=True)):
        stage, previous_as_of, previous_observed_at = _validate_stage(
            raw_stage,
            index=index,
            expected_stage=expected_stage,
            generated_at=generated_at,
            max_evidence_age_seconds=max_age,
            previous_as_of=previous_as_of,
            previous_observed_at=previous_observed_at,
        )
        if stage["event_id"] in seen_event_ids:
            raise ValueError(f"duplicate paper-live sim evidence event_id: {stage['event_id']}")
        if stage["stage"] in seen_stage_names:
            raise ValueError(f"duplicate paper-live sim evidence stage: {stage['stage']}")
        if correlation_id is None:
            correlation_id = stage["correlation_id"]
        elif stage["correlation_id"] != correlation_id:
            raise ValueError("paper-live sim evidence correlation_id must be consistent")
        seen_event_ids.add(stage["event_id"])
        seen_stage_names.add(stage["stage"])
        stages.append(stage)
    reconcile_payload = stages[REQUIRED_STAGES.index("position_reconcile")]["payload"]
    if reconcile_payload.get("reconciled") is not True:
        raise ValueError("final position reconcile must be reconciled")
    unreconciled_quantity = _require_number(
        reconcile_payload.get("unreconciled_quantity"),
        f"stages[{REQUIRED_STAGES.index('position_reconcile')}].payload.unreconciled_quantity",
    )
    if unreconciled_quantity != 0.0:
        raise ValueError("final position reconcile must have zero unreconciled_quantity")
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "generated_at": manifest["generated_at"],
        "max_evidence_age_seconds": max_age,
        "evidence_source": source,
        "lineage": lineage,
        "stages": stages,
        "checks": {
            "paper_live_sim_evidence_complete": True,
            "paper_live_sim_schema_valid": True,
            "paper_live_sim_freshness_valid": True,
            "paper_live_sim_reconciled": True,
        },
        "summary": {
            "stage_count": len(stages),
            "correlation_id": correlation_id,
            "first_as_of": stages[0]["as_of"],
            "last_as_of": stages[-1]["as_of"],
        },
        "reasons": [],
    }


def validate_paper_live_sim_evidence_bundle(payload: Mapping[str, Any]) -> dict[str, Any]:
    return build_paper_live_sim_evidence_bundle(payload)


def write_paper_live_sim_evidence_bundle(manifest: Mapping[str, Any], output_dir: str | Path) -> Path:
    bundle = build_paper_live_sim_evidence_bundle(manifest)
    output_path = Path(output_dir) / FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output_path
