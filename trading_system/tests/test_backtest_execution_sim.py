from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from trading_system.app.backtest.execution_sim import (
    DepthLevel,
    ExecutionFill,
    OrderBookSnapshot,
    TradePrint,
    _validate_evidence_contract,
    reference_close_fill,
    next_bar_ohlcv_fill,
    simulate_maker_limit_fill,
    simulate_taker_depth_fill,
    simulate_taker_fill,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_evidence_contract_rejects_duplicate_same_symbol_trade_fill_id() -> None:
    with pytest.raises(ValueError, match="duplicate trade.fill_id: fill-001"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id="fill-001",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="ETHUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id="fill-001",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:03Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id="fill-001",
                ),
            ),
        )


@pytest.mark.parametrize("fill_id", ["", " fill-001", "fill-001 ", 123])
def test_evidence_contract_rejects_noncanonical_trade_fill_id(fill_id: object) -> None:
    with pytest.raises(ValueError, match="trade.fill_id must be a canonical string"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id=fill_id,
                ),
            ),
        )


def test_evidence_contract_rejects_non_monotonic_same_symbol_trade_timestamps() -> None:
    with pytest.raises(ValueError, match="trade timestamps must be monotonic for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(),
            trades=(
                TradePrint(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", price=100.1, quantity=1.0),
                TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", price=100.2, quantity=1.0),
                TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=100.2, quantity=1.0),
            ),
        )


def test_evidence_contract_rejects_mixed_trade_timestamp_timezone_awareness() -> None:
    with pytest.raises(ValueError, match="trade.timestamp must be timezone-aware for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(),
            trades=(
                TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=100.1, quantity=1.0),
                TradePrint(timestamp=datetime(2026, 3, 10, 0, 0, 2), symbol="BTCUSDT", price=100.2, quantity=1.0),
            ),
        )


def test_evidence_contract_rejects_non_monotonic_same_symbol_order_book_timestamps() -> None:
    with pytest.raises(ValueError, match="order book timestamps must be monotonic for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=99.9, ask=100.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.8, ask=100.0),
            ),
            trades=(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid", True),
        ("bid", "99.9"),
        ("bid", math.nan),
        ("bid", math.inf),
        ("bid", -math.inf),
        ("bid", 0.0),
        ("bid", -1.0),
        ("ask", True),
        ("ask", "100.1"),
        ("ask", math.nan),
        ("ask", math.inf),
        ("ask", -math.inf),
        ("ask", 0.0),
        ("ask", -1.0),
    ],
)
def test_evidence_contract_rejects_invalid_order_book_bid_ask(field: str, value: object) -> None:
    book_kwargs = {"bid": 99.9, "ask": 100.1}
    book_kwargs[field] = value

    with pytest.raises(ValueError, match=f"order_book.{field} must be a positive finite number"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", **book_kwargs),
            ),
            trades=(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid_size", True),
        ("bid_size", "1.0"),
        ("bid_size", -1.0),
        ("ask_size", True),
        ("ask_size", "1.0"),
        ("ask_size", -1.0),
    ],
)
def test_evidence_contract_rejects_invalid_order_book_optional_size(field: str, value: object) -> None:
    book_kwargs = {"bid_size": None, "ask_size": None}
    book_kwargs[field] = value

    with pytest.raises(ValueError, match=f"order_book.{field} must be a non-negative finite number"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.1,
                    **book_kwargs,
                ),
            ),
            trades=(),
        )


def test_evidence_contract_rejects_crossed_order_book_bid_ask() -> None:
    with pytest.raises(ValueError, match="order_book.ask must be greater than or equal to bid"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=100.1,
                    ask=99.9,
                ),
            ),
            trades=(),
        )


@pytest.mark.parametrize(("field", "value"), [("price", 0.0), ("price", -1.0), ("quantity", 0.0), ("quantity", -1.0)])
def test_evidence_contract_rejects_non_positive_depth_level_values(field: str, value: object) -> None:
    level_kwargs = {"price": 99.9, "quantity": 1.0}
    level_kwargs[field] = value

    with pytest.raises(ValueError, match=f"depth level {field} must be a positive finite number"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.1,
                    bid_levels=(DepthLevel(**level_kwargs),),
                ),
            ),
            trades=(),
        )


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


