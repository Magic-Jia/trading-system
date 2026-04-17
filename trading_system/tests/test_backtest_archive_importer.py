from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.raw_market import (
    archive_raw_market_payload,
    load_phase1_raw_market_imports,
    load_phase1_raw_market_series,
)


def test_archive_raw_market_payload_writes_manifest_and_canonical_futures_ohlcv_path(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    payload = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "rows": [
            {"open_time": 1711929600000, "close": "70000.0"},
            {"open_time": 1711933200000, "close": "70100.0"},
        ],
    }

    stored = archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T01:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/klines",
        payload=payload,
    )

    expected_dir = archive_root / "raw-market" / "binance" / "futures" / "ohlcv" / "BTCUSDT" / "1h"
    expected_payload = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    expected_stem = "2024-04-01T00-00-00Z__2024-04-01T01-00-00Z__2026-04-01T01-02-03Z"

    assert stored.storage_dir == expected_dir
    assert stored.data_path == expected_dir / f"{expected_stem}.json"
    assert stored.manifest_path == expected_dir / f"{expected_stem}.manifest.json"
    assert stored.data_path.read_bytes() == expected_payload
    assert json.loads(stored.manifest_path.read_text(encoding="utf-8")) == {
        "schema_version": "raw_market_manifest.v1",
        "source": "binance",
        "exchange": "binance",
        "endpoint": "/fapi/v1/klines",
        "market": "futures",
        "dataset": "ohlcv",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "coverage_start": "2024-04-01T00:00:00Z",
        "coverage_end": "2024-04-01T01:00:00Z",
        "fetched_at": "2026-04-01T01:02:03Z",
        "sync": {
            "mode": "coverage",
            "series_key": "binance:futures:ohlcv:BTCUSDT:1h",
            "cursor_field": "coverage_end",
            "cursor": "2024-04-01T01:00:00Z",
            "next_start": "2024-04-01T01:00:00Z",
        },
        "file": {
            "path": str(stored.data_path),
            "sha256": hashlib.sha256(expected_payload).hexdigest(),
            "size_bytes": len(expected_payload),
        },
    }


def test_archive_raw_market_payload_omits_timeframe_segment_for_non_ohlcv_dataset(tmp_path: Path) -> None:
    stored = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        timeframe=None,
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-07T00:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": 1711929600000, "fundingRate": "0.0001"}],
    )

    assert stored.storage_dir == tmp_path / "archive" / "raw-market" / "binance" / "futures" / "funding" / "BTCUSDT"
    assert stored.data_path.parent == stored.storage_dir
    assert "timeframe" not in stored.manifest


def test_archive_raw_market_payload_round_trips_explicit_symbol_metadata_through_imports(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    symbol_metadata = {
        "listing_timestamp": "2020-05-01T00:00:00Z",
        "quantity_step": 0.005,
        "price_tick": 0.25,
    }

    stored = archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T02:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/klines",
        payload={
            "symbol": "BTCUSDT",
            "interval": "1h",
            "rows": [
                {"open_time": 1711929600000, "close": "70000.0"},
                {"open_time": 1711933200000, "close": "70100.0"},
            ],
        },
        symbol_metadata=symbol_metadata,
    )

    imported = load_phase1_raw_market_series(
        archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
    )

    assert stored.manifest["symbol_metadata"] == symbol_metadata
    assert imported.symbol_metadata == symbol_metadata


