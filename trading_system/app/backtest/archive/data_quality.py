from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .raw_market import ImportedRawMarketFile, ImportedRawMarketSeries, load_phase1_raw_market_imports, raw_market_series_key

RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION = "raw_market_data_quality_report.v1"
RAW_MARKET_COVERAGE_GAP_TYPES = frozenset({"missing", "duplicate", "overlap", "maintenance", "outage"})


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _interval_key(series: ImportedRawMarketSeries) -> str:
    if series.dataset == "ohlcv" and series.timeframe:
        return f"ohlcv:{series.timeframe}"
    return series.dataset


def _expected_interval(value: Any, key: str) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise ValueError(f"expected interval for {key} must be a timedelta")
    if value.total_seconds() <= 0:
        raise ValueError(f"expected interval for {key} must be positive")
    return value


def _expected_intervals(values: Mapping[str, timedelta] | None) -> dict[str, timedelta]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("expected_intervals must be an object")
    parsed: dict[str, timedelta] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValueError("expected interval key must be canonical")
        parsed[key] = value
    return parsed


def _validate_manifest_interval_scope(series: ImportedRawMarketSeries, intervals: Mapping[str, timedelta]) -> None:
    interval_key = _interval_key(series)
    if interval_key not in intervals or series.dataset != "ohlcv" or not series.timeframe:
        return
    for item in series.files:
        metadata = item.manifest.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        interval = metadata.get("interval")
        if interval is None:
            continue
        if not isinstance(interval, str) or not interval.strip() or interval != interval.strip():
            raise ValueError("raw-market manifest interval must be canonical")
        if interval != series.timeframe:
            raise ValueError("raw-market manifest interval must match series timeframe")


def _canonical_series_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"raw-market {field} must be canonical")
    return value


def _optional_canonical_series_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"raw-market {field} must be canonical")
    return value


