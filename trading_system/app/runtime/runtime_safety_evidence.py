from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "runtime_safety_gate_input.v1"
_RUNTIME_SAFETY_REASON_SEVERITIES = {"block", "warn", "info"}
_RUNTIME_SAFETY_REASON_CATEGORIES = {"runtime_safety", "execution_preview"}
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REQUIRED_EVENTS = {
    "kill_switch_dry_run": ("kill_switch_dry_run_met", "kill_switch_dry_run_missing"),
    "order_position_reconciliation": (
        "order_position_reconciliation_met",
        "order_position_reconciliation_missing",
    ),
    "runtime_fail_closed": ("runtime_fail_closed_met", "runtime_fail_closed_missing"),
    "live_dust_before_scale": ("live_dust_before_scale_met", "live_dust_before_scale_missing"),
    "live_trade_ledger": ("live_trade_ledger_met", "live_trade_ledger_missing"),
    "runtime_explainability": ("runtime_explainability_met", "runtime_explainability_missing"),
    "drift_guard": ("drift_guard_met", "drift_guard_missing"),
}
EXECUTION_PREVIEW_UNSUPPORTED_REASON_PREFIXES = (
    ("symbol not allowed for testnet preview", "symbol_not_allowed"),
    ("missing exchange metadata", "missing_exchange_metadata"),
    ("order type incompatible with exchange metadata", "order_type_incompatible"),
    ("entry notional below exchange minimum", "entry_notional_below_minimum"),
    ("entry notional exceeds testnet cap", "entry_notional_exceeds_cap"),
    ("quantity step size or precision incompatible", "quantity_precision_incompatible"),
    ("price tick size or precision incompatible", "price_precision_incompatible"),
    ("fixed futures payload mapping incompatible: entry.type", "entry_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: entry.timeInForce", "entry_time_in_force_incompatible"),
    ("fixed futures payload mapping incompatible: entry.price", "entry_price_missing"),
    ("fixed futures payload mapping incompatible: stop.type", "stop_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: stop.closePosition", "stop_close_position_incompatible"),
    ("fixed futures payload mapping incompatible: stop.workingType", "stop_working_type_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.type", "take_profit_order_type_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.closePosition", "take_profit_close_position_incompatible"),
    ("fixed futures payload mapping incompatible: take_profit.workingType", "take_profit_working_type_incompatible"),
)


def _is_exact_string(value: Any) -> bool:
    return type(value) is str


@dataclass(frozen=True)
class RuntimeSafetyReason:
    code: str
    severity: str
    category: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "category": self.category,
            "source": self.source,
        }

    @classmethod
    def canonicalize_many(cls, raw_reasons: Any) -> list[RuntimeSafetyReason]:
        if raw_reasons is None:
            return []
        if not isinstance(raw_reasons, list):
            raise ValueError("runtime safety reasons must be a list")
        reasons: list[RuntimeSafetyReason] = []
        seen: dict[str, tuple[str, str, str]] = {}
        for raw_reason in raw_reasons:
            code, severity, category, source = _canonical_reason_fields(raw_reason)
            previous = seen.get(code)
            current = (severity, category, source)
            if previous is not None and previous != current:
                raise ValueError(f"runtime safety reason duplicate conflicts for code: {code}")
            seen[code] = current
            reason = cls.from_mapping(raw_reason)
            reasons.append(reason)
        return reasons

    @classmethod
    def from_mapping(cls, raw_reason: Any) -> RuntimeSafetyReason:
        code, severity, category, source = _canonical_reason_fields(raw_reason)
        if code not in RUNTIME_SAFETY_REASON_TAXONOMY:
            raise ValueError(f"unknown runtime safety reason code: {code}")
        expected = RUNTIME_SAFETY_REASON_TAXONOMY[code]
        if severity not in _RUNTIME_SAFETY_REASON_SEVERITIES:
            raise ValueError(f"unknown runtime safety reason severity: {severity}")
        if category not in _RUNTIME_SAFETY_REASON_CATEGORIES:
            raise ValueError(f"unknown runtime safety reason category: {category}")
        if severity != expected["severity"] or category != expected["category"] or source != expected["source"]:
            raise ValueError(f"runtime safety reason taxonomy mismatch for code: {code}")
        return cls(code=code, severity=severity, category=category, source=source)


def _required_event_reason_taxonomy() -> dict[str, dict[str, str]]:
    return {
        reason_code: {"severity": "block", "category": "runtime_safety", "source": "runtime_safety_gate"}
        for _, reason_code in _REQUIRED_EVENTS.values()
    }


def _execution_preview_reason_taxonomy() -> dict[str, dict[str, str]]:
    return {
        reason_code: {"severity": "block", "category": "execution_preview", "source": "execution_preview"}
        for _, reason_code in EXECUTION_PREVIEW_UNSUPPORTED_REASON_PREFIXES
    } | {
        "unsupported_preview_payload": {
            "severity": "block",
            "category": "execution_preview",
            "source": "execution_preview",
        }
    }


RUNTIME_SAFETY_REASON_TAXONOMY = _required_event_reason_taxonomy() | _execution_preview_reason_taxonomy()


def _is_canonical_utc_timestamp(value: str) -> bool:
    if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _is_safe_evidence_identifier(value: str) -> bool:
    return _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is not None


