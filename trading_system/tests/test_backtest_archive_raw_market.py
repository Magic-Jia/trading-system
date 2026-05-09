from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.raw_market import (
    archive_raw_market_payload,
    ImportedRawMarketRecord,
    ImportedRawMarketSeries,
    load_phase1_raw_market_manifest,
)
from trading_system.app.backtest.archive.data_quality import _expected_intervals, _l2_tick_coverage, _series_report
from trading_system.app.backtest.archive.importer import (
    Phase1DatasetBundleMaterial,
    _funding_rate,
    _increment_execution_coverage,
    _increment_context_coverage,
    _mark_price,
    _material_metadata_source,
    _merged_execution_evidence_coverage,
    _merged_futures_context_coverage,
    _merged_import_trace,
    _merged_ohlcv_timeframe_coverage,
    _ohlcv_bar_lookup,
    _open_interest_units,
    _ordered_timeframes,
    _order_book_payload,
    _trade_payload,
)
from trading_system.app.backtest.archive.materialization import _execution_evidence_gap
from trading_system.app.backtest.archive.materialization import _manifest_coverage_bounds


def test_material_metadata_source_rejects_present_null_source() -> None:
    material = Phase1DatasetBundleMaterial(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        run_id="phase1-import-2026-01-01T00-00-00Z",
        metadata={"source": None},
        market_context={},
        derivatives_snapshot={},
        account_snapshot={},
    )

    with pytest.raises(ValueError, match="materialized dataset bundle metadata source must contain a JSON object"):
        _material_metadata_source(material)


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


