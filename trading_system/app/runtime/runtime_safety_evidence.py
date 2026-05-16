from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "runtime_safety_gate_input.v1"
INCIDENT_BUNDLE_SCHEMA_VERSION = "runtime_incident_replay_bundle.v1"
_RUNTIME_SAFETY_REASON_SEVERITIES = {"block", "warn", "info"}
_RUNTIME_SAFETY_REASON_CATEGORIES = {"runtime_safety", "execution_preview"}
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_INCIDENT_CRITICAL_PATH = [
    "signal",
    "order_intent",
    "risk_check",
    "kill_switch_decision",
    "submit",
    "ack",
    "fill",
    "cancel",
    "reconcile",
]
_INCIDENT_REQUIRED_SECTIONS = {
    "schema_version",
    "incident_id",
    "runtime_config_hash",
    "generated_at",
    "replay_window",
    "clocks",
    "evidence_refs",
    "remediation",
    "events",
}
_INCIDENT_EVENT_NUMERIC_FIELDS = {
    "score",
    "quantity",
    "max_notional",
    "filled_quantity",
    "price",
}
_INCIDENT_EVENT_BOOL_FIELDS = {"passed", "fail_closed"}
_CURRENT_APPROVAL_DATE = date(2026, 5, 16)
_ENVIRONMENT_PERMISSION_CHECKS = {
    "environment_identity_present": "environment_identity_missing",
    "permission_scope_isolated": "permission_scope_not_isolated",
    "order_routing_current_approval_met": "order_routing_current_approval_missing",
}
_ALLOWED_ENVIRONMENTS = {"research", "paper", "testnet", "prod"}
_ALLOWED_EXECUTION_MODES = {"paper", "dry-run", "testnet", "live"}
_ALLOWED_ENDPOINT_CLASSES = {"none", "testnet", "live"}
_ALLOWED_KEY_SCOPES = {"none", "testnet", "live"}
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
_HARD_KILL_SWITCH_EVIDENCE = {
    "market_data": ("market_data_fresh_met", "market_data_stale"),
    "account_snapshot": ("account_snapshot_fresh_met", "account_snapshot_stale"),
    "clock_skew": ("clock_skew_within_limit_met", "clock_skew_exceeded"),
    "max_daily_loss": ("max_daily_loss_within_limit_met", "max_daily_loss_exceeded"),
    "max_order_count": ("max_order_count_within_limit_met", "max_order_count_exceeded"),
    "max_notional": ("max_notional_within_limit_met", "max_notional_exceeded"),
    "exchange_account_state": (
        "exchange_account_state_unambiguous_met",
        "exchange_account_state_ambiguous",
    ),
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
    required_event_reasons = {
        reason_code: {"severity": "block", "category": "runtime_safety", "source": "runtime_safety_gate"}
        for _, reason_code in _REQUIRED_EVENTS.values()
    }
    kill_switch_reasons = {
        reason_code: {"severity": "block", "category": "runtime_safety", "source": "runtime_safety_gate"}
        for _, reason_code in _HARD_KILL_SWITCH_EVIDENCE.values()
    }
    return required_event_reasons | kill_switch_reasons


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


def _parse_canonical_timestamp(value: Any, field_path: str) -> datetime:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field_path} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _require_env_permission_string(raw: Mapping[str, Any], field: str) -> str:
    if field not in raw:
        raise ValueError(f"environment_permission_evidence {field} must be present")
    value = raw[field]
    if not _is_exact_string(value):
        raise ValueError(f"environment_permission_evidence {field} must be a string")
    if not value.strip():
        raise ValueError(f"environment_permission_evidence {field} must be non-empty")
    if value != value.strip():
        raise ValueError(f"environment_permission_evidence {field} must be canonical")
    return value