def _provenance_sha256(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("raw-market provenance sha256 must be canonical")
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("raw-market provenance sha256 must be lowercase 64-hex")
    return value


def _provenance_file_metadata(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    value = manifest.get("file", {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("raw-market provenance file metadata must be an object")
    return value


def _provenance_path(value: Path, field: str) -> str:
    rendered = str(value)
    if not rendered.strip() or rendered != rendered.strip() or rendered == ".":
        raise ValueError(f"raw-market provenance {field} must be canonical")
    return rendered


def _provenance_timestamp(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"raw-market provenance {field} must be timezone-aware")
    return _utc_timestamp(value)


def _file_raw_market_provenance(item: ImportedRawMarketSeries, file_item: ImportedRawMarketFile) -> dict[str, Any]:
    source = _canonical_series_string(file_item.manifest.get("source"), "provenance source")
    if source != item.exchange:
        raise ValueError("raw-market provenance source must match series exchange")
    return {
        "source": source,
        "exchange": item.exchange,
        "market": item.market,
        "dataset": item.dataset,
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "series_key": item.series_key,
        "coverage_start": _provenance_timestamp(file_item.coverage_start, "coverage_start"),
        "coverage_end": _provenance_timestamp(file_item.coverage_end, "coverage_end"),
        "fetched_at": _provenance_timestamp(file_item.fetched_at, "fetched_at"),
    }


def _coverage_interval_identity(series: ImportedRawMarketSeries, *, gap_type: str) -> dict[str, Any]:
    return {
        "series_key": series.series_key,
        "exchange": series.exchange,
        "market": series.market,
        "dataset": series.dataset,
        "symbol": series.symbol,
        "timeframe": series.timeframe,
        "source": "raw_market_archive",
        "gap_type": gap_type,
    }


def _missing_intervals(series: ImportedRawMarketSeries, expected_interval: timedelta | None) -> list[dict[str, Any]]:
    if expected_interval is None or expected_interval.total_seconds() <= 0 or not series.records:
        return []
    gaps: list[dict[str, Any]] = []
    previous = series.records[0].observed_at
    for record in series.records[1:]:
        delta = record.observed_at - previous
        if delta > expected_interval:
            missing_records = int(delta / expected_interval) - 1
            gaps.append(
                {
                    **_coverage_interval_identity(series, gap_type="missing"),
                    "start": _utc_timestamp(previous + expected_interval),
                    "end": _utc_timestamp(record.observed_at),
                    "missing_records": missing_records,
                }
            )
        previous = record.observed_at
    return gaps


def _coverage_ratio(series: ImportedRawMarketSeries, expected_interval: timedelta | None) -> float:
    if expected_interval is None or expected_interval.total_seconds() <= 0 or not series.files:
        return 1.0 if series.records else 0.0
    start = min(item.coverage_start for item in series.files)
    end = max(item.coverage_end for item in series.files)
    expected = int((end - start) / expected_interval)
    if expected <= 0:
        return 1.0 if series.records else 0.0
    return min(1.0, len(series.records) / expected)


def _series_report(series: ImportedRawMarketSeries, expected_interval: timedelta | None) -> dict[str, Any]:
    series_key = _canonical_series_string(series.series_key, "series_key")
    exchange = _canonical_series_string(series.exchange, "exchange")
    market = _canonical_series_string(series.market, "market")
    dataset = _canonical_series_string(series.dataset, "dataset")
    symbol = _canonical_series_string(series.symbol, "symbol")
    timeframe = _optional_canonical_series_string(series.timeframe, "timeframe")
    expected_series_key = raw_market_series_key(
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    if series_key != expected_series_key:
        raise ValueError("raw-market series_key must match embedded identity")
    missing = _missing_intervals(series, expected_interval)
    files = list(series.files)
    timestamp_counts = Counter(record.observed_at for record in series.records)
    duplicate_observed_at = [
        {
            **_coverage_interval_identity(series, gap_type="duplicate"),
            "start": _utc_timestamp(observed_at),
            "end": _utc_timestamp(observed_at),
            "observed_at": _utc_timestamp(observed_at),
            "duplicate_records": count,
            "count": count,
        }
        for observed_at, count in sorted(timestamp_counts.items())
        if count > 1
    ]
    observed_at_unique = not duplicate_observed_at
    first_observed_at = _utc_timestamp(series.records[0].observed_at) if series.records else None
    last_observed_at = _utc_timestamp(series.records[-1].observed_at) if series.records else None
    coverage_start = min(item.coverage_start for item in files) if files else None
    coverage_end = max(item.coverage_end for item in files) if files else None
    first_record_at = series.records[0].observed_at if series.records else None
    last_record_at = series.records[-1].observed_at if series.records else None
    coverage_start_matches_first = bool(coverage_start is None or first_record_at is None or coverage_start == first_record_at)
    if coverage_end is None or last_record_at is None or expected_interval is None:
        coverage_end_matches_terminal = True
    else:
        coverage_end_matches_terminal = coverage_end == last_record_at + expected_interval
    coverage_alignment = {
        "coverage_start_matches_first_observed_at": coverage_start_matches_first,
        "coverage_end_matches_expected_terminal_boundary": coverage_end_matches_terminal,
    }
    provenance = [
        {
            "raw_market": _file_raw_market_provenance(series, item),
            "manifest_path": _provenance_path(item.manifest_path, "manifest_path"),
            "data_path": _provenance_path(item.data_path, "data_path"),
            "coverage_start": _provenance_timestamp(item.coverage_start, "coverage_start"),
            "coverage_end": _provenance_timestamp(item.coverage_end, "coverage_end"),
            "fetched_at": _provenance_timestamp(item.fetched_at, "fetched_at"),
            "sha256": _provenance_sha256(_provenance_file_metadata(item.manifest).get("sha256")),
        }
        for item in files
    ]
    provenance_missing_sha256_count = sum(1 for item in provenance if not item.get("sha256"))
    provenance_missing_data_path_count = sum(1 for item in provenance if not item.get("data_path"))
    provenance_missing_manifest_path_count = sum(1 for item in provenance if not item.get("manifest_path"))
    provenance_complete = bool(
        provenance
        and provenance_missing_sha256_count == 0
        and provenance_missing_data_path_count == 0
        and provenance_missing_manifest_path_count == 0
    )
    return {
        "series_key": series_key,
        "exchange": exchange,
        "market": market,
        "dataset": dataset,
        "symbol": symbol,
        "timeframe": timeframe,
        "record_count": len(series.records),
        "file_count": len(files),
        "observed_at_unique": observed_at_unique,
        "duplicate_observed_at_count": len(duplicate_observed_at),
        "duplicate_observed_at": duplicate_observed_at,
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
        "coverage_alignment": coverage_alignment,
        "coverage_start": _utc_timestamp(coverage_start) if coverage_start else None,
        "coverage_end": _utc_timestamp(coverage_end) if coverage_end else None,
        "expected_interval_seconds": int(expected_interval.total_seconds()) if expected_interval else None,
        "coverage_ratio": _coverage_ratio(series, expected_interval),
        "missing_intervals": missing,
        "has_missing_intervals": bool(missing),
        "provenance_complete": provenance_complete,
        "provenance_file_count": len(provenance),
        "provenance_missing_sha256_count": provenance_missing_sha256_count,
        "provenance_missing_data_path_count": provenance_missing_data_path_count,
        "provenance_missing_manifest_path_count": provenance_missing_manifest_path_count,
        "provenance": provenance,
    }


def _l2_coverage_ratio_value(report: Mapping[str, Any]) -> float:
    value = report.get("coverage_ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("l2 coverage ratio must be numeric")
    parsed = float(value)
    if not parsed == parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError("l2 coverage_ratio must be finite")
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("l2 coverage_ratio must be between 0 and 1")
    return parsed


def _l2_missing_intervals_value(report: Mapping[str, Any]) -> bool:
    value = report.get("has_missing_intervals")
    if not isinstance(value, bool):
        raise ValueError("l2 has_missing_intervals must be boolean")
    return value


def _l2_canonical_string(report: Mapping[str, Any], field: str) -> str:
    value = report.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"l2 {field} must be canonical")
    return value


def _l2_optional_canonical_string(report: Mapping[str, Any], field: str) -> str | None:
    value = report.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"l2 {field} must be canonical")
    return value


def _l2_optional_non_negative_integer(report: Mapping[str, Any], field: str) -> int | None:
    value = report.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"l2 {field} must be a non-negative integer")
    return value


def _l2_required_interval_string(item: Mapping[str, Any], field: str, *, index: int) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"l2 missing_intervals[{index}].{field} must be canonical")
    return value


def _l2_optional_interval_string(item: Mapping[str, Any], field: str, *, index: int) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"l2 missing_intervals[{index}].{field} must be canonical")
    return value


def _l2_required_positive_integer(item: Mapping[str, Any], field: str, *, index: int) -> int:
    value = item.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"l2 missing_intervals[{index}].{field} must be a positive integer")
    return value


