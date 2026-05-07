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
