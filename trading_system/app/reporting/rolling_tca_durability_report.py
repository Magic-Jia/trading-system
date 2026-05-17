from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from trading_system.app.execution.calibration import PassiveOrderCalibrationRecord, load_calibration_records


SCHEMA_VERSION = "rolling_tca_durability_report.v1"
MODE = "simulated_live"
DURABLE_DECISION = "durable"
INSUFFICIENT_DECISION = "insufficient"
REJECTED_DECISION = "rejected"

DEFAULT_WINDOWS = ("1d", "3d", "7d")
DEFAULT_BUCKET_DIMENSIONS = ("global", "symbol", "setup_type", "maker_taker")
ALLOWED_BUCKET_DIMENSIONS = (*DEFAULT_BUCKET_DIMENSIONS, "session_utc_hour")
DEFAULT_THRESHOLDS = {
    "max_p95_slippage_bps": 5.0,
    "max_p99_slippage_bps": None,
    "max_p95_latency_ms": 1000.0,
    "max_p99_latency_ms": None,
    "max_reject_cancel_rate": None,
    "max_maker_taker_mix_shift": None,
}

REASON_ORDER = (
    "missing_dates",
    "stale_dates",
    "malformed_dates",
    "malformed_records",
    "unknown_bucket_fields",
    "insufficient_bucket_sample_size",
    "rolling_slippage_exceeds_threshold",
    "bucket_latency_regression",
    "maker_taker_mix_shift",
)
HARD_REASONS = {
    "malformed_dates",
    "malformed_records",
    "unknown_bucket_fields",
    "rolling_slippage_exceeds_threshold",
    "bucket_latency_regression",
    "maker_taker_mix_shift",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WINDOW_RE = re.compile(r"^([1-9][0-9]*)d$")
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


def _generated_at(value: str | None) -> str:
    if value is not None:
        _parse_timestamp(value, "generated_at")
        return value
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return parsed.astimezone(UTC)


def _parse_date(value: Any, field: str, malformed: list[dict[str, Any]]) -> date | None:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        malformed.append({"field": field, "reason": "malformed_dates", "value": value})
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        malformed.append({"field": field, "reason": "malformed_dates", "value": value})
        return None


def _date_range(start: date, end: date) -> list[str]:
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _parse_window(value: str, malformed: list[dict[str, Any]]) -> int | None:
    if type(value) is not str:
        malformed.append({"field": "windows", "reason": "malformed_dates", "value": value})
        return None
    match = _WINDOW_RE.fullmatch(value)
    if match is None:
        malformed.append({"field": "windows", "reason": "malformed_dates", "value": value})
        return None
    return int(match.group(1))


def _ordered_reasons(reasons: set[str]) -> list[str]:
    return [reason for reason in REASON_ORDER if reason in reasons]


def _strict_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _strict_optional_thresholds(thresholds: Mapping[str, Any] | None) -> dict[str, float | None]:
    if thresholds is None:
        return dict(DEFAULT_THRESHOLDS)
    if not isinstance(thresholds, Mapping):
        raise ValueError("thresholds must be an object")
    unknown_fields = sorted(set(thresholds) - set(DEFAULT_THRESHOLDS))
    if unknown_fields:
        raise ValueError("unknown rolling tca threshold field: " + ", ".join(unknown_fields))
    parsed = dict(DEFAULT_THRESHOLDS)
    for field, value in thresholds.items():
        if value is None:
            parsed[field] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"thresholds.{field} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0.0:
            raise ValueError(f"thresholds.{field} must be a non-negative finite number")
        parsed[field] = numeric
    return parsed


def _candidate_record_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return [path]
    files = [candidate for candidate in path.rglob("*.jsonl") if candidate.is_file()]
    return sorted(files, key=lambda candidate: candidate.as_posix())


def _load_records_from_paths(input_paths: Sequence[str | Path]) -> tuple[list[PassiveOrderCalibrationRecord], list[dict[str, Any]]]:
    records: list[PassiveOrderCalibrationRecord] = []
    malformed: list[dict[str, Any]] = []
    for raw_path in input_paths:
        for path in _candidate_record_files(Path(raw_path)):
            try:
                records.extend(load_calibration_records(path))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                malformed.append({"path": str(path), "reason": "malformed_records", "detail": str(exc)})
    return records, malformed


def _is_filled(record: PassiveOrderCalibrationRecord) -> bool:
    if (record.filled_qty or 0.0) > 0.0 or (record.filled_notional or 0.0) > 0.0:
        return True
    return record.status in {"filled", "partially_filled", "partial"}


def _is_partial(record: PassiveOrderCalibrationRecord) -> bool:
    if not _is_filled(record):
        return False
    if record.status in {"partially_filled", "partial"}:
        return True
    if record.requested_qty and record.filled_qty is not None:
        return 0.0 < record.filled_qty < record.requested_qty
    if record.requested_notional and record.filled_notional is not None:
        return 0.0 < record.filled_notional < record.requested_notional
    return False


def _is_reject_or_cancel(record: PassiveOrderCalibrationRecord) -> bool:
    return record.status in {"cancelled", "canceled", "expired", "rejected"} or record.cancel_ack_at is not None


def _slippage_bps(record: PassiveOrderCalibrationRecord) -> float | None:
    if record.slippage_bps is not None:
        return record.slippage_bps
    if record.ref_price is None or record.ref_price <= 0.0:
        return None
    fill_price = None
    if record.filled_qty and record.filled_notional:
        fill_price = record.filled_notional / record.filled_qty
    elif record.intended_limit_price > 0.0:
        fill_price = record.intended_limit_price
    if fill_price is None:
        return None
    if record.side == "buy":
        return ((fill_price - record.ref_price) / record.ref_price) * 10_000.0
    return ((record.ref_price - fill_price) / record.ref_price) * 10_000.0


def _latency_ms(record: PassiveOrderCalibrationRecord) -> float:
    if record.latency_ms is not None:
        return record.latency_ms
    return (record.exchange_ack_at - record.submitted_at).total_seconds() * 1000.0


def _fees_funding_bps(record: PassiveOrderCalibrationRecord) -> float | None:
    if not _is_filled(record) or record.filled_notional is None or record.filled_notional <= 0.0:
        return None
    return ((record.fees or 0.0) + (record.funding or 0.0)) / record.filled_notional * 10_000.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = list(values)
    return {
        "sample_count": len(rows),
        "p50": median(rows) if rows else None,
        "p95": _percentile(rows, 0.95),
        "p99": _percentile(rows, 0.99),
    }


def _metrics(records: Sequence[PassiveOrderCalibrationRecord]) -> dict[str, Any]:
    rows = list(records)
    sample_count = len(rows)
    filled = [record for record in rows if _is_filled(record)]
    partial = [record for record in rows if _is_partial(record)]
    maker = [record for record in rows if record.maker_taker == "maker"]
    taker = [record for record in rows if record.maker_taker == "taker"]
    reject_cancel = [record for record in rows if _is_reject_or_cancel(record)]
    slippage = [value for record in filled if (value := _slippage_bps(record)) is not None]
    latency = [_latency_ms(record) for record in rows]
    fee_funding = [value for record in rows if (value := _fees_funding_bps(record)) is not None]
    return {
        "sample_count": sample_count,
        "slippage_bps": _distribution(slippage),
        "latency_ms": _distribution(latency),
        "fill_rate": len(filled) / sample_count if sample_count else None,
        "partial_fill_rate": len(partial) / sample_count if sample_count else None,
        "maker_rate": len(maker) / sample_count if sample_count else None,
        "taker_rate": len(taker) / sample_count if sample_count else None,
        "reject_cancel_rate": len(reject_cancel) / sample_count if sample_count else None,
        "fees_funding_bps": _distribution(fee_funding),
    }


def _bucket_value(record: PassiveOrderCalibrationRecord, dimension: str) -> str:
    if dimension == "global":
        return "all"
    if dimension == "session_utc_hour":
        return f"{record.submitted_at.astimezone(UTC).hour:02d}:00"
    value = getattr(record, dimension)
    if value is None or value == "":
        return "unknown"
    return str(value)


def _bucketed_records(
    records: Sequence[PassiveOrderCalibrationRecord],
    bucket_dimensions: Sequence[str],
) -> list[tuple[dict[str, str], list[PassiveOrderCalibrationRecord]]]:
    buckets: dict[tuple[str, str], list[PassiveOrderCalibrationRecord]] = {}
    for dimension in bucket_dimensions:
        for record in records:
            buckets.setdefault((dimension, _bucket_value(record, dimension)), []).append(record)
    order = {dimension: index for index, dimension in enumerate(bucket_dimensions)}
    return [
        ({"dimension": dimension, "value": value}, rows)
        for (dimension, value), rows in sorted(buckets.items(), key=lambda item: (order[item[0][0]], item[0][1]))
    ]


def _bucket_decision(
    metrics: Mapping[str, Any],
    *,
    min_samples_per_bucket: int,
    thresholds: Mapping[str, float | None],
) -> tuple[str, list[str]]:
    reasons: set[str] = set()
    sample_count = metrics.get("sample_count")
    if sample_count < min_samples_per_bucket:
        reasons.add("insufficient_bucket_sample_size")
    slippage = metrics.get("slippage_bps") if isinstance(metrics.get("slippage_bps"), Mapping) else {}
    latency = metrics.get("latency_ms") if isinstance(metrics.get("latency_ms"), Mapping) else {}
    p95_slippage = slippage.get("p95")
    p99_slippage = slippage.get("p99")
    p95_latency = latency.get("p95")
    p99_latency = latency.get("p99")
    if thresholds["max_p95_slippage_bps"] is not None and p95_slippage is not None and p95_slippage > thresholds["max_p95_slippage_bps"]:
        reasons.add("rolling_slippage_exceeds_threshold")
    if thresholds["max_p99_slippage_bps"] is not None and p99_slippage is not None and p99_slippage > thresholds["max_p99_slippage_bps"]:
        reasons.add("rolling_slippage_exceeds_threshold")
    if thresholds["max_p95_latency_ms"] is not None and p95_latency is not None and p95_latency > thresholds["max_p95_latency_ms"]:
        reasons.add("bucket_latency_regression")
    if thresholds["max_p99_latency_ms"] is not None and p99_latency is not None and p99_latency > thresholds["max_p99_latency_ms"]:
        reasons.add("bucket_latency_regression")
    reject_cancel_rate = metrics.get("reject_cancel_rate")
    if (
        thresholds["max_reject_cancel_rate"] is not None
        and reject_cancel_rate is not None
        and reject_cancel_rate > thresholds["max_reject_cancel_rate"]
    ):
        reasons.add("rolling_slippage_exceeds_threshold")
    max_mix_shift = thresholds["max_maker_taker_mix_shift"]
    if max_mix_shift is not None:
        maker_rate = metrics.get("maker_rate")
        taker_rate = metrics.get("taker_rate")
        if maker_rate is not None and taker_rate is not None and abs(maker_rate - taker_rate) > max_mix_shift:
            reasons.add("maker_taker_mix_shift")
    ordered = _ordered_reasons(reasons)
    if any(reason in HARD_REASONS for reason in ordered):
        return REJECTED_DECISION, ordered
    if ordered:
        return INSUFFICIENT_DECISION, ordered
    return DURABLE_DECISION, []


def build_rolling_tca_durability_report(
    *,
    input_paths: Sequence[str | Path],
    start_date: str,
    end_date: str,
    generated_at: str | None = None,
    windows: Sequence[str] = DEFAULT_WINDOWS,
    min_samples_per_bucket: int = 30,
    bucket_dimensions: Sequence[str] = DEFAULT_BUCKET_DIMENSIONS,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    malformed_inputs: list[dict[str, Any]] = []
    evaluated_at = _generated_at(generated_at)
    start = _parse_date(start_date, "start_date", malformed_inputs)
    end = _parse_date(end_date, "end_date", malformed_inputs)
    if start is not None and end is not None and end < start:
        malformed_inputs.append({"field": "end_date", "reason": "malformed_dates", "value": end_date})
    parsed_windows: list[tuple[str, int]] = []
    for window in windows:
        days = _parse_window(window, malformed_inputs)
        if days is not None:
            parsed_windows.append((window, days))
    if not parsed_windows:
        parsed_windows = [("1d", 1)]
    min_samples = _strict_non_negative_int(min_samples_per_bucket, "min_samples_per_bucket")
    parsed_thresholds = _strict_optional_thresholds(thresholds)
    parsed_bucket_dimensions: list[str] = []
    for index, dimension in enumerate(bucket_dimensions):
        if dimension not in ALLOWED_BUCKET_DIMENSIONS:
            malformed_inputs.append({"field": f"bucket_dimensions[{index}]", "reason": "unknown_bucket_fields", "value": dimension})
            continue
        if dimension not in parsed_bucket_dimensions:
            parsed_bucket_dimensions.append(dimension)
    if not parsed_bucket_dimensions:
        parsed_bucket_dimensions = ["global"]

    rows, record_malformed = _load_records_from_paths(input_paths)
    malformed_inputs.extend(record_malformed)
    has_malformed_records = bool(record_malformed)

    canonical_dates = _date_range(start, end) if start is not None and end is not None and end >= start else []
    records_by_date: dict[str, list[PassiveOrderCalibrationRecord]] = {day: [] for day in canonical_dates}
    stale_dates: set[str] = set()
    for record in rows:
        record_date = record.submitted_at.astimezone(UTC).date().isoformat()
        if record_date in records_by_date:
            records_by_date[record_date].append(record)
        else:
            stale_dates.add(record_date)
    missing_dates = [] if has_malformed_records else [day for day in canonical_dates if not records_by_date.get(day)]

    report_reasons: set[str] = set()
    for item in malformed_inputs:
        reason = item.get("reason")
        if isinstance(reason, str):
            report_reasons.add(reason)
    if missing_dates:
        report_reasons.add("missing_dates")
    if stale_dates:
        report_reasons.add("stale_dates")

    report_windows: list[dict[str, Any]] = []
    for window_name, window_days in parsed_windows:
        if end is None:
            window_start = None
            window_end = None
            window_dates: list[str] = []
        else:
            window_start_date = end - timedelta(days=window_days - 1)
            if start is not None and window_start_date < start:
                window_start_date = start
            window_start = window_start_date.isoformat()
            window_end = end.isoformat()
            window_dates = _date_range(window_start_date, end)
        window_records = [record for day in window_dates for record in records_by_date.get(day, [])]
        window_metrics = _metrics(window_records)
        if has_malformed_records:
            window_decision, window_reasons = REJECTED_DECISION, ["malformed_records"]
        else:
            window_decision, window_reasons = _bucket_decision(
                window_metrics,
                min_samples_per_bucket=min_samples,
                thresholds=parsed_thresholds,
            )
        report_reasons.update(window_reasons)
        bucket_payloads: list[dict[str, Any]] = []
        for bucket, bucket_rows in _bucketed_records(window_records, parsed_bucket_dimensions):
            bucket_metrics = _metrics(bucket_rows)
            bucket_decision, bucket_reasons = _bucket_decision(
                bucket_metrics,
                min_samples_per_bucket=min_samples,
                thresholds=parsed_thresholds,
            )
            report_reasons.update(bucket_reasons)
            bucket_payloads.append(
                {
                    "bucket": bucket,
                    "decision": bucket_decision,
                    "reasons": bucket_reasons,
                    "metrics": bucket_metrics,
                }
            )
        report_windows.append(
            {
                "window": window_name,
                "start_date": window_start,
                "end_date": window_end,
                "dates": window_dates,
                "decision": window_decision,
                "reasons": window_reasons,
                "metrics": window_metrics,
                "buckets": bucket_payloads,
            }
        )

    reasons = _ordered_reasons(report_reasons)
    if any(reason in HARD_REASONS for reason in reasons):
        decision = REJECTED_DECISION
    elif reasons:
        decision = INSUFFICIENT_DECISION
    else:
        decision = DURABLE_DECISION
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "decision": decision,
        "reasons": reasons,
        "generated_at": evaluated_at,
        "input_paths": [str(path) for path in input_paths],
        "canonical_dates": canonical_dates,
        "missing_dates": missing_dates,
        "stale_dates": sorted(stale_dates),
        "malformed_inputs": malformed_inputs,
        "windows": report_windows,
        "bucket_dimensions": parsed_bucket_dimensions,
        "min_samples_per_bucket": min_samples,
        "thresholds": parsed_thresholds,
        "checks": {
            "all_expected_dates_present": not missing_dates,
            "no_stale_dates": not stale_dates,
            "all_records_well_formed": not any(item.get("reason") == "malformed_records" for item in malformed_inputs),
            "all_bucket_fields_known": not any(item.get("reason") == "unknown_bucket_fields" for item in malformed_inputs),
            "all_bucket_windows_sufficiently_sampled": "insufficient_bucket_sample_size" not in reasons,
            "no_threshold_breaches": not any(
                reason in reasons
                for reason in ("rolling_slippage_exceeds_threshold", "bucket_latency_regression", "maker_taker_mix_shift")
            ),
        },
        "caveats": [
            "Simulated-live calibration only; this report performs no real-money or real-exchange side effects.",
            "Consumers should fail closed on rejected decisions and hold on insufficient decisions.",
        ],
    }


def write_rolling_tca_durability_report(
    output_path: str | Path,
    *,
    input_paths: Sequence[str | Path],
    start_date: str,
    end_date: str,
    generated_at: str | None = None,
    windows: Sequence[str] = DEFAULT_WINDOWS,
    min_samples_per_bucket: int = 30,
    bucket_dimensions: Sequence[str] = DEFAULT_BUCKET_DIMENSIONS,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_rolling_tca_durability_report(
        input_paths=input_paths,
        start_date=start_date,
        end_date=end_date,
        generated_at=generated_at,
        windows=windows,
        min_samples_per_bucket=min_samples_per_bucket,
        bucket_dimensions=bucket_dimensions,
        thresholds=thresholds,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
