from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.raw_market import (
    archive_raw_market_payload,
    load_phase1_raw_market_manifest,
)


def test_load_raw_market_manifest_fails_fast_on_duplicate_file_timestamps(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T03:00:00Z",
        fetched_at="2026-01-01T03:01:00Z",
        endpoint="/fapi/v1/klines",
        payload={
            "rows": [
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0, "volume": 11.0},
            ]
        },
    )

    with pytest.raises(ValueError, match="raw-market duplicate record timestamp"):
        load_phase1_raw_market_manifest(archived.manifest_path)


def test_raw_market_data_quality_reports_timestamp_uniqueness(tmp_path: Path) -> None:
    from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report

    archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T03:00:00Z",
        fetched_at="2026-01-01T03:01:00Z",
        endpoint="/fapi/v1/klines",
        payload={
            "rows": [
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
                {"open_time": "2026-01-01T02:00:00Z", "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0, "volume": 11.0},
            ]
        },
    )

    report = build_raw_market_data_quality_report(tmp_path / "archive")
    series_report = next(iter(report["series"].values()))

    assert series_report["observed_at_unique"] is True
    assert series_report["duplicate_observed_at_count"] == 0
    assert series_report["duplicate_observed_at"] == []
    assert series_report["first_observed_at"] == "2026-01-01T00:00:00Z"
    assert series_report["last_observed_at"] == "2026-01-01T02:00:00Z"
    assert series_report["coverage_alignment"] == {
        "coverage_start_matches_first_observed_at": True,
        "coverage_end_matches_expected_terminal_boundary": True,
    }
    assert report["summary"]["series_with_duplicate_observed_at"] == 0
    assert report["summary"]["series_with_coverage_alignment_issues"] == 0
    assert report["promotion_gate"]["checks"]["raw_market_observed_at_unique_met"] is True
    assert report["promotion_gate"]["checks"]["raw_market_coverage_alignment_met"] is True


def test_load_raw_market_manifest_rejects_noncanonical_required_string_fields(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T02:00:00Z",
        fetched_at="2026-01-01T02:01:00Z",
        endpoint="/fapi/v1/klines",
        payload={
            "rows": [
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )
    manifest = json.loads(archived.manifest_path.read_text(encoding="utf-8"))
    manifest["symbol"] = " BTCUSDT "
    archived.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest field 'symbol' must be canonical"):
        load_phase1_raw_market_manifest(archived.manifest_path)


def test_load_raw_market_manifest_rejects_noncanonical_timeframe(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T02:00:00Z",
        fetched_at="2026-01-01T02:01:00Z",
        endpoint="/fapi/v1/klines",
        payload={
            "rows": [
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )
    manifest = json.loads(archived.manifest_path.read_text(encoding="utf-8"))
    manifest["timeframe"] = " 1h "
    archived.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest timeframe must be canonical"):
        load_phase1_raw_market_manifest(archived.manifest_path)