def test_archive_raw_market_payload_rejects_empty_list_metadata(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    expected_dir = archive_root / "raw-market" / "binance" / "futures" / "ohlcv" / "BTCUSDT" / "1h"

    with pytest.raises(ValueError, match="raw-market metadata must be a JSON object"):
        archive_raw_market_payload(
            archive_root=archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start="2026-01-01T00:00:00Z",
            coverage_end="2026-01-01T01:00:00Z",
            fetched_at="2026-01-01T01:01:00Z",
            endpoint="/fapi/v1/klines",
            payload={
                "rows": [
                    {
                        "open_time": "2026-01-01T00:00:00Z",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10.0,
                    },
                ]
            },
            metadata=[],
        )

    assert not expected_dir.exists() or list(expected_dir.iterdir()) == []


@pytest.mark.parametrize("metadata", [{123: "x"}, {" bad ": "x"}])
def test_archive_raw_market_payload_rejects_noncanonical_metadata_keys_before_archive_side_effects(
    tmp_path: Path,
    metadata: dict[object, str],
) -> None:
    archive_root = tmp_path / "archive"

    with pytest.raises(ValueError, match="raw-market metadata keys must be canonical strings"):
        archive_raw_market_payload(
            archive_root=archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start="2026-01-01T00:00:00Z",
            coverage_end="2026-01-01T01:00:00Z",
            fetched_at="2026-01-01T01:01:00Z",
            endpoint="/fapi/v1/klines",
            payload={
                "rows": [
                    {
                        "open_time": "2026-01-01T00:00:00Z",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10.0,
                    },
                ]
            },
            metadata=metadata,
        )

    assert not archive_root.exists()


def test_archive_raw_market_payload_rejects_boolean_coverage_start_before_archive_side_effects(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"

    with pytest.raises(ValueError, match="raw-market coverage_start must be a string timestamp"):
        archive_raw_market_payload(
            archive_root=archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start=True,
            coverage_end="2026-01-01T01:00:00Z",
            fetched_at="2026-01-01T01:01:00Z",
            endpoint="/fapi/v1/klines",
            payload={
                "rows": [
                    {
                        "open_time": "2026-01-01T00:00:00Z",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10.0,
                    },
                ]
            },
        )

    assert not archive_root.exists()


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


def test_raw_market_data_quality_rejects_invalid_expected_interval(tmp_path: Path) -> None:
    from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report

    archive_raw_market_payload(
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

    with pytest.raises(ValueError, match="expected interval for ohlcv:1h must be a timedelta"):
        build_raw_market_data_quality_report(tmp_path / "archive", expected_intervals={"ohlcv:1h": True})


def test_raw_market_data_quality_rejects_non_positive_expected_interval(tmp_path: Path) -> None:
    from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report

    archive_raw_market_payload(
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

    with pytest.raises(ValueError, match="expected interval for ohlcv:1h must be positive"):
        build_raw_market_data_quality_report(tmp_path / "archive", expected_intervals={"ohlcv:1h": timedelta(0)})


def test_raw_market_data_quality_rejects_noncanonical_expected_interval_key(tmp_path: Path) -> None:
    from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report

    archive_raw_market_payload(
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

    with pytest.raises(ValueError, match="expected interval key must be canonical"):
        build_raw_market_data_quality_report(tmp_path / "archive", expected_intervals={" ohlcv:1h ": timedelta(hours=1)})


def test_raw_market_data_quality_rejects_non_object_expected_intervals() -> None:
    with pytest.raises(ValueError, match="expected_intervals must be an object"):
        _expected_intervals([("ohlcv:1h", timedelta(hours=1))])


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


def test_materialization_manifest_coverage_bounds_reject_object_boundary_values() -> None:
    with pytest.raises(ValueError, match="coverage_start must be a string or numeric milliseconds"):
        _manifest_coverage_bounds(
            {
                "coverage_start": {"timestamp": "2026-01-01T00:00:00Z"},
                "coverage_end": "2026-01-01T01:00:00Z",
            }
        )


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


def test_l2_tick_coverage_rejects_noncanonical_dataset_before_filtering() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": " trades ",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 0.5,
            "has_missing_intervals": False,
            "missing_intervals": [],
        }
    }

    with pytest.raises(ValueError, match="l2 dataset must be canonical"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_pair_list_series_reports_container() -> None:
    reports = [
        (
            "BTCUSDT:trades",
            {
                "series_key": "BTCUSDT:trades",
                "dataset": "trades",
                "symbol": "BTCUSDT",
                "timeframe": None,
                "coverage_ratio": 1.0,
                "has_missing_intervals": False,
                "missing_intervals": [],
            },
        )
    ]

    with pytest.raises(ValueError, match="l2 series reports must be an object"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_non_object_series_reports_before_filtering() -> None:
    reports = {"BTCUSDT:trades": [("dataset", "trades")]}

    with pytest.raises(ValueError, match=r"l2 series report BTCUSDT:trades must be an object"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_noncanonical_series_report_keys() -> None:
    reports = {
        " BTCUSDT:trades ": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 1.0,
            "has_missing_intervals": False,
            "missing_intervals": [],
        },
        123: {
            "series_key": "ETHUSDT:trades",
            "dataset": "trades",
            "symbol": "ETHUSDT",
            "timeframe": None,
            "coverage_ratio": 1.0,
            "has_missing_intervals": False,
            "missing_intervals": [],
        },
    }

    with pytest.raises(ValueError, match="l2 series report key must be canonical"):
        _l2_tick_coverage(reports, required_coverage=0.99)


@pytest.mark.parametrize("bad_field", [123, " coverage_ratio "])
def test_l2_tick_coverage_rejects_noncanonical_series_report_fields(bad_field: object) -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 1.0,
            "has_missing_intervals": False,
            "missing_intervals": [],
            bad_field: "ambiguous",
        }
    }

    with pytest.raises(ValueError, match="l2 series report fields must be canonical"):
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


def test_l2_tick_coverage_rejects_noncanonical_missing_interval_fields() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 0.5,
            "has_missing_intervals": True,
            "missing_intervals": [
                {"start": " 2026-01-01T00:00:00Z ", "end": "2026-01-01T01:00:00Z", "missing_records": 1}
            ],
        }
    }

    with pytest.raises(ValueError, match=r"l2 missing_intervals\[1\].start must be canonical"):
        _l2_tick_coverage(reports, required_coverage=0.99)


def test_l2_tick_coverage_rejects_boolean_required_coverage() -> None:
    reports = {
        "BTCUSDT:trades": {
            "series_key": "BTCUSDT:trades",
            "dataset": "trades",
            "symbol": "BTCUSDT",
            "timeframe": None,
            "coverage_ratio": 1.0,
            "has_missing_intervals": False,
            "missing_intervals": [],
        }
    }

    with pytest.raises(ValueError, match="l2 required_coverage must be numeric"):
        _l2_tick_coverage(reports, required_coverage=True)


def test_materialization_execution_gap_rejects_boolean_counts() -> None:
    coverage = {"execution_evidence": {"materialized": {"order_book": True, "trades": 1}}}

    with pytest.raises(ValueError, match="execution_evidence.materialized.order_book must be a non-negative integer"):
        _execution_evidence_gap(coverage)


def test_series_report_rejects_noncanonical_series_identity() -> None:
    series = ImportedRawMarketSeries(
        series_key=" BTCUSDT:ohlcv:1h ",
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        symbol_metadata=None,
        files=(),
        records=(
            ImportedRawMarketRecord(
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
            ),
        ),
    )

    with pytest.raises(ValueError, match="raw-market series_key must be canonical"):
        _series_report(series, expected_interval=None)


def test_series_report_rejects_non_string_provenance_sha256(tmp_path: Path) -> None:
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
    raw_file = load_phase1_raw_market_manifest(archived.manifest_path)
    bad_file = raw_file.__class__(
        series_key=raw_file.series_key,
        manifest_path=raw_file.manifest_path,
        data_path=raw_file.data_path,
        manifest={**raw_file.manifest, "file": {"sha256": 123}},
        symbol_metadata=raw_file.symbol_metadata,
        coverage_start=raw_file.coverage_start,
        coverage_end=raw_file.coverage_end,
        fetched_at=raw_file.fetched_at,
        records=raw_file.records,
    )
    bad_series = ImportedRawMarketSeries(
        series_key=raw_file.series_key,
        exchange=raw_file.manifest["exchange"],
        market=raw_file.manifest["market"],
        dataset=raw_file.manifest["dataset"],
        symbol=raw_file.manifest["symbol"],
        timeframe=raw_file.manifest.get("timeframe"),
        symbol_metadata=raw_file.symbol_metadata,
        files=(bad_file,),
        records=raw_file.records,
    )

    with pytest.raises(ValueError, match="raw-market provenance sha256 must be canonical"):
        _series_report(bad_series, expected_interval=None)


def test_series_report_rejects_non_object_provenance_file_metadata(tmp_path: Path) -> None:
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
    raw_file = load_phase1_raw_market_manifest(archived.manifest_path)
    bad_file = raw_file.__class__(
        series_key=raw_file.series_key,
        manifest_path=raw_file.manifest_path,
        data_path=raw_file.data_path,
        manifest={**raw_file.manifest, "file": ["not-object"]},
        symbol_metadata=raw_file.symbol_metadata,
        coverage_start=raw_file.coverage_start,
        coverage_end=raw_file.coverage_end,
        fetched_at=raw_file.fetched_at,
        records=raw_file.records,
    )
    bad_series = ImportedRawMarketSeries(
        series_key=raw_file.series_key,
        exchange=raw_file.manifest["exchange"],
        market=raw_file.manifest["market"],
        dataset=raw_file.manifest["dataset"],
        symbol=raw_file.manifest["symbol"],
        timeframe=raw_file.manifest.get("timeframe"),
        symbol_metadata=raw_file.symbol_metadata,
        files=(bad_file,),
        records=raw_file.records,
    )

    with pytest.raises(ValueError, match="raw-market provenance file metadata must be an object"):
        _series_report(bad_series, expected_interval=None)


def test_merged_ohlcv_timeframe_coverage_rejects_empty_list_not_materialized() -> None:
    with pytest.raises(ValueError, match="ohlcv_timeframes.not_materialized must be a JSON object"):
        _merged_ohlcv_timeframe_coverage(
            [
                {
                    "ohlcv_timeframes": {
                        "available": ["1h"],
                        "materialized": ["1h"],
                        "not_materialized": [],
                    }
                }
            ]
        )



def test_merged_ohlcv_timeframe_coverage_rejects_non_object_trace() -> None:
    with pytest.raises(ValueError, match="ohlcv_timeframes trace entry must be a JSON object"):
        _merged_ohlcv_timeframe_coverage([[('ohlcv_timeframes', {"available": ["1h"]})]])


def test_merged_execution_evidence_rejects_non_object_trace() -> None:
    with pytest.raises(ValueError, match="execution_evidence trace entry must be a JSON object"):
        _merged_execution_evidence_coverage([[('execution_evidence', {"available": False})]])


def test_merged_futures_context_rejects_non_object_trace() -> None:
    with pytest.raises(ValueError, match="futures_context trace entry must be a JSON object"):
        _merged_futures_context_coverage([[('futures_context', {"available": False})]])



def test_merged_import_trace_rejects_non_object_trace() -> None:
    with pytest.raises(ValueError, match="import_trace entry must be a JSON object"):
        _merged_import_trace(
            [
                [
                    ("scope", "phase1_archive_import"),
                    ("exchange", "binance"),
                    ("market", "futures"),
                    ("symbols", ["BTCUSDT"]),
                ]
            ]
        )


def test_merged_import_trace_rejects_object_symbols_list() -> None:
    with pytest.raises(ValueError, match="import_trace.symbols must be a list"):
        _merged_import_trace(
            [
                {
                    "scope": "phase1_binance_futures",
                    "exchange": "binance",
                    "market": "futures",
                    "symbols": {"BTCUSDT": True},
                }
            ]
        )



def test_merged_execution_evidence_rejects_empty_list_bucket() -> None:
    with pytest.raises(ValueError, match="execution_evidence.materialized must be a JSON object"):
        _merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": False,
                        "max_staleness_seconds": 300,
                        "materialized": [],
                    }
                }
            ]
        )


