from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "stress_replay_contract.v1"
FILENAME = "stress_replay_contract.json"
MODE = "offline_simulated"

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_REQUIRED_SCENARIO_TYPES = frozenset({"cancel_failure", "stuck_partial_order_replay"})


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") == value


def _parse_timestamp(value: Any, field_path: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{field_path}_not_string")
    if not _is_canonical_utc_timestamp(value):
        raise ValueError(f"{field_path}_noncanonical_timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _require_mapping(value: Any, field_path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_path}_not_object")
    return value


def _safe_identifier(value: Any, field_path: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_path}_not_string")
    if not value:
        raise ValueError(f"{field_path}_blank")
    if value != value.strip():
        raise ValueError(f"{field_path}_noncanonical")
    if _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path}_not_identifier")
    return value


def _number(value: Any, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path}_not_number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_path}_not_finite")
    return parsed


def _non_negative_number(value: Any, field_path: str) -> float:
    parsed = _number(value, field_path)
    if parsed < 0.0:
        raise ValueError(f"{field_path}_negative")
    return parsed


def _non_negative_int(value: Any, field_path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_path}_not_non_negative_int")
    return value


def _strict_bool(value: Any, field_path: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_path}_not_bool")
    return value


def _evidence_source(value: Any) -> dict[str, str]:
    raw = _require_mapping(value, "evidence_source")
    unknown_fields = sorted(set(raw) - {"type", "run_id", "exported_at"})
    if unknown_fields:
        raise ValueError("evidence_source.unknown_field: " + ", ".join(unknown_fields))
    source_type = _safe_identifier(raw.get("type"), "evidence_source.type")
    if source_type != "simulated_offline":
        raise ValueError("stress_replay_evidence_source_not_simulated_offline")
    source = {"type": source_type}
    for optional_field in ("run_id", "exported_at"):
        if optional_field in raw and raw[optional_field] is not None:
            source[optional_field] = _safe_identifier(raw[optional_field], f"evidence_source.{optional_field}")
    return source


def _scenario(
    value: Any,
    *,
    index: int,
    generated_at: datetime,
    contract_max_age_seconds: float,
) -> dict[str, Any]:
    raw = _require_mapping(value, f"scenarios[{index}]")
    allowed_fields = {
        "scenario_id",
        "scenario_type",
        "generated_at",
        "observed_at",
        "max_evidence_age_seconds",
        "attempt_count",
        "failed_cancel_count",
        "stuck_partial_order_count",
        "fail_closed_triggered",
        "replay_completed",
        "passed",
        "evidence_ref",
    }
    unknown_fields = sorted(set(raw) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"scenarios[{index}].unknown_field: " + ", ".join(unknown_fields))
    scenario_id = _safe_identifier(raw.get("scenario_id"), f"scenarios[{index}].scenario_id")
    scenario_type = _safe_identifier(raw.get("scenario_type"), f"scenarios[{index}].scenario_type")
    if scenario_type not in _REQUIRED_SCENARIO_TYPES:
        raise ValueError(f"scenarios[{index}].scenario_type_unsupported")
    scenario_generated_at = _parse_timestamp(raw.get("generated_at"), f"scenarios[{index}].generated_at")
    observed_at = _parse_timestamp(raw.get("observed_at"), f"scenarios[{index}].observed_at")
    if scenario_generated_at > generated_at:
        raise ValueError(f"scenarios[{index}].generated_at_future")
    if observed_at > scenario_generated_at:
        raise ValueError(f"scenarios[{index}].evidence_future")
    max_age = _non_negative_number(raw.get("max_evidence_age_seconds"), f"scenarios[{index}].max_evidence_age_seconds")
    effective_max_age = min(max_age, contract_max_age_seconds)
    if (scenario_generated_at - observed_at).total_seconds() > effective_max_age:
        raise ValueError(f"scenarios[{index}].evidence_stale")
    attempt_count = _non_negative_int(raw.get("attempt_count"), f"scenarios[{index}].attempt_count")
    failed_cancel_count = _non_negative_int(
        raw.get("failed_cancel_count"),
        f"scenarios[{index}].failed_cancel_count",
    )
    stuck_partial_order_count = _non_negative_int(
        raw.get("stuck_partial_order_count"),
        f"scenarios[{index}].stuck_partial_order_count",
    )
    if scenario_type == "cancel_failure" and failed_cancel_count <= 0:
        raise ValueError("stress_replay_contract must include cancel failure evidence")
    if scenario_type == "stuck_partial_order_replay" and stuck_partial_order_count <= 0:
        raise ValueError("stress_replay_contract must include stuck partial-order replay evidence")
    return {
        "scenario_id": scenario_id,
        "scenario_type": scenario_type,
        "generated_at": raw["generated_at"],
        "observed_at": raw["observed_at"],
        "max_evidence_age_seconds": max_age,
        "attempt_count": attempt_count,
        "failed_cancel_count": failed_cancel_count,
        "stuck_partial_order_count": stuck_partial_order_count,
        "fail_closed_triggered": _strict_bool(raw.get("fail_closed_triggered"), f"scenarios[{index}].fail_closed_triggered"),
        "replay_completed": _strict_bool(raw.get("replay_completed"), f"scenarios[{index}].replay_completed"),
        "passed": _strict_bool(raw.get("passed"), f"scenarios[{index}].passed"),
        "evidence_ref": _safe_identifier(raw.get("evidence_ref"), f"scenarios[{index}].evidence_ref"),
    }


