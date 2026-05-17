from __future__ import annotations

import pytest

from trading_system.app.backtest.microstructure_evidence import (
    build_longitudinal_l2_replay_calibration_report,
    load_l2_replay_reports_jsonl,
    replay_l2_order_book,
)


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


def _replay_report(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
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
        "session_id": "binance_futures:BTCUSDT:2026-05-17T08:00:00Z",
        "reason_codes": [],
    }
    row.update(overrides)
    return row


def _replay_report_without(field_name: str) -> dict[str, object]:
    row = _replay_report()
    del row[field_name]
    return row


def test_longitudinal_l2_replay_calibration_aggregates_many_sessions() -> None:
    report = build_longitudinal_l2_replay_calibration_report(
        [
            _replay_report(
                bid_level_count=2,
                ask_level_count=4,
                session_id="session-1",
                first_timestamp="2026-05-15T08:00:00Z",
                last_timestamp="2026-05-15T08:00:02Z",
            ),
            _replay_report(
                bid_level_count=6,
                ask_level_count=8,
                session_id="session-2",
                first_timestamp="2026-05-16T08:00:00Z",
                last_timestamp="2026-05-16T08:00:02Z",
            ),
            _replay_report(
                bid_level_count=10,
                ask_level_count=12,
                session_id="session-3",
                first_timestamp="2026-05-17T08:00:00Z",
                last_timestamp="2026-05-17T08:00:02Z",
            ),
        ],
        venue="binance_futures",
        symbol="BTCUSDT",
        generated_at="2026-05-17T09:00:00Z",
        min_samples=3,
        required_session_count=3,
    )

    assert report == {
        "schema_version": "longitudinal_l2_replay_calibration_report.v1",
        "venue": "binance_futures",
        "symbol": "BTCUSDT",
        "generated_at": "2026-05-17T09:00:00Z",
        "sample_count": 3,
        "session_count": 3,
        "gap_rate": 0.0,
        "crossed_book_rate": 0.0,
        "stale_rate": 0.0,
        "median_bid_level_count": 6.0,
        "median_ask_level_count": 8.0,
        "max_bid_level_count": 10,
        "max_ask_level_count": 12,
        "first_timestamp": "2026-05-15T08:00:00Z",
        "last_timestamp": "2026-05-17T08:00:02Z",
        "quality_status": "pass",
        "reason_codes": [],
    }


def test_longitudinal_l2_replay_calibration_accepts_jsonl_fixture_path(tmp_path) -> None:
    path = tmp_path / "l2-replay-reports.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"schema_version":"l2_order_book_replay_report.v1","venue":"binance_futures","symbol":"BTCUSDT","best_bid":100.1,"best_ask":100.8,"bid_level_count":2,"ask_level_count":4,"gap_detected":false,"crossed_book":false,"first_sequence":100,"last_sequence":102,"first_timestamp":"2026-05-15T08:00:00Z","last_timestamp":"2026-05-15T08:00:02Z","session_id":"session-1","reason_codes":[]}',
                '{"schema_version":"l2_order_book_replay_report.v1","venue":"binance_futures","symbol":"BTCUSDT","best_bid":100.2,"best_ask":100.9,"bid_level_count":6,"ask_level_count":8,"gap_detected":false,"crossed_book":false,"first_sequence":200,"last_sequence":202,"first_timestamp":"2026-05-16T08:00:00Z","last_timestamp":"2026-05-16T08:00:02Z","session_id":"session-2","reason_codes":[]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_longitudinal_l2_replay_calibration_report(
        load_l2_replay_reports_jsonl(path),
        venue="binance_futures",
        symbol="BTCUSDT",
        generated_at="2026-05-17T09:00:00Z",
        min_samples=2,
        required_session_count=2,
    )

    assert report["sample_count"] == 2
    assert report["session_count"] == 2
    assert report["quality_status"] == "pass"


@pytest.mark.parametrize(
    ("reports", "expected_error"),
    [
        ([], "longitudinal L2 replay samples must be non-empty"),
        ([_replay_report(venue="coinbase")], "l2_replay_reports\\[1\\] venue mismatch"),
        ([_replay_report(symbol="ETHUSDT")], "l2_replay_reports\\[1\\] symbol mismatch"),
        ([_replay_report(first_timestamp="2026-05-17T08:00:00+00:00")], "first_timestamp must be canonical"),
        ([_replay_report(gap_detected=True, reason_codes=["not_a_replay_reason"])], "unknown replay reason code"),
        ([_replay_report(gap_detected=True, gap_rate=-0.1)], "unknown l2_replay_reports\\[1\\] field"),
        ([_replay_report(bid_level_count=-1)], "bid_level_count must be non-negative"),
        ([_replay_report_without("bid_level_count")], "missing required field: bid_level_count"),
        ([_replay_report(session_id="session-1"), _replay_report(session_id="session-1")], "duplicate session identity"),
    ],
)
def test_longitudinal_l2_replay_calibration_fails_closed_for_invalid_reports(
    reports: list[dict[str, object]], expected_error: str
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        build_longitudinal_l2_replay_calibration_report(
            reports,
            venue="binance_futures",
            symbol="BTCUSDT",
            generated_at="2026-05-17T09:00:00Z",
        )


def test_longitudinal_l2_replay_calibration_degrades_quality_conservatively() -> None:
    report = build_longitudinal_l2_replay_calibration_report(
        [
            _replay_report(gap_detected=True, session_id="session-1", reason_codes=["sequence_gap"]),
            _replay_report(crossed_book=True, session_id="session-2", reason_codes=["crossed_book"]),
            _replay_report(
                session_id="session-3",
                last_timestamp="2026-05-17T08:05:00Z",
                reason_codes=["stale_replay_data"],
            ),
        ],
        venue="binance_futures",
        symbol="BTCUSDT",
        generated_at="2026-05-17T09:00:00Z",
        min_samples=5,
        required_session_count=4,
        max_gap_rate=0.0,
    )

    assert report["quality_status"] == "review"
    assert report["gap_rate"] == pytest.approx(1 / 3)
    assert report["crossed_book_rate"] == pytest.approx(1 / 3)
    assert report["stale_rate"] == pytest.approx(1 / 3)
    assert report["reason_codes"] == [
        "insufficient_samples",
        "missing_sessions",
        "gap_rate_above_threshold",
        "crossed_book_detected",
        "stale_replay_data",
    ]
