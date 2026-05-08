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
from trading_system.app.backtest.archive.data_quality import _l2_tick_coverage
from trading_system.app.backtest.archive.importer import (
    _funding_rate,
    _merged_execution_evidence_coverage,
    _merged_futures_context_coverage,
    _merged_import_trace,
    _merged_ohlcv_timeframe_coverage,
    _mark_price,
    _ohlcv_bar_lookup,
    _open_interest_units,
    _ordered_timeframes,
    _order_book_payload,
    _trade_payload,
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


def test_raw_market_data_quality_reports_provenance_completeness(tmp_path: Path) -> None:
    from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report

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

    report = build_raw_market_data_quality_report(tmp_path / "archive")
    series_report = next(iter(report["series"].values()))

    assert series_report["provenance_complete"] is True
    assert series_report["provenance_file_count"] == 1
    assert series_report["provenance_missing_sha256_count"] == 0
    assert series_report["provenance_missing_data_path_count"] == 0
    assert series_report["provenance_missing_manifest_path_count"] == 0
    assert series_report["provenance"][0]["manifest_path"] == str(archived.manifest_path)
    assert series_report["provenance"][0]["data_path"] == str(archived.data_path)
    assert series_report["provenance"][0]["sha256"]
    assert report["summary"]["series_with_incomplete_provenance"] == 0
    assert report["promotion_gate"]["checks"]["raw_market_provenance_complete_met"] is True


def test_l2_tick_coverage_rejects_string_coverage_ratio() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": "1.0",
            "has_missing_intervals": False,
            "missing_intervals": [],
        }
    }

    with pytest.raises(ValueError, match="l2 coverage ratio must be numeric"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_noncanonical_identity_fields() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": " BTCUSDT:trades ",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 0.5,
            "has_missing_intervals": False,
            "missing_intervals": [],
        }
    }

    with pytest.raises(ValueError, match="l2 series_key must be canonical"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_non_object_missing_interval_entries() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 0.5,
            "has_missing_intervals": True,
            "missing_intervals": ["bad-gap"],
        }
    }

    with pytest.raises(ValueError, match=r"l2 missing_intervals\[1\] must be an object"):
        _l2_tick_coverage(reports, required_coverage=0.99)


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


def test_load_raw_market_manifest_rejects_unsafe_file_path(tmp_path: Path) -> None:
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
    escaped_data_path = archived.manifest_path.parent.parent / archived.data_path.name
    escaped_data_path.write_bytes(archived.data_path.read_bytes())
    manifest = json.loads(archived.manifest_path.read_text(encoding="utf-8"))
    manifest["file"]["path"] = f"../{escaped_data_path.name}"
    archived.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest file.path must be safe"):
        load_phase1_raw_market_manifest(archived.manifest_path)


def test_load_raw_market_manifest_rejects_string_symbol_metadata_numerics(tmp_path: Path) -> None:
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
        symbol_metadata={
            "listing_timestamp": "2025-01-01T00:00:00Z",
            "quantity_step": 0.001,
            "price_tick": 0.1,
        },
        payload={
            "rows": [
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )
    manifest = json.loads(archived.manifest_path.read_text(encoding="utf-8"))
    manifest["symbol_metadata"]["quantity_step"] = "0.001"
    archived.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market symbol_metadata quantity_step must be numeric"):
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


def test_importer_rejects_invalid_order_book_size_fields_when_present() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "bid": 100.0, "ask": 101.0, "bid_size": "not-a-number"},
    )

    with pytest.raises(ValueError, match="order book bid_size must be numeric"):
        _order_book_payload(record, symbol="BTCUSDT")


def test_importer_rejects_invalid_trade_price_and_quantity_fields() -> None:
    price_record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "price": "not-a-number", "quantity": 1.0},
    )
    quantity_record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "price": 100.0, "quantity": 0.0},
    )

    with pytest.raises(ValueError, match="trade price must be numeric"):
        _trade_payload(price_record, symbol="BTCUSDT")
    with pytest.raises(ValueError, match="trade quantity must be positive"):
        _trade_payload(quantity_record, symbol="BTCUSDT")


def test_importer_rejects_noncanonical_execution_symbol() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": " BTCUSDT ", "bid": 100.0, "ask": 101.0},
    )

    with pytest.raises(ValueError, match="execution symbol must be canonical"):
        _order_book_payload(record, symbol="BTCUSDT")