def test_maker_buy_queue_ahead_consumes_sell_trade_volume_before_own_fill() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        latency_ms=0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=3.0,
                side="sell",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.filled is True
    assert fill.fill_price == pytest.approx(99.5)
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.queue_ahead_initial == pytest.approx(2.0)
    assert fill.queue_ahead_remaining == pytest.approx(0.0)
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.maker_wait_seconds == pytest.approx(1.0)
    assert "queue_depleted" in fill.maker_reasons


def test_maker_sell_queue_fills_only_on_buy_aggressor_trades_at_or_above_limit() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="sell",
        limit_price=100.5,
        quantity=1.0,
        queue_ahead_quantity=0.5,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.6,
                quantity=2.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.4,
                quantity=2.0,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=100.5,
                quantity=1.5,
                side="buy",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:03Z")


def test_maker_timeout_returns_expired_partial_with_unfilled_quantity() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        queue_ahead_quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=2.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=2.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.4,
                quantity=10.0,
                side="sell",
            ),
        ),
    )

    assert fill.maker_status == "expired"
    assert fill.filled is True
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.queue_ahead_remaining == pytest.approx(0.0)
    assert "timeout_expired" in fill.maker_reasons


def test_maker_latency_ignores_trade_prints_before_effective_placement_time() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=0.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=50,
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.010000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.060000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:00.060000Z")
    assert fill.maker_wait_seconds == pytest.approx(0.01)
    assert "latency_applied" in fill.maker_reasons


@pytest.mark.parametrize("latency_ms", [True, "50", math.nan, math.inf, -math.inf, -1.0])
def test_maker_latency_rejects_invalid_latency_ms(latency_ms: object) -> None:
    with pytest.raises(ValueError, match="latency_ms must be a non-negative finite number"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            latency_ms=latency_ms,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:00.060000Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


@pytest.mark.parametrize("timeout_seconds", [True, "2", math.nan, math.inf, -math.inf, -1.0])
def test_maker_timeout_rejects_invalid_timeout_seconds(timeout_seconds: object) -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be a non-negative finite number"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            timeout_seconds=timeout_seconds,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:00.060000Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


def test_maker_cancel_replace_before_fill_stops_later_prints() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=_ts("2026-03-10T00:00:02Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="sell",
            ),
        ),
    )

    assert fill.maker_status == "cancelled_replaced"
    assert fill.filled is False
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.queue_ahead_remaining == pytest.approx(1.0)
    assert "cancel_replace_before_fill" in fill.maker_reasons


def test_maker_cancel_replace_at_same_timestamp_as_trade_fails_closed_without_fill() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=0.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=_ts("2026-03-10T00:00:02Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
            ),
        ),
    )

    assert fill.maker_status == "cancelled_replaced"
    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.execution_price_source == "no_crossing_evidence"
    assert "cancel_replace_before_fill" in fill.maker_reasons


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


@pytest.mark.parametrize("limit_price", [True, "99.5", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_maker_limit_rejects_invalid_limit_price(limit_price: object) -> None:
    with pytest.raises(ValueError, match="limit_price must be a positive finite number"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=limit_price,
            quantity=1.0,
            trades=(TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=99.5, quantity=1.0),),
        )


@pytest.mark.parametrize("quantity", [True, "1.0", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_maker_limit_rejects_invalid_quantity(quantity: object) -> None:
    with pytest.raises(ValueError, match="quantity must be a positive finite number"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=quantity,
            trades=(TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=99.5, quantity=1.0),),
        )


@pytest.mark.parametrize("side", [True, "BUY", " buy ", "hold", "", 1])
def test_execution_sim_rejects_non_canonical_order_side(side: object) -> None:
    with pytest.raises(ValueError, match="side must be one of: buy, sell"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side=side,
            quantity=1.0,
            reference_price=100.0,
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
        )


@pytest.mark.parametrize("order_type", [True, "MARKET", " market ", "limit", "", 1])
def test_taker_fill_rejects_non_canonical_order_type(order_type: object) -> None:
    with pytest.raises(ValueError, match="order_type must be one of: market"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            order_type=order_type,
            quantity=1.0,
            reference_price=100.0,
        )


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


def test_taker_orderbook_fill_interval_is_bounded_by_supporting_book_timestamp() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=99.0, ask=100.0),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:03Z"), symbol="BTCUSDT", bid=99.8, ask=100.2),
        ),
    )

    assert fill.fill_model == "taker_orderbook"
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


@pytest.mark.parametrize("ask", [True, "100.1", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_taker_rejects_invalid_best_ask_price(ask: object) -> None:
    with pytest.raises(ValueError, match="order_book.ask must be a positive finite number"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=ask),
            ),
        )


