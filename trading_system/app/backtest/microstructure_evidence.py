from __future__ import annotations

import argparse
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "market_microstructure_gate_input.v1"
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_DEFAULT_COVERAGE_KEYS = (
    "l2_snapshot_coverage",
    "l2_update_coverage",
    "tick_coverage",
)
_INTERVAL_IDENTITY_KEYS = ("source", "symbol", "venue", "interval", "generated_at")
_INTERVAL_PATH_COMPONENT_KEYS = ("source", "symbol", "venue", "interval")


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


def _artifact_ref_is_path_safe(value: str) -> bool:
    path = Path(value)
    return (
        bool(value.strip())
        and "\x00" not in value
        and "\\" not in value
        and not path.is_absolute()
        and ".." not in path.parts
        and "" not in path.parts
    )


def _artifact_ref_is_canonical(value: str) -> bool:
    return value == value.strip() and "\\" not in value and value == str(Path(value))


def _path_component_is_safe(value: str) -> bool:
    return (
        bool(value.strip())
        and "\x00" not in value
        and len(Path(value).parts) == 1
        and value not in {".", ".."}
    )


def _normalise_canonical_string(name: str, value: Any) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must be canonical")
    return value


def _normalise_identity_fingerprint(name: str, value: Any) -> str:
    if not _is_exact_string(value):
        raise ValueError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip().casefold()


def _normalise_coverage_value(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    coverage = float(value)
    if not math.isfinite(coverage) or coverage < 0 or coverage > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return coverage


def _normalise_required_intervals(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("required_intervals must be a list")
    intervals: list[str] = []
    for index, item in enumerate(value, start=1):
        interval = _normalise_canonical_string(f"required_intervals[{index}]", item)
        if not _path_component_is_safe(interval):
            raise ValueError(f"required_intervals[{index}] must be path-safe")
        if interval in intervals:
            raise ValueError(f"required_intervals[{index}] must be unique")
        intervals.append(interval)
    return intervals


def _normalise_interval_coverage(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("interval_coverage must be a list")
    normalised: list[dict[str, Any]] = []
    interval_identities: set[tuple[str, str, str, str, str]] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"interval_coverage[{index}] must be an object")
        unknown_fields = sorted(set(item) - {*_INTERVAL_IDENTITY_KEYS, "coverage", "artifact_ref"})
        if unknown_fields:
            raise ValueError(
                f"unknown interval_coverage[{index}] field: " + ", ".join(unknown_fields)
            )
        identity = tuple(
            _normalise_identity_fingerprint(f"interval_coverage[{index}] {key}", item.get(key))
            for key in _INTERVAL_IDENTITY_KEYS
        )
        if identity in interval_identities:
            raise ValueError(f"interval_coverage[{index}] duplicates interval identity")
        interval_identities.add(identity)
        row: dict[str, Any] = {
            key: _normalise_canonical_string(f"interval_coverage[{index}] {key}", item.get(key))
            for key in _INTERVAL_IDENTITY_KEYS
        }
        for key in _INTERVAL_PATH_COMPONENT_KEYS:
            if not _path_component_is_safe(row[key]):
                raise ValueError(f"interval_coverage[{index}] {key} must be path-safe")
        if not _is_canonical_utc_timestamp(row["generated_at"]):
            raise ValueError(f"interval_coverage[{index}] generated_at must be a canonical UTC timestamp")
        coverage_input = item.get("coverage")
        if not isinstance(coverage_input, Mapping):
            raise ValueError(f"interval_coverage[{index}] coverage must be an object")
        unknown_coverage_fields = sorted(set(coverage_input) - set(_DEFAULT_COVERAGE_KEYS))
        if unknown_coverage_fields:
            raise ValueError(
                f"unknown interval_coverage[{index}] coverage field: " + ", ".join(unknown_coverage_fields)
            )
        row["coverage"] = {
            key: _normalise_coverage_value(f"interval_coverage[{index}] {key}", coverage_input.get(key))
            for key in _DEFAULT_COVERAGE_KEYS
        }
        artifact_ref = _normalise_canonical_string(
            f"interval_coverage[{index}] artifact_ref", item.get("artifact_ref")
        )
        if "\\" in artifact_ref:
            raise ValueError(f"interval_coverage[{index}] artifact_ref must use / separators")
        if not _artifact_ref_is_path_safe(artifact_ref) or not _artifact_ref_is_canonical(artifact_ref):
            raise ValueError(f"interval_coverage[{index}] artifact_ref must be path-safe")
        row["artifact_ref"] = artifact_ref
        normalised.append(row)
    return normalised


def _normalise_positive_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive number")
    return number


def _normalise_finite_float(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _normalise_non_negative_float(name: str, value: Any) -> float:
    number = _normalise_finite_float(name, value)
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
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


def _ensure_sorted_book_side(name: str, levels: list[dict[str, float]], *, descending: bool) -> None:
    for index in range(1, len(levels)):
        previous_price = levels[index - 1]["price"]
        current_price = levels[index]["price"]
        if descending:
            if current_price >= previous_price:
                raise ValueError(f"{name} must be sorted by descending price")
        elif current_price <= previous_price:
            raise ValueError(f"{name} must be sorted by ascending price")


def simulate_depth_driven_taker_fill(
    *,
    side: str,
    quantity: float,
    reference_price: float,
    bids: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    asks: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Simulate a marketable taker order against visible orderbook depth."""

    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    requested_quantity = _normalise_positive_float("quantity", quantity)
    reference = _normalise_positive_float("reference_price", reference_price)
    bid_levels = _normalise_book_levels(bids)
    ask_levels = _normalise_book_levels(asks)
    if side == "sell" and not bid_levels:
        raise ValueError("bids must contain at least one level")
    if side == "buy" and not ask_levels:
        raise ValueError("asks must contain at least one level")
    _ensure_sorted_book_side("bids", bid_levels, descending=True)
    _ensure_sorted_book_side("asks", ask_levels, descending=False)
    if bid_levels and ask_levels and bid_levels[0]["price"] >= ask_levels[0]["price"]:
        raise ValueError("book must not be crossed")
    levels = ask_levels if side == "buy" else bid_levels

    remaining = requested_quantity
    filled = 0.0
    notional = 0.0
    consumed_levels: list[dict[str, float]] = []

    for level in levels:
        if remaining <= 0:
            break
        consume_quantity = min(remaining, level["quantity"])
        consume_notional = consume_quantity * level["price"]
        consumed_levels.append(
            {"price": level["price"], "quantity": consume_quantity, "notional": consume_notional}
        )
        filled += consume_quantity
        notional += consume_notional
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
        "filled_notional": notional,
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

    unknown_manifest_fields = sorted(
        set(manifest)
        - {
            "evidence_source",
            "coverage",
            "required_intervals",
            "interval_coverage",
            "depth_driven_taker_fills",
            "depth_driven_taker_met",
            "passive_maker_fills",
        }
    )
    if unknown_manifest_fields:
        raise ValueError("unknown microstructure manifest field: " + ", ".join(unknown_manifest_fields))
    min_required_coverage = _normalise_coverage_value("min_coverage", min_coverage)
    assert min_required_coverage is not None

    coverage_input = manifest.get("coverage", {})
    if not isinstance(coverage_input, Mapping):
        raise ValueError("coverage must be a mapping")
    unknown_coverage_fields = sorted(set(coverage_input) - set(_DEFAULT_COVERAGE_KEYS))
    if unknown_coverage_fields:
        raise ValueError("unknown microstructure coverage field: " + ", ".join(unknown_coverage_fields))

    coverage = {
        key: _normalise_coverage_value(key, coverage_input.get(key))
        for key in _DEFAULT_COVERAGE_KEYS
    }
    coverage["min_required_coverage"] = min_required_coverage

    required_intervals = _normalise_required_intervals(manifest.get("required_intervals"))
    interval_coverage = _normalise_interval_coverage(manifest.get("interval_coverage"))
    interval_rows_by_interval: dict[str, list[dict[str, Any]]] = {}
    for row in interval_coverage:
        interval_rows_by_interval.setdefault(row["interval"], []).append(row)
    interval_coverage_reasons: list[str] = []
    for interval in required_intervals:
        rows = interval_rows_by_interval.get(interval, [])
        if not rows:
            interval_coverage_reasons.append(f"required_interval_coverage_missing:{interval}")
            continue
        if any(
            row["coverage"][key] is None or row["coverage"][key] <= 0
            for row in rows
            for key in _DEFAULT_COVERAGE_KEYS
        ):
            interval_coverage_reasons.append(f"required_interval_coverage_zero:{interval}")
        elif any(
            row["coverage"][key] < min_required_coverage
            for row in rows
            for key in _DEFAULT_COVERAGE_KEYS
        ):
            interval_coverage_reasons.append(f"required_interval_coverage_below_threshold:{interval}")

    evidence_source = manifest.get("evidence_source") or {"type": "synthetic_fixture"}
    if not isinstance(evidence_source, Mapping):
        raise ValueError("evidence_source must be an object")
    for source_key in evidence_source:
        if not isinstance(source_key, str) or not source_key.strip() or source_key != source_key.strip():
            raise ValueError(
                f"evidence_source key {source_key!r} must be a canonical non-empty string"
            )
    evidence_source = dict(evidence_source)
    evidence_source.setdefault("type", "synthetic_fixture")
    unknown_source_fields = sorted(set(evidence_source) - {"type", "run_id", "exported_at"})
    if unknown_source_fields:
        raise ValueError("unknown evidence_source field: " + ", ".join(unknown_source_fields))
    if not _is_exact_string(evidence_source.get("type")):
        raise ValueError("evidence_source type must be a string")
    if not evidence_source["type"].strip():
        raise ValueError("evidence_source type must be non-empty")
    if evidence_source["type"] != evidence_source["type"].strip():
        raise ValueError("evidence_source type must be canonical")
    for optional_field in ("run_id", "exported_at"):
        optional_value = evidence_source.get(optional_field)
        if optional_value is not None and not _is_exact_string(optional_value):
            raise ValueError(f"evidence_source {optional_field} must be a string")
        if _is_exact_string(optional_value) and not optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be non-empty")
        if _is_exact_string(optional_value) and optional_value != optional_value.strip():
            raise ValueError(f"evidence_source {optional_field} must be canonical")
        if (
            optional_field == "exported_at"
            and _is_exact_string(optional_value)
            and not _is_canonical_utc_timestamp(optional_value)
        ):
            raise ValueError("evidence_source exported_at must be a canonical UTC timestamp")

    l2_tick_coverage_met = all(
        coverage[key] is not None and coverage[key] >= min_required_coverage
        for key in _DEFAULT_COVERAGE_KEYS
    ) and not interval_coverage_reasons
    depth_fills = manifest.get("depth_driven_taker_fills")
    if depth_fills is None:
        fill_count = 0
        complete_fill_count = 0
        incomplete_fill_count = 0
        incomplete_filled_quantity = 0.0
        incomplete_residual_quantity = 0.0
        depth_driven_taker_override = manifest.get("depth_driven_taker_met", False)
        if not isinstance(depth_driven_taker_override, bool):
            raise ValueError("depth_driven_taker_met must be a boolean")
        depth_driven_taker_met = depth_driven_taker_override
    elif isinstance(depth_fills, list):
        fill_count = len(depth_fills)
        complete_fill_count = 0
        incomplete_filled_quantity = 0.0
        incomplete_residual_quantity = 0.0
        for fill in depth_fills:
            if not isinstance(fill, Mapping):
                raise ValueError("depth_driven_taker_fills entries must be mappings")
            allowed_fill_fields = {
                "side",
                "requested_quantity",
                "filled_quantity",
                "filled_notional",
                "residual_quantity",
                "complete",
                "vwap",
                "slippage_bps",
                "consumed_levels",
            }
            unknown_fill_fields = sorted(set(fill) - allowed_fill_fields)
            if unknown_fill_fields:
                raise ValueError("unknown depth_driven_taker_fills field: " + ", ".join(unknown_fill_fields))
            side = fill.get("side")
            if side is not None:
                if not isinstance(side, str):
                    raise ValueError("depth_driven_taker_fills side must be a string")
                if side != side.strip():
                    raise ValueError("depth_driven_taker_fills side must be canonical")
                if side not in {"buy", "sell"}:
                    raise ValueError("depth_driven_taker_fills side must be buy or sell")
            normalised_fill_numbers: dict[str, float] = {}
            for numeric_field in ("requested_quantity", "filled_quantity", "filled_notional", "residual_quantity"):
                numeric_value = fill.get(numeric_field)
                if numeric_value is not None:
                    normalised_value = _normalise_finite_float(
                        f"depth_driven_taker_fills {numeric_field}", numeric_value
                    )
                    if numeric_field == "requested_quantity" and normalised_value <= 0:
                        raise ValueError(f"depth_driven_taker_fills {numeric_field} must be a positive number")
                    if numeric_field in {"filled_quantity", "filled_notional", "residual_quantity"} and normalised_value < 0:
                        raise ValueError(f"depth_driven_taker_fills {numeric_field} must be a non-negative number")
                    normalised_fill_numbers[numeric_field] = normalised_value
            for numeric_field in ("vwap", "slippage_bps"):
                numeric_value = fill.get(numeric_field)
                if numeric_value is not None:
                    normalised_value = _normalise_finite_float(
                        f"depth_driven_taker_fills {numeric_field}", numeric_value
                    )
                    if numeric_field == "vwap" and normalised_value <= 0:
                        raise ValueError(f"depth_driven_taker_fills {numeric_field} must be a positive number")
                    normalised_fill_numbers[numeric_field] = normalised_value
            consumed_levels = fill.get("consumed_levels")
            consumed_quantity = 0.0
            consumed_notional = 0.0
            if consumed_levels is not None:
                if not isinstance(consumed_levels, list):
                    raise ValueError("depth_driven_taker_fills consumed_levels must be a list")
                for level in consumed_levels:
                    if not isinstance(level, Mapping):
                        raise ValueError("depth_driven_taker_fills consumed_levels entries must be mappings")
                    unknown_level_fields = sorted(set(level) - {"price", "quantity", "notional"})
                    if unknown_level_fields:
                        raise ValueError(
                            "unknown depth_driven_taker_fills consumed_levels field: "
                            + ", ".join(unknown_level_fields)
                        )
                    normalised_level: dict[str, float] = {}
                    for numeric_field in ("price", "quantity", "notional"):
                        numeric_value = level.get(numeric_field)
                        if numeric_value is None:
                            raise ValueError(
                                f"depth_driven_taker_fills consumed_levels {numeric_field} must be a number"
                            )
                        normalised_value = _normalise_finite_float(
                            f"depth_driven_taker_fills consumed_levels {numeric_field}", numeric_value
                        )
                        if normalised_value <= 0:
                            raise ValueError(
                                f"depth_driven_taker_fills consumed_levels {numeric_field} "
                                "must be a positive number"
                            )
                        normalised_level[numeric_field] = normalised_value
                    expected_level_notional = normalised_level["price"] * normalised_level["quantity"]
                    if not math.isclose(
                        normalised_level["notional"], expected_level_notional, rel_tol=1e-12, abs_tol=1e-9
                    ):
                        raise ValueError(
                            "depth_driven_taker_fills consumed_levels notional must equal price * quantity"
                        )
                    consumed_quantity += normalised_level["quantity"]
                    consumed_notional += normalised_level["notional"]
            complete = fill.get("complete", False)
            if not isinstance(complete, bool):
                raise ValueError("depth_driven_taker_fills complete must be a boolean")
            if complete:
                required_complete_fields = {
                    "requested_quantity",
                    "filled_quantity",
                    "filled_notional",
                    "residual_quantity",
                    "vwap",
                    "consumed_levels",
                }
                missing_complete_fields = sorted(required_complete_fields - set(fill))
                if missing_complete_fields:
                    raise ValueError(
                        "complete depth_driven_taker_fills require " + ", ".join(missing_complete_fields)
                    )
                if not consumed_levels:
                    raise ValueError("complete depth_driven_taker_fills require consumed_levels")
                requested_quantity = normalised_fill_numbers["requested_quantity"]
                filled_quantity = normalised_fill_numbers["filled_quantity"]
                residual_quantity = normalised_fill_numbers["residual_quantity"]
                filled_notional = normalised_fill_numbers["filled_notional"]
                vwap = normalised_fill_numbers["vwap"]
                if residual_quantity != 0.0:
                    raise ValueError("complete depth_driven_taker_fills must have zero residual quantity")
                if not math.isclose(filled_quantity, requested_quantity, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError(
                        "complete depth_driven_taker_fills filled_quantity must equal requested_quantity"
                    )
                if not math.isclose(filled_quantity, consumed_quantity, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError(
                        "depth_driven_taker_fills filled_quantity must equal consumed level quantity"
                    )
                if not math.isclose(filled_notional, consumed_notional, rel_tol=1e-12, abs_tol=1e-9):
                    raise ValueError(
                        "depth_driven_taker_fills filled_notional must equal consumed level notional"
                    )
                expected_vwap = filled_notional / filled_quantity
                if not math.isclose(vwap, expected_vwap, rel_tol=1e-12, abs_tol=1e-9):
                    raise ValueError("depth_driven_taker_fills vwap must equal filled_notional / filled_quantity")
                complete_fill_count += 1
            else:
                required_incomplete_fields = {
                    "requested_quantity",
                    "filled_quantity",
                    "residual_quantity",
                }
                missing_incomplete_fields = sorted(
                    field
                    for field in required_incomplete_fields
                    if field not in normalised_fill_numbers
                )
                if missing_incomplete_fields:
                    raise ValueError(
                        "incomplete depth_driven_taker_fills require "
                        + "filled_quantity, residual_quantity, requested_quantity"
                    )
                requested_quantity = normalised_fill_numbers["requested_quantity"]
                filled_quantity = normalised_fill_numbers["filled_quantity"]
                residual_quantity = normalised_fill_numbers["residual_quantity"]
                if not math.isclose(
                    filled_quantity + residual_quantity,
                    requested_quantity,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        "incomplete depth_driven_taker_fills filled plus residual must equal requested_quantity"
                    )
                if residual_quantity <= 0.0:
                    raise ValueError("incomplete depth_driven_taker_fills must have positive residual quantity")
                if "filled_notional" in normalised_fill_numbers and consumed_levels is not None:
                    filled_notional = normalised_fill_numbers["filled_notional"]
                    if not math.isclose(
                        filled_notional,
                        consumed_notional,
                        rel_tol=1e-12,
                        abs_tol=1e-9,
                    ):
                        raise ValueError(
                            "depth_driven_taker_fills filled_notional must equal consumed level notional"
                        )
                if consumed_levels is not None and not math.isclose(
                    filled_quantity,
                    consumed_quantity,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        "depth_driven_taker_fills filled_quantity must equal consumed level quantity"
                    )
                incomplete_filled_quantity += filled_quantity
                incomplete_residual_quantity += residual_quantity
        incomplete_fill_count = fill_count - complete_fill_count
        depth_driven_taker_met = fill_count > 0 and incomplete_fill_count == 0
    else:
        raise ValueError("depth_driven_taker_fills must be a list")

    passive_maker_fills = manifest.get("passive_maker_fills")
    passive_maker_fill_count = 0
    passive_maker_complete_fill_count = 0
    passive_maker_incomplete_fill_count = 0
    passive_maker_missing_evidence_count = 0
    passive_maker_malformed_evidence_count = 0
    passive_maker_queue_met = True
    if passive_maker_fills is not None:
        if not isinstance(passive_maker_fills, list):
            raise ValueError("passive_maker_fills must be a list")
        passive_maker_fill_count = len(passive_maker_fills)
        for fill in passive_maker_fills:
            if not isinstance(fill, Mapping):
                raise ValueError("passive_maker_fills entries must be mappings")
            allowed_fill_fields = {
                "complete",
                "requested_quantity",
                "filled_quantity",
                "residual_quantity",
                "queue_ahead_initial",
                "queue_ahead_remaining",
                "maker_status",
                "touch_timestamp",
                "first_fill_timestamp",
                "last_fill_timestamp",
                "fill_id",
            }
            unknown_fill_fields = sorted(set(fill) - allowed_fill_fields)
            if unknown_fill_fields:
                raise ValueError("unknown passive_maker_fills field: " + ", ".join(unknown_fill_fields))
            complete = fill.get("complete", False)
            if not isinstance(complete, bool):
                raise ValueError("passive_maker_fills complete must be a boolean")
            required_evidence_fields = (
                "queue_ahead_initial",
                "queue_ahead_remaining",
                "maker_status",
                "touch_timestamp",
                "first_fill_timestamp",
                "last_fill_timestamp",
                "fill_id",
            )
            missing_evidence = [
                field
                for field in required_evidence_fields
                if fill.get(field) is None or (_is_exact_string(fill.get(field)) and not fill.get(field).strip())
            ]
            malformed_evidence = False
            for numeric_field in (
                "requested_quantity",
                "filled_quantity",
                "residual_quantity",
                "queue_ahead_initial",
                "queue_ahead_remaining",
            ):
                numeric_value = fill.get(numeric_field)
                if numeric_value is not None:
                    try:
                        normalised_value = _normalise_finite_float(
                            f"passive_maker_fills {numeric_field}", numeric_value
                        )
                    except ValueError:
                        malformed_evidence = True
                        break
                    if normalised_value < 0.0:
                        malformed_evidence = True
                        break
                    if numeric_field == "requested_quantity" and normalised_value <= 0.0:
                        malformed_evidence = True
                        break
            for timestamp_field in ("touch_timestamp", "first_fill_timestamp", "last_fill_timestamp"):
                timestamp_value = fill.get(timestamp_field)
                if timestamp_value is not None and (
                    not _is_exact_string(timestamp_value) or not _is_canonical_utc_timestamp(timestamp_value)
                ):
                    malformed_evidence = True
            maker_status = fill.get("maker_status")
            if maker_status is not None and maker_status not in {"filled", "partial", "no_fill", "expired", "cancelled_replaced"}:
                malformed_evidence = True
            if complete and missing_evidence:
                passive_maker_missing_evidence_count += 1
                passive_maker_incomplete_fill_count += 1
                continue
            if malformed_evidence:
                passive_maker_malformed_evidence_count += 1
                passive_maker_incomplete_fill_count += 1
                continue
            if complete:
                passive_maker_complete_fill_count += 1
            else:
                passive_maker_incomplete_fill_count += 1
        passive_maker_queue_met = (
            passive_maker_fill_count > 0
            and passive_maker_missing_evidence_count == 0
            and passive_maker_malformed_evidence_count == 0
        )

    reasons: list[str] = []
    if not l2_tick_coverage_met:
        reasons.append("l2_tick_coverage_below_threshold")
    reasons.extend(interval_coverage_reasons)
    if not depth_driven_taker_met:
        if fill_count > 0 and incomplete_fill_count > 0:
            reasons.append("depth_driven_taker_incomplete_fill")
        else:
            reasons.append("depth_driven_taker_evidence_missing")
    if passive_maker_fills is not None and not passive_maker_queue_met:
        if passive_maker_missing_evidence_count:
            reasons.append("passive_maker_queue_evidence_missing")
        if passive_maker_malformed_evidence_count:
            reasons.append("passive_maker_queue_evidence_malformed")

    gate = {
        "schema_version": SCHEMA_VERSION,
        "evidence_source": evidence_source,
        "checks": {
            "l2_tick_coverage_met": l2_tick_coverage_met,
            "depth_driven_taker_met": depth_driven_taker_met,
            "passive_maker_queue_met": passive_maker_queue_met,
        },
        "coverage": coverage,
        "reasons": reasons,
    }
    if required_intervals:
        gate["required_intervals"] = required_intervals
    if interval_coverage:
        gate["interval_coverage"] = interval_coverage
    if depth_fills is not None:
        gate["depth_driven_taker"] = {
            "fill_count": fill_count,
            "complete_fill_count": complete_fill_count,
            "incomplete_fill_count": incomplete_fill_count,
            "incomplete_filled_quantity": incomplete_filled_quantity,
            "incomplete_residual_quantity": incomplete_residual_quantity,
        }
    if passive_maker_fills is not None:
        gate["passive_maker_queue"] = {
            "fill_count": passive_maker_fill_count,
            "complete_fill_count": passive_maker_complete_fill_count,
            "incomplete_fill_count": passive_maker_incomplete_fill_count,
            "missing_evidence_count": passive_maker_missing_evidence_count,
            "malformed_evidence_count": passive_maker_malformed_evidence_count,
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
