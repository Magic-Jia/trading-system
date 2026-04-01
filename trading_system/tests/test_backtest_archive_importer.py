from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.raw_market import archive_raw_market_payload


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
