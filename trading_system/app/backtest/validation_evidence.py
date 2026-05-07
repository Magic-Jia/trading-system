from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "validation_gate_input.v1"


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


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


def build_validation_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_source = manifest.get("evidence_source")
    if raw_source is None:
        source: dict[str, Any] = {"type": "unknown_offline_records"}
    elif not isinstance(raw_source, Mapping):
        raise ValueError("evidence_source must be an object")
    else:
        source = dict(raw_source)
    source.setdefault("type", "unknown_offline_records")

    oos = _mapping(manifest.get("oos", {}), "oos")
    baseline = _float_or_none(oos.get("baseline_net_pnl"))
    oos_pnl = _float_or_none(oos.get("oos_net_pnl"))
    max_degradation = _float_or_none(oos.get("max_degradation_fraction"))
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
    for regime in regimes_raw:
        regime_mapping = _mapping(regime, "regime")
        trade_count = _integer_count(regime_mapping.get("trade_count", 0), "regime trade_count")
        net_pnl = _float_or_none(regime_mapping.get("net_pnl"))
        if trade_count > 0:
            eligible_regime_count += 1
        if trade_count > 0 and net_pnl is not None and net_pnl > 0:
            profitable_regime_count += 1
    multi_regime_resilience_met = eligible_regime_count >= 2 and profitable_regime_count >= 2

    cost_stress = _mapping(manifest.get("cost_stress", {}), "cost_stress")
    stressed_net_pnl = _float_or_none(cost_stress.get("stressed_net_pnl"))
    cost_stress_positive_met = stressed_net_pnl is not None and stressed_net_pnl > 0

    forward = _mapping(manifest.get("forward_contamination", {}), "forward_contamination")
    forward_contamination_absent_met = forward.get("absent") is True

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
