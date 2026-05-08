from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.backtest.archive.binance_execution_downloader import (
    BinanceExecutionDownloadError,
    BinanceExecutionHttpError,
    _execution_metadata,
    download_binance_execution_evidence,
    main,
)
from trading_system.app.backtest.archive.raw_market import load_phase1_raw_market_series


class _FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, endpoint: str, params: dict[str, object]) -> object:
        self.calls.append((endpoint, dict(params)))
        if not self.responses:
            return []
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_mocked_depth_response_writes_order_book_archive_with_endpoint_metadata(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            {
                "lastUpdateId": 123,
                "bids": [["64389.50", "3.25"]],
                "asks": [["64390.50", "2.75"]],
            },
        ]
    )

    result = download_binance_execution_evidence(
        archive_root=tmp_path / "archive",
        symbol="BTCUSDT",
        start_time="2024-02-29T23:00:00Z",
        end_time="2024-02-29T23:05:00Z",
        include_trades=False,
        fetch_json=transport,
        now=lambda: "2026-04-01T07:33:00Z",
    )

    assert result.order_book_record_count == 1
    assert result.trade_record_count == 0
    imported = load_phase1_raw_market_series(
        tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="order_book",
        symbol="BTCUSDT",
    )
    assert imported.records[0].payload == {
        "timestamp": "2026-04-01T07:33:00Z",
        "symbol": "BTCUSDT",
        "bid": "64389.50",
        "ask": "64390.50",
        "bid_size": "3.25",
        "ask_size": "2.75",
        "evidence_time_semantics": "point_in_time_fetch",
        "lastUpdateId": 123,
    }
    manifest = imported.files[0].manifest
    assert manifest["endpoint"] == "/fapi/v1/depth"
    assert manifest["metadata"]["endpoint"] == "/fapi/v1/depth"
    assert manifest["metadata"]["requested_window"] == {
        "start_time": "2024-02-29T23:00:00Z",
        "end_time": "2024-02-29T23:05:00Z",
    }
    assert manifest["metadata"]["evidence_time_semantics"] == "point_in_time_fetch_not_historical"
    assert manifest["metadata"]["rows"] == 1
    assert transport.calls == [("/fapi/v1/depth", {"symbol": "BTCUSDT", "limit": 5})]


@pytest.mark.parametrize(
    ("side", "index", "value", "expected_message"),
    [
        ("bids", 0, 64389.50, "depth payload top bid price must be a non-empty string"),
        ("asks", 0, True, "depth payload top ask price must be a non-empty string"),
        ("bids", 1, ["3.25"], "depth payload top bid quantity must be a non-empty string"),
        ("asks", 1, "", "depth payload top ask quantity must be a non-empty string"),
    ],
)
def test_depth_rejects_non_string_or_empty_top_of_book_values_without_partial_archive(
    tmp_path: Path,
    side: str,
    index: int,
    value: object,
    expected_message: str,
) -> None:
    payload = {
        "lastUpdateId": 123,
        "bids": [["64389.50", "3.25"]],
        "asks": [["64390.50", "2.75"]],
    }
    payload[side][0][index] = value
    transport = _FakeTransport([payload])

    with pytest.raises(BinanceExecutionDownloadError, match=expected_message):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_trades=False,
            fetch_json=transport,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


