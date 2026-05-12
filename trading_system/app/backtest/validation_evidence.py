from __future__ import annotations

import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "validation_gate_input.v1"
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPTIONAL_REGIME_IDENTIFIER_FIELDS = frozenset({"regime_id", "regime_name", "label", "name"})


def _is_exact_string(value: Any) -> bool:
    return type(value) is str


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


def _optional_safe_identifier(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not _is_exact_string(value):
        raise ValueError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must be canonical")
    if not _is_safe_evidence_identifier(value):
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _float_or_none(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _require_positive(value: float | None, name: str) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(value: float | None, name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_fraction(value: float | None, name: str) -> None:
    if value is not None and not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _integer_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer count")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative count")
    return value


def _validate_canonical_keys(value: Mapping[Any, Any], name: str) -> None:
    for key in value:
        if not isinstance(key, str):
            raise ValueError(f"{name}.<key> must be a string")
        if not key.strip():
            raise ValueError(f"{name}.<key> must be non-empty")
        if key != key.strip():
            raise ValueError(f"{name}.<key> must be canonical")


def build_validation_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    unknown_manifest_fields = sorted(
        set(manifest) - {"evidence_source", "oos", "regimes", "cost_stress", "forward_contamination"}
    )
    if unknown_manifest_fields:
        raise ValueError("unknown validation manifest field: " + ", ".join(unknown_manifest_fields))
    raw_source = manifest.get("evidence_source")
    if raw_source is None:
        source: dict[str, Any] = {"type": "unknown_offline_records"}
    elif not isinstance(raw_source, Mapping):
        raise ValueError("evidence_source must be an object")
    else:
        _validate_canonical_keys(raw_source, "evidence_source")
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
        if optional_value is not None and not _is_exact_string(optional_value):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if _is_exact_string(optional_value) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")
        if _is_exact_string(optional_value) and optional_value != optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be canonical")
        if (
            optional_field == "run_id"
            and _is_exact_string(optional_value)
            and not _is_safe_evidence_identifier(optional_value)
        ):
            raise ValueError("evidence_source run_id must be a safe identifier")
        if (
            optional_field == "exported_at"
            and _is_exact_string(optional_value)
            and not _is_canonical_utc_timestamp(optional_value)
        ):
            raise ValueError("evidence_source exported_at must be a canonical UTC timestamp")

    oos = _mapping(manifest.get("oos", {}), "oos")
    unknown_oos_fields = sorted(set(oos) - {"baseline_net_pnl", "oos_net_pnl", "max_degradation_fraction"})
    if unknown_oos_fields:
        raise ValueError("unknown validation oos field: " + ", ".join(unknown_oos_fields))
    baseline = _float_or_none(oos.get("baseline_net_pnl"), "oos baseline_net_pnl")
    oos_pnl = _float_or_none(oos.get("oos_net_pnl"), "oos oos_net_pnl")
    max_degradation = _float_or_none(oos.get("max_degradation_fraction"), "oos max_degradation_fraction")
    _require_positive(baseline, "oos baseline_net_pnl")
    _require_non_negative(oos_pnl, "oos oos_net_pnl")
    _require_fraction(max_degradation, "oos max_degradation_fraction")
    if max_degradation is None:
        max_degradation = 0.2
    if baseline is None or baseline <= 0 or oos_pnl is None:
        degradation_fraction = None
        oos_non_degraded_met = False
    else:
        degradation_fraction = (baseline - oos_pnl) / baseline
        oos_non_degraded_met = degradation_fraction <= max_degradation

    regimes_raw = manifest.get("regimes", [])
    if not isinstance(regimes_raw, list):
        raise ValueError("regimes must be a list")
    profitable_regime_count = 0
    eligible_regime_count = 0
    regime_identifiers: set[str] = set()
    for index, regime in enumerate(regimes_raw):
        regime_name = f"regimes[{index}]"
        regime_mapping = _mapping(regime, regime_name)
        unknown_regime_fields = sorted(set(regime_mapping) - {"trade_count", "net_pnl"} - _OPTIONAL_REGIME_IDENTIFIER_FIELDS)
        if unknown_regime_fields:
            raise ValueError("unknown validation regime field: " + ", ".join(unknown_regime_fields))
        for identifier_field in _OPTIONAL_REGIME_IDENTIFIER_FIELDS:
            identifier = _optional_safe_identifier(regime_mapping.get(identifier_field), f"{regime_name}.{identifier_field}")
            if identifier is None:
                continue
            if identifier in regime_identifiers:
                raise ValueError(f"duplicate validation regime identifier: {identifier}")
            regime_identifiers.add(identifier)
        trade_count = _integer_count(regime_mapping.get("trade_count", 0), "regime trade_count")
        net_pnl = _float_or_none(regime_mapping.get("net_pnl"), f"{regime_name}.net_pnl")
        if trade_count > 0:
            eligible_regime_count += 1
        if trade_count > 0 and net_pnl is not None and net_pnl > 0:
            profitable_regime_count += 1
    multi_regime_resilience_met = eligible_regime_count >= 2 and profitable_regime_count >= 2

    cost_stress = _mapping(manifest.get("cost_stress", {}), "cost_stress")
    unknown_cost_stress_fields = sorted(set(cost_stress) - {"stressed_net_pnl"})
    if unknown_cost_stress_fields:
        raise ValueError("unknown validation cost_stress field: " + ", ".join(unknown_cost_stress_fields))
    stressed_net_pnl = _float_or_none(cost_stress.get("stressed_net_pnl"), "cost_stress stressed_net_pnl")
    _require_non_negative(stressed_net_pnl, "cost_stress stressed_net_pnl")
    cost_stress_positive_met = stressed_net_pnl is not None and stressed_net_pnl > 0

    forward = _mapping(manifest.get("forward_contamination", {}), "forward_contamination")
    unknown_forward_fields = sorted(set(forward) - {"absent", "audit_id"})
    if unknown_forward_fields:
        raise ValueError("unknown validation forward_contamination field: " + ", ".join(unknown_forward_fields))
    forward_absent = forward.get("absent", False)
    if not isinstance(forward_absent, bool):
        raise ValueError("forward_contamination absent must be a boolean")
    audit_id = forward.get("audit_id")
    if audit_id is not None:
        _optional_safe_identifier(audit_id, "forward_contamination audit_id")
    forward_contamination_absent_met = forward_absent

    checks = {
        "oos_non_degraded_met": oos_non_degraded_met,
        "multi_regime_resilience_met": multi_regime_resilience_met,
        "cost_stress_positive_met": cost_stress_positive_met,
        "forward_contamination_absent_met": forward_contamination_absent_met,
    }
    reasons: list[str] = []
    if not oos_non_degraded_met:
        reasons.append("oos_degraded")
    if not multi_regime_resilience_met:
        reasons.append("regime_single_point_survivor")
    if not cost_stress_positive_met:
        reasons.append("cost_stress_not_positive")
    if not forward_contamination_absent_met:
        reasons.append("forward_contamination_unproven")

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": source,
        "checks": checks,
        "summary": {
            "baseline_net_pnl": baseline,
            "oos_net_pnl": oos_pnl,
            "max_degradation_fraction": max_degradation,
            "oos_degradation_fraction": degradation_fraction,
            "eligible_regime_count": eligible_regime_count,
            "profitable_regime_count": profitable_regime_count,
            "stressed_net_pnl": stressed_net_pnl,
            "forward_contamination_audit_id": forward.get("audit_id"),
        },
        "reasons": reasons,
    }


def write_validation_gate(manifest: Mapping[str, Any], output_dir: str | Path) -> Path:
    output_path = Path(output_dir) / "validation_gate.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(build_validation_gate(manifest), indent=2, sort_keys=True) + "\n")
    return output_path


def _load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest JSON must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write validation gate evidence")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    print(write_validation_gate(_load_manifest(args.manifest), args.output_dir))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
