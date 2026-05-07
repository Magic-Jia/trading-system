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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    coverage = float(value)
    if coverage < 0 or coverage > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return coverage


def _normalise_positive_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    number = float(value)
    if number <= 0:
        raise ValueError(f"{name} must be a positive number")
    return number


def _normalise_book_levels(levels: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> list[dict[str, float]]:
    normalised: list[dict[str, float]] = []
    for index, level in enumerate(levels):
        if not isinstance(level, Mapping):
            raise ValueError(f"book level {index} must be a mapping")
        normalised.append(
            {
                "price": _normalise_positive_float(f"book level {index} price", level.get("price")),
                "quantity": _normalise_positive_float(f"book level {index} quantity", level.get("quantity")),
            }
        )
    return normalised


def simulate_depth_driven_taker_fill(
    *,
    side: str,
    quantity: float,
    reference_price: float,
    bids: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    asks: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Simulate a marketable taker order against visible orderbook depth."""

    side = side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    requested_quantity = _normalise_positive_float("quantity", quantity)
    reference = _normalise_positive_float("reference_price", reference_price)
    levels = _normalise_book_levels(asks if side == "buy" else bids)

    remaining = requested_quantity
    filled = 0.0
    notional = 0.0
    consumed_levels: list[dict[str, float]] = []

    for level in levels:
        if remaining <= 0:
            break
        consume_quantity = min(remaining, level["quantity"])
        consumed_levels.append({"price": level["price"], "quantity": consume_quantity})
        filled += consume_quantity
        notional += consume_quantity * level["price"]
        remaining -= consume_quantity

    complete = remaining <= 1e-12
    residual_quantity = 0.0 if complete else remaining
    vwap = notional / filled if filled else None
    if vwap is None:
        slippage_bps = None
    elif side == "buy":
        slippage_bps = ((vwap - reference) / reference) * 10_000
    else:
        slippage_bps = ((reference - vwap) / reference) * 10_000

    return {
        "side": side,
        "requested_quantity": requested_quantity,
        "filled_quantity": filled,
        "residual_quantity": residual_quantity,
        "complete": complete,
        "vwap": vwap,
        "slippage_bps": slippage_bps,
        "consumed_levels": consumed_levels,
    }


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
        raise ValueError("evidence_source must be an object")
    evidence_source = dict(evidence_source)
    evidence_source.setdefault("type", "synthetic_fixture")
    if not isinstance(evidence_source.get("type"), str):
        raise ValueError("evidence_source type must be a string")
    if not evidence_source["type"].strip():
        raise ValueError("evidence_source type must be non-empty")
    for optional_field in ("run_id", "exported_at"):
        optional_value = evidence_source.get(optional_field)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if isinstance(optional_value, str) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")

    l2_tick_coverage_met = all(
        coverage[key] is not None and coverage[key] >= min_required_coverage
        for key in _DEFAULT_COVERAGE_KEYS
    )
    depth_fills = manifest.get("depth_driven_taker_fills")
    if depth_fills is None:
        fill_count = 0
        complete_fill_count = 0
        incomplete_fill_count = 0
        depth_driven_taker_override = manifest.get("depth_driven_taker_met", False)
        if not isinstance(depth_driven_taker_override, bool):
            raise ValueError("depth_driven_taker_met must be a boolean")
        depth_driven_taker_met = depth_driven_taker_override
    elif isinstance(depth_fills, list):
        fill_count = len(depth_fills)
        complete_fill_count = 0
        for fill in depth_fills:
            if not isinstance(fill, Mapping):
                raise ValueError("depth_driven_taker_fills entries must be mappings")
            complete = fill.get("complete", False)
            if not isinstance(complete, bool):
                raise ValueError("depth_driven_taker_fills complete must be a boolean")
            if complete:
                complete_fill_count += 1
        incomplete_fill_count = fill_count - complete_fill_count
        depth_driven_taker_met = fill_count > 0 and incomplete_fill_count == 0
    else:
        raise ValueError("depth_driven_taker_fills must be a list")

    reasons: list[str] = []
    if not l2_tick_coverage_met:
        reasons.append("l2_tick_coverage_below_threshold")
    if not depth_driven_taker_met:
        if fill_count > 0 and incomplete_fill_count > 0:
            reasons.append("depth_driven_taker_incomplete_fill")
        else:
            reasons.append("depth_driven_taker_evidence_missing")

    gate = {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": evidence_source,
        "checks": {
            "l2_tick_coverage_met": l2_tick_coverage_met,
            "depth_driven_taker_met": depth_driven_taker_met,
        },
        "coverage": coverage,
        "reasons": reasons,
    }
    if depth_fills is not None:
        gate["depth_driven_taker"] = {
            "fill_count": fill_count,
            "complete_fill_count": complete_fill_count,
            "incomplete_fill_count": incomplete_fill_count,
        }
    return gate


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
