from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.importer import (
    build_phase1_dataset_bundle_materials,
    write_phase1_dataset_bundle,
)
from trading_system.app.backtest.archive.raw_market import archive_raw_market_payload, load_phase1_raw_market_imports
from trading_system.app.backtest.dataset import load_historical_dataset


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _archive_phase1_symbol_history(archive_root: Path, *, symbol: str) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    hourly_rows: list[dict[str, str | int]] = []
    funding_rows: list[dict[str, str | int]] = []
    open_interest_rows: list[dict[str, str | int]] = []

    for index in range(60 * 24):
        observed_at = start + timedelta(hours=index)
        close = 50_000.0 + (index * 10.0)
        volume = 1_000.0 + index
        hourly_rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": f"{close - 5.0:.6f}",
                "high": f"{close + 20.0:.6f}",
                "low": f"{close - 20.0:.6f}",
                "close": f"{close:.6f}",
                "volume": f"{volume:.6f}",
                "quote_asset_volume": f"{close * volume:.6f}",
            }
        )
        open_interest_rows.append(
            {
                "timestamp": _timestamp_ms(observed_at),
                "sumOpenInterest": f"{10_000.0 + (index * 10.0):.6f}",
            }
        )
        if index % 8 == 0:
            funding_rows.append(
                {
                    "fundingTime": _timestamp_ms(observed_at),
                    "fundingRate": f"{0.0001 + ((index // 8) * 0.000001):.8f}",
                }
            )

    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol=symbol,
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": symbol, "interval": "1h", "rows": hourly_rows},
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:31:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=funding_rows,
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="open_interest",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:32:00Z",
        endpoint="/futures/data/openInterestHist",
        payload=open_interest_rows,
    )


def test_build_phase1_dataset_bundle_materials_returns_dataset_ready_bundle_and_writes_dataset_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    latest = materials[-1]
    assert latest.run_id.startswith("phase1-import-")
    assert latest.metadata["timestamp"] == latest.timestamp.isoformat().replace("+00:00", "Z")
    assert latest.metadata["source"]["series_keys"] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
    ]

    market_symbol = latest.market_context["symbols"]["BTCUSDT"]
    assert latest.market_context["as_of"] == latest.metadata["timestamp"]
    assert latest.market_context["schema_version"] == "imported_market_context.v1"
    assert market_symbol["sector"] == "majors"
    assert market_symbol["liquidity_tier"] == "top"
    assert market_symbol["1h"]["close"] == pytest.approx(64_390.0)
    assert market_symbol["1h"]["volume_usdt_24h"] > 0.0
    assert market_symbol["4h"]["return_pct_3d"] > 0.0
    assert market_symbol["daily"]["return_pct_7d"] > 0.0

    assert latest.derivatives_snapshot["as_of"] == latest.metadata["timestamp"]
    assert latest.derivatives_snapshot["schema_version"] == "imported_derivatives_snapshot.v1"
    assert latest.derivatives_snapshot["rows"] == [
        {
            "symbol": "BTCUSDT",
            "funding_rate": pytest.approx(0.000279),
            "open_interest_usdt": pytest.approx(24_954_710_000.0),
            "open_interest_change_24h_pct": pytest.approx(0.019215, rel=1e-6),
            "mark_price_change_24h_pct": pytest.approx(0.0037412315, rel=1e-6),
            "taker_buy_sell_ratio": 1.0,
            "basis_bps": 0.0,
        }
    ]

    bundle_dir = write_phase1_dataset_bundle(latest, dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert bundle_dir == dataset_root / "2024-02-29T23-00-00Z__phase1-import-2024-02-29T23-00-00Z"
    assert len(rows) == 1
    assert rows[0].timestamp == latest.timestamp
    assert rows[0].market["symbols"]["BTCUSDT"]["1h"]["close"] == pytest.approx(64_390.0)
    assert rows[0].derivatives[0]["open_interest_usdt"] == pytest.approx(24_954_710_000.0)


def test_build_phase1_dataset_bundle_materials_requires_complete_phase1_symbol_set(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="/fapi/v1/klines",
        payload={"rows": [{"open_time": _timestamp_ms(start), "close": "50000.0"}]},
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:34:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": _timestamp_ms(start), "fundingRate": "0.0001"}],
    )

    imported = load_phase1_raw_market_imports(archive_root)

    with pytest.raises(ValueError, match="missing required phase1 raw-market series for symbol BTCUSDT: open-interest"):
        build_phase1_dataset_bundle_materials(imported)