def test_importer_rejects_non_string_execution_symbol() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": 123, "price": 100.0, "quantity": 1.0},
    )

    with pytest.raises(ValueError, match="execution symbol must be a string"):
        _trade_payload(record, symbol="BTCUSDT")


def test_importer_rejects_invalid_market_context_numeric_fields() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    open_interest_record = ImportedRawMarketRecord(
        observed_at=observed_at,
        payload={"sumOpenInterest": "not-a-number"},
    )
    funding_record = ImportedRawMarketRecord(
        observed_at=observed_at,
        payload={"fundingRate": "not-a-number"},
    )
    mark_price_record = ImportedRawMarketRecord(
        observed_at=observed_at,
        payload={"markPrice": "not-a-number"},
    )

    with pytest.raises(ValueError, match="open interest must be numeric"):
        _open_interest_units(open_interest_record)
    with pytest.raises(ValueError, match="funding rate must be numeric"):
        _funding_rate(funding_record)
    with pytest.raises(ValueError, match="mark price must be numeric"):
        _mark_price(mark_price_record)


def test_importer_rejects_non_boolean_execution_coverage_available() -> None:
    with pytest.raises(ValueError, match="execution_evidence.available must be boolean"):
        _merged_execution_evidence_coverage([{"execution_evidence": {"available": "false"}}])


def test_importer_rejects_string_execution_coverage_counts() -> None:
    with pytest.raises(ValueError, match="execution_evidence.materialized.order_book must be a non-negative integer"):
        _merged_execution_evidence_coverage([
            {"execution_evidence": {"materialized": {"order_book": "1"}}}
        ])


def test_importer_rejects_non_boolean_futures_context_available() -> None:
    with pytest.raises(ValueError, match="futures_context.available must be boolean"):
        _merged_futures_context_coverage([{"futures_context": {"available": "false"}}])


def test_importer_rejects_string_futures_context_counts() -> None:
    with pytest.raises(ValueError, match="futures_context.materialized.mark_price must be a non-negative integer"):
        _merged_futures_context_coverage([
            {"futures_context": {"materialized": {"mark_price": "1"}}}
        ])


def test_importer_rejects_noncanonical_import_trace_identity_fields() -> None:
    with pytest.raises(ValueError, match="import_trace.scope must be canonical"):
        _merged_import_trace([{"scope": " phase1 ", "exchange": "binance", "market": "futures"}])


def test_importer_rejects_non_string_import_trace_symbols() -> None:
    with pytest.raises(ValueError, match=r"import_trace.symbols\[0\] must be a string"):
        _merged_import_trace([{"scope": "phase1", "exchange": "binance", "market": "futures", "symbols": [123]}])


def test_importer_rejects_noncanonical_import_trace_manifest_paths() -> None:
    with pytest.raises(ValueError, match=r"import_trace.manifest_paths\[0\] must be canonical"):
        _merged_import_trace([
            {"scope": "phase1", "exchange": "binance", "market": "futures", "manifest_paths": [" manifests/a.json "]}
        ])


def test_importer_rejects_noncanonical_ohlcv_timeframe_coverage_values() -> None:
    with pytest.raises(ValueError, match=r"ohlcv_timeframes.available\[0\] must be canonical"):
        _merged_ohlcv_timeframe_coverage([{"ohlcv_timeframes": {"available": [" 1h "]}}])


def test_importer_rejects_non_string_ohlcv_not_materialized_reasons() -> None:
    with pytest.raises(ValueError, match=r"ohlcv_timeframes.not_materialized.5m must be a string"):
        _merged_ohlcv_timeframe_coverage([{"ohlcv_timeframes": {"not_materialized": {"5m": 123}}}])


def test_importer_rejects_non_string_ordered_timeframes() -> None:
    with pytest.raises(ValueError, match=r"ohlcv_timeframes.value\[0\] must be a string"):
        _ordered_timeframes([123])


def test_importer_rejects_noncanonical_ordered_timeframes() -> None:
    with pytest.raises(ValueError, match=r"ohlcv_timeframes.value\[0\] must be canonical"):
        _ordered_timeframes([" 1h "])