def _require_env_permission_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _canonical_approval(raw_approval: Any, *, order_routing_enabled: bool) -> dict[str, Any] | None:
    if raw_approval is None:
        if order_routing_enabled:
            raise ValueError("order routing requires explicit current approval evidence")
        return None
    if not isinstance(raw_approval, Mapping):
        raise ValueError("environment_permission_evidence approval must be an object")
    unknown_fields = sorted(set(raw_approval) - {"approval_id", "approved_at", "expires_at"})
    if unknown_fields:
        raise ValueError("unknown environment_permission_evidence approval field: " + ", ".join(unknown_fields))
    approval_id = raw_approval.get("approval_id")
    if not _is_exact_string(approval_id):
        raise ValueError("approval approval_id must be a string")
    if not approval_id.strip():
        raise ValueError("approval approval_id must be non-empty")
    if approval_id != approval_id.strip() or not _is_safe_evidence_identifier(approval_id):
        raise ValueError("approval approval_id must be a safe identifier")
    approved_at = _parse_canonical_timestamp(raw_approval.get("approved_at"), "approval approved_at")
    expires_at = _parse_canonical_timestamp(raw_approval.get("expires_at"), "approval expires_at")
    if approved_at.date() > _CURRENT_APPROVAL_DATE:
        raise ValueError("approval approved_at must not be in the future")
    if approved_at.date() != _CURRENT_APPROVAL_DATE:
        raise ValueError("approval approved_at must be current")
    if expires_at.date() < _CURRENT_APPROVAL_DATE:
        raise ValueError("approval expires_at must not be stale")
    return {
        "approval_id": approval_id,
        "approved_at": raw_approval["approved_at"],
        "expires_at": raw_approval["expires_at"],
    }


