from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.fetch import (
    PHASE1_BINANCE_FUTURES_ENDPOINTS,
    Phase1RawMarketFetchResult,
    fetch_phase1_raw_market_coverage,
    main,
)
from trading_system.app.backtest.archive.raw_market import load_phase1_raw_market_series


class _FakeFetcher:
    def __init__(self, pages: dict[tuple[str, int], list[object]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, endpoint: str, params: dict[str, object]) -> list[object]:
        self.calls.append((endpoint, dict(params)))
        key = (endpoint, int(params["startTime"]))
        return list(self.pages.get(key, []))


def test_fetch_phase1_raw_market_coverage_paginates_ohlcv_with_exchange_max_limit(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    fetcher = _FakeFetcher(
        {
            (
                "/fapi/v1/klines",
                1711929600000,
            ): [
                [1711929600000, "70000", "70100", "69900", "70050", "123.4"],
                [1711933200000, "70050", "70200", "70000", "70100", "223.4"],
            ],
            (
                "/fapi/v1/klines",
                1711936800000,
            ): [
                [1711936800000, "70100", "70300", "70080", "70250", "323.4"],
            ],
        }
    )

    result = fetch_phase1_raw_market_coverage(
        archive_root=archive_root,
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T03:00:00Z",
        fetch_json=fetcher,
    )

    assert isinstance(result, Phase1RawMarketFetchResult)
    assert result.dataset == "ohlcv"
    assert result.symbol == "BTCUSDT"
    assert result.request_count == 2
    assert result.archived_count == 2
    assert result.record_count == 3
    assert [call[0] for call in fetcher.calls] == ["/fapi/v1/klines", "/fapi/v1/klines"]
    assert all(call[1]["limit"] == PHASE1_BINANCE_FUTURES_ENDPOINTS["ohlcv"].max_limit for call in fetcher.calls)
    assert fetcher.calls[0][1]["interval"] == "1h"
    assert fetcher.calls[1][1]["startTime"] == 1711936800000

    imported = load_phase1_raw_market_series(
        archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    assert [record.observed_at.isoformat().replace('+00:00', 'Z') for record in imported.records] == [
        "2024-04-01T00:00:00Z",
        "2024-04-01T01:00:00Z",
        "2024-04-01T02:00:00Z",
    ]


def test_fetch_phase1_raw_market_coverage_uses_dataset_specific_endpoint_defaults(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    funding_fetcher = _FakeFetcher(
        {
            (
                "/fapi/v1/fundingRate",
                1711929600000,
            ): [
                {"symbol": "BTCUSDT", "fundingTime": 1711929600000, "fundingRate": "0.0001"},
            ],
        }
    )
    open_interest_fetcher = _FakeFetcher(
        {
            (
                "/futures/data/openInterestHist",
                1711929600000,
            ): [
                {"symbol": "BTCUSDT", "timestamp": 1711929600000, "sumOpenInterest": "12345.6", "sumOpenInterestValue": "88888.0"},
            ],
        }
    )

    funding_result = fetch_phase1_raw_market_coverage(
        archive_root=archive_root,
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T08:00:00Z",
        fetch_json=funding_fetcher,
    )
    open_interest_result = fetch_phase1_raw_market_coverage(
        archive_root=archive_root,
        dataset="open_interest",
        symbol="BTCUSDT",
        coverage_start="2024-04-01T00:00:00Z",
        coverage_end="2024-04-01T01:00:00Z",
        fetch_json=open_interest_fetcher,
    )

    assert funding_result.record_count == 1
    assert funding_fetcher.calls[0][0] == "/fapi/v1/fundingRate"
    assert funding_fetcher.calls[0][1]["limit"] == PHASE1_BINANCE_FUTURES_ENDPOINTS["funding"].max_limit
    assert "interval" not in funding_fetcher.calls[0][1]

    assert open_interest_result.record_count == 1
    assert open_interest_fetcher.calls[0][0] == "/futures/data/openInterestHist"
    assert open_interest_fetcher.calls[0][1]["period"] == "1h"
    assert open_interest_fetcher.calls[0][1]["limit"] == PHASE1_BINANCE_FUTURES_ENDPOINTS["open-interest"].max_limit


def test_main_emits_structured_json_for_multiple_symbols(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive_root = tmp_path / "archive"
    pages = {
        ("/fapi/v1/klines", 1711929600000): [
            [1711929600000, "70000", "70100", "69900", "70050", "123.4"],
        ],
        ("/fapi/v1/klines", 1711933200000): [
            [1711933200000, "3500", "3520", "3490", "3510", "22.1"],
        ],
    }
    fetcher = _FakeFetcher(pages)

    exit_code = main(
        [
            "--archive-root",
            str(archive_root),
            "--dataset",
            "ohlcv",
            "--symbol",
            "BTCUSDT",
            "--symbol",
            "ETHUSDT",
            "--timeframe",
            "1h",
            "--coverage-start",
            "2024-04-01T00:00:00Z",
            "--coverage-end",
            "2024-04-01T01:00:00Z",
        ],
        fetch_json=fetcher,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [item["symbol"] for item in payload] == ["BTCUSDT", "ETHUSDT"]
    assert [item["record_count"] for item in payload] == [1, 1]
