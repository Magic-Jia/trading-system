from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .raw_market import ImportedRawMarketSeries, load_phase1_raw_market_imports

RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION = "raw_market_data_quality_report.v1"


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _interval_key(series: ImportedRawMarketSeries) -> str:
    if series.dataset == "ohlcv" and series.timeframe:
        return f"ohlcv:{series.timeframe}"
    return series.dataset


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
    missing = _missing_intervals(series, expected_interval)
    files = list(series.files)
    return {
        "series_key": series.series_key,
        "exchange": series.exchange,
        "market": series.market,
        "dataset": series.dataset,
        "symbol": series.symbol,
        "timeframe": series.timeframe,
        "record_count": len(series.records),
        "file_count": len(files),
        "coverage_start": _utc_timestamp(min(item.coverage_start for item in files)) if files else None,
        "coverage_end": _utc_timestamp(max(item.coverage_end for item in files)) if files else None,
        "expected_interval_seconds": int(expected_interval.total_seconds()) if expected_interval else None,
        "coverage_ratio": _coverage_ratio(series, expected_interval),
        "missing_intervals": missing,
        "has_missing_intervals": bool(missing),
        "provenance": [
            {
                "manifest_path": str(item.manifest_path),
                "data_path": str(item.data_path),
                "coverage_start": _utc_timestamp(item.coverage_start),
                "coverage_end": _utc_timestamp(item.coverage_end),
                "sha256": item.manifest.get("file", {}).get("sha256"),
            }
            for item in files
        ],
    }


def _l2_tick_coverage(series_reports: Mapping[str, Mapping[str, Any]], required_coverage: float) -> dict[str, Any]:
    l2_reports = [dict(report) for report in series_reports.values() if report.get("dataset") in {"order-book", "trades"}]
    if not l2_reports:
        return {
            "required_coverage": required_coverage,
            "coverage_ratio": 0.0,
            "met": False,
            "missing_by_symbol_timeframe": [],
            "series": [],
        }
    coverage_ratio = min(float(report.get("coverage_ratio") or 0.0) for report in l2_reports)
    missing = []
    for report in l2_reports:
        if float(report.get("coverage_ratio") or 0.0) < required_coverage or report.get("has_missing_intervals"):
            missing.append(
                {
                    "symbol": report.get("symbol"),
                    "dataset": report.get("dataset"),
                    "timeframe": report.get("timeframe"),
                    "coverage_ratio": report.get("coverage_ratio"),
                    "missing_intervals": report.get("missing_intervals", []),
                }
            )
    return {
        "required_coverage": required_coverage,
        "coverage_ratio": coverage_ratio,
        "met": coverage_ratio >= required_coverage and not missing,
        "missing_by_symbol_timeframe": missing,
        "series": [report["series_key"] for report in l2_reports],
    }


def build_raw_market_data_quality_report(
    archive_root: str | Path,
    *,
    expected_intervals: Mapping[str, timedelta] | None = None,
    required_l2_coverage: float = 0.99,
) -> dict[str, Any]:
    intervals = dict(expected_intervals or {})
    series = load_phase1_raw_market_imports(archive_root)
    reports = {
        item.series_key: _series_report(item, intervals.get(_interval_key(item)))
        for item in series
    }
    series_with_missing = sum(1 for report in reports.values() if report["has_missing_intervals"])
    l2 = _l2_tick_coverage(reports, required_l2_coverage)
    reasons: list[str] = []
    if series_with_missing:
        reasons.append("raw_market_missing_intervals")
    if not l2["met"]:
        reasons.append("l2_coverage_below_threshold")
    decision = "ready_for_live_promotion_review" if not reasons else "reject_for_live_promotion"
    return {
        "schema_version": RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION,
        "archive_root": str(Path(archive_root)),
        "summary": {
            "series_count": len(reports),
            "series_with_missing_intervals": series_with_missing,
            "l2_coverage_met": l2["met"],
        },
        "series": reports,
        "l2_tick_coverage": l2,
        "promotion_gate": {
            "decision": decision,
            "checks": {
                "raw_market_missing_intervals_met": series_with_missing == 0,
                "l2_coverage_met": l2["met"],
            },
            "reasons": reasons,
        },
    }


__all__ = ["RAW_MARKET_DATA_QUALITY_SCHEMA_VERSION", "build_raw_market_data_quality_report"]
