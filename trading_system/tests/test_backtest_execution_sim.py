from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_system.app.backtest.execution_sim import (
    OrderBookSnapshot,
    TradePrint,
    next_bar_ohlcv_fill,
    simulate_maker_limit_fill,
    simulate_taker_fill,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_maker_buy_limit_fills_when_trade_path_crosses_limit_with_evidence() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.4, ask=100.0),
        ),
        trades=(
            TradePrint(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", price=99.5, quantity=1.0),
            TradePrint(timestamp=_ts("2026-03-10T00:00:03Z"), symbol="BTCUSDT", price=99.4, quantity=1.5),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(99.5)
    assert fill.fill_model == "maker_orderbook_trade_evidence"
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.outcome == "filled"


def test_maker_buy_limit_misses_when_no_trade_or_book_evidence_crosses_limit() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.6, ask=99.8),
        ),
        trades=(
            TradePrint(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", price=99.7, quantity=1.0),
        ),
    )

    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.fill_model == "maker_orderbook_trade_evidence"
    assert fill.execution_price_source == "no_crossing_evidence"
    assert fill.fill_quality == "no_fill"
    assert fill.outcome == "missed_alpha"


def test_maker_sell_limit_fills_when_bid_crosses_limit_with_orderbook_evidence() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="sell",
        limit_price=100.5,
        quantity=1.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=100.6, ask=100.8),
        ),
        trades=(),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.5)
    assert fill.fill_model == "maker_orderbook_trade_evidence"
    assert fill.execution_price_source == "book_cross"
    assert fill.fill_quality == "evidence_backed"


def test_taker_uses_best_ask_for_buy_when_orderbook_is_available() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.1)
    assert fill.fill_model == "taker_orderbook"
    assert fill.execution_price_source == "best_ask"
    assert fill.fill_quality == "evidence_backed"


def test_taker_without_orderbook_keeps_ohlcv_approximation_label() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=1.0,
        reference_price=100.0,
        order_books=(),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.0)
    assert fill.fill_model == "taker_ohlcv_approx"
    assert fill.execution_price_source == "ohlcv_reference"
    assert fill.fill_quality == "approximate"


def test_next_bar_ohlcv_fill_prefers_1m_then_5m_open_over_reference_close() -> None:
    fill = next_bar_ohlcv_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_close=100.0,
        symbol_payload={
            "15m": {"close": 100.0, "next_bar": {"open": 101.5, "timestamp": "2026-03-10T00:15:00Z"}},
            "5m": {"next_bar": {"open": 100.8, "timestamp": "2026-03-10T00:05:00Z"}},
            "1m": {"next_bar": {"open": 100.2, "timestamp": "2026-03-10T00:01:00Z"}},
        },
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.2)
    assert fill.fill_model == "next_bar_ohlcv"
    assert fill.execution_price_source == "ohlcv_next_open"
    assert fill.fill_quality == "evidence_backed"
    assert fill.execution_timeframe == "1m"
    assert fill.execution_lag_bars == 1
    assert fill.evidence_timestamp == _ts("2026-03-10T00:01:00Z")


def test_next_bar_ohlcv_fill_falls_back_to_reference_close_without_evidence() -> None:
    fill = next_bar_ohlcv_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=1.0,
        reference_close=100.0,
        symbol_payload={
            "15m": {"close": 99.0},
            "5m": {"close": 100.8},
        },
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.0)
    assert fill.fill_model == "reference_close"
    assert fill.execution_price_source == "ohlcv_close"
    assert fill.fill_quality == "approximate"
    assert fill.execution_timeframe == ""
    assert fill.execution_lag_bars == 0