def test_increment_execution_coverage_rejects_present_invalid_counter() -> None:
    coverage = {"materialized": {"order_book": "0"}}

    with pytest.raises(ValueError, match="execution_evidence.materialized.order_book must be a non-negative integer"):
        _increment_execution_coverage(coverage, "materialized", "order_book")


def test_increment_context_coverage_rejects_present_invalid_counter() -> None:
    coverage = {"materialized": {"mark_price": "0"}}

    with pytest.raises(ValueError, match="futures_context.materialized.mark_price must be a non-negative integer"):
        _increment_context_coverage(coverage, "materialized", "mark_price")


def test_merged_futures_context_rejects_empty_list_bucket() -> None:
    with pytest.raises(ValueError, match="futures_context.materialized must be a JSON object"):
        _merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": False,
                        "max_age_seconds": {"mark_price": 300, "funding": 3600, "open_interest": 3600},
                        "materialized": [],
                    }
                }
            ]
        )



def test_merged_futures_context_rejects_empty_list_max_age_seconds() -> None:
    with pytest.raises(ValueError, match="futures_context.max_age_seconds must be a JSON object"):
        _merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": False,
                        "max_age_seconds": [],
                    }
                }
            ]
        )



def test_mark_price_rejects_boolean_value() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"markPrice": True},
    )

    with pytest.raises(ValueError, match="mark price must be numeric"):
        _mark_price(record)


