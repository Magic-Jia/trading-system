from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

OrderSide = Literal["buy", "sell"]
ExecutionFillModel = Literal[
    "reference_close",
    "next_bar_ohlcv",
    "taker_ohlcv_approx",
    "taker_orderbook",
    "maker_orderbook_trade_evidence",
]
ExecutionPriceSource = Literal[
    "ohlcv_close",
    "ohlcv_next_open",
    "ohlcv_reference",
    "best_bid",
    "best_ask",
    "trade_print",
    "book_cross",
    "no_crossing_evidence",
]
FillQuality = Literal["approximate", "evidence_backed", "no_fill"]
FillOutcome = Literal["filled", "missed_alpha"]


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class TradePrint:
    timestamp: datetime
    symbol: str
    price: float
    quantity: float


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    symbol: str
    side: OrderSide
    quantity: float
    filled: bool
    fill_price: float | None
    fill_model: ExecutionFillModel
    execution_price_source: ExecutionPriceSource
    fill_quality: FillQuality
    outcome: FillOutcome
    evidence_timestamp: datetime | None = None
    execution_timeframe: str = ""
    execution_lag_bars: int = 0


def reference_close_fill(*, symbol: str, side: OrderSide, quantity: float, close_price: float) -> ExecutionFill:
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled=True,
        fill_price=float(close_price),
        fill_model="reference_close",
        execution_price_source="ohlcv_close",
        fill_quality="approximate",
        outcome="filled",
    )


def next_bar_ohlcv_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    reference_close: float,
    symbol_payload: Mapping[str, Any],
    execution_timeframes: tuple[str, ...] = ("1m", "5m", "15m", "30m"),
) -> ExecutionFill:
    for timeframe in execution_timeframes:
        timeframe_row = symbol_payload.get(timeframe)
        if not isinstance(timeframe_row, Mapping):
            continue
        next_bar = timeframe_row.get("next_bar")
        if not isinstance(next_bar, Mapping):
            continue
        open_price = _positive_float(next_bar.get("open"))
        if open_price is None:
            continue
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=quantity,
            filled=True,
            fill_price=open_price,
            fill_model="next_bar_ohlcv",
            execution_price_source="ohlcv_next_open",
            fill_quality="evidence_backed",
            outcome="filled",
            evidence_timestamp=_datetime_or_none(next_bar.get("timestamp")),
            execution_timeframe=timeframe,
            execution_lag_bars=1,
        )

    return reference_close_fill(symbol=symbol, side=side, quantity=quantity, close_price=reference_close)


def simulate_taker_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    reference_price: float,
    order_books: tuple[OrderBookSnapshot, ...] = (),
) -> ExecutionFill:
    book = _first_symbol_book(symbol, order_books)
    if book is None:
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=quantity,
            filled=True,
            fill_price=float(reference_price),
            fill_model="taker_ohlcv_approx",
            execution_price_source="ohlcv_reference",
            fill_quality="approximate",
            outcome="filled",
        )

    if side == "buy":
        price = book.ask
        source: ExecutionPriceSource = "best_ask"
    else:
        price = book.bid
        source = "best_bid"
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled=True,
        fill_price=float(price),
        fill_model="taker_orderbook",
        execution_price_source=source,
        fill_quality="evidence_backed",
        outcome="filled",
        evidence_timestamp=book.timestamp,
    )


def simulate_maker_limit_fill(
    *,
    symbol: str,
    side: OrderSide,
    limit_price: float,
    quantity: float,
    order_books: tuple[OrderBookSnapshot, ...] = (),
    trades: tuple[TradePrint, ...] = (),
) -> ExecutionFill:
    sorted_trades = sorted((trade for trade in trades if trade.symbol == symbol), key=lambda trade: trade.timestamp)
    filled_qty = 0.0
    for trade in sorted_trades:
        if not _crosses_limit(side=side, price=trade.price, limit_price=limit_price):
            continue
        filled_qty += max(0.0, float(trade.quantity))
        if filled_qty >= quantity:
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                filled=True,
                fill_price=float(limit_price),
                fill_model="maker_orderbook_trade_evidence",
                execution_price_source="trade_print",
                fill_quality="evidence_backed",
                outcome="filled",
                evidence_timestamp=trade.timestamp,
            )

    sorted_books = sorted((book for book in order_books if book.symbol == symbol), key=lambda book: book.timestamp)
    for book in sorted_books:
        book_price = book.ask if side == "buy" else book.bid
        if _crosses_limit(side=side, price=book_price, limit_price=limit_price):
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                filled=True,
                fill_price=float(limit_price),
                fill_model="maker_orderbook_trade_evidence",
                execution_price_source="book_cross",
                fill_quality="evidence_backed",
                outcome="filled",
                evidence_timestamp=book.timestamp,
            )

    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled=False,
        fill_price=None,
        fill_model="maker_orderbook_trade_evidence",
        execution_price_source="no_crossing_evidence",
        fill_quality="no_fill",
        outcome="missed_alpha",
    )


def _first_symbol_book(symbol: str, order_books: tuple[OrderBookSnapshot, ...]) -> OrderBookSnapshot | None:
    return next((book for book in sorted(order_books, key=lambda item: item.timestamp) if book.symbol == symbol), None)


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _crosses_limit(*, side: OrderSide, price: float, limit_price: float) -> bool:
    if side == "buy":
        return float(price) <= float(limit_price)
    return float(price) >= float(limit_price)