def _canonical_environment_permission_evidence(raw_evidence: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    if raw_evidence is None:
        raise ValueError("environment_permission_evidence must be present")
    if not isinstance(raw_evidence, Mapping):
        raise ValueError("environment_permission_evidence must be an object")
    unknown_fields = sorted(
        set(raw_evidence)
        - {
            "environment",
            "execution_mode",
            "endpoint_class",
            "key_scope",
            "order_routing_enabled",
            "production_gate",
            "approval",
            "max_order_notional_usdt",
            "max_open_positions",
        }
    )
    if unknown_fields:
        raise ValueError("unknown environment_permission_evidence field: " + ", ".join(unknown_fields))
    environment = _require_env_permission_string(raw_evidence, "environment")
    execution_mode = _require_env_permission_string(raw_evidence, "execution_mode")
    endpoint_class = _require_env_permission_string(raw_evidence, "endpoint_class")
    key_scope = _require_env_permission_string(raw_evidence, "key_scope")
    production_gate = _require_env_permission_string(raw_evidence, "production_gate")
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("environment_permission_evidence environment is invalid")
    if execution_mode not in _ALLOWED_EXECUTION_MODES:
        raise ValueError("environment_permission_evidence execution_mode is invalid")
    if endpoint_class not in _ALLOWED_ENDPOINT_CLASSES:
        raise ValueError("environment_permission_evidence endpoint_class is invalid")
    if key_scope not in _ALLOWED_KEY_SCOPES:
        raise ValueError("environment_permission_evidence key_scope is invalid")
    order_routing_enabled = raw_evidence.get("order_routing_enabled")
    if not isinstance(order_routing_enabled, bool):
        raise ValueError("environment_permission_evidence order_routing_enabled must be a boolean")
    if environment in {"research", "paper"} and (
        endpoint_class == "live" or key_scope == "live" or execution_mode == "live"
    ):
        raise ValueError("prod-like permissions are not allowed in research or paper environments")
    if (endpoint_class == "live" and key_scope == "testnet") or (
        endpoint_class == "testnet" and key_scope == "live"
    ):
        raise ValueError("live endpoint and key permissions must not be mixed")
    if environment == "prod" and production_gate != "production-approved":
        raise ValueError("production environment requires canonical production gate evidence")
    if environment != "prod" and production_gate != "not-production":
        raise ValueError("non-production environment requires not-production gate evidence")
    approval = _canonical_approval(raw_evidence.get("approval"), order_routing_enabled=order_routing_enabled)
    canonical: dict[str, Any] = {
        "environment": environment,
        "execution_mode": execution_mode,
        "endpoint_class": endpoint_class,
        "key_scope": key_scope,
        "order_routing_enabled": order_routing_enabled,
        "production_gate": production_gate,
        "approval": approval,
    }
    for field in ("max_order_notional_usdt", "max_open_positions"):
        if field in raw_evidence:
            number = _require_env_permission_number(
                raw_evidence[field],
                f"environment_permission_evidence {field}",
            )
            if number <= 0:
                raise ValueError(f"environment_permission_evidence {field} must be positive")
            canonical[field] = raw_evidence[field]
    return (
        canonical,
        {
            "environment_identity_present": True,
            "permission_scope_isolated": True,
            "order_routing_current_approval_met": (not order_routing_enabled) or approval is not None,
        },
    )


def _require_kill_switch_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _canonical_kill_switch_decision(raw_decision: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    if raw_decision is None:
        raise ValueError("kill_switch_decision must be present")
    if not isinstance(raw_decision, Mapping):
        raise ValueError("kill_switch_decision must be an object")
    unknown_fields = sorted(set(raw_decision) - {"evaluated_at", "decision", "max_evidence_age_seconds", "evidence"})
    if unknown_fields:
        raise ValueError("unknown kill_switch_decision field: " + ", ".join(unknown_fields))

    evaluated_at = _parse_canonical_timestamp(
        raw_decision.get("evaluated_at"), "kill_switch_decision evaluated_at"
    )
    raw_state = raw_decision.get("decision")
    if not _is_exact_string(raw_state):
        raise ValueError("kill_switch_decision decision must be a string")
    if raw_state not in {"allow", "kill"}:
        raise ValueError("kill_switch_decision decision must be allow or kill")
    max_age = _require_kill_switch_number(
        raw_decision.get("max_evidence_age_seconds"),
        "kill_switch_decision max_evidence_age_seconds",
    )
    if max_age < 0:
        raise ValueError("kill_switch_decision max_evidence_age_seconds must be non-negative")

    evidence = raw_decision.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("kill_switch_decision evidence must be an object")
    unknown_evidence = sorted(set(evidence) - set(_HARD_KILL_SWITCH_EVIDENCE))
    if unknown_evidence:
        raise ValueError("unknown kill_switch_decision evidence field: " + ", ".join(unknown_evidence))

    canonical_evidence: dict[str, dict[str, Any]] = {}
    checks: dict[str, bool] = {}
    for evidence_name, (check_name, _reason) in _HARD_KILL_SWITCH_EVIDENCE.items():
        raw_item = evidence.get(evidence_name)
        if raw_item is None:
            raise ValueError(f"kill_switch_decision evidence {evidence_name} must be present")
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"kill_switch_decision evidence {evidence_name} must be an object")
        unknown_item_fields = sorted(set(raw_item) - {"ok", "observed_at", "age_seconds", "skew_seconds", "value", "limit"})
        if unknown_item_fields:
            raise ValueError(
                f"unknown kill_switch_decision evidence {evidence_name} field: "
                + ", ".join(unknown_item_fields)
            )
        ok = raw_item.get("ok")
        if not isinstance(ok, bool):
            raise ValueError(f"kill_switch_decision evidence {evidence_name} ok must be a boolean")
        observed_at = _parse_canonical_timestamp(
            raw_item.get("observed_at"),
            f"kill_switch_decision evidence {evidence_name} observed_at",
        )
        if observed_at > evaluated_at:
            raise ValueError(
                f"kill_switch_decision evidence {evidence_name} observed_at must not be in the future"
            )

        canonical_item: dict[str, Any] = {"ok": ok, "observed_at": raw_item["observed_at"]}
        if "age_seconds" in raw_item:
            age = _require_kill_switch_number(
                raw_item["age_seconds"],
                f"kill_switch_decision evidence {evidence_name} age_seconds",
            )
            if age < 0:
                raise ValueError(f"kill_switch_decision evidence {evidence_name} age_seconds must be non-negative")
            if age > max_age:
                raise ValueError(f"kill_switch_decision evidence {evidence_name} is stale")
            canonical_item["age_seconds"] = raw_item["age_seconds"]
        if "skew_seconds" in raw_item:
            _require_kill_switch_number(
                raw_item["skew_seconds"],
                f"kill_switch_decision evidence {evidence_name} skew_seconds",
            )
            canonical_item["skew_seconds"] = raw_item["skew_seconds"]
        for field in ("value", "limit"):
            if field in raw_item:
                _require_kill_switch_number(
                    raw_item[field],
                    f"kill_switch_decision evidence {evidence_name} {field}",
                )
                canonical_item[field] = raw_item[field]
        canonical_evidence[evidence_name] = canonical_item
        checks[check_name] = ok

    return (
        {
            "evaluated_at": raw_decision["evaluated_at"],
            "decision": raw_state,
            "max_evidence_age_seconds": raw_decision["max_evidence_age_seconds"],
            "evidence": canonical_evidence,
        },
        checks,
    )


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


def _require_incident_mapping(value: Any, field_path: str) -> Mapping[str, Any]:
    if value is None:
        raise ValueError(f"{field_path} must be present")
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_path} must be an object")
    return value


