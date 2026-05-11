from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any, Literal, Mapping

OrderSide = Literal["buy", "sell"]
ExecutionFillModel = Literal[
    "reference_close",
    "next_bar_ohlcv",
    "taker_ohlcv_approx",
    "taker_orderbook",
    "taker_orderbook_depth",
    "taker_trade_print",
    "maker_orderbook_trade_evidence",
    "maker_post_only_queue",
]
ExecutionPriceSource = Literal[
    "ohlcv_close",
    "ohlcv_next_open",
    "ohlcv_reference",
    "best_bid",
    "best_ask",
    "bid_depth",
    "ask_depth",
    "trade_print",
    "book_cross",
    "no_crossing_evidence",
]
FillQuality = Literal["approximate", "evidence_backed", "partial_evidence_backed", "no_fill"]
FillOutcome = Literal["filled", "missed_alpha"]
TradePrintSide = Literal["buy", "sell"]
MakerStatus = Literal["filled", "partial", "no_fill", "expired", "cancelled_replaced"]


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: float
    quantity: float


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_size: float | None = None
    ask_size: float | None = None
    bid_levels: tuple[DepthLevel, ...] = ()
    ask_levels: tuple[DepthLevel, ...] = ()


@dataclass(frozen=True, slots=True)
class TradePrint:
    timestamp: datetime
    symbol: str
    price: float
    quantity: float
    side: TradePrintSide | None = None


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
    requested_quantity: float | None = None
    requested_notional: float | None = None
    filled_quantity: float | None = None
    filled_notional: float | None = None
    unfilled_quantity: float | None = None
    depth_levels_consumed: int | None = None
    execution_impact_bps: float | None = None
    slippage_bps: float | None = None
    maker_status: MakerStatus | None = None
    first_fill_timestamp: datetime | None = None
    last_fill_timestamp: datetime | None = None
    queue_ahead_initial: float | None = None
    queue_ahead_remaining: float | None = None
    maker_wait_seconds: float | None = None
    maker_reasons: tuple[str, ...] = ()


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
    trades: tuple[TradePrint, ...] = (),
) -> ExecutionFill:
    book = _first_symbol_book(symbol, order_books)
    if book is not None:
        if _side_levels(book, side=side):
            return simulate_taker_depth_fill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                reference_price=reference_price,
                order_book=book,
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
            requested_quantity=quantity,
            filled_quantity=quantity if quantity > 0.0 else None,
            filled_notional=(quantity * float(price)) if quantity > 0.0 else None,
            unfilled_quantity=0.0 if quantity > 0.0 else None,
            depth_levels_consumed=1 if quantity > 0.0 else None,
        )

    trade_fill = _conservative_trade_print_taker_fill(symbol=symbol, side=side, quantity=quantity, trades=trades)
    if trade_fill is not None:
        return trade_fill

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