def test_archive_raw_market_payload_rejects_invalid_symbol_metadata_without_writing_archive_files(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    expected_dir = archive_root / "raw-market" / "binance" / "futures" / "ohlcv" / "BTCUSDT" / "1h"

    with pytest.raises(ValueError, match="raw-market symbol_metadata quantity_step must be positive"):
        archive_raw_market_payload(
            archive_root=archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start="2024-04-01T00:00:00Z",
            coverage_end="2024-04-01T02:00:00Z",
            fetched_at="2026-04-01T01:02:03Z",
            endpoint="/fapi/v1/klines",
            payload={
                "symbol": "BTCUSDT",
                "interval": "1h",
                "rows": [
                    {"open_time": 1711929600000, "close": "70000.0"},
                    {"open_time": 1711933200000, "close": "70100.0"},
                ],
            },
            symbol_metadata={
                "listing_timestamp": "2020-05-01T00:00:00Z",
                "quantity_step": 0.0,
                "price_tick": 0.25,
            },
        )

    assert not expected_dir.exists() or list(expected_dir.iterdir()) == []


def test_archive_raw_market_payload_adds_coverage_driven_sync_metadata_and_canonical_open_interest_dataset(
    tmp_path: Path,
) -> None:
    stored = archive_raw_market_payload(
        archive_root=tmp_path / "archive",
        exchange="Binance",
        market="FUTURES",
        dataset="open_interest",
        symbol="BTCUSDT",
        timeframe=None,
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T04:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/futures/data/openInterestHist",
        payload=[{"symbol": "BTCUSDT", "sumOpenInterest": "12345.6"}],
    )

    assert stored.storage_dir == tmp_path / "archive" / "raw-market" / "binance" / "futures" / "open-interest" / "BTCUSDT"
    assert stored.manifest["dataset"] == "open-interest"
    assert stored.manifest["sync"] == {
        "mode": "coverage",
        "series_key": "binance:futures:open-interest:BTCUSDT",
        "cursor_field": "coverage_end",
        "cursor": "2024-04-01T04:00:00Z",
        "next_start": "2024-04-01T04:00:00Z",
    }


def test_archive_raw_market_payload_rejects_out_of_scope_phase1_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only binance futures raw-market datasets are supported in phase 1"):
        archive_raw_market_payload(
            archive_root=tmp_path / "archive",
            exchange="okx",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start="2024-04-01T00:00:00Z",
            coverage_end="2024-04-01T01:00:00Z",
            fetched_at="2026-04-01T01:02:03Z",
            endpoint="/api/v5/market/candles",
            payload=[],
        )

    with pytest.raises(ValueError, match="dataset must be one of: funding, ohlcv, open-interest"):
        archive_raw_market_payload(
            archive_root=tmp_path / "archive",
            exchange="binance",
            market="futures",
            dataset="trades",
            symbol="BTCUSDT",
            timeframe=None,
            coverage_start="2024-04-01T00:00:00Z",
            coverage_end="2024-04-01T01:00:00Z",
            fetched_at="2026-04-01T01:02:03Z",
            endpoint="/fapi/v1/trades",
            payload=[],
        )

    with pytest.raises(ValueError, match="ohlcv dataset requires timeframe"):
        archive_raw_market_payload(
            archive_root=tmp_path / "archive",
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe=None,
            coverage_start="2024-04-01T00:00:00Z",
            coverage_end="2024-04-01T01:00:00Z",
            fetched_at="2026-04-01T01:02:03Z",
            endpoint="/fapi/v1/klines",
            payload=[],
        )

    with pytest.raises(ValueError, match="funding dataset does not allow timeframe"):
        archive_raw_market_payload(
            archive_root=tmp_path / "archive",
            exchange="binance",
            market="futures",
            dataset="funding",
            symbol="BTCUSDT",
            timeframe="1h",
            coverage_start="2024-04-01T00:00:00Z",
            coverage_end="2024-04-01T01:00:00Z",
            fetched_at="2026-04-01T01:02:03Z",
            endpoint="/fapi/v1/fundingRate",
            payload=[],
        )


def test_archive_raw_market_payload_rejects_duplicate_coverage_window_even_with_new_fetch_timestamp(
    tmp_path: Path,
) -> None:
    kwargs = {
        "archive_root": tmp_path / "archive",
        "exchange": "binance",
        "market": "futures",
        "dataset": "ohlcv",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "coverage_start": "2024-04-01T00:00:00Z",
        "coverage_end": "2024-04-01T01:00:00Z",
        "endpoint": "/fapi/v1/klines",
        "payload": [{"open_time": 1711929600000, "close": "70000.0"}],
    }

    archive_raw_market_payload(fetched_at="2026-04-01T01:02:03Z", **kwargs)

    with pytest.raises(FileExistsError, match="coverage window already archived"):
        archive_raw_market_payload(fetched_at="2026-04-01T01:05:00Z", **kwargs)


def test_load_phase1_raw_market_series_rejects_overlapping_coverage_windows_for_same_series(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T04:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/klines",
        payload={
            "symbol": "BTCUSDT",
            "interval": "1h",
            "rows": [
                {"open_time": 1711929600000, "close": "70000.0"},
                {"open_time": 1711933200000, "close": "70100.0"},
                {"open_time": 1711936800000, "close": "70200.0"},
                {"open_time": 1711940400000, "close": "70300.0"},
            ],
        },
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T02:00:00Z",
        coverage_end="2024-04-01T06:00:00Z",
        fetched_at="2026-04-01T01:05:03Z",
        endpoint="/fapi/v1/klines",
        payload={
            "symbol": "BTCUSDT",
            "interval": "1h",
            "rows": [
                {"open_time": 1711936800000, "close": "70250.0"},
                {"open_time": 1711940400000, "close": "70350.0"},
                {"open_time": 1711944000000, "close": "70450.0"},
                {"open_time": 1711947600000, "close": "70550.0"},
            ],
        },
    )

    with pytest.raises(ValueError, match="raw-market coverage windows overlap for series"):
        load_phase1_raw_market_series(
            archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol="BTCUSDT",
            timeframe="1h",
        )


def test_load_phase1_raw_market_series_reads_manifest_backed_files_into_importer_structures(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T02:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/klines",
        payload={
            "symbol": "BTCUSDT",
            "interval": "1h",
            "rows": [
                {"open_time": 1711929600000, "close": "70000.0"},
                {"open_time": 1711933200000, "close": "70100.0"},
            ],
        },
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T02:00:00Z",
        coverage_end="2024-04-01T04:00:00Z",
        fetched_at="2026-04-01T01:05:03Z",
        endpoint="/fapi/v1/klines",
        payload={
            "symbol": "BTCUSDT",
            "interval": "1h",
            "rows": [
                {"open_time": 1711936800000, "close": "70200.0"},
                {"open_time": 1711940400000, "close": "70300.0"},
            ],
        },
    )

    imported = load_phase1_raw_market_series(
        archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
    )

    assert imported.series_key == "binance:futures:ohlcv:BTCUSDT:1h"
    assert imported.dataset == "ohlcv"
    assert imported.symbol == "BTCUSDT"
    assert imported.timeframe == "1h"
    assert len(imported.files) == 2
    assert [file.coverage_start for file in imported.files] == [
        datetime(2024, 4, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 4, 1, 2, 0, tzinfo=UTC),
    ]
    assert [record.observed_at for record in imported.records] == [
        datetime(2024, 4, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 4, 1, 1, 0, tzinfo=UTC),
        datetime(2024, 4, 1, 2, 0, tzinfo=UTC),
        datetime(2024, 4, 1, 3, 0, tzinfo=UTC),
    ]
    assert [record.payload["close"] for record in imported.records] == ["70000.0", "70100.0", "70200.0", "70300.0"]


def test_load_phase1_raw_market_imports_groups_supported_binance_futures_series(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T16:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": 1711929600000, "fundingRate": "0.0001"}],
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="open_interest",
        symbol="BTCUSDT",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T01:00:00Z",
        fetched_at="2026-04-01T01:03:03Z",
        endpoint="/futures/data/openInterestHist",
        payload=[{"timestamp": 1711929600000, "sumOpenInterest": "12345.6"}],
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T01:00:00Z",
        fetched_at="2026-04-01T01:04:03Z",
        endpoint="/fapi/v1/klines",
        payload={"rows": [{"open_time": 1711929600000, "close": "70000.0"}]},
    )

    imported = load_phase1_raw_market_imports(archive_root)

    assert [series.series_key for series in imported] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
    ]
    assert imported[0].records[0].payload["fundingRate"] == "0.0001"
    assert imported[1].records[0].payload["close"] == "70000.0"
    assert imported[2].records[0].payload["sumOpenInterest"] == "12345.6"


def test_load_phase1_raw_market_series_rejects_manifest_file_hash_mismatch(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archived = archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T16:00:00Z",
        fetched_at="2026-04-01T01:02:03Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": 1711929600000, "fundingRate": "0.0001"}],
    )
    archived.data_path.write_text('[{"fundingTime":1711929600000,"fundingRate":"0.0002"}]', encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market file sha256 mismatch"):
        load_phase1_raw_market_series(
            archive_root,
            exchange="binance",
            market="futures",
            dataset="funding",
            symbol="BTCUSDT",
        )