def test_taker_depth_buy_consumes_multiple_ask_levels_with_weighted_average() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=3.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=101.0, quantity=2.0)),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx((100.0 * 1.0 + 101.0 * 2.0) / 3.0)
    assert fill.fill_model == "taker_orderbook_depth"
    assert fill.execution_price_source == "ask_depth"
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_quantity == pytest.approx(3.0)
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.filled_notional == pytest.approx(302.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.depth_levels_consumed == 2
    assert fill.execution_impact_bps == pytest.approx(((302.0 / 3.0) - 100.0) / 100.0 * 10_000.0)
    assert fill.slippage_bps == pytest.approx(((302.0 / 3.0) - 100.0) / 100.0 * 10_000.0)


def test_taker_depth_sell_consumes_multiple_bid_levels_with_weighted_average() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=4.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.2,
            bid_levels=(DepthLevel(price=100.0, quantity=1.5), DepthLevel(price=99.5, quantity=2.5)),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx((100.0 * 1.5 + 99.5 * 2.5) / 4.0)
    assert fill.fill_model == "taker_orderbook_depth"
    assert fill.execution_price_source == "bid_depth"
    assert fill.fill_quality == "evidence_backed"
    assert fill.filled_quantity == pytest.approx(4.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.depth_levels_consumed == 2
    assert fill.execution_impact_bps == pytest.approx((100.0 - fill.fill_price) / 100.0 * 10_000.0)


def test_taker_depth_buy_can_consume_by_requested_notional() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=251.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=101.0, quantity=2.0)),
        ),
    )

    assert fill.filled is True
    assert fill.requested_notional == pytest.approx(251.0)
    assert fill.filled_notional == pytest.approx(251.0)
    assert fill.filled_quantity == pytest.approx(1.0 + 151.0 / 101.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.depth_levels_consumed == 2


@pytest.mark.parametrize("requested_notional", [True, "251.0", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_taker_depth_rejects_invalid_requested_notional(requested_notional: object) -> None:
    with pytest.raises(ValueError, match="requested_notional must be a positive finite number"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            requested_notional=requested_notional,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


def test_taker_depth_returns_partial_fill_when_depth_is_insufficient() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=5.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=101.0, quantity=2.0)),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(302.0 / 3.0)
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_quantity == pytest.approx(5.0)
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.unfilled_quantity == pytest.approx(2.0)
    assert fill.depth_levels_consumed == 2


def test_taker_depth_returns_no_fill_without_side_liquidity() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            bid_levels=(DepthLevel(price=99.9, quantity=3.0),),
        ),
    )

    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.fill_quality == "no_fill"
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.depth_levels_consumed == 0


@pytest.mark.parametrize("quantity", [True, False, math.nan, math.inf, -math.inf])
def test_taker_depth_rejects_bool_and_non_finite_requested_quantity(quantity: float | bool) -> None:
    with pytest.raises(ValueError, match="quantity must be a finite number"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=quantity,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


@pytest.mark.parametrize("quantity", ["1.0", 0.0, -1.0])
def test_taker_depth_rejects_string_and_non_positive_requested_quantity(quantity: object) -> None:
    with pytest.raises(ValueError, match="quantity must be a positive finite number"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=quantity,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", True),
        ("price", "100.0"),
        ("price", math.nan),
        ("price", math.inf),
        ("price", -math.inf),
        ("quantity", True),
        ("quantity", "1.0"),
        ("quantity", math.nan),
        ("quantity", math.inf),
        ("quantity", -math.inf),
    ],
)
def test_taker_depth_rejects_non_exact_depth_level_values(field: str, value: object) -> None:
    level_kwargs = {"price": 100.0, "quantity": 1.0}
    level_kwargs[field] = value

    with pytest.raises(ValueError, match=f"depth level {field} must be a finite number"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(**level_kwargs),),
            ),
        )


@pytest.mark.parametrize(("field", "value"), [("price", 0.0), ("price", -1.0), ("quantity", 0.0), ("quantity", -1.0)])
def test_taker_depth_rejects_non_positive_depth_level_values(field: str, value: object) -> None:
    level_kwargs = {"price": 100.0, "quantity": 1.0}
    level_kwargs[field] = value

    with pytest.raises(ValueError, match=f"depth level {field} must be a positive finite number"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(**level_kwargs),),
            ),
        )


