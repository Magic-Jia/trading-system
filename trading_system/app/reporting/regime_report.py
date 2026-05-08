from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
import math
from typing import Any, Mapping, Sequence


def _strict_mapping_object(value: Any, field: str, *, optional: bool = False) -> dict[str, Any]:
    if value is None:
        if optional:
            return {}
        raise ValueError(f"{field} must be mapping or dataclass")
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise ValueError(f"{field} must be mapping or dataclass")


def _strict_sequence_field(row: Mapping[str, Any], field: str) -> Sequence[Any]:
    value = row.get(field, [])
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a non-string sequence")
    return value


def _strict_string_value(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{field} must be non-empty string")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be string")
    if value != value.strip():
        raise ValueError(f"{field} must be canonical string")
    if required and not value:
        raise ValueError(f"{field} must be non-empty string")
    return value


def _strict_string_sequence(row: Mapping[str, Any], field: str) -> list[str]:
    values = _strict_sequence_field(row, field)
    return [_strict_string_value(value, field, required=True) for value in values]


def _strict_optional_number(row: Mapping[str, Any], field: str) -> float | None:
    if field not in row:
        return None
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite int or float")
    return float(value)


def _strict_number_with_default(row: Mapping[str, Any], field: str, default: float) -> float:
    value = _strict_optional_number(row, field)
    if value is None:
        return default
    return value


def build_regime_summary(
    *,
    regime: Any,
    universes: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    trend_report: Mapping[str, Any] | None = None,
    rotation_report: Mapping[str, Any] | None = None,
    short_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    regime_row = _strict_mapping_object(regime, "regime")
    trend_row = _strict_mapping_object(trend_report, "trend_report", optional=True)
    rotation_row = _strict_mapping_object(rotation_report, "rotation_report", optional=True)
    short_row = _strict_mapping_object(short_report, "short_report", optional=True)
    accepted = [row for row in allocations if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    rejected = [row for row in allocations if row.get("status") == "REJECTED"]
    total_allocated_risk = round(sum(_strict_number_with_default(row, "final_risk_budget", 0.0) for row in accepted), 6)
    aggressiveness_values = [
        aggressiveness_multiplier
        for row in accepted
        if (aggressiveness_multiplier := _strict_optional_number(row, "aggressiveness_multiplier")) is not None
    ]
    avg_aggressiveness = round(sum(aggressiveness_values) / len(aggressiveness_values), 6) if aggressiveness_values else 0.0
    compressed_count = len([value for value in aggressiveness_values if value < 1.0])
    compression_reason_counts: Counter[str] = Counter()
    for row in accepted:
        reasons = row.get("compression_reasons")
        regime_hazard_multiplier = _strict_number_with_default(row, "regime_hazard_multiplier", 1.0)
        late_stage_heat_multiplier = _strict_number_with_default(row, "late_stage_heat_multiplier", 1.0)
        if reasons is not None:
            compression_reason_counts.update(_strict_string_sequence(row, "compression_reasons"))
            continue
        if regime_hazard_multiplier < 1.0:
            compression_reason_counts["regime_hazard"] += 1
        if late_stage_heat_multiplier < 1.0:
            compression_reason_counts["late_stage_heat"] += 1

    return {
        "regime": {
            "label": regime_row.get("label"),
            "confidence": regime_row.get("confidence"),
            "risk_multiplier": regime_row.get("risk_multiplier"),
            "execution_policy": regime_row.get("execution_policy"),
            "suppression_rules": list(_strict_sequence_field(regime_row, "suppression_rules")),
            "late_stage_heat": regime_row.get("late_stage_heat", "none"),
            "execution_hazard": regime_row.get("execution_hazard", "none"),
        },
        "universes": {
            "major_count": len(_strict_sequence_field(universes, "major_universe")),
            "rotation_count": len(_strict_sequence_field(universes, "rotation_universe")),
            "short_count": len(_strict_sequence_field(universes, "short_universe")),
        },
        "candidates": {
            "total": len(candidates),
            "trend": len([row for row in candidates if row.get("engine") == "trend"]),
            "rotation": len([row for row in candidates if row.get("engine") == "rotation"]),
            "short": len([row for row in candidates if row.get("engine") == "short"]),
        },
        "allocations": {
            "total": len(allocations),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "total_allocated_risk": total_allocated_risk,
            "avg_aggressiveness": avg_aggressiveness,
            "compressed_count": compressed_count,
            "compression_reason_counts": dict(compression_reason_counts),
            "regime_hazard_compressed_count": compression_reason_counts.get("regime_hazard", 0),
            "late_stage_heat_compressed_count": compression_reason_counts.get("late_stage_heat", 0),
        },
        "executions": {
            "count": len(executions),
            "symbols": sorted(
                {
                    _strict_string_value(row.get("symbol"), "symbol", required=True)
                    for row in executions
                    if row.get("symbol") is not None
                }
            ),
        },
        "trend": trend_row,
        "rotation": rotation_row,
        "short": short_row,
    }