def _l2_dataset(report: Mapping[str, Any]) -> str:
    return _l2_canonical_string(report, "dataset")


def _l2_series_reports(series_reports: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(series_reports, Mapping):
        raise ValueError("l2 series reports must be an object")
    parsed: list[dict[str, Any]] = []
    for key, report in series_reports.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValueError("l2 series report key must be canonical")
        if not isinstance(report, Mapping):
            raise ValueError(f"l2 series report {key} must be an object")
        if any(not isinstance(field, str) or not field.strip() or field != field.strip() for field in report):
            raise ValueError("l2 series report fields must be canonical")
        series_key = _l2_canonical_string(report, "series_key")
        if series_key != key:
            raise ValueError("l2 series report key must match series_key")
        expected_series_key = raw_market_series_key(
            exchange=_l2_canonical_string(report, "exchange"),
            market=_l2_canonical_string(report, "market"),
            dataset=_l2_canonical_string(report, "dataset"),
            symbol=_l2_canonical_string(report, "symbol"),
            timeframe=_l2_optional_canonical_string(report, "timeframe"),
        )
        if series_key != expected_series_key:
            raise ValueError("l2 series_key must match embedded identity")
        for count_field in (
            "record_count",
            "file_count",
            "duplicate_observed_at_count",
            "provenance_file_count",
            "provenance_missing_sha256_count",
            "provenance_missing_data_path_count",
            "provenance_missing_manifest_path_count",
        ):
            _l2_optional_non_negative_integer(report, count_field)
        parsed.append(dict(report))
    return parsed


def _l2_missing_intervals(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = report.get("missing_intervals", [])
    if not isinstance(value, list):
        raise ValueError("l2 missing_intervals must be a list")
    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    expected_series_key = _l2_canonical_string(report, "series_key")
    expected_exchange = _l2_canonical_string(report, "exchange")
    expected_market = _l2_canonical_string(report, "market")
    expected_dataset = _l2_canonical_string(report, "dataset")
    expected_symbol = _l2_canonical_string(report, "symbol")
    expected_timeframe = _l2_optional_canonical_string(report, "timeframe")
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"l2 missing_intervals[{index}] must be an object")
        if any(not isinstance(field, str) or not field.strip() or field != field.strip() for field in item):
            raise ValueError(f"l2 missing_intervals[{index}] fields must be canonical")
        series_key = _l2_required_interval_string(item, "series_key", index=index)
        exchange = _l2_required_interval_string(item, "exchange", index=index)
        market = _l2_required_interval_string(item, "market", index=index)
        dataset = _l2_required_interval_string(item, "dataset", index=index)
        symbol = _l2_required_interval_string(item, "symbol", index=index)
        timeframe = item.get("timeframe")
        if timeframe is not None and (
            not isinstance(timeframe, str) or not timeframe.strip() or timeframe != timeframe.strip()
        ):
            raise ValueError(f"l2 missing_intervals[{index}].timeframe must be canonical")
        if (
            series_key != expected_series_key
            or exchange != expected_exchange
            or market != expected_market
            or dataset != expected_dataset
            or symbol != expected_symbol
            or timeframe != expected_timeframe
        ):
            raise ValueError(f"l2 missing_intervals[{index}].series_key must match series identity")
        source = _l2_required_interval_string(item, "source", index=index)
        gap_type = _l2_required_interval_string(item, "gap_type", index=index)
        if gap_type not in RAW_MARKET_COVERAGE_GAP_TYPES:
            raise ValueError(f"l2 missing_intervals[{index}].gap_type must be one of: duplicate, maintenance, missing, outage, overlap")
        start = _l2_required_interval_string(item, "start", index=index)
        end = _l2_required_interval_string(item, "end", index=index)
        claim_key = (series_key, gap_type, start, end)
        if claim_key in seen:
            raise ValueError(f"l2 missing_intervals[{index}] duplicates an earlier interval claim")
        seen.add(claim_key)
        normalized = {
            "series_key": series_key,
            "exchange": exchange,
            "market": market,
            "dataset": dataset,
            "symbol": symbol,
            "timeframe": timeframe,
            "source": source,
            "gap_type": gap_type,
            "start": start,
            "end": end,
        }
        if gap_type == "missing":
            normalized["missing_records"] = _l2_required_positive_integer(item, "missing_records", index=index)
        elif gap_type == "duplicate":
            normalized["duplicate_records"] = _l2_required_positive_integer(item, "duplicate_records", index=index)
        elif gap_type == "overlap":
            normalized["overlap_records"] = _l2_required_positive_integer(item, "overlap_records", index=index)
        else:
            reason = _l2_optional_interval_string(item, "reason", index=index)
            if reason is not None:
                normalized["reason"] = reason
        parsed.append(normalized)
    return parsed


def _l2_required_coverage(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("l2 required_coverage must be numeric")
    parsed = float(value)
    if not parsed == parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError("l2 required_coverage must be finite")
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError("l2 required_coverage must be between 0 and 1")
    return parsed


def _l2_tick_coverage(series_reports: Mapping[str, Mapping[str, Any]], required_coverage: float) -> dict[str, Any]:
    required_coverage_value = _l2_required_coverage(required_coverage)
    l2_reports = [report for report in _l2_series_reports(series_reports) if _l2_dataset(report) in {"order-book", "trades"}]
    if not l2_reports:
        return {
            "required_coverage": required_coverage_value,
            "coverage_ratio": 0.0,
            "met": False,
            "missing_by_symbol_timeframe": [],
            "series": [],
        }
    coverage_ratio = min(_l2_coverage_ratio_value(report) for report in l2_reports)
    validated_series = [_l2_canonical_string(report, "series_key") for report in l2_reports]
    missing = []
    for report in l2_reports:
        report_coverage_ratio = _l2_coverage_ratio_value(report)
        if report_coverage_ratio < required_coverage_value or _l2_missing_intervals_value(report):
            missing.append(
                {
                    "symbol": _l2_canonical_string(report, "symbol"),
                    "dataset": _l2_canonical_string(report, "dataset"),
                    "timeframe": _l2_optional_canonical_string(report, "timeframe"),
                    "coverage_ratio": report_coverage_ratio,
                    "missing_intervals": _l2_missing_intervals(report),
                }
            )
    return {
        "required_coverage": required_coverage_value,
        "coverage_ratio": coverage_ratio,
        "met": coverage_ratio >= required_coverage_value and not missing,
        "missing_by_symbol_timeframe": missing,
        "series": validated_series,
    }


def build_raw_market_data_quality_report(
    archive_root: str | Path,
    *,
    expected_intervals: Mapping[str, timedelta] | None = None,
    required_l2_coverage: float = 0.99,
) -> dict[str, Any]:
    intervals = _expected_intervals(expected_intervals)
    series = load_phase1_raw_market_imports(archive_root)
    for item in series:
        _validate_manifest_interval_scope(item, intervals)
    reports = {
        item.series_key: _series_report(item, _expected_interval(intervals.get(_interval_key(item)), _interval_key(item)))
        for item in series
    }
    series_with_missing = sum(1 for report in reports.values() if report["has_missing_intervals"])
    series_with_duplicate_observed_at = sum(1 for report in reports.values() if not report["observed_at_unique"])
    series_with_coverage_alignment_issues = sum(
        1 for report in reports.values() if not all(report["coverage_alignment"].values())
    )
    series_with_incomplete_provenance = sum(1 for report in reports.values() if not report["provenance_complete"])
    l2 = _l2_tick_coverage(reports, required_l2_coverage)
    reasons: list[str] = []
    if series_with_missing:
        reasons.append("raw_market_missing_intervals")
    if series_with_duplicate_observed_at:
        reasons.append("raw_market_duplicate_observed_at")
    if series_with_coverage_alignment_issues:
        reasons.append("raw_market_coverage_alignment_mismatch")
    if series_with_incomplete_provenance:
        reasons.append("raw_market_incomplete_provenance")
    if not l2["met"]:
        reasons.append("l2_coverage_below_threshold")
    decision = "ready_for_live_promotion_review" if not reasons else "reject_for_live_promotion"
    return {
        "schema_version": RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION,
        "archive_root": str(Path(archive_root)),
        "summary": {
            "series_count": len(reports),
            "series_with_missing_intervals": series_with_missing,
            "series_with_duplicate_observed_at": series_with_duplicate_observed_at,
            "series_with_coverage_alignment_issues": series_with_coverage_alignment_issues,
            "series_with_incomplete_provenance": series_with_incomplete_provenance,
            "l2_coverage_met": l2["met"],
        },
        "series": reports,
        "l2_tick_coverage": l2,
        "promotion_gate": {
            "decision": decision,
            "checks": {
                "raw_market_missing_intervals_met": series_with_missing == 0,
                "raw_market_observed_at_unique_met": series_with_duplicate_observed_at == 0,
                "raw_market_coverage_alignment_met": series_with_coverage_alignment_issues == 0,
                "raw_market_provenance_complete_met": series_with_incomplete_provenance == 0,
                "l2_coverage_met": l2["met"],
            },
            "reasons": reasons,
        },
    }


__all__ = ["RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION", "build_raw_market_data_quality_report"]