def build_stress_replay_contract(
    *,
    generated_at: str,
    max_evidence_age_seconds: float,
    evidence_source: Mapping[str, Any],
    scenarios: list[Mapping[str, Any]],
) -> dict[str, Any]:
    generated = _parse_timestamp(generated_at, "stress_replay_generated_at")
    max_age = _non_negative_number(max_evidence_age_seconds, "stress_replay_max_evidence_age_seconds")
    source = _evidence_source(evidence_source)
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("stress_replay_scenarios_not_non_empty_list")
    parsed_scenarios = [
        _scenario(
            scenario,
            index=index,
            generated_at=generated,
            contract_max_age_seconds=max_age,
        )
        for index, scenario in enumerate(scenarios)
    ]
    scenario_types = {scenario["scenario_type"] for scenario in parsed_scenarios}
    if "cancel_failure" not in scenario_types:
        raise ValueError("cancel_failure_scenario_missing")
    if "stuck_partial_order_replay" not in scenario_types:
        raise ValueError("stuck_partial_order_replay_missing")
    cancel_present = any(
        scenario["scenario_type"] == "cancel_failure" and scenario["failed_cancel_count"] > 0
        for scenario in parsed_scenarios
    )
    stuck_partial_present = any(
        scenario["scenario_type"] == "stuck_partial_order_replay" and scenario["stuck_partial_order_count"] > 0
        for scenario in parsed_scenarios
    )
    all_passed = all(
        scenario["passed"] and scenario["fail_closed_triggered"] and scenario["replay_completed"]
        for scenario in parsed_scenarios
    )
    checks = {
        "stress_replay_contract_present": True,
        "stress_replay_contract_schema_valid": True,
        "stress_replay_scenarios_passed": all_passed,
        "offline_simulated_evidence_only": True,
        "cancel_failure_scenario_present": cancel_present,
        "stuck_partial_order_replay_present": stuck_partial_present,
        "all_scenarios_passed": all_passed,
        "fail_closed": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "fail_closed": True,
        "generated_at": generated_at,
        "max_evidence_age_seconds": max_age,
        "evidence_source": source,
        "scenarios": parsed_scenarios,
        "checks": checks,
        "decision": "stress_replay_within_contract" if all_passed else "reject_for_live_promotion",
        "reasons": [] if all_passed else ["stress_replay_scenario_failed"],
    }


def validate_stress_replay_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("stress_replay_contract_not_object")
    allowed_fields = {
        "schema_version",
        "mode",
        "fail_closed",
        "generated_at",
        "max_evidence_age_seconds",
        "evidence_source",
        "scenarios",
        "checks",
        "decision",
        "reasons",
    }
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError("stress_replay_contract_unknown_field: " + ", ".join(unknown_fields))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("stress_replay_contract_schema_version_invalid")
    if payload.get("mode") != MODE:
        raise ValueError("stress_replay_contract_mode_not_offline_simulated")
    if payload.get("fail_closed") is not True:
        raise ValueError("stress_replay_contract_fail_closed_not_true")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list):
        raise ValueError("stress_replay_scenarios_not_non_empty_list")
    rebuilt = build_stress_replay_contract(
        generated_at=payload.get("generated_at"),
        max_evidence_age_seconds=payload.get("max_evidence_age_seconds"),
        evidence_source=_require_mapping(payload.get("evidence_source"), "evidence_source"),
        scenarios=raw_scenarios,
    )
    for field in ("checks", "decision", "reasons"):
        if payload.get(field) != rebuilt[field]:
            raise ValueError(f"stress_replay_contract_{field}_mismatch")
    return rebuilt


def write_stress_replay_contract(root: str | Path, **kwargs: Any) -> dict[str, Any]:
    contract = build_stress_replay_contract(**kwargs)
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    (root_path / FILENAME).write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract
