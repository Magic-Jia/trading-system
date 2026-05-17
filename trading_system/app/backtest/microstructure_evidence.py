from __future__ import annotations

import argparse
import json
import math
import re
import statistics
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
L2_REPLAY_SCHEMA_VERSION = "l2_order_book_replay_report.v1"
LONGITUDINAL_L2_REPLAY_CALIBRATION_SCHEMA_VERSION = (
    "longitudinal_l2_replay_calibration_report.v1"
)
_L2_EVENT_TYPES = frozenset(("snapshot", "update"))
_L2_REPLAY_REASON_CODES = frozenset(
    (
        "crossed_book",
        "duplicate_level",
        "duplicate_sequence",
        "invalid_event_type",
        "invalid_price",
        "invalid_quantity",
        "invalid_sequence",
        "malformed_event",
        "malformed_jsonl",
        "malformed_level",
        "non_canonical_timestamp",
        "out_of_order_sequence",
        "sequence_gap",
        "snapshot_missing",
        "stale_replay_data",
        "symbol_mismatch",
        "venue_mismatch",
    )
)
_L2_REPLAY_REPORT_FIELDS = frozenset(
    (
        "schema_version",
        "venue",
        "symbol",
        "best_bid",
        "best_ask",
        "bid_level_count",
        "ask_level_count",
        "gap_detected",
        "crossed_book",
        "first_sequence",
        "last_sequence",
        "first_timestamp",
        "last_timestamp",
        "session_id",
        "reason_codes",
    )
)
CROSS_SOURCE_PARITY_SCHEMA_VERSION = "cross_source_market_execution_parity.v1"
_DEFAULT_PARITY_THRESHOLDS = {
    "max_mid_bps_diff": 5.0,
    "max_spread_bps_diff": 5.0,
    "volume_diff_ratio": 0.10,
    "latency_diff_ms": 100.0,
}



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


