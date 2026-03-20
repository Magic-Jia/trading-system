from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return {}


def build_regime_summary(
    *,
    regime: Any,
    universes: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    allocations: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    rotation_report: Mapping[str, Any] | None = None,
    short_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    regime_row = _as_dict(regime)
    accepted = [row for row in allocations if row.get("status") in {"ACCEPTED", "DOWNSIZED"}]
    rejected = [row for row in allocations if row.get("status") == "REJECTED"]
    total_allocated_risk = round(sum(float(row.get("final_risk_budget", 0.0) or 0.0) for row in accepted), 6)

    return {
        "regime": {
            "label": regime_row.get("label"),
            "confidence": regime_row.get("confidence"),
            "risk_multiplier": regime_row.get("risk_multiplier"),
            "execution_policy": regime_row.get("execution_policy"),
            "suppression_rules": regime_row.get("suppression_rules", []),
        },
        "universes": {
            "major_count": len(list(universes.get("major_universe", []))),
            "rotation_count": len(list(universes.get("rotation_universe", []))),
            "short_count": len(list(universes.get("short_universe", []))),
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
        },
        "executions": {
            "count": len(executions),
            "symbols": sorted({str(row.get("symbol")) for row in executions if row.get("symbol")}),
        },
        "rotation": dict(rotation_report or {}),
        "short": dict(short_report or {}),
    }