def test_taker_depth_caps_notional_fill_quantity_to_available_requested_notional() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=100.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=99.99, quantity=2.0),),
        ),
    )

    assert fill.filled_notional == pytest.approx(100.0)
    assert fill.filled_quantity <= (100.0 / 99.99) + 1e-12


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


def test_taker_buy_trade_print_fill_uses_buy_aggressor_evidence_and_preserves_request() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.5,
                quantity=10.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=2.0,
                side="buy",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.quantity == pytest.approx(2.0)
    assert fill.fill_price == pytest.approx(100.2)
    assert fill.fill_model == "taker_trade_print"
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


def test_taker_trade_print_fill_is_partial_when_print_quantity_is_smaller_than_request() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=0.75,
                side="buy",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.quantity == pytest.approx(2.0)
    assert fill.fill_price == pytest.approx(100.2)
    assert fill.fill_model == "taker_trade_print"
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(0.75)
    assert fill.filled_notional == pytest.approx(75.15)
    assert fill.unfilled_quantity == pytest.approx(1.25)


def test_taker_trade_print_fill_aggregates_multiple_eligible_prints_to_cover_request() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=0.75,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.6,
                quantity=1.25,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.9,
                quantity=10.0,
                side="sell",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.quantity == pytest.approx(2.0)
    assert fill.fill_price == pytest.approx(100.6)
    assert fill.fill_model == "taker_trade_print"
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(2.0)
    assert fill.filled_notional == pytest.approx(200.9)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


def test_taker_trade_print_fill_clips_overshoot_on_final_selected_print() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=1.5,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.6,
                quantity=1.0,
                side="buy",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_price == pytest.approx(100.6)
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(2.0)
    assert fill.filled_notional == pytest.approx(200.6)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


@pytest.mark.parametrize("price", [True, "100.0", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_taker_trade_print_rejects_invalid_price(price: object) -> None:
    with pytest.raises(ValueError, match="trade.price must be a positive finite number"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            trades=(
                TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=price, quantity=1.0),
            ),
        )


@pytest.mark.parametrize("quantity", [True, "1.0", math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_taker_trade_print_rejects_invalid_quantity(quantity: object) -> None:
    with pytest.raises(ValueError, match="trade.quantity must be a positive finite number"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            trades=(
                TradePrint(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", price=100.0, quantity=quantity),
            ),
        )


@pytest.mark.parametrize(
    ("side", "price", "message"),
    [
        ("buy", 99.4, "trade.price cannot be below contemporaneous order_book.bid for BTCUSDT"),
        ("sell", 100.6, "trade.price cannot be above contemporaneous order_book.ask for BTCUSDT"),
    ],
)
def test_trade_print_rejects_same_timestamp_price_outside_order_book_bounds(
    side: str,
    price: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side=side,
            quantity=1.0,
            reference_price=100.0,
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.5,
                    ask=100.5,
                ),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=price,
                    quantity=1.0,
                    side=side,
                ),
            ),
        )


@pytest.mark.parametrize("trade_side", [True, "BUY", " sell ", "hold", "", 1])
def test_maker_limit_rejects_non_canonical_trade_print_side(trade_side: object) -> None:
    with pytest.raises(ValueError, match="trade.side must be one of: buy, sell"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side=trade_side,
                ),
            ),
        )


def test_maker_limit_rejects_duplicate_trade_print_identifiers() -> None:
    with pytest.raises(ValueError, match="duplicate trade.fill_id: fill-001"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id="fill-001",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=0.5,
                    fill_id="fill-001",
                ),
            ),
        )


def test_taker_fill_rejects_non_monotonic_trade_print_timestamps() -> None:
    with pytest.raises(ValueError, match="trade timestamps must be monotonic for BTCUSDT"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="BTCUSDT",
                    price=100.1,
                    quantity=1.0,
                    fill_id="fill-002",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=100.2,
                    quantity=1.0,
                    fill_id="fill-003",
                ),
            ),
        )


def test_taker_fill_rejects_stale_trade_book_evidence_skew() -> None:
    with pytest.raises(ValueError, match="taker evidence timestamp skew exceeds tolerance for BTCUSDT"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:06Z"),
                    symbol="BTCUSDT",
                    price=100.0,
                    quantity=1.0,
                    side="buy",
                ),
            ),
        )


