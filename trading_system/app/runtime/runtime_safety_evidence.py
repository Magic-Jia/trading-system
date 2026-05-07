from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "runtime_safety_gate_input.v1"
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


def build_runtime_safety_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_source = manifest.get("evidence_source")
    if raw_source is None:
        source: dict[str, Any] = {"type": "unknown_offline_records"}
    elif not isinstance(raw_source, Mapping):
        raise ValueError("evidence_source must be an object")
    else:
        source = dict(raw_source)
    source.setdefault("type", "unknown_offline_records")
    if not isinstance(source.get("type"), str):
        raise ValueError("evidence_source type must be a string")
    if not source["type"].strip():
        raise ValueError("evidence_source type must be non-empty")
    for optional_field in ("run_id", "exported_at"):
        optional_value = source.get(optional_field)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if isinstance(optional_value, str) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")
    events = manifest.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events must be a list")

    passed_by_type: dict[str, bool] = {}
    counts_by_type: dict[str, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("runtime safety event must be a mapping")
        raw_event_type = event.get("type") if event.get("type") is not None else event.get("event_type")
        if raw_event_type is None:
            continue
        if not isinstance(raw_event_type, str):
            raise ValueError("runtime safety event type must be a string")
        event_type = raw_event_type.strip()
        if not event_type:
            continue
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
        },
        "reasons": reasons,
    }


def write_runtime_safety_gate(manifest: Mapping[str, Any], output_dir: str | Path) -> Path:
    output_path = Path(output_dir) / "runtime_safety_gate.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(build_runtime_safety_gate(manifest), indent=2, sort_keys=True) + "\n")
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
