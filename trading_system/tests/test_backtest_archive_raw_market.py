from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.raw_market import (
    archive_raw_market_payload,
    ImportedRawMarketRecord,
    load_phase1_raw_market_manifest,
)
from trading_system.app.backtest.archive.importer import _ohlcv_bar_lookup, _order_book_payload, _trade_payload


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


def test_importer_rejects_invalid_ohlcv_numeric_fields() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={
            "open": 100.0,
            "high": "not-a-number",
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        },
    )

    with pytest.raises(ValueError, match="ohlcv high must be numeric"):
        _ohlcv_bar_lookup([record])


def test_importer_rejects_inconsistent_ohlcv_price_domain() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={
            "open": 100.0,
            "high": 99.0,
            "low": 98.0,
            "close": 100.5,
            "volume": 10.0,
        },
    )

    with pytest.raises(ValueError, match="ohlcv high must cover open and close"):
        _ohlcv_bar_lookup([record])


def test_importer_rejects_negative_ohlcv_volume() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": -1.0,
        },
    )

    with pytest.raises(ValueError, match="ohlcv volume must be non-negative"):
        _ohlcv_bar_lookup([record])


def test_importer_rejects_invalid_ohlcv_quote_volume_when_present() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
            "quote_asset_volume": "not-a-number",
        },
    )

    with pytest.raises(ValueError, match="ohlcv quote volume must be numeric"):
        _ohlcv_bar_lookup([record])


def test_importer_rejects_noncanonical_trade_side_payload() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "price": 100.0, "quantity": 1.0, "side": " buy "},
    )

    with pytest.raises(ValueError, match="trade side must be canonical"):
        _trade_payload(record, symbol="BTCUSDT")


def test_importer_rejects_non_boolean_trade_maker_flag() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "price": 100.0, "quantity": 1.0, "m": "false"},
    )

    with pytest.raises(ValueError, match="trade maker flag must be boolean"):
        _trade_payload(record, symbol="BTCUSDT")


def test_importer_rejects_invalid_order_book_numeric_fields() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "bid": "not-a-number", "ask": 101.0},
    )

    with pytest.raises(ValueError, match="order book bid must be numeric"):
        _order_book_payload(record, symbol="BTCUSDT")


def test_importer_rejects_crossed_order_book_prices() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "bid": 102.0, "ask": 101.0},
    )

    with pytest.raises(ValueError, match="order book ask must be greater than or equal to bid"):
        _order_book_payload(record, symbol="BTCUSDT")