def _require_incident_string(value: Any, field_path: str) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{field_path} must be a string")
    if not value.strip():
        raise ValueError(f"{field_path} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{field_path} must be canonical")
    return value


def _require_incident_number(value: Any, field_path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_path} must be numeric, not boolean")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_path} must be finite")
    return number


def _require_incident_bool(value: Any, field_path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_path} must be a boolean")
    return value


def _canonical_incident_reference_list(value: Any, field_path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_path} must be a non-empty list")
    return [_require_incident_string(item, f"{field_path}[{index}]") for index, item in enumerate(value)]


def _canonicalize_incident_event_payload(raw_payload: Any, field_path: str) -> dict[str, Any]:
    payload = _require_incident_mapping(raw_payload, field_path)
    canonical: dict[str, Any] = {}
    for key, value in payload.items():
        key_path = f"{field_path}.{key}"
        if not _is_exact_string(key):
            raise ValueError(f"{field_path}.<key> must be a string")
        if key != key.strip() or not key:
            raise ValueError(f"{field_path}.<key> must be canonical")
        if key in _INCIDENT_EVENT_BOOL_FIELDS:
            canonical[key] = _require_incident_bool(value, key_path)
        elif key in _INCIDENT_EVENT_NUMERIC_FIELDS:
            _require_incident_number(value, key_path)
            canonical[key] = value
        elif isinstance(value, bool) or value is None:
            canonical[key] = value
        elif isinstance(value, (int, float)):
            _require_incident_number(value, key_path)
            canonical[key] = value
        elif _is_exact_string(value):
            canonical[key] = _require_incident_string(value, key_path)
        else:
            raise ValueError(f"{key_path} must be a scalar")
    return canonical


def _canonicalize_incident_events(
    raw_events: Any,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    if raw_events is None:
        raise ValueError("events must be present")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("events must be a non-empty list")

    canonical_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    critical_cursor = 0
    fail_closed = False
    previous_occurred_at: datetime | None = None

    for index, raw_event in enumerate(raw_events):
        field_path = f"events[{index}]"
        event = _require_incident_mapping(raw_event, field_path)
        unknown_fields = sorted(set(event) - {"event_id", "event_type", "occurred_at", "payload"})
        if unknown_fields:
            raise ValueError(f"unknown {field_path} field: " + ", ".join(unknown_fields))
        event_id = _require_incident_string(event.get("event_id"), f"{field_path}.event_id")
        if event_id in seen_ids:
            raise ValueError(f"duplicate incident event_id: {event_id}")
        seen_ids.add(event_id)

        event_type = _require_incident_string(event.get("event_type"), f"{field_path}.event_type")
        occurred_at = _parse_canonical_timestamp(event.get("occurred_at"), f"{field_path}.occurred_at")
        if occurred_at < window_start or occurred_at > window_end:
            raise ValueError(f"{field_path}.occurred_at must be inside replay_window")
        if previous_occurred_at is not None and occurred_at < previous_occurred_at:
            raise ValueError("incident events cannot replay critical path ordering")
        previous_occurred_at = occurred_at

        if critical_cursor >= len(_INCIDENT_CRITICAL_PATH) or event_type != _INCIDENT_CRITICAL_PATH[critical_cursor]:
            raise ValueError("incident events cannot replay critical path ordering")
        critical_cursor += 1

        payload = _canonicalize_incident_event_payload(event.get("payload"), f"{field_path}.payload")
        unknown_state = payload.get("decision") == "unknown" or payload.get("state") == "unknown"
        event_fail_closed = payload.get("fail_closed") is True
        if unknown_state and not event_fail_closed:
            raise ValueError("unknown incident state must have fail_closed outcome")
        fail_closed = fail_closed or event_fail_closed

        canonical_events.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": event["occurred_at"],
                "payload": payload,
            }
        )

    if critical_cursor != len(_INCIDENT_CRITICAL_PATH):
        raise ValueError("incident events cannot replay critical path ordering")
    return canonical_events, fail_closed