def test_maker_limit_validates_all_trade_rows_before_returning_fill() -> None:
    with pytest.raises(ValueError, match="trade.price must be a positive finite number"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    fill_id="fill-001",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="BTCUSDT",
                    price=math.nan,
                    quantity=1.0,
                    fill_id="fill-002",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("price", "99.5", "trade.price must be a positive finite number"),
        ("quantity", "1.0", "trade.quantity must be a positive finite number"),
    ],
)
def test_maker_limit_rejects_string_trade_evidence(field: str, value: object, match: str) -> None:
    trade_kwargs = {
        "timestamp": _ts("2026-03-10T00:00:01Z"),
        "symbol": "BTCUSDT",
        "price": 99.5,
        "quantity": 1.0,
    }
    trade_kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            trades=(TradePrint(**trade_kwargs),),
        )


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


def test_execution_fill_provenance_shape_is_deterministic_for_reference_close() -> None:
    fill = reference_close_fill(symbol="BTCUSDT", side="buy", quantity=1.0, close_price=100.0)

    assert fill.execution_provenance == {
        "simulator": "offline_execution_sim",
        "fill_model": "reference_close",
        "price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "evidence": "synthetic_reference",
    }


def test_execution_fill_rejects_non_canonical_maker_status() -> None:
    with pytest.raises(ValueError, match="maker_status must be one of: cancelled_replaced, expired, filled, no_fill, partial"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            maker_status=" filled ",
        )


def test_execution_fill_rejects_no_fill_with_fill_price() -> None:
    with pytest.raises(ValueError, match="no-fill execution cannot include fill_price"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=100.0,
            fill_model="maker_orderbook_trade_evidence",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="missed_alpha",
        )


@pytest.mark.parametrize(
    ("filled_quantity", "unfilled_quantity"),
    [
        (1.5, 0.4),
        (2.1, 0.0),
    ],
)
def test_execution_fill_rejects_quantity_conservation_break(
    filled_quantity: float,
    unfilled_quantity: float,
) -> None:
    with pytest.raises(ValueError, match="fill quantities must conserve requested quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="partial_evidence_backed",
            outcome="filled",
            requested_quantity=2.0,
            filled_quantity=filled_quantity,
            unfilled_quantity=unfilled_quantity,
        )


def test_execution_fill_rejects_full_evidence_backed_trade_print_with_unfilled_quantity() -> None:
    with pytest.raises(ValueError, match="evidence-backed trade-print fills cannot leave unfilled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_trade_print",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=2.0,
            filled_quantity=0.75,
            filled_notional=75.0,
            unfilled_quantity=1.25,
        )


def test_execution_fill_rejects_fill_timestamp_interval_inversion() -> None:
    with pytest.raises(ValueError, match="first_fill_timestamp cannot be after last_fill_timestamp"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            first_fill_timestamp=_ts("2026-03-10T00:00:02Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("quantity", True, "quantity must be a non-negative finite number"),
        ("quantity", math.nan, "quantity must be a non-negative finite number"),
        ("quantity", -1.0, "quantity must be a non-negative finite number"),
        ("fill_price", "100.0", "fill_price must be a positive finite number"),
        ("fill_price", True, "fill_price must be a positive finite number"),
        ("fill_price", math.inf, "fill_price must be a positive finite number"),
        ("requested_quantity", "1.0", "requested_quantity must be a non-negative finite number"),
        ("requested_quantity", -1.0, "requested_quantity must be a non-negative finite number"),
        ("filled_quantity", math.nan, "filled_quantity must be a non-negative finite number"),
        ("filled_notional", "100.0", "filled_notional must be a non-negative finite number"),
        ("filled_notional", True, "filled_notional must be a non-negative finite number"),
        ("unfilled_quantity", -1.0, "unfilled_quantity must be a non-negative finite number"),
        ("execution_impact_bps", math.inf, "execution_impact_bps must be a non-negative finite number"),
        ("slippage_bps", math.inf, "slippage_bps must be a finite number"),
        ("queue_ahead_initial", True, "queue_ahead_initial must be a non-negative finite number"),
        ("maker_wait_seconds", -1.0, "maker_wait_seconds must be a non-negative finite number"),
    ],
)
def test_execution_fill_rejects_invalid_numeric_contract_fields(field: str, value: object, match: str) -> None:
    fill_kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "taker_orderbook",
        "execution_price_source": "best_ask",
        "fill_quality": "evidence_backed",
        "outcome": "filled",
    }
    fill_kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**fill_kwargs)