def simulate_taker_depth_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float | None = None,
    requested_notional: float | None = None,
    reference_price: float,
    order_book: OrderBookSnapshot,
) -> ExecutionFill:
    notional_request = None
    if requested_notional is not None:
        notional_request = _positive_finite_float("requested_notional", requested_notional)
    requested_quantity = 0.0 if quantity is None else max(_finite_float("quantity", quantity), 0.0)
    levels = _side_levels(order_book, side=side)
    source: ExecutionPriceSource = "ask_depth" if side == "buy" else "bid_depth"
    if not levels:
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=requested_quantity,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source=source,
            fill_quality="no_fill",
            outcome="missed_alpha",
            evidence_timestamp=order_book.timestamp,
            requested_quantity=requested_quantity,
            requested_notional=notional_request,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=requested_quantity,
            depth_levels_consumed=0,
        )
    if requested_quantity <= 0.0 and notional_request is None:
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=requested_quantity,
            filled=True,
            fill_price=float(levels[0].price),
            fill_model="taker_orderbook_depth",
            execution_price_source=source,
            fill_quality="evidence_backed",
            outcome="filled",
            evidence_timestamp=order_book.timestamp,
            requested_quantity=requested_quantity,
            requested_notional=None,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=0,
            execution_impact_bps=0.0,
            slippage_bps=_side_slippage_bps(side=side, fill_price=float(levels[0].price), reference_price=reference_price),
        )

    remaining = requested_quantity
    remaining_notional = notional_request
    filled_quantity = 0.0
    filled_notional = 0.0
    levels_consumed = 0
    for level in levels:
        available = max(float(level.quantity), 0.0)
        if available <= 0.0:
            continue
        if remaining_notional is not None:
            take_quantity = min(available, remaining_notional / float(level.price))
        else:
            take_quantity = min(remaining, available)
        if take_quantity <= 0.0:
            continue
        filled_quantity += take_quantity
        level_notional = take_quantity * float(level.price)
        filled_notional += level_notional
        if remaining_notional is not None:
            remaining_notional -= level_notional
        else:
            remaining -= take_quantity
        levels_consumed += 1
        if remaining_notional is not None and remaining_notional <= 1e-12:
            remaining_notional = 0.0
            break
        if remaining_notional is None and remaining <= 1e-12:
            remaining = 0.0
            break

    if filled_quantity <= 0.0:
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=requested_quantity,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source=source,
            fill_quality="no_fill",
            outcome="missed_alpha",
            evidence_timestamp=order_book.timestamp,
            requested_quantity=requested_quantity,
            requested_notional=notional_request,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=requested_quantity,
            depth_levels_consumed=0,
        )

    average_price = filled_notional / filled_quantity
    top_price = float(levels[0].price)
    unfilled_quantity = 0.0 if remaining_notional is not None and remaining_notional <= 0.0 else remaining
    if remaining_notional is not None and remaining_notional > 0.0:
        fill_quality: FillQuality = "partial_evidence_backed"
    else:
        fill_quality = "evidence_backed" if remaining <= 0.0 else "partial_evidence_backed"
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=requested_quantity,
        filled=True,
        fill_price=average_price,
        fill_model="taker_orderbook_depth",
        execution_price_source=source,
        fill_quality=fill_quality,
        outcome="filled",
        evidence_timestamp=order_book.timestamp,
        requested_quantity=requested_quantity,
        requested_notional=notional_request,
        filled_quantity=filled_quantity,
        filled_notional=filled_notional,
        unfilled_quantity=unfilled_quantity,
        depth_levels_consumed=levels_consumed,
        execution_impact_bps=_side_slippage_bps(side=side, fill_price=average_price, reference_price=top_price),
        slippage_bps=_side_slippage_bps(side=side, fill_price=average_price, reference_price=reference_price),
    )


def simulate_maker_limit_fill(
    *,
    symbol: str,
    side: OrderSide,
    limit_price: float,
    quantity: float,
    queue_ahead_quantity: float | None = None,
    placement_timestamp: datetime | None = None,
    timeout_seconds: float | None = None,
    latency_ms: int | float = 0,
    cancel_replace_timestamp: datetime | None = None,
    order_books: tuple[OrderBookSnapshot, ...] = (),
    trades: tuple[TradePrint, ...] = (),
) -> ExecutionFill:
    validated_limit_price = _positive_finite_float("limit_price", limit_price)
    uses_queue_model = (
        queue_ahead_quantity is not None
        or placement_timestamp is not None
        or timeout_seconds is not None
        or latency_ms
        or cancel_replace_timestamp is not None
    )
    if uses_queue_model:
        return _simulate_maker_queue_fill(
            symbol=symbol,
            side=side,
            limit_price=validated_limit_price,
            quantity=quantity,
            queue_ahead_quantity=queue_ahead_quantity,
            placement_timestamp=placement_timestamp,
            timeout_seconds=timeout_seconds,
            latency_ms=latency_ms,
            cancel_replace_timestamp=cancel_replace_timestamp,
            order_books=order_books,
            trades=trades,
        )

    sorted_trades = sorted((trade for trade in trades if trade.symbol == symbol), key=lambda trade: trade.timestamp)
    filled_qty = 0.0
    for trade in sorted_trades:
        if not _crosses_limit(side=side, price=trade.price, limit_price=validated_limit_price):
            continue
        filled_qty += max(0.0, float(trade.quantity))
        if filled_qty >= quantity:
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                filled=True,
                fill_price=validated_limit_price,
                fill_model="maker_orderbook_trade_evidence",
                execution_price_source="trade_print",
                fill_quality="evidence_backed",
                outcome="filled",
                evidence_timestamp=trade.timestamp,
            )

    sorted_books = sorted((book for book in order_books if book.symbol == symbol), key=lambda book: book.timestamp)
    for book in sorted_books:
        book_price = book.ask if side == "buy" else book.bid
        if _crosses_limit(side=side, price=book_price, limit_price=validated_limit_price):
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=quantity,
                filled=True,
                fill_price=validated_limit_price,
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


