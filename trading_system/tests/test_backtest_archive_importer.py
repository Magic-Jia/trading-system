from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
