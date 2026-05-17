from __future__ import annotations

import pytest

from trading_system.app.backtest.microstructure_evidence import replay_l2_order_book


def _snapshot(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "type": "snapshot",
        "sequence": 100,
        "timestamp": "2026-05-17T08:00:00Z",
        "venue": "binance_futures",
        "symbol": "BTCUSDT",
        "bids": [
            {"price": 100.0, "quantity": 2.0},
            {"price": 99.5, "quantity": 4.0},
        ],
        "asks": [
            {"price": 100.5, "quantity": 1.5},
            {"price": 101.0, "quantity": 3.0},
        ],
    }
    row.update(overrides)
    return row


def _update(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "type": "update",
        "sequence": 101,
        "timestamp": "2026-05-17T08:00:01Z",
        "venue": "binance_futures",
        "symbol": "BTCUSDT",
        "bids": [
            {"price": 100.1, "quantity": 1.0},
            {"price": 99.5, "quantity": 0.0},
        ],
        "asks": [
            {"price": 100.5, "quantity": 0.0},
            {"price": 100.8, "quantity": 2.5},
        ],
    }
    row.update(overrides)
    return row


def test_l2_event_replay_reconstructs_book_and_reports_diagnostics() -> None:
    report = replay_l2_order_book(
        [
            _snapshot(),
            _update(),
            _update(
                sequence=102,
                timestamp="2026-05-17T08:00:02Z",
                bids=[{"price": 100.1, "quantity": 0.5}],
                asks=[{"price": 101.0, "quantity": 0.0}],
            ),
        ],
        venue="binance_futures",
        symbol="BTCUSDT",
    )

    assert report == {
        "schema_version": "l2_order_book_replay_report.v1",
        "venue": "binance_futures",
        "symbol": "BTCUSDT",
        "best_bid": 100.1,
        "best_ask": 100.8,
        "bid_level_count": 2,
        "ask_level_count": 1,
        "gap_detected": False,
        "crossed_book": False,
        "first_sequence": 100,
        "last_sequence": 102,
        "first_timestamp": "2026-05-17T08:00:00Z",
        "last_timestamp": "2026-05-17T08:00:02Z",
        "reason_codes": [],
    }


@pytest.mark.parametrize(
    ("events", "reason_code"),
    [
        ([_snapshot(), _update(sequence=102)], "sequence_gap"),
        ([_snapshot(), _update(sequence=100)], "duplicate_sequence"),
        ([_snapshot(), _update(sequence=99)], "out_of_order_sequence"),
        ([_snapshot(), _update(symbol="ETHUSDT")], "symbol_mismatch"),
        ([_snapshot(), _update(venue="coinbase")], "venue_mismatch"),
        ([_snapshot(bids=[{"price": -100.0, "quantity": 1.0}])], "invalid_price"),
        ([_snapshot(asks=[{"price": 100.5, "quantity": float("nan")}])], "invalid_quantity"),
        ([_snapshot(bids=[{"price": 100.0, "quantity": 1.0}, {"price": 100.0, "quantity": 2.0}])], "duplicate_level"),
        ([_snapshot(), _update(timestamp="2026-05-17T08:00:01+00:00")], "non_canonical_timestamp"),
        ([_snapshot(), _update(asks=[{"price": 100.0, "quantity": 1.0}])], "crossed_book"),
    ],
)
def test_l2_event_replay_fails_closed_for_malformed_evidence(
    events: list[dict[str, object]],
    reason_code: str,
) -> None:
    report = replay_l2_order_book(events, venue="binance_futures", symbol="BTCUSDT")

    assert report["reason_codes"] == [reason_code]
    assert report["gap_detected"] is (reason_code == "sequence_gap")
    assert report["crossed_book"] is (reason_code == "crossed_book")
    assert report["best_bid"] is None
    assert report["best_ask"] is None


def test_l2_event_replay_accepts_jsonl_records() -> None:
    report = replay_l2_order_book(
        [
            '{"type":"snapshot","sequence":100,"timestamp":"2026-05-17T08:00:00Z","venue":"binance_futures","symbol":"BTCUSDT","bids":[[100.0,2.0]],"asks":[[100.5,1.5]]}',
            '{"type":"update","sequence":101,"timestamp":"2026-05-17T08:00:01Z","venue":"binance_futures","symbol":"BTCUSDT","bids":[[100.2,1.0]],"asks":[[100.5,0.0],[100.8,3.0]]}',
        ],
        venue="binance_futures",
        symbol="BTCUSDT",
    )

    assert report["best_bid"] == 100.2
    assert report["best_ask"] == 100.8
    assert report["bid_level_count"] == 2
    assert report["ask_level_count"] == 1
    assert report["reason_codes"] == []