def test_mocked_agg_trades_response_writes_trade_rows_with_conservative_side(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": 10, "p": "64391.00", "q": "0.20", "T": 1709247602000, "m": False},
                {"a": 11, "p": "64392.00", "q": "0.30", "T": 1709247840000, "m": True},
            ],
        ]
    )

    result = download_binance_execution_evidence(
        archive_root=tmp_path / "archive",
        symbol="BTCUSDT",
        start_time="2024-02-29T23:00:00Z",
        end_time="2024-02-29T23:05:00Z",
        include_order_book=False,
        fetch_json=transport,
        sleep=lambda _: None,
        now=lambda: "2026-04-01T07:33:00Z",
    )

    assert result.trade_record_count == 2
    imported = load_phase1_raw_market_series(
        tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="trades",
        symbol="BTCUSDT",
    )
    assert [record.payload for record in imported.records] == [
        {
            "timestamp": 1709247602000,
            "symbol": "BTCUSDT",
            "price": "64391.00",
            "quantity": "0.20",
            "side": "buy",
            "agg_trade_id": 10,
            "is_buyer_maker": False,
            "evidence_time_semantics": "trade_execution_time",
        },
        {
            "timestamp": 1709247840000,
            "symbol": "BTCUSDT",
            "price": "64392.00",
            "quantity": "0.30",
            "side": "sell",
            "agg_trade_id": 11,
            "is_buyer_maker": True,
            "evidence_time_semantics": "historical_agg_trade_time",
        },
    ]
    assert imported.files[0].manifest["metadata"]["maker_side_mapping"] == {
        "m_false": "buyer_was_taker_side_buy",
        "m_true": "buyer_was_maker_side_sell",
    }


def test_agg_trades_rejects_non_boolean_maker_flag_without_partial_archive(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": 10, "p": "64391.00", "q": "0.20", "T": 1709247602000, "m": "false"},
            ],
        ]
    )

    with pytest.raises(BinanceExecutionDownloadError, match="aggTrades row maker flag must be boolean"):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_order_book=False,
            fetch_json=transport,
            sleep=lambda _: None,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("p", 64391.00, "aggTrades row price must be a string"),
        ("q", 0.20, "aggTrades row quantity must be a string"),
    ],
)
def test_agg_trades_rejects_non_string_price_or_quantity_without_partial_archive(
    tmp_path: Path,
    field: str,
    value: object,
    expected_message: str,
) -> None:
    payload = {"a": 10, "p": "64391.00", "q": "0.20", "T": 1709247602000, "m": False}
    payload[field] = value
    transport = _FakeTransport([[payload]])

    with pytest.raises(BinanceExecutionDownloadError, match=expected_message):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_order_book=False,
            fetch_json=transport,
            sleep=lambda _: None,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


def test_agg_trades_rejects_boolean_trade_id_without_partial_archive(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": True, "p": "64391.00", "q": "0.20", "T": 1709247602000, "m": False},
            ],
        ]
    )

    with pytest.raises(BinanceExecutionDownloadError, match="aggTrades row trade id must be integer"):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_order_book=False,
            fetch_json=transport,
            sleep=lambda _: None,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


def test_agg_trades_rejects_boolean_trade_timestamp_without_partial_archive(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": 10, "p": "64391.00", "q": "0.20", "T": True, "m": False},
            ],
        ]
    )

    with pytest.raises(BinanceExecutionDownloadError, match="aggTrades row trade timestamp must be integer"):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_order_book=False,
            fetch_json=transport,
            sleep=lambda _: None,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


def test_execution_metadata_rejects_present_non_mapping_extra() -> None:
    with pytest.raises(BinanceExecutionDownloadError, match="execution metadata extra must be a mapping"):
        _execution_metadata(
            endpoint="/fapi/v1/depth",
            symbol="BTCUSDT",
            requested_start_time="2024-02-29T23:00:00Z",
            requested_end_time="2024-02-29T23:05:00Z",
            fetched_at="2026-04-01T07:33:00Z",
            rows=1,
            evidence_time_semantics="point_in_time_fetch_not_historical",
            extra=[("x", "y")],
        )