def validate_runtime_incident_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, Mapping):
        raise ValueError("runtime incident bundle must be an object")
    missing_sections = [section for section in sorted(_INCIDENT_REQUIRED_SECTIONS) if section not in bundle]
    if missing_sections:
        raise ValueError(f"{missing_sections[0]} must be present")
    unknown_sections = sorted(set(bundle) - _INCIDENT_REQUIRED_SECTIONS)
    if unknown_sections:
        raise ValueError("unknown runtime incident bundle field: " + ", ".join(unknown_sections))

    schema_version = _require_incident_string(bundle.get("schema_version"), "schema_version")
    if schema_version != INCIDENT_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {INCIDENT_BUNDLE_SCHEMA_VERSION}")
    incident_id = _require_incident_string(bundle.get("incident_id"), "incident_id")
    runtime_config_hash = _require_incident_string(bundle.get("runtime_config_hash"), "runtime_config_hash")
    generated_at = _parse_canonical_timestamp(bundle.get("generated_at"), "generated_at")

    replay_window = _require_incident_mapping(bundle.get("replay_window"), "replay_window")
    unknown_window_fields = sorted(
        set(replay_window) - {"started_at", "ended_at", "max_clock_skew_seconds", "max_event_age_seconds"}
    )
    if unknown_window_fields:
        raise ValueError("unknown replay_window field: " + ", ".join(unknown_window_fields))
    window_start = _parse_canonical_timestamp(replay_window.get("started_at"), "replay_window started_at")
    window_end = _parse_canonical_timestamp(replay_window.get("ended_at"), "replay_window ended_at")
    if window_end < window_start:
        raise ValueError("replay_window ended_at must not be before started_at")
    max_clock_skew_seconds = _require_incident_number(
        replay_window.get("max_clock_skew_seconds"),
        "replay_window max_clock_skew_seconds",
    )
    max_event_age_seconds = _require_incident_number(
        replay_window.get("max_event_age_seconds"),
        "replay_window max_event_age_seconds",
    )
    if max_clock_skew_seconds < 0 or max_event_age_seconds < 0:
        raise ValueError("replay_window limits must be non-negative")
    if (generated_at - window_end).total_seconds() > max_event_age_seconds:
        raise ValueError("replay_window ended_at is stale")
    if generated_at < window_end:
        raise ValueError("generated_at must not precede replay_window ended_at")

    clocks = _require_incident_mapping(bundle.get("clocks"), "clocks")
    unknown_clock_fields = sorted(
        set(clocks) - {"runtime_observed_at", "exchange_observed_at", "monotonic_started_ns", "monotonic_ended_ns"}
    )
    if unknown_clock_fields:
        raise ValueError("unknown clocks field: " + ", ".join(unknown_clock_fields))
    runtime_observed_at = _parse_canonical_timestamp(clocks.get("runtime_observed_at"), "clocks runtime_observed_at")
    exchange_observed_at = _parse_canonical_timestamp(
        clocks.get("exchange_observed_at"),
        "clocks exchange_observed_at",
    )
    if abs((exchange_observed_at - runtime_observed_at).total_seconds()) > max_clock_skew_seconds:
        raise ValueError("clocks exchange_observed_at exceeds max_clock_skew_seconds")
    monotonic_started_ns = _require_incident_number(clocks.get("monotonic_started_ns"), "clocks monotonic_started_ns")
    monotonic_ended_ns = _require_incident_number(clocks.get("monotonic_ended_ns"), "clocks monotonic_ended_ns")
    if monotonic_ended_ns < monotonic_started_ns:
        raise ValueError("clocks monotonic_ended_ns must not be before monotonic_started_ns")

    refs = _require_incident_mapping(bundle.get("evidence_refs"), "evidence_refs")
    unknown_ref_fields = sorted(set(refs) - {"logs", "metrics", "traces"})
    if unknown_ref_fields:
        raise ValueError("unknown evidence_refs field: " + ", ".join(unknown_ref_fields))
    evidence_refs = {
        "logs": _canonical_incident_reference_list(refs.get("logs"), "evidence_refs.logs"),
        "metrics": _canonical_incident_reference_list(refs.get("metrics"), "evidence_refs.metrics"),
        "traces": _canonical_incident_reference_list(refs.get("traces"), "evidence_refs.traces"),
    }

    remediation = _require_incident_mapping(bundle.get("remediation"), "remediation")
    unknown_remediation_fields = sorted(set(remediation) - {"status", "owner", "updated_at", "fail_closed"})
    if unknown_remediation_fields:
        raise ValueError("unknown remediation field: " + ", ".join(unknown_remediation_fields))
    remediation_status = _require_incident_string(remediation.get("status"), "remediation status")
    if remediation_status not in {"open", "in_progress", "complete", "failed_closed"}:
        raise ValueError("remediation status must be open, in_progress, complete, or failed_closed")
    remediation_owner = _require_incident_string(remediation.get("owner"), "remediation owner")
    remediation_updated_at = _parse_canonical_timestamp(remediation.get("updated_at"), "remediation updated_at")
    if remediation_updated_at < window_start or remediation_updated_at > generated_at:
        raise ValueError("remediation updated_at must be between replay_window started_at and generated_at")
    remediation_fail_closed = _require_incident_bool(remediation.get("fail_closed"), "remediation fail_closed")

    events, events_fail_closed = _canonicalize_incident_events(
        bundle.get("events"),
        window_start=window_start,
        window_end=window_end,
    )

    return {
        "schema_version": schema_version,
        "incident_id": incident_id,
        "runtime_config_hash": runtime_config_hash,
        "generated_at": bundle["generated_at"],
        "replay_window": {
            "started_at": replay_window["started_at"],
            "ended_at": replay_window["ended_at"],
            "max_clock_skew_seconds": replay_window["max_clock_skew_seconds"],
            "max_event_age_seconds": replay_window["max_event_age_seconds"],
        },
        "clocks": {
            "runtime_observed_at": clocks["runtime_observed_at"],
            "exchange_observed_at": clocks["exchange_observed_at"],
            "monotonic_started_ns": clocks["monotonic_started_ns"],
            "monotonic_ended_ns": clocks["monotonic_ended_ns"],
        },
        "evidence_refs": evidence_refs,
        "remediation": {
            "status": remediation_status,
            "owner": remediation_owner,
            "updated_at": remediation["updated_at"],
            "fail_closed": remediation_fail_closed,
        },
        "events": events,
        "summary": {
            "event_count": len(events),
            "critical_path": list(_INCIDENT_CRITICAL_PATH),
            "fail_closed": remediation_fail_closed or events_fail_closed,
        },
    }