def _parse_canonical_timestamp(value: str) -> datetime:
    if not _is_canonical_utc_timestamp(value):
        raise ValueError("non_canonical_timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _normalise_parity_thresholds(thresholds: Mapping[str, Any] | None) -> dict[str, float]:
    parsed = dict(_DEFAULT_PARITY_THRESHOLDS)
    if thresholds is None:
        return parsed
    if not isinstance(thresholds, Mapping):
        raise ValueError("thresholds must be an object")
    unknown_fields = sorted(set(thresholds) - set(parsed))
    if unknown_fields:
        raise ValueError("unknown cross_source_parity threshold: " + ", ".join(unknown_fields))
    for key, value in thresholds.items():
        parsed[key] = _normalise_non_negative_float(f"thresholds {key}", value)
    return parsed


def _normalise_parity_record(
    raw_record: Any,
    *,
    index: int,
    max_sample_age_ms: float | None,
) -> dict[str, Any]:
    if not isinstance(raw_record, Mapping):
        raise ValueError("parity_record_not_object")
    unknown_fields = sorted(
        set(raw_record)
        - {
            "source",
            "venue",
            "symbol",
            "interval",
            "timestamp",
            "received_at",
            "bid",
            "ask",
            "last",
            "volume",
            "latency_ms",
        }
    )
    if unknown_fields:
        raise ValueError("unknown_parity_record_field: " + ", ".join(unknown_fields))

    source = _normalise_canonical_string(f"records[{index}] source", raw_record.get("source"))
    venue = _normalise_canonical_string(f"records[{index}] venue", raw_record.get("venue"))
    symbol = _normalise_canonical_string(f"records[{index}] symbol", raw_record.get("symbol"))
    timestamp = _normalise_canonical_string(f"records[{index}] timestamp", raw_record.get("timestamp"))
    parsed_timestamp = _parse_canonical_timestamp(timestamp)
    interval_input = raw_record.get("interval")
    interval = None if interval_input is None else _normalise_canonical_string(f"records[{index}] interval", interval_input)
    if interval is not None and not _path_component_is_safe(interval):
        raise ValueError("invalid_interval")

    try:
        bid = _normalise_positive_float(f"records[{index}] bid", raw_record.get("bid"))
        ask = _normalise_positive_float(f"records[{index}] ask", raw_record.get("ask"))
        last = _normalise_positive_float(f"records[{index}] last", raw_record.get("last"))
    except ValueError as exc:
        raise ValueError("invalid_price") from exc
    if bid >= ask:
        raise ValueError("crossed_quote")
    try:
        volume = _normalise_non_negative_float(f"records[{index}] volume", raw_record.get("volume"))
    except ValueError as exc:
        raise ValueError("invalid_volume") from exc
    latency_input = raw_record.get("latency_ms")
    latency_ms = None
    if latency_input is not None:
        latency_ms = _normalise_non_negative_float(f"records[{index}] latency_ms", latency_input)

    received_at_input = raw_record.get("received_at")
    if received_at_input is not None:
        received_at = _normalise_canonical_string(f"records[{index}] received_at", received_at_input)
        parsed_received_at = _parse_canonical_timestamp(received_at)
        if parsed_received_at < parsed_timestamp:
            raise ValueError("received_before_timestamp")
        if max_sample_age_ms is not None:
            age_ms = (parsed_received_at - parsed_timestamp).total_seconds() * 1000.0
            if age_ms > max_sample_age_ms:
                raise ValueError("stale_sample")

    return {
        "source": source,
        "venue": venue,
        "symbol": symbol,
        "interval": interval,
        "timestamp": timestamp,
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": volume,
        "latency_ms": latency_ms,
    }


def _max_ratio(values: list[float]) -> float | None:
    if not values:
        return None
    lowest = min(values)
    highest = max(values)
    if highest == 0.0:
        return 0.0
    return (highest - lowest) / highest


def build_cross_source_parity_report(
    records: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    thresholds: Mapping[str, Any] | None = None,
    min_overlap_count: int = 2,
    max_sample_age_ms: float | None = None,
    allow_mixed_symbol_venue: bool = False,
) -> dict[str, Any]:
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be a list or tuple")
    if isinstance(min_overlap_count, bool) or not isinstance(min_overlap_count, int) or min_overlap_count < 1:
        raise ValueError("min_overlap_count must be a positive integer")
    max_age = None if max_sample_age_ms is None else _normalise_non_negative_float("max_sample_age_ms", max_sample_age_ms)
    parity_thresholds = _normalise_parity_thresholds(thresholds)

    normalised = [
        _normalise_parity_record(record, index=index, max_sample_age_ms=max_age)
        for index, record in enumerate(records)
    ]
    sources = sorted({record["source"] for record in normalised})
    venues = sorted({record["venue"] for record in normalised})
    symbols = sorted({record["symbol"] for record in normalised})
    if len(symbols) > 1 and not allow_mixed_symbol_venue:
        raise ValueError("mixed_symbol")
    if len(venues) > 1 and not allow_mixed_symbol_venue:
        raise ValueError("mixed_venue")

    identities: set[tuple[str, str, str, str | None, str]] = set()
    for record in normalised:
        identity = (
            record["source"].casefold(),
            record["venue"].casefold(),
            record["symbol"].casefold(),
            record["interval"].casefold() if isinstance(record["interval"], str) else None,
            record["timestamp"],
        )
        if identity in identities:
            raise ValueError("duplicate_source_interval_identity")
        identities.add(identity)

    by_timestamp: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in normalised:
        by_timestamp.setdefault((record["venue"], record["symbol"], record["timestamp"]), []).append(record)

    missing_source_intervals: list[dict[str, str]] = []
    matched_groups: list[list[dict[str, Any]]] = []
    for venue, symbol, timestamp in sorted(by_timestamp):
        rows = by_timestamp[(venue, symbol, timestamp)]
        present_sources = {row["source"] for row in rows}
        missing_sources = [source for source in sources if source not in present_sources]
        if missing_sources:
            for source in missing_sources:
                missing_source_intervals.append(
                    {
                        "source": source,
                        "venue": venue,
                        "symbol": symbol,
                        "timestamp": timestamp,
                    }
                )
            continue
        matched_groups.append(rows)

    max_mid_bps_diff = 0.0
    max_spread_bps_diff = 0.0
    volume_diff_ratio = 0.0
    latency_diff_ms: float | None = None
    for rows in matched_groups:
        mids = [(row["bid"] + row["ask"]) / 2.0 for row in rows]
        spreads = [row["ask"] - row["bid"] for row in rows]
        volumes = [row["volume"] for row in rows]
        mid_denominator = max(mids)
        if mid_denominator > 0.0:
            max_mid_bps_diff = max(max_mid_bps_diff, ((max(mids) - min(mids)) / mid_denominator) * 10_000.0)
            max_spread_bps_diff = max(
                max_spread_bps_diff,
                ((max(spreads) - min(spreads)) / mid_denominator) * 10_000.0,
            )
        ratio = _max_ratio(volumes)
        if ratio is not None:
            volume_diff_ratio = max(volume_diff_ratio, ratio)
        latencies = [row["latency_ms"] for row in rows if row["latency_ms"] is not None]
        if len(latencies) >= 2:
            diff = max(latencies) - min(latencies)
            latency_diff_ms = diff if latency_diff_ms is None else max(latency_diff_ms, diff)

    reason_codes: list[str] = []
    if missing_source_intervals:
        reason_codes.append("missing_source_interval")
    if len(matched_groups) < min_overlap_count:
        reason_codes.append("insufficient_overlap")
    if max_mid_bps_diff > parity_thresholds["max_mid_bps_diff"]:
        reason_codes.append("mid_price_drift")
    if max_spread_bps_diff > parity_thresholds["max_spread_bps_diff"]:
        reason_codes.append("spread_drift")
    if volume_diff_ratio > parity_thresholds["volume_diff_ratio"]:
        reason_codes.append("volume_drift")
    if latency_diff_ms is not None and latency_diff_ms > parity_thresholds["latency_diff_ms"]:
        reason_codes.append("latency_drift")

    return {
        "schema_version": CROSS_SOURCE_PARITY_SCHEMA_VERSION,
        "venue": venues[0] if len(venues) == 1 else None,
        "symbol": symbols[0] if len(symbols) == 1 else None,
        "source_count": len(sources),
        "matched_timestamp_count": len(matched_groups),
        "missing_source_intervals": missing_source_intervals,
        "max_mid_bps_diff": max_mid_bps_diff,
        "max_spread_bps_diff": max_spread_bps_diff,
        "volume_diff_ratio": volume_diff_ratio,
        "latency_diff_ms": latency_diff_ms,
        "drift_status": "hold" if reason_codes else "pass",
        "reason_codes": reason_codes,
        "thresholds": parity_thresholds,
    }


def _empty_l2_replay_report(
    *,
    venue: str,
    symbol: str,
    reason_codes: list[str] | None = None,
    gap_detected: bool = False,
    crossed_book: bool = False,
    first_sequence: int | None = None,
    last_sequence: int | None = None,
    first_timestamp: str | None = None,
    last_timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": L2_REPLAY_SCHEMA_VERSION,
        "venue": venue,
        "symbol": symbol,
        "best_bid": None,
        "best_ask": None,
        "bid_level_count": 0,
        "ask_level_count": 0,
        "gap_detected": gap_detected,
        "crossed_book": crossed_book,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "reason_codes": reason_codes or [],
    }


def _l2_replay_report(
    *,
    venue: str,
    symbol: str,
    bids: dict[float, float],
    asks: dict[float, float],
    first_sequence: int | None,
    last_sequence: int | None,
    first_timestamp: str | None,
    last_timestamp: str | None,
    reason_codes: list[str] | None = None,
    gap_detected: bool = False,
    crossed_book: bool = False,
) -> dict[str, Any]:
    if reason_codes:
        return _empty_l2_replay_report(
            venue=venue,
            symbol=symbol,
            reason_codes=reason_codes,
            gap_detected=gap_detected,
            crossed_book=crossed_book,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
        )
    best_bid = max(bids) if bids else None
    best_ask = min(asks) if asks else None
    return {
        "schema_version": L2_REPLAY_SCHEMA_VERSION,
        "venue": venue,
        "symbol": symbol,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_level_count": len(bids),
        "ask_level_count": len(asks),
        "gap_detected": gap_detected,
        "crossed_book": crossed_book,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "reason_codes": [],
    }


def _normalise_l2_sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid_sequence")
    if value < 0:
        raise ValueError("invalid_sequence")
    return value


def _normalise_l2_event_row(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed_jsonl") from exc
    if not isinstance(value, Mapping):
        raise ValueError("malformed_event")
    return value


def _normalise_l2_level(level: Any, *, side: str, seen_prices: set[float]) -> tuple[float, float]:
    if isinstance(level, Mapping):
        unknown_fields = sorted(set(level) - {"price", "quantity"})
        if unknown_fields:
            raise ValueError("malformed_level")
        price_input = level.get("price")
        quantity_input = level.get("quantity")
    elif isinstance(level, (list, tuple)) and len(level) == 2:
        price_input, quantity_input = level
    else:
        raise ValueError("malformed_level")

    try:
        price = _normalise_positive_float(f"{side} price", price_input)
    except ValueError as exc:
        raise ValueError("invalid_price") from exc
    try:
        quantity = _normalise_non_negative_float(f"{side} quantity", quantity_input)
    except ValueError as exc:
        raise ValueError("invalid_quantity") from exc
    if price in seen_prices:
        raise ValueError("duplicate_level")
    seen_prices.add(price)
    return price, quantity


def _normalise_l2_levels(event: Mapping[str, Any], *, side: str) -> list[tuple[float, float]]:
    value = event.get(side, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("malformed_level")
    seen_prices: set[float] = set()
    return [_normalise_l2_level(level, side=side, seen_prices=seen_prices) for level in value]


def replay_l2_order_book(
    events: list[Mapping[str, Any] | str] | tuple[Mapping[str, Any] | str, ...],
    *,
    venue: str,
    symbol: str,
) -> dict[str, Any]:
    """Replay deterministic L2 snapshot/update events into visible book diagnostics.

    The replay is deliberately offline-only: input is Python mappings or JSONL
    strings, and malformed evidence returns a fail-closed machine-readable
    report instead of a partially trusted book state.
    """

    venue = _normalise_canonical_string("venue", venue)
    symbol = _normalise_canonical_string("symbol", symbol)
    if not isinstance(events, (list, tuple)):
        raise ValueError("events must be a list or tuple")

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    first_sequence: int | None = None
    last_sequence: int | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    expected_next_sequence: int | None = None
    snapshot_seen = False

    for raw_event in events:
        try:
            event = _normalise_l2_event_row(raw_event)
            event_type = event.get("type")
            if not isinstance(event_type, str) or event_type not in _L2_EVENT_TYPES:
                raise ValueError("invalid_event_type")
            event_venue = _normalise_canonical_string("event venue", event.get("venue"))
            if event_venue != venue:
                raise ValueError("venue_mismatch")
            event_symbol = _normalise_canonical_string("event symbol", event.get("symbol"))
            if event_symbol != symbol:
                raise ValueError("symbol_mismatch")
            sequence = _normalise_l2_sequence(event.get("sequence"))
            timestamp = _normalise_canonical_string("event timestamp", event.get("timestamp"))
            if not _is_canonical_utc_timestamp(timestamp):
                raise ValueError("non_canonical_timestamp")
            bid_updates = _normalise_l2_levels(event, side="bids")
            ask_updates = _normalise_l2_levels(event, side="asks")
        except ValueError as exc:
            reason = str(exc) or "malformed_event"
            return _l2_replay_report(
                venue=venue,
                symbol=symbol,
                bids=bids,
                asks=asks,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                first_timestamp=first_timestamp,
                last_timestamp=last_timestamp,
                reason_codes=[reason],
                gap_detected=reason == "sequence_gap",
                crossed_book=reason == "crossed_book",
            )

        if first_sequence is None:
            first_sequence = sequence
            first_timestamp = timestamp
        if expected_next_sequence is not None:
            if sequence == last_sequence:
                return _l2_replay_report(
                    venue=venue,
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    first_sequence=first_sequence,
                    last_sequence=last_sequence,
                    first_timestamp=first_timestamp,
                    last_timestamp=last_timestamp,
                    reason_codes=["duplicate_sequence"],
                )
            if last_sequence is not None and sequence < last_sequence:
                return _l2_replay_report(
                    venue=venue,
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    first_sequence=first_sequence,
                    last_sequence=last_sequence,
                    first_timestamp=first_timestamp,
                    last_timestamp=last_timestamp,
                    reason_codes=["out_of_order_sequence"],
                )
            if sequence != expected_next_sequence:
                return _l2_replay_report(
                    venue=venue,
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    first_sequence=first_sequence,
                    last_sequence=last_sequence,
                    first_timestamp=first_timestamp,
                    last_timestamp=last_timestamp,
                    reason_codes=["sequence_gap"],
                    gap_detected=True,
                )
        if event_type == "snapshot":
            bids = {}
            asks = {}
            snapshot_seen = True
        elif not snapshot_seen:
            return _l2_replay_report(
                venue=venue,
                symbol=symbol,
                bids=bids,
                asks=asks,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                first_timestamp=first_timestamp,
                last_timestamp=last_timestamp,
                reason_codes=["snapshot_missing"],
            )

        for price, quantity in bid_updates:
            if quantity == 0.0:
                bids.pop(price, None)
            else:
                bids[price] = quantity
        for price, quantity in ask_updates:
            if quantity == 0.0:
                asks.pop(price, None)
            else:
                asks[price] = quantity

        last_sequence = sequence
        last_timestamp = timestamp
        expected_next_sequence = sequence + 1
        if bids and asks and max(bids) >= min(asks):
            return _l2_replay_report(
                venue=venue,
                symbol=symbol,
                bids=bids,
                asks=asks,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                first_timestamp=first_timestamp,
                last_timestamp=last_timestamp,
                reason_codes=["crossed_book"],
                crossed_book=True,
            )

    return _l2_replay_report(
        venue=venue,
        symbol=symbol,
        bids=bids,
        asks=asks,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
    )


def load_l2_replay_reports_jsonl(path: str | Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"l2 replay reports JSONL line {line_number} must be valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"l2 replay reports JSONL line {line_number} must be an object")
        reports.append(row)
    return reports


def _normalise_non_negative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative int")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _normalise_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _normalise_optional_sequence(name: str, value: Any) -> int | None:
    if value is None:
        return None
    return _normalise_l2_sequence(value)


def _normalise_l2_replay_report(
    raw_report: Any,
    *,
    index: int,
    venue: str,
    symbol: str,
) -> dict[str, Any]:
    prefix = f"l2_replay_reports[{index}]"
    if not isinstance(raw_report, Mapping):
        raise ValueError(f"{prefix} must be an object")
    unknown_fields = sorted(set(raw_report) - _L2_REPLAY_REPORT_FIELDS)
    if unknown_fields:
        raise ValueError(f"unknown {prefix} field: " + ", ".join(unknown_fields))
    required_fields = {
        "schema_version",
        "venue",
        "symbol",
        "bid_level_count",
        "ask_level_count",
        "gap_detected",
        "crossed_book",
        "first_sequence",
        "last_sequence",
        "first_timestamp",
        "last_timestamp",
        "session_id",
        "reason_codes",
    }
    for field in sorted(required_fields):
        if field not in raw_report:
            raise ValueError(f"{prefix} missing required field: {field}")
    schema_version = _normalise_canonical_string(f"{prefix} schema_version", raw_report.get("schema_version"))
    if schema_version != L2_REPLAY_SCHEMA_VERSION:
        raise ValueError(f"{prefix} schema_version must be {L2_REPLAY_SCHEMA_VERSION}")
    report_venue = _normalise_canonical_string(f"{prefix} venue", raw_report.get("venue"))
    if report_venue != venue:
        raise ValueError(f"{prefix} venue mismatch")
    report_symbol = _normalise_canonical_string(f"{prefix} symbol", raw_report.get("symbol"))
    if report_symbol != symbol:
        raise ValueError(f"{prefix} symbol mismatch")
    first_timestamp = _normalise_canonical_string(f"{prefix} first_timestamp", raw_report.get("first_timestamp"))
    if not _is_canonical_utc_timestamp(first_timestamp):
        raise ValueError(f"{prefix} first_timestamp must be canonical")
    last_timestamp = _normalise_canonical_string(f"{prefix} last_timestamp", raw_report.get("last_timestamp"))
    if not _is_canonical_utc_timestamp(last_timestamp):
        raise ValueError(f"{prefix} last_timestamp must be canonical")
    if last_timestamp < first_timestamp:
        raise ValueError(f"{prefix} last_timestamp must not precede first_timestamp")
    session_id = _normalise_canonical_string(f"{prefix} session_id", raw_report.get("session_id"))
    reason_codes_input = raw_report.get("reason_codes")
    if not isinstance(reason_codes_input, list):
        raise ValueError(f"{prefix} reason_codes must be a list")
    reason_codes: list[str] = []
    for reason_index, reason_code in enumerate(reason_codes_input, start=1):
        reason = _normalise_canonical_string(
            f"{prefix} reason_codes[{reason_index}]",
            reason_code,
        )
        if reason not in _L2_REPLAY_REASON_CODES:
            raise ValueError(f"{prefix} unknown replay reason code: {reason}")
        reason_codes.append(reason)
    return {
        "bid_level_count": _normalise_non_negative_int(
            f"{prefix} bid_level_count", raw_report.get("bid_level_count")
        ),
        "ask_level_count": _normalise_non_negative_int(
            f"{prefix} ask_level_count", raw_report.get("ask_level_count")
        ),
        "gap_detected": _normalise_bool(f"{prefix} gap_detected", raw_report.get("gap_detected")),
        "crossed_book": _normalise_bool(f"{prefix} crossed_book", raw_report.get("crossed_book")),
        "first_sequence": _normalise_optional_sequence(f"{prefix} first_sequence", raw_report.get("first_sequence")),
        "last_sequence": _normalise_optional_sequence(f"{prefix} last_sequence", raw_report.get("last_sequence")),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "session_id": session_id,
        "reason_codes": reason_codes,
    }


def build_longitudinal_l2_replay_calibration_report(
    reports: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    venue: str,
    symbol: str,
    generated_at: str,
    min_samples: int = 30,
    required_session_count: int = 1,
    max_gap_rate: float = 0.0,
) -> dict[str, Any]:
    venue = _normalise_canonical_string("venue", venue)
    symbol = _normalise_canonical_string("symbol", symbol)
    generated_at = _normalise_canonical_string("generated_at", generated_at)
    if not _is_canonical_utc_timestamp(generated_at):
        raise ValueError("generated_at must be canonical")
    min_samples = _normalise_non_negative_int("min_samples", min_samples)
    required_session_count = _normalise_non_negative_int(
        "required_session_count", required_session_count
    )
    max_gap_rate = _normalise_coverage_value("max_gap_rate", max_gap_rate)
    assert max_gap_rate is not None
    if not isinstance(reports, (list, tuple)):
        raise ValueError("longitudinal L2 replay samples must be a list or tuple")
    if not reports:
        raise ValueError("longitudinal L2 replay samples must be non-empty")

    normalised_reports = [
        _normalise_l2_replay_report(report, index=index, venue=venue, symbol=symbol)
        for index, report in enumerate(reports, start=1)
    ]
    session_ids: set[str] = set()
    for report in normalised_reports:
        if report["session_id"] in session_ids:
            raise ValueError("duplicate session identity")
        session_ids.add(report["session_id"])

    sample_count = len(normalised_reports)
    session_count = len(session_ids)
    gap_count = sum(1 for report in normalised_reports if report["gap_detected"])
    crossed_book_count = sum(1 for report in normalised_reports if report["crossed_book"])
    stale_count = sum(
        1 for report in normalised_reports if "stale_replay_data" in report["reason_codes"]
    )
    bid_level_counts = [report["bid_level_count"] for report in normalised_reports]
    ask_level_counts = [report["ask_level_count"] for report in normalised_reports]

    reason_codes: list[str] = []
    if sample_count < min_samples:
        reason_codes.append("insufficient_samples")
    if session_count < required_session_count:
        reason_codes.append("missing_sessions")
    gap_rate = gap_count / sample_count
    crossed_book_rate = crossed_book_count / sample_count
    stale_rate = stale_count / sample_count
    if gap_rate > max_gap_rate:
        reason_codes.append("gap_rate_above_threshold")
    if crossed_book_count > 0:
        reason_codes.append("crossed_book_detected")
    if stale_count > 0:
        reason_codes.append("stale_replay_data")

    return {
        "schema_version": LONGITUDINAL_L2_REPLAY_CALIBRATION_SCHEMA_VERSION,
        "venue": venue,
        "symbol": symbol,
        "generated_at": generated_at,
        "sample_count": sample_count,
        "session_count": session_count,
        "gap_rate": gap_rate,
        "crossed_book_rate": crossed_book_rate,
        "stale_rate": stale_rate,
        "median_bid_level_count": float(statistics.median(bid_level_counts)),
        "median_ask_level_count": float(statistics.median(ask_level_counts)),
        "max_bid_level_count": max(bid_level_counts),
        "max_ask_level_count": max(ask_level_counts),
        "first_timestamp": min(report["first_timestamp"] for report in normalised_reports),
        "last_timestamp": max(report["last_timestamp"] for report in normalised_reports),
        "quality_status": "review" if reason_codes else "pass",
        "reason_codes": reason_codes,
    }


def write_longitudinal_l2_replay_calibration_report(
    reports: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    output_dir: str | Path,
    *,
    venue: str,
    symbol: str,
    generated_at: str,
    min_samples: int = 30,
    required_session_count: int = 1,
    max_gap_rate: float = 0.0,
) -> Path:
    output_path = Path(output_dir) / "longitudinal_l2_replay_calibration_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_longitudinal_l2_replay_calibration_report(
        reports,
        venue=venue,
        symbol=symbol,
        generated_at=generated_at,
        min_samples=min_samples,
        required_session_count=required_session_count,
        max_gap_rate=max_gap_rate,
    )
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


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
            "cross_source_parity",
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

    cross_source_input = manifest.get("cross_source_parity")
    cross_source_report = None
    cross_source_parity_met = True
    if cross_source_input is not None:
        if not isinstance(cross_source_input, Mapping):
            raise ValueError("cross_source_parity must be an object")
        unknown_cross_source_fields = sorted(
            set(cross_source_input)
            - {
                "records",
                "thresholds",
                "min_overlap_count",
                "max_sample_age_ms",
                "allow_mixed_symbol_venue",
            }
        )
        if unknown_cross_source_fields:
            raise ValueError("unknown cross_source_parity field: " + ", ".join(unknown_cross_source_fields))
        allow_mixed = cross_source_input.get("allow_mixed_symbol_venue", False)
        if not isinstance(allow_mixed, bool):
            raise ValueError("cross_source_parity allow_mixed_symbol_venue must be a boolean")
        cross_source_report = build_cross_source_parity_report(
            cross_source_input.get("records", []),
            thresholds=cross_source_input.get("thresholds"),
            min_overlap_count=cross_source_input.get("min_overlap_count", 2),
            max_sample_age_ms=cross_source_input.get("max_sample_age_ms"),
            allow_mixed_symbol_venue=allow_mixed,
        )
        cross_source_parity_met = cross_source_report["drift_status"] == "pass"

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
    if cross_source_input is not None and not cross_source_parity_met:
        reasons.append("cross_source_parity_drift")

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
    if cross_source_input is not None:
        gate["checks"]["cross_source_parity_met"] = cross_source_parity_met
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
    if cross_source_report is not None:
        gate["cross_source_parity"] = cross_source_report
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