def _simulate_maker_queue_fill(
    *,
    symbol: str,
    side: OrderSide,
    limit_price: float,
    quantity: float,
    queue_ahead_quantity: float | None,
    placement_timestamp: datetime | None,
    timeout_seconds: float | None,
    latency_ms: int | float,
    cancel_replace_timestamp: datetime | None,
    order_books: tuple[OrderBookSnapshot, ...],
    trades: tuple[TradePrint, ...],
) -> ExecutionFill:
    requested_quantity = max(float(quantity), 0.0)
    effective_placement = placement_timestamp
    reasons: list[str] = []
    if placement_timestamp is not None and float(latency_ms or 0.0) > 0.0:
        effective_placement = placement_timestamp + timedelta(milliseconds=float(latency_ms))
        reasons.append("latency_applied")
    queue_initial = _maker_queue_ahead(
        side=side,
        queue_ahead_quantity=queue_ahead_quantity,
        order_books=order_books,
        effective_placement=effective_placement,
    )
    queue_remaining = queue_initial
    filled_quantity = 0.0
    first_fill_timestamp: datetime | None = None
    last_fill_timestamp: datetime | None = None
    deadline = (
        effective_placement + timedelta(seconds=float(timeout_seconds))
        if effective_placement is not None and timeout_seconds is not None
        else None
    )
    cutoff = _maker_cutoff(deadline=deadline, cancel_replace_timestamp=cancel_replace_timestamp)

    for trade in sorted((trade for trade in trades if trade.symbol == symbol), key=lambda trade: trade.timestamp):
        if effective_placement is not None and trade.timestamp < effective_placement:
            continue
        if cutoff is not None and trade.timestamp > cutoff:
            continue
        if not _crosses_limit(side=side, price=trade.price, limit_price=limit_price):
            continue
        if not _maker_trade_side_consumes_queue(side=side, trade_side=trade.side):
            continue
        if trade.side is None:
            reasons.append("ambiguous_trade_side_assumed")
        remaining_print_quantity = max(float(trade.quantity), 0.0)
        if remaining_print_quantity <= 0.0:
            continue
        if queue_remaining > 0.0:
            queue_take = min(queue_remaining, remaining_print_quantity)
            queue_remaining -= queue_take
            remaining_print_quantity -= queue_take
            if queue_remaining <= 1e-12:
                queue_remaining = 0.0
                reasons.append("queue_depleted")
        if remaining_print_quantity <= 0.0:
            continue
        own_fill = min(requested_quantity - filled_quantity, remaining_print_quantity)
        if own_fill <= 0.0:
            continue
        filled_quantity += own_fill
        if first_fill_timestamp is None:
            first_fill_timestamp = trade.timestamp
        last_fill_timestamp = trade.timestamp
        if filled_quantity >= requested_quantity - 1e-12:
            filled_quantity = requested_quantity
            break

    unfilled_quantity = max(requested_quantity - filled_quantity, 0.0)
    full_fill = requested_quantity <= 0.0 or unfilled_quantity <= 1e-12
    status: MakerStatus
    if full_fill:
        status = "filled"
    elif cancel_replace_timestamp is not None and (deadline is None or cancel_replace_timestamp <= deadline):
        status = "cancelled_replaced"
        reasons.append("cancel_replace_after_partial" if filled_quantity > 0.0 else "cancel_replace_before_fill")
    elif deadline is not None:
        status = "expired"
        reasons.append("timeout_expired")
    elif filled_quantity > 0.0:
        status = "partial"
    else:
        status = "no_fill"
        reasons.append("no_crossing_evidence")

    fill_quality: FillQuality
    if filled_quantity <= 0.0:
        fill_quality = "no_fill"
    elif full_fill:
        fill_quality = "evidence_backed"
    else:
        fill_quality = "partial_evidence_backed"
    end_time = first_fill_timestamp or cutoff
    maker_wait_seconds = (
        max((end_time - effective_placement).total_seconds(), 0.0)
        if effective_placement is not None and end_time is not None
        else None
    )
    filled_notional = filled_quantity * float(limit_price)
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=requested_quantity,
        filled=filled_quantity > 0.0,
        fill_price=float(limit_price) if filled_quantity > 0.0 else None,
        fill_model="maker_post_only_queue",
        execution_price_source="trade_print" if filled_quantity > 0.0 else "no_crossing_evidence",
        fill_quality=fill_quality,
        outcome="filled" if filled_quantity > 0.0 else "missed_alpha",
        evidence_timestamp=last_fill_timestamp,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        filled_notional=filled_notional,
        unfilled_quantity=unfilled_quantity,
        maker_status=status,
        first_fill_timestamp=first_fill_timestamp,
        last_fill_timestamp=last_fill_timestamp,
        queue_ahead_initial=queue_initial,
        queue_ahead_remaining=queue_remaining,
        maker_wait_seconds=maker_wait_seconds,
        maker_reasons=tuple(dict.fromkeys(reasons)),
    )