def build_runtime_safety_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    unknown_manifest_fields = sorted(
        set(manifest) - {"evidence_source", "environment_permission_evidence", "events", "reasons", "kill_switch_decision"}
    )
    if unknown_manifest_fields:
        raise ValueError("unknown runtime safety manifest field: " + ", ".join(unknown_manifest_fields))
    environment_permission_evidence, environment_checks = _canonical_environment_permission_evidence(
        manifest.get("environment_permission_evidence")
    )
    kill_switch_decision, kill_switch_checks = _canonical_kill_switch_decision(manifest.get("kill_switch_decision"))
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
    checks.update(environment_checks)
    for check_name, reason in _ENVIRONMENT_PERMISSION_CHECKS.items():
        if not environment_checks.get(check_name, False):
            reasons.append(reason)
    for event_type, (check_name, reason) in _REQUIRED_EVENTS.items():
        met = passed_by_type.get(event_type, False)
        checks[check_name] = met
        if not met:
            reasons.append(reason)
    for _evidence_name, (check_name, reason) in _HARD_KILL_SWITCH_EVIDENCE.items():
        met = kill_switch_checks[check_name]
        checks[check_name] = met
        if not met:
            reasons.append(reason)

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": source,
        "environment_permission_evidence": environment_permission_evidence,
        "kill_switch_decision": kill_switch_decision,
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