def _require_reason_string(raw_reason: Mapping[str, Any], field: str) -> str:
    if field not in raw_reason:
        raise ValueError(f"runtime safety reason {field} must be present")
    value = raw_reason[field]
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError(f"runtime safety reason {field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"runtime safety reason {field} must be non-empty")
    if value != normalized:
        raise ValueError(f"runtime safety reason {field} must be canonical")
    return normalized


def _canonical_reason_fields(raw_reason: Any) -> tuple[str, str, str, str]:
    if not isinstance(raw_reason, Mapping):
        raise ValueError("runtime safety reason must be a mapping")
    unknown_reason_fields = sorted(set(raw_reason) - {"code", "severity", "category", "source"})
    if unknown_reason_fields:
        raise ValueError("unknown runtime safety reason field: " + ", ".join(unknown_reason_fields))
    return (
        _require_reason_string(raw_reason, "code"),
        _require_reason_string(raw_reason, "severity"),
        _require_reason_string(raw_reason, "category"),
        _require_reason_string(raw_reason, "source"),
    )


def build_runtime_safety_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    unknown_manifest_fields = sorted(set(manifest) - {"evidence_source", "events", "reasons"})
    if unknown_manifest_fields:
        raise ValueError("unknown runtime safety manifest field: " + ", ".join(unknown_manifest_fields))
    raw_source = manifest.get("evidence_source")
    if raw_source is None:
        source: dict[str, Any] = {"type": "unknown_offline_records"}
    elif not isinstance(raw_source, Mapping):
        raise ValueError("evidence_source must be an object")
    else:
        for key in raw_source:
            if not isinstance(key, str):
                raise ValueError("evidence_source.<key> must be a string")
            if not key.strip():
                raise ValueError("evidence_source.<key> must be non-empty")
            if key != key.strip():
                raise ValueError("evidence_source.<key> must be canonical")
        source = dict(raw_source)
    source.setdefault("type", "unknown_offline_records")
    unknown_source_fields = sorted(set(source) - {"type", "run_id", "exported_at"})
    if unknown_source_fields:
        raise ValueError("unknown evidence_source field: " + ", ".join(unknown_source_fields))
    if not _is_exact_string(source.get("type")):
        raise ValueError("evidence_source type must be a string")
    if not source["type"].strip():
        raise ValueError("evidence_source type must be non-empty")
    if source["type"] != source["type"].strip():
        raise ValueError("evidence_source type must be canonical")
    if not _is_safe_evidence_identifier(source["type"]):
        raise ValueError("evidence_source type must be a safe identifier")
    for optional_field in ("run_id", "exported_at"):
        optional_value = source.get(optional_field)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if isinstance(optional_value, str) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")
        if isinstance(optional_value, str) and optional_value != optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be canonical")
        if (
            optional_field == "run_id"
            and isinstance(optional_value, str)
            and not _is_safe_evidence_identifier(optional_value)
        ):
            raise ValueError("evidence_source run_id must be a safe identifier")
        if (
            optional_field == "exported_at"
            and isinstance(optional_value, str)
            and not _is_canonical_utc_timestamp(optional_value)
        ):
            raise ValueError("evidence_source exported_at must be a canonical UTC timestamp")
    events = manifest.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    runtime_reasons = RuntimeSafetyReason.canonicalize_many(manifest.get("reasons", []))

    passed_by_type: dict[str, bool] = {}
    counts_by_type: dict[str, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("runtime safety event must be a mapping")
        unknown_event_fields = sorted(set(event) - {"type", "event_type", "passed"})
        if unknown_event_fields:
            raise ValueError("unknown runtime safety event field: " + ", ".join(unknown_event_fields))
        raw_event_type = event.get("type") if event.get("type") is not None else event.get("event_type")
        if raw_event_type is None:
            raise ValueError("runtime safety event type must be present")
        if not isinstance(raw_event_type, str):
            raise ValueError("runtime safety event type must be a string")
        event_type = raw_event_type.strip()
        if not event_type:
            raise ValueError("runtime safety event type must be non-empty")
        if raw_event_type != event_type:
            raise ValueError("runtime safety event type must be canonical")
        passed = event.get("passed", False)
        if not isinstance(passed, bool):
            raise ValueError("runtime safety event passed must be a boolean")
        counts_by_type[event_type] = counts_by_type.get(event_type, 0) + 1
        passed_by_type[event_type] = passed_by_type.get(event_type, False) or passed is True

    checks: dict[str, bool] = {}
    reasons: list[str] = []
    for event_type, (check_name, reason) in _REQUIRED_EVENTS.items():
        met = passed_by_type.get(event_type, False)
        checks[check_name] = met
        if not met:
            reasons.append(reason)

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": source,
        "checks": checks,
        "summary": {
            "event_count": len(events),
            "counts_by_type": counts_by_type,
            "reason_count": len(runtime_reasons),
            "reasons_by_code": {
                reason.code: sum(1 for item in runtime_reasons if item.code == reason.code)
                for reason in runtime_reasons
            },
        },
        "reasons": reasons,
        "runtime_reasons": [reason.to_dict() for reason in runtime_reasons],
    }


def write_runtime_safety_gate(manifest: Mapping[str, Any], output_dir: str | Path) -> Path:
    output_path = Path(output_dir) / "runtime_safety_gate.json"
    gate = build_runtime_safety_gate(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")
    return output_path


def _load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest JSON must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write runtime safety gate evidence")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    print(write_runtime_safety_gate(_load_manifest(args.manifest), args.output_dir))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