def test_funding_rate_rejects_boolean_value() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"fundingRate": True},
    )

    with pytest.raises(ValueError, match="funding rate must be numeric"):
        _funding_rate(record)


def test_imported_funding_rate_rejects_padded_numeric_string(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        timeframe=None,
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T08:00:00Z",
        fetched_at="2026-01-01T08:01:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload={"rows": [{"fundingTime": "2026-01-01T00:00:00Z", "fundingRate": " 0.0001 "}]},
    )
    imported_file = load_phase1_raw_market_manifest(archived.manifest_path)

    with pytest.raises(ValueError, match="funding rate must be canonical"):
        _funding_rate(imported_file.records[0])


def test_imported_mark_price_rejects_underscored_numeric_string(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="mark-price",
        symbol="BTCUSDT",
        timeframe=None,
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T01:00:00Z",
        fetched_at="2026-01-01T01:01:00Z",
        endpoint="/fapi/v1/premiumIndex",
        payload={"rows": [{"timestamp": "2026-01-01T00:00:00Z", "markPrice": "70_000.5"}]},
    )
    imported_file = load_phase1_raw_market_manifest(archived.manifest_path)

    with pytest.raises(ValueError, match="mark price must be canonical"):
        _mark_price(imported_file.records[0])


def test_open_interest_rejects_boolean_value() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"sumOpenInterestValue": True},
    )

    with pytest.raises(ValueError, match="open interest must be numeric"):
        _open_interest_units(record)


def test_load_raw_market_manifest_rejects_invalid_open_interest_optional_quote_value(tmp_path: Path) -> None:
    archived = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="open-interest",
        symbol="BTCUSDT",
        timeframe=None,
        coverage_start="2026-01-01T00:00:00Z",
        coverage_end="2026-01-01T05:00:00Z",
        fetched_at="2026-01-01T05:01:00Z",
        endpoint="/futures/data/openInterestHist",
        payload={
            "rows": [
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "sumOpenInterest": "100.0",
                    "sumOpenInterestValue": "1_000.0",
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="open-interest row sumOpenInterestValue must be canonical"):
        load_phase1_raw_market_manifest(archived.manifest_path)


def test_load_raw_market_manifest_rejects_boolean_ohlcv_close(tmp_path: Path) -> None:
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
                {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "high": 101.0, "low": 99.0, "close": True, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )
    series = load_phase1_raw_market_manifest(archived.manifest_path)

    with pytest.raises(ValueError, match="ohlcv close must be numeric"):
        _ohlcv_bar_lookup(series.records)


def test_load_raw_market_manifest_rejects_short_ohlcv_array_rows(tmp_path: Path) -> None:
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
                [1767225600000, "100.0", "101.0", "99.0", "100.5"],
                [1767229200000, "100.5", "102.0", "100.0", "101.0", "12.0"],
            ]
        },
    )

    with pytest.raises(ValueError, match="ohlcv array payload must match Binance kline layout"):
        load_phase1_raw_market_manifest(archived.manifest_path)