def _maker_queue_ahead(
    *,
    side: OrderSide,
    queue_ahead_quantity: float | None,
    order_books: tuple[OrderBookSnapshot, ...],
    effective_placement: datetime | None,
) -> float:
    explicit = _positive_float(queue_ahead_quantity)
    if explicit is not None:
        return explicit
    eligible_books = [book for book in order_books if effective_placement is None or book.timestamp <= effective_placement]
    if not eligible_books:
        eligible_books = list(order_books)
    if not eligible_books:
        return 0.0
    book = sorted(eligible_books, key=lambda item: item.timestamp)[-1]
    size = book.bid_size if side == "buy" else book.ask_size
    return float(size or 0.0)


def _maker_cutoff(
    *,
    deadline: datetime | None,
    cancel_replace_timestamp: datetime | None,
) -> datetime | None:
    if deadline is None:
        return cancel_replace_timestamp
    if cancel_replace_timestamp is None:
        return deadline
    return min(deadline, cancel_replace_timestamp)


def _first_symbol_book(symbol: str, order_books: tuple[OrderBookSnapshot, ...]) -> OrderBookSnapshot | None:
    return next((book for book in sorted(order_books, key=lambda item: item.timestamp) if book.symbol == symbol), None)


def _side_levels(order_book: OrderBookSnapshot, *, side: OrderSide) -> tuple[DepthLevel, ...]:
    raw_levels = order_book.ask_levels if side == "buy" else order_book.bid_levels
    valid_levels = []
    for level in raw_levels:
        price = _finite_float("depth level price", level.price)
        quantity = _finite_float("depth level quantity", level.quantity)
        if price > 0.0 and quantity > 0.0:
            valid_levels.append(DepthLevel(price=price, quantity=quantity))
    if side == "buy":
        return tuple(sorted(valid_levels, key=lambda level: level.price))
    return tuple(sorted(valid_levels, key=lambda level: level.price, reverse=True))


def _side_slippage_bps(*, side: OrderSide, fill_price: float, reference_price: float) -> float | None:
    if reference_price <= 0.0 or fill_price <= 0.0:
        return None
    if side == "buy":
        return ((float(fill_price) - float(reference_price)) / float(reference_price)) * 10_000.0
    return ((float(reference_price) - float(fill_price)) / float(reference_price)) * 10_000.0


def _conservative_trade_print_taker_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    trades: tuple[TradePrint, ...],
) -> ExecutionFill | None:
    symbol_trades = [trade for trade in trades if trade.symbol == symbol and trade.price > 0.0]
    if not symbol_trades:
        return None
    trade = max(symbol_trades, key=lambda item: item.price) if side == "buy" else min(symbol_trades, key=lambda item: item.price)
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled=True,
        fill_price=float(trade.price),
        fill_model="taker_trade_print",
        execution_price_source="trade_print",
        fill_quality="evidence_backed",
        outcome="filled",
        evidence_timestamp=trade.timestamp,
    )


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _finite_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive_finite_float(name: str, value: Any) -> float:
    if isinstance(value, (bool, str)):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


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


def _maker_trade_side_consumes_queue(*, side: OrderSide, trade_side: TradePrintSide | None) -> bool:
    if trade_side is None:
        return True
    return trade_side == ("sell" if side == "buy" else "buy")
