from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "market_microstructure_gate_input.v1"
_DEFAULT_COVERAGE_KEYS = (
    "l2_snapshot_coverage",
    "l2_update_coverage",
    "tick_coverage",
)


def _normalise_coverage_value(name: str, value: Any) -> float | None:
    if value is None:
        return None
    try:
        coverage = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number between 0 and 1") from exc
    if coverage < 0 or coverage > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return coverage


def build_microstructure_gate(
    manifest: Mapping[str, Any],
    *,
    min_coverage: float = 0.99,
) -> dict[str, Any]:
    """Build conservative market microstructure evidence for live-readiness gates.

    The input is a manifest-style mapping produced by offline data audits. This
    function does not fetch data, place orders, or infer real evidence from test
    fixtures. If depth-driven fills are not explicitly attached in a later
    implementation stage, the taker-depth check remains false.
    """

    min_required_coverage = _normalise_coverage_value("min_coverage", min_coverage)
    assert min_required_coverage is not None

    coverage_input = manifest.get("coverage", {})
    if not isinstance(coverage_input, Mapping):
        raise ValueError("coverage must be a mapping")

    coverage = {
        key: _normalise_coverage_value(key, coverage_input.get(key))
        for key in _DEFAULT_COVERAGE_KEYS
    }
    coverage["min_required_coverage"] = min_required_coverage

    evidence_source = manifest.get("evidence_source") or {"type": "synthetic_fixture"}
    if not isinstance(evidence_source, Mapping):
        raise ValueError("evidence_source must be a mapping")
    evidence_source = dict(evidence_source)
    evidence_source.setdefault("type", "synthetic_fixture")

    l2_tick_coverage_met = all(
        coverage[key] is not None and coverage[key] >= min_required_coverage
        for key in _DEFAULT_COVERAGE_KEYS
    )
    depth_driven_taker_met = bool(manifest.get("depth_driven_taker_met", False))

    reasons: list[str] = []
    if not l2_tick_coverage_met:
        reasons.append("l2_tick_coverage_below_threshold")
    if not depth_driven_taker_met:
        reasons.append("depth_driven_taker_evidence_missing")

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": evidence_source,
        "checks": {
            "l2_tick_coverage_met": l2_tick_coverage_met,
            "depth_driven_taker_met": depth_driven_taker_met,
        },
        "coverage": coverage,
        "reasons": reasons,
    }


def write_microstructure_gate(
    manifest: Mapping[str, Any],
    output_dir: str | Path,
    *,
    min_coverage: float = 0.99,
) -> Path:
    output_path = Path(output_dir) / "market_microstructure_gate.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gate = build_microstructure_gate(manifest, min_coverage=min_coverage)
    output_path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")
    return output_path


def _load_manifest(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest JSON must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write offline market microstructure gate evidence")
    parser.add_argument("--manifest", required=True, help="Path to manifest JSON with coverage metrics")
    parser.add_argument("--output-dir", required=True, help="Directory for market_microstructure_gate.json")
    parser.add_argument("--min-coverage", type=float, default=0.99)
    args = parser.parse_args(argv)

    output_path = write_microstructure_gate(
        _load_manifest(args.manifest),
        args.output_dir,
        min_coverage=args.min_coverage,
    )
    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