def test_importer_rejects_imported_ohlcv_rows_missing_explicit_price_bounds(tmp_path: Path) -> None:
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
                {"open_time": "2026-01-01T00:00:00Z", "close": 100.5, "volume": 10.0},
                {
                    "open_time": "2026-01-01T01:00:00Z",
                    "open": 100.5,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.0,
                    "volume": 12.0,
                },
            ]
        },
    )
    series = load_phase1_raw_market_manifest(archived.manifest_path)

    with pytest.raises(ValueError, match="ohlcv open must be present"):
        _ohlcv_bar_lookup(series.records)



def test_order_book_payload_rejects_boolean_bid() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "bid": True, "ask": 101.0},
    )

    with pytest.raises(ValueError, match="order book bid must be numeric"):
        _order_book_payload(record, symbol="BTCUSDT")


def test_order_book_payload_rejects_string_encoded_ask() -> None:
    record = ImportedRawMarketRecord(
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "BTCUSDT", "bid": 100.0, "ask": "101.0"},
    )

    with pytest.raises(ValueError, match="order book ask must be numeric"):
        _order_book_payload(record, symbol="BTCUSDT")



def test_load_raw_market_manifest_rejects_noncanonical_file_sha256(tmp_path: Path) -> None:
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
    manifest_path = archived.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file"]["sha256"] = f" {manifest['file']['sha256']} "
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest file.sha256 must be canonical"):
        load_phase1_raw_market_manifest(manifest_path)


def test_load_raw_market_manifest_rejects_malformed_file_sha256(tmp_path: Path) -> None:
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
    manifest_path = archived.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file"]["sha256"] = "not-a-sha256-digest"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest file.sha256 must be canonical"):
        load_phase1_raw_market_manifest(manifest_path)


def test_load_raw_market_manifest_rejects_source_exchange_mismatch(tmp_path: Path) -> None:
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
    manifest_path = archived.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"] = "coinbase"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest source must match exchange"):
        load_phase1_raw_market_manifest(manifest_path)



def test_load_raw_market_manifest_rejects_boolean_file_size(tmp_path: Path) -> None:
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
    manifest_path = archived.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file"]["size_bytes"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest file.size_bytes must be a non-negative integer"):
        load_phase1_raw_market_manifest(manifest_path)



def test_load_raw_market_manifest_rejects_padded_symbol_metadata_listing_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="raw-market symbol_metadata listing_timestamp must be canonical"):
        archive_raw_market_payload(
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
            symbol_metadata={
                "listing_timestamp": " 2025-12-01T00:00:00Z ",
                "quantity_step": 0.001,
                "price_tick": 0.01,
            },
        )



def test_load_raw_market_manifest_rejects_padded_row_timestamps(tmp_path: Path) -> None:
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
                {"open_time": " 2026-01-01T00:00:00Z ", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp value must be canonical"):
        load_phase1_raw_market_manifest(archived.manifest_path)



def test_load_raw_market_manifest_rejects_non_scalar_row_timestamps(tmp_path: Path) -> None:
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
                {"open_time": ["2026-01-01T00:00:00Z"], "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp value must be a string or numeric milliseconds"):
        load_phase1_raw_market_manifest(archived.manifest_path)



def test_load_raw_market_manifest_rejects_nonfinite_row_timestamps(tmp_path: Path) -> None:
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
                {"open_time": float("inf"), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp value must be finite"):
        load_phase1_raw_market_manifest(archived.manifest_path)



def test_load_raw_market_manifest_rejects_boolean_row_timestamps(tmp_path: Path) -> None:
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
                {"open_time": True, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0},
                {"open_time": "2026-01-01T01:00:00Z", "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.0, "volume": 12.0},
            ]
        },
    )

    with pytest.raises(ValueError, match="timestamp value must not be boolean"):
        load_phase1_raw_market_manifest(archived.manifest_path)



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


def test_importer_rejects_non_object_market_context_payloads() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="open interest payload must be an object"):
        _open_interest_units(ImportedRawMarketRecord(observed_at=observed_at, payload=[]))
    with pytest.raises(ValueError, match="funding rate payload must be an object"):
        _funding_rate(ImportedRawMarketRecord(observed_at=observed_at, payload=[]))
    with pytest.raises(ValueError, match="mark price payload must be an object"):
        _mark_price(ImportedRawMarketRecord(observed_at=observed_at, payload=[]))


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