def test_agg_trades_pagination_handles_multiple_pages_without_duplicate_rows(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": 10, "p": "100.00", "q": "1.00", "T": 1709247600000, "m": False},
                {"a": 11, "p": "101.00", "q": "1.10", "T": 1709247601000, "m": False},
            ],
            [
                {"a": 11, "p": "101.00", "q": "1.10", "T": 1709247601000, "m": False},
                {"a": 12, "p": "102.00", "q": "1.20", "T": 1709247602000, "m": True},
            ],
        ]
    )

    result = download_binance_execution_evidence(
        archive_root=tmp_path / "archive",
        symbol="BTCUSDT",
        start_time="2024-02-29T23:00:00Z",
        end_time="2024-02-29T23:00:03Z",
        include_order_book=False,
        agg_trades_limit=2,
        fetch_json=transport,
        sleep=lambda _: None,
        now=lambda: "2026-04-01T07:33:00Z",
    )

    imported = load_phase1_raw_market_series(
        tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="trades",
        symbol="BTCUSDT",
    )
    assert result.trade_request_count == 3
    assert [record.payload["agg_trade_id"] for record in imported.records] == [10, 11, 12]
    agg_trade_params = [call[1] for call in transport.calls if call[0] == "/fapi/v1/aggTrades"]
    assert [params.get("fromId") for params in agg_trade_params] == [None, 12, 13]
    assert "endTime" in agg_trade_params[0]
    assert all("endTime" not in params for params in agg_trade_params[1:])


def test_agg_trades_pagination_stops_when_from_id_page_is_beyond_requested_end(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            [
                {"a": 10, "p": "100.00", "q": "1.00", "T": 1709247600000, "m": False},
                {"a": 11, "p": "101.00", "q": "1.10", "T": 1709247601000, "m": False},
            ],
            [
                {"a": 12, "p": "102.00", "q": "1.20", "T": 1709247605000, "m": True},
                {"a": 13, "p": "103.00", "q": "1.30", "T": 1709247606000, "m": True},
            ],
        ]
    )

    result = download_binance_execution_evidence(
        archive_root=tmp_path / "archive",
        symbol="BTCUSDT",
        start_time="2024-02-29T23:00:00Z",
        end_time="2024-02-29T23:00:03Z",
        include_order_book=False,
        agg_trades_limit=2,
        fetch_json=transport,
        sleep=lambda _: None,
        now=lambda: "2026-04-01T07:33:00Z",
    )

    imported = load_phase1_raw_market_series(
        tmp_path / "archive",
        exchange="binance",
        market="futures",
        dataset="trades",
        symbol="BTCUSDT",
    )
    assert result.trade_request_count == 2
    assert [record.payload["agg_trade_id"] for record in imported.records] == [10, 11]
    assert [call[1].get("fromId") for call in transport.calls if call[0] == "/fapi/v1/aggTrades"] == [None, 12]


def test_rate_limit_error_is_retried_then_surfaces_without_partial_archive(tmp_path: Path) -> None:
    transport = _FakeTransport(
        [
            BinanceExecutionHttpError("rate limited", status_code=429),
            BinanceExecutionHttpError("rate limited", status_code=429),
        ]
    )

    with pytest.raises(BinanceExecutionDownloadError, match="aggTrades failed"):
        download_binance_execution_evidence(
            archive_root=tmp_path / "archive",
            symbol="BTCUSDT",
            start_time="2024-02-29T23:00:00Z",
            end_time="2024-02-29T23:05:00Z",
            include_order_book=False,
            max_retries=1,
            fetch_json=transport,
            sleep=lambda _: None,
            now=lambda: "2026-04-01T07:33:00Z",
        )

    assert not (tmp_path / "archive" / "raw-market").exists()


def test_cli_dry_run_proves_no_network_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden_network(endpoint: str, params: dict[str, object]) -> object:
        raise AssertionError(f"network should not be called for {endpoint} {params}")

    exit_code = main(
        [
            "--archive-root",
            str(tmp_path / "archive"),
            "--symbol",
            "BTCUSDT",
            "--start-time",
            "2024-02-29T23:00:00Z",
            "--end-time",
            "2024-02-29T23:05:00Z",
        ],
        fetch_json=forbidden_network,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["would_request"] == ["/fapi/v1/depth", "/fapi/v1/aggTrades"]
    assert not (tmp_path / "archive").exists()
