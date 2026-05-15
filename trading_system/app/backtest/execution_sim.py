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
OrderType = Literal["market", "limit"]

_ORDER_SIDES = frozenset(("buy", "sell"))
_TAKER_ORDER_TYPES = frozenset(("market",))
_MAKER_ORDER_TYPES = frozenset(("limit",))
_TRADE_PRINT_SIDES = frozenset(("buy", "sell"))
_FILL_MODELS = frozenset(
    (
        "reference_close",
        "next_bar_ohlcv",
        "taker_ohlcv_approx",
        "taker_orderbook",
        "taker_orderbook_depth",
        "taker_trade_print",
        "maker_orderbook_trade_evidence",
        "maker_post_only_queue",
    )
)
_PRICE_SOURCES = frozenset(
    (
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
    )
)
_FILL_QUALITIES = frozenset(("approximate", "evidence_backed", "partial_evidence_backed", "no_fill"))
_FILL_OUTCOMES = frozenset(("filled", "missed_alpha"))
_MAKER_STATUSES = frozenset(("filled", "partial", "no_fill", "expired", "cancelled_replaced"))
_PRICE_SOURCES_BY_FILL_MODEL = {
    "reference_close": frozenset(("ohlcv_close",)),
    "next_bar_ohlcv": frozenset(("ohlcv_next_open",)),
    "taker_ohlcv_approx": frozenset(("ohlcv_reference",)),
    "taker_orderbook": frozenset(("best_bid", "best_ask", "no_crossing_evidence")),
    "taker_orderbook_depth": frozenset(("bid_depth", "ask_depth", "no_crossing_evidence")),
    "taker_trade_print": frozenset(("trade_print",)),
    "maker_orderbook_trade_evidence": frozenset(("trade_print", "book_cross", "no_crossing_evidence")),
    "maker_post_only_queue": frozenset(("trade_print", "no_crossing_evidence")),
}
_TAKER_EVIDENCE_MAX_SKEW = timedelta(seconds=1)


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
    fill_id: str | None = None

    def __post_init__(self) -> None:
        if self.side is not None:
            object.__setattr__(self, "side", _canonical_domain("trade.side", self.side, _TRADE_PRINT_SIDES))
        if self.fill_id is not None:
            object.__setattr__(self, "fill_id", _canonical_string("trade.fill_id", self.fill_id))


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
    unfilled_notional: float | None = None
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "side", _canonical_domain("side", self.side, _ORDER_SIDES))
        object.__setattr__(self, "symbol", _canonical_symbol("symbol", self.symbol))
        object.__setattr__(self, "fill_model", _canonical_domain("fill_model", self.fill_model, _FILL_MODELS))
        object.__setattr__(
            self,
            "execution_price_source",
            _canonical_domain("execution_price_source", self.execution_price_source, _PRICE_SOURCES),
        )
        object.__setattr__(self, "fill_quality", _canonical_domain("fill_quality", self.fill_quality, _FILL_QUALITIES))
        object.__setattr__(self, "outcome", _canonical_domain("outcome", self.outcome, _FILL_OUTCOMES))
        if not isinstance(self.execution_timeframe, str) or self.execution_timeframe.strip() != self.execution_timeframe:
            raise ValueError("execution_timeframe must be a canonical string")
        if (
            isinstance(self.execution_lag_bars, bool)
            or not isinstance(self.execution_lag_bars, int)
            or self.execution_lag_bars < 0
        ):
            raise ValueError("execution_lag_bars must be a non-negative integer")
        if (
            self.fill_model == "next_bar_ohlcv" or self.execution_price_source == "ohlcv_next_open"
        ) and (not self.execution_timeframe or self.execution_lag_bars <= 0):
            raise ValueError("next-bar OHLCV executions must include execution timeframe and positive lag")
        if (
            self.fill_model == "reference_close"
            and (self.execution_timeframe or self.execution_lag_bars > 0)
        ):
            raise ValueError("reference-close executions cannot include execution timing metadata")
        if self.filled and self.fill_quality != "no_fill" and self.outcome != "filled":
            raise ValueError("filled executions must have filled outcome")
        if not self.filled and self.outcome == "filled":
            raise ValueError("unfilled executions cannot have filled outcome")
        if self.maker_status is not None:
            object.__setattr__(
                self,
                "maker_status",
                _canonical_domain("maker_status", self.maker_status, _MAKER_STATUSES),
            )
        _validate_maker_reasons(self.maker_reasons)
        object.__setattr__(self, "quantity", _non_negative_finite_float("quantity", self.quantity))
        if self.fill_price is not None:
            _positive_finite_float("fill_price", self.fill_price)
        fill_timestamps_present = self.first_fill_timestamp is not None or self.last_fill_timestamp is not None
        if fill_timestamps_present and (
            self.first_fill_timestamp is None or self.last_fill_timestamp is None
        ):
            raise ValueError("fill timestamps must be provided as a pair")
        if self.depth_levels_consumed is not None and (
            isinstance(self.depth_levels_consumed, bool)
            or not isinstance(self.depth_levels_consumed, int)
            or self.depth_levels_consumed < 0
        ):
            raise ValueError("depth_levels_consumed must be a non-negative integer")
        if self.depth_levels_consumed is not None and self.fill_model not in {"taker_orderbook", "taker_orderbook_depth"}:
            raise ValueError("depth_levels_consumed requires taker orderbook fill model")
        if self.depth_levels_consumed is not None and self.execution_price_source in {"best_ask", "best_bid"}:
            raise ValueError("depth_levels_consumed requires depth price source")
        if self.depth_levels_consumed is not None and self.fill_model != "taker_orderbook_depth":
            raise ValueError("depth_levels_consumed requires taker orderbook depth fill model")
        if self.execution_price_source not in _PRICE_SOURCES_BY_FILL_MODEL[self.fill_model]:
            raise ValueError("execution_price_source must agree with fill_model")
        if self.fill_quality == "no_fill" and self.fill_price is not None:
            raise ValueError("no-fill execution cannot include fill_price")
        if self.fill_quality == "no_fill":
            if self.filled:
                raise ValueError("filled executions cannot have no_fill quality")
            if self.filled_quantity is not None and self.filled_quantity > 0.0:
                raise ValueError("no-fill execution cannot include filled quantity")
            if self.filled_notional is not None and self.filled_notional > 0.0:
                raise ValueError("no-fill execution cannot include filled notional")
            if self.depth_levels_consumed is not None and self.depth_levels_consumed > 0:
                raise ValueError("no-fill execution cannot consume depth levels")
            if self.first_fill_timestamp is not None or self.last_fill_timestamp is not None:
                raise ValueError("no-fill execution cannot include fill timestamps")
        for field_name in ("evidence_timestamp", "first_fill_timestamp", "last_fill_timestamp"):
            value = getattr(self, field_name)
            if value is not None:
                _timezone_aware_datetime(field_name, value, symbol=self.symbol)
        if self.evidence_timestamp is not None and self.fill_quality == "approximate":
            raise ValueError("evidence_timestamp requires market evidence or no-fill evidence")
        if self.first_fill_timestamp is not None and self.last_fill_timestamp is not None:
            if self.first_fill_timestamp > self.last_fill_timestamp:
                raise ValueError("first_fill_timestamp cannot be after last_fill_timestamp")
        if (
            self.evidence_timestamp is not None
            and self.first_fill_timestamp is not None
            and self.last_fill_timestamp is not None
            and not (self.first_fill_timestamp <= self.evidence_timestamp <= self.last_fill_timestamp)
        ):
            raise ValueError("evidence_timestamp must fall within fill timestamp interval")
        for field_name in (
            "requested_quantity",
            "requested_notional",
            "filled_quantity",
            "filled_notional",
            "unfilled_quantity",
            "unfilled_notional",
            "execution_impact_bps",
            "queue_ahead_initial",
            "queue_ahead_remaining",
            "maker_wait_seconds",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _non_negative_finite_float(field_name, value)
        if self.slippage_bps is not None:
            if self.fill_model == "taker_orderbook_depth":
                _non_negative_finite_float("slippage_bps", self.slippage_bps)
            else:
                _finite_float("slippage_bps", self.slippage_bps)
        if (
            self.queue_ahead_initial is not None
            and self.queue_ahead_remaining is not None
            and float(self.queue_ahead_remaining) > float(self.queue_ahead_initial)
        ):
            raise ValueError("queue_ahead_remaining cannot exceed queue_ahead_initial")
        if not self.fill_model.startswith("maker_"):
            for field_name in (
                "maker_status",
                "queue_ahead_initial",
                "queue_ahead_remaining",
                "maker_wait_seconds",
            ):
                if getattr(self, field_name) is not None:
                    raise ValueError(f"{field_name} requires maker fill model")
            if self.maker_reasons:
                raise ValueError("maker_reasons requires maker fill model")
        if self.maker_status is not None:
            if self.filled:
                if self.maker_status in {"no_fill", "expired", "cancelled_replaced"}:
                    raise ValueError("maker_status must agree with filled execution state")
            elif self.fill_quality == "no_fill" and self.maker_status in {"filled", "partial"}:
                raise ValueError("maker_status must agree with filled execution state")
        queue_evidence_present = self.queue_ahead_initial is not None or self.queue_ahead_remaining is not None
        if queue_evidence_present and (
            self.queue_ahead_initial is None or self.queue_ahead_remaining is None
        ):
            raise ValueError("maker queue evidence requires both queue_ahead_initial and queue_ahead_remaining")
        if queue_evidence_present and self.maker_status is None:
            raise ValueError("maker queue evidence requires maker_status")
        if self.maker_wait_seconds is not None and self.maker_status is None:
            raise ValueError("maker_wait_seconds requires maker_status")
        positive_fill_request = (
            self.quantity > 0.0
            or (self.requested_quantity is not None and float(self.requested_quantity) > 0.0)
            or (self.requested_notional is not None and float(self.requested_notional) > 0.0)
        )
        if fill_timestamps_present:
            if not self.filled or self.outcome != "filled" or self.fill_quality == "no_fill":
                raise ValueError("fill timestamps require a filled execution state")
            if positive_fill_request and (
                self.fill_price is None
                or self.filled_quantity is None
                or float(self.filled_quantity) <= 0.0
                or self.filled_notional is None
                or float(self.filled_notional) <= 0.0
            ):
                raise ValueError("fill timestamps require complete positive fill accounting")
        if self.filled:
            if self.fill_price is None:
                raise ValueError("filled executions must include fill_price")
            if positive_fill_request and (
                self.filled_quantity is None or float(self.filled_quantity) <= 0.0
            ):
                raise ValueError("filled executions must include positive filled quantity")
            if positive_fill_request and (
                self.filled_notional is None or float(self.filled_notional) <= 0.0
            ):
                raise ValueError("filled executions must include positive filled notional")
        else:
            if self.filled_quantity is not None and float(self.filled_quantity) > 0.0:
                raise ValueError("unfilled executions cannot include positive filled quantity")
            if self.filled_notional is not None and float(self.filled_notional) > 0.0:
                raise ValueError("unfilled executions cannot include positive filled notional")
        if (
            self.fill_model == "taker_orderbook_depth"
            and self.fill_price is not None
            and self.filled_quantity is not None
            and float(self.filled_quantity) > 0.0
            and self.filled_notional is not None
            and self.fill_quality in {"evidence_backed", "partial_evidence_backed"}
            and not math.isclose(
                float(self.filled_notional),
                float(self.filled_quantity) * float(self.fill_price),
                rel_tol=1e-12,
                abs_tol=1e-9,
            )
        ):
            raise ValueError("filled notional must equal filled quantity times fill_price")
        for field_name in ("execution_impact_bps", "slippage_bps"):
            if getattr(self, field_name) is not None and (
                not self.filled
                or self.outcome != "filled"
                or self.fill_quality not in {"evidence_backed", "partial_evidence_backed"}
                or self.evidence_timestamp is None
            ):
                raise ValueError(f"{field_name} requires an evidence-backed filled execution")
        if (
            self.requested_quantity is not None
            and float(self.requested_quantity) > 0.0
            and self.filled_quantity is not None
            and self.unfilled_quantity is None
        ):
            raise ValueError("fill quantities must include unfilled quantity")
        if (
            self.requested_quantity is not None
            and float(self.requested_quantity) > 0.0
            and self.filled_quantity is not None
            and self.unfilled_quantity is not None
            and not math.isclose(
                float(self.requested_quantity),
                float(self.filled_quantity) + float(self.unfilled_quantity),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise ValueError("fill quantities must conserve requested quantity")
        if (
            self.fill_model.startswith("maker_")
            and self.maker_status in {"filled", "partial"}
            and self.requested_quantity is not None
            and float(self.requested_quantity) > 0.0
            and self.filled_quantity is not None
            and float(self.filled_quantity) > 0.0
            and self.unfilled_quantity is not None
        ):
            if self.maker_status == "partial" and float(self.unfilled_quantity) <= 0.0:
                raise ValueError("partial maker fills require positive unfilled quantity")
            if self.maker_status == "filled" and float(self.unfilled_quantity) > 0.0:
                raise ValueError("filled maker status requires zero unfilled quantity")
        if (
            self.requested_notional is not None
            and float(self.requested_notional) > 0.0
            and self.filled_notional is not None
            and self.unfilled_notional is None
        ):
            raise ValueError("fill notionals must include unfilled notional")
        if (
            self.requested_notional is not None
            and float(self.requested_notional) > 0.0
            and self.filled_notional is not None
            and self.unfilled_notional is not None
            and not math.isclose(
                float(self.requested_notional),
                float(self.filled_notional) + float(self.unfilled_notional),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise ValueError("fill notionals must conserve requested notional")
        if (
            self.fill_model == "taker_trade_print"
            and self.fill_quality == "evidence_backed"
            and self.unfilled_quantity is not None
            and self.unfilled_quantity > 1e-12
        ):
            raise ValueError("evidence-backed trade-print fills cannot leave unfilled quantity")
        if (
            self.fill_model == "taker_orderbook_depth"
            and self.fill_quality == "evidence_backed"
            and self.unfilled_quantity is not None
            and self.unfilled_quantity > 1e-12
        ):
            raise ValueError("evidence-backed orderbook-depth fills cannot leave unfilled quantity")
        if self.maker_status is not None:
            expected_quality_by_status = {
                "partial": "partial_evidence_backed",
                "filled": "evidence_backed",
                "no_fill": "no_fill",
                "expired": "no_fill",
                "cancelled_replaced": "no_fill",
            }
            if self.fill_quality != expected_quality_by_status[self.maker_status]:
                raise ValueError("maker_status must agree with fill_quality")
        if (
            self.fill_quality in {"evidence_backed", "partial_evidence_backed"}
            and self.evidence_timestamp is None
        ):
            raise ValueError("evidence-backed executions must include evidence_timestamp")
        if (
            self.fill_model == "taker_trade_print"
            and self.fill_quality in {"evidence_backed", "partial_evidence_backed"}
            and (self.first_fill_timestamp is None or self.last_fill_timestamp is None)
        ):
            raise ValueError("trade-print evidence-backed executions must include fill timestamps")
        if (
            self.first_fill_timestamp is not None
            and self.last_fill_timestamp is not None
            and self.first_fill_timestamp > self.last_fill_timestamp
        ):
            raise ValueError("first_fill_timestamp cannot be after last_fill_timestamp")

    @property
    def execution_provenance(self) -> dict[str, str]:
        evidence = "synthetic_reference"
        if self.fill_quality == "no_fill":
            evidence = "no_crossing_evidence"
        elif self.fill_quality in {"evidence_backed", "partial_evidence_backed"}:
            evidence = "market_evidence"
        return {
            "simulator": "offline_execution_sim",
            "fill_model": self.fill_model,
            "price_source": self.execution_price_source,
            "fill_quality": self.fill_quality,
            "evidence": evidence,
        }


def reference_close_fill(*, symbol: str, side: OrderSide, quantity: float, close_price: float) -> ExecutionFill:
    side = _canonical_order_side(side)
    quantity = _non_negative_finite_float("quantity", quantity)
    close_price = _positive_finite_float("close_price", close_price)
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
        requested_quantity=quantity,
        filled_quantity=quantity if quantity > 0.0 else None,
        filled_notional=(quantity * float(close_price)) if quantity > 0.0 else None,
        unfilled_quantity=0.0 if quantity > 0.0 else None,
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
    side = _canonical_order_side(side)
    quantity = _non_negative_finite_float("quantity", quantity)
    reference_close = _positive_finite_float("reference_close", reference_close)
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
            requested_quantity=quantity,
            filled_quantity=quantity if quantity > 0.0 else None,
            filled_notional=(quantity * open_price) if quantity > 0.0 else None,
            unfilled_quantity=0.0 if quantity > 0.0 else None,
        )

    return reference_close_fill(symbol=symbol, side=side, quantity=quantity, close_price=reference_close)


def simulate_taker_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    reference_price: float,
    order_type: OrderType = "market",
    placement_timestamp: datetime | None = None,
    max_evidence_lag: timedelta | None = None,
    order_books: tuple[OrderBookSnapshot, ...] = (),
    trades: tuple[TradePrint, ...] = (),
) -> ExecutionFill:
    side = _canonical_order_side(side)
    _canonical_domain("order_type", order_type, _TAKER_ORDER_TYPES)
    quantity = _non_negative_finite_float("quantity", quantity)
    _positive_finite_float("reference_price", reference_price)
    if placement_timestamp is not None:
        placement_timestamp = _placement_timestamp_datetime(placement_timestamp)
    if max_evidence_lag is not None:
        max_evidence_lag = _non_negative_timedelta("max_evidence_lag", max_evidence_lag)
        if placement_timestamp is None:
            raise ValueError("max_evidence_lag requires placement_timestamp")
    _validate_evidence_contract(symbol=symbol, order_books=order_books, trades=trades)
    eligible_books = _eligible_symbol_order_books(
        symbol,
        order_books,
        placement_timestamp=placement_timestamp,
        max_evidence_lag=max_evidence_lag,
    )
    eligible_trades = _eligible_symbol_trade_prints(
        symbol,
        trades,
        placement_timestamp=placement_timestamp,
        max_evidence_lag=max_evidence_lag,
    )
    _validate_taker_evidence_timestamp_skew(
        symbol=symbol,
        order_books=tuple(eligible_books),
        trades=tuple(eligible_trades),
    )
    book = eligible_books[0] if eligible_books else None
    if book is not None:
        if _side_levels(book, side=side):
            depth_quantity = None if not isinstance(quantity, bool) and quantity == 0.0 else quantity
            return simulate_taker_depth_fill(
                symbol=symbol,
                side=side,
                quantity=depth_quantity,
                reference_price=reference_price,
                order_book=book,
                placement_timestamp=placement_timestamp,
                max_evidence_lag=max_evidence_lag,
            )
        if side == "buy":
            price = _positive_finite_float("order_book.ask", book.ask)
            top_size = book.ask_size
            top_size_field = "order_book.ask_size"
            source: ExecutionPriceSource = "best_ask"
        else:
            price = _positive_finite_float("order_book.bid", book.bid)
            top_size = book.bid_size
            top_size_field = "order_book.bid_size"
            source = "best_bid"
        if top_size is None:
            filled_quantity = quantity
        else:
            visible_quantity = _non_negative_finite_float(top_size_field, top_size)
            if visible_quantity <= 0.0:
                return ExecutionFill(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    filled=False,
                    fill_price=None,
                    fill_model="taker_orderbook",
                    execution_price_source="no_crossing_evidence",
                    fill_quality="no_fill",
                    outcome="missed_alpha",
                    evidence_timestamp=book.timestamp,
                    requested_quantity=quantity,
                    filled_quantity=0.0,
                    filled_notional=0.0,
                    unfilled_quantity=quantity,
                )
            filled_quantity = min(quantity, visible_quantity)
        unfilled_quantity = quantity - filled_quantity
        fill_quality: FillQuality = (
            "evidence_backed" if unfilled_quantity <= 0.0 else "partial_evidence_backed"
        )
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=quantity,
            filled=True,
            fill_price=price,
            fill_model="taker_orderbook",
            execution_price_source=source,
            fill_quality=fill_quality,
            outcome="filled",
            evidence_timestamp=book.timestamp,
            requested_quantity=quantity,
            filled_quantity=filled_quantity if quantity > 0.0 else None,
            filled_notional=(filled_quantity * price) if quantity > 0.0 else None,
            unfilled_quantity=unfilled_quantity if quantity > 0.0 else None,
            first_fill_timestamp=book.timestamp,
            last_fill_timestamp=book.timestamp,
        )

    trade_fill = _conservative_trade_print_taker_fill(
        symbol=symbol,
        side=side,
        quantity=quantity,
        trades=tuple(eligible_trades),
    )
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
        requested_quantity=quantity,
        filled_quantity=quantity if quantity > 0.0 else None,
        filled_notional=(quantity * float(reference_price)) if quantity > 0.0 else None,
        unfilled_quantity=0.0 if quantity > 0.0 else None,
    )


def simulate_taker_depth_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float | None = None,
    requested_notional: float | None = None,
    reference_price: float,
    order_book: OrderBookSnapshot,
    placement_timestamp: datetime | None = None,
    max_evidence_lag: timedelta | None = None,
) -> ExecutionFill:
    side = _canonical_order_side(side)
    _positive_finite_float("reference_price", reference_price)
    if order_book.symbol != symbol:
        raise ValueError(f"order_book.symbol {order_book.symbol} does not match requested symbol {symbol}")
    if placement_timestamp is not None:
        placement_timestamp = _placement_timestamp_datetime(placement_timestamp)
    if max_evidence_lag is not None:
        max_evidence_lag = _non_negative_timedelta("max_evidence_lag", max_evidence_lag)
        if placement_timestamp is None:
            raise ValueError("max_evidence_lag requires placement_timestamp")
    _validate_evidence_contract(symbol=symbol, order_books=(order_book,), trades=())
    notional_request = None
    if requested_notional is not None:
        notional_request = _positive_finite_float("requested_notional", requested_notional)
    requested_quantity = 0.0 if quantity is None else _depth_quantity_float("quantity", quantity)
    if placement_timestamp is not None and (
        order_book.timestamp < placement_timestamp
        or (max_evidence_lag is not None and order_book.timestamp > placement_timestamp + max_evidence_lag)
    ):
        return ExecutionFill(
            symbol=symbol,
            side=side,
            quantity=requested_quantity,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="missed_alpha",
            evidence_timestamp=order_book.timestamp,
            requested_quantity=requested_quantity,
            requested_notional=notional_request,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=requested_quantity,
            unfilled_notional=notional_request,
            depth_levels_consumed=0,
        )
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
            unfilled_notional=notional_request,
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
            unfilled_notional=None,
            depth_levels_consumed=0,
            execution_impact_bps=0.0,
            slippage_bps=_side_slippage_bps(side=side, fill_price=float(levels[0].price), reference_price=reference_price),
        )

    remaining = requested_quantity
    remaining_notional = notional_request
    filled_quantity = 0.0
    filled_notional = 0.0
    levels_consumed = 0
    last_consumed_price: float | None = None
    for level in levels:
        available = max(float(level.quantity), 0.0)
        if available <= 0.0:
            continue
        level_price = float(level.price)
        if remaining_notional is not None:
            take_quantity = min(available, remaining_notional / level_price)
        else:
            take_quantity = min(remaining, available)
        if take_quantity <= 0.0:
            continue
        filled_quantity += take_quantity
        level_notional = take_quantity * level_price
        filled_notional += level_notional
        last_consumed_price = level_price
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
            unfilled_notional=notional_request,
            depth_levels_consumed=0,
        )

    average_price = filled_notional / filled_quantity
    top_price = float(levels[0].price)
    unfilled_notional = remaining_notional if remaining_notional is not None else None
    if remaining_notional is not None:
        if remaining_notional <= 0.0:
            unfilled_quantity = 0.0
        else:
            evidence_price = last_consumed_price if last_consumed_price is not None else average_price
            unfilled_quantity = remaining_notional / evidence_price
    else:
        unfilled_quantity = remaining
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
        unfilled_notional=unfilled_notional,
        depth_levels_consumed=levels_consumed,
        execution_impact_bps=_side_slippage_bps(side=side, fill_price=average_price, reference_price=top_price),
        slippage_bps=_side_slippage_bps(side=side, fill_price=average_price, reference_price=reference_price),
        first_fill_timestamp=order_book.timestamp,
        last_fill_timestamp=order_book.timestamp,
    )


def simulate_maker_limit_fill(
    *,
    symbol: str,
    side: OrderSide,
    limit_price: float,
    quantity: float,
    order_type: OrderType = "limit",
    queue_ahead_quantity: float | None = None,
    placement_timestamp: datetime | None = None,
    timeout_seconds: float | None = None,
    latency_ms: int | float = 0,
    cancel_replace_timestamp: datetime | None = None,
    order_books: tuple[OrderBookSnapshot, ...] = (),
    trades: tuple[TradePrint, ...] = (),
) -> ExecutionFill:
    side = _canonical_order_side(side)
    _canonical_domain("order_type", order_type, _MAKER_ORDER_TYPES)
    validated_limit_price = _positive_finite_float("limit_price", limit_price)
    validated_quantity = _positive_quantity_float("quantity", quantity)
    validated_latency_ms = _non_negative_finite_float("latency_ms", latency_ms)
    validated_timeout_seconds = (
        _non_negative_finite_float("timeout_seconds", timeout_seconds) if timeout_seconds is not None else None
    )
    if placement_timestamp is not None:
        placement_timestamp = _placement_timestamp_datetime(placement_timestamp)
    if cancel_replace_timestamp is not None:
        cancel_replace_timestamp = _cancel_replace_timestamp_datetime(cancel_replace_timestamp)
    if placement_timestamp is not None and cancel_replace_timestamp is not None:
        effective_placement = placement_timestamp
        if validated_latency_ms > 0.0:
            effective_placement = placement_timestamp + timedelta(milliseconds=validated_latency_ms)
        if cancel_replace_timestamp < effective_placement:
            raise ValueError("cancel_replace_timestamp cannot be before placement_timestamp")
    uses_queue_model = (
        queue_ahead_quantity is not None
        or placement_timestamp is not None
        or validated_timeout_seconds is not None
        or validated_latency_ms > 0.0
        or cancel_replace_timestamp is not None
    )
    if uses_queue_model:
        effective_placement = placement_timestamp
        if placement_timestamp is not None and validated_latency_ms > 0.0:
            effective_placement = placement_timestamp + timedelta(milliseconds=validated_latency_ms)
        _maker_queue_ahead(
            side=side,
            queue_ahead_quantity=queue_ahead_quantity,
            order_books=order_books,
            effective_placement=effective_placement,
        )
        _validate_maker_queue_trade_fill_ids(
            symbol=symbol,
            side=side,
            limit_price=validated_limit_price,
            placement_timestamp=placement_timestamp,
            timeout_seconds=validated_timeout_seconds,
            latency_ms=validated_latency_ms,
            cancel_replace_timestamp=cancel_replace_timestamp,
            trades=trades,
        )
    _validate_evidence_contract(symbol=symbol, order_books=order_books, trades=trades)
    if uses_queue_model:
        return _simulate_maker_queue_fill(
            symbol=symbol,
            side=side,
            limit_price=validated_limit_price,
            quantity=validated_quantity,
            queue_ahead_quantity=queue_ahead_quantity,
            placement_timestamp=placement_timestamp,
            timeout_seconds=validated_timeout_seconds,
            latency_ms=validated_latency_ms,
            cancel_replace_timestamp=cancel_replace_timestamp,
            order_books=order_books,
            trades=trades,
        )

    sorted_trades = tuple(trade for trade in trades if trade.symbol == symbol)
    filled_qty = 0.0
    first_fill_timestamp: datetime | None = None
    for trade in sorted_trades:
        trade_price = _positive_finite_float("trade.price", trade.price)
        trade_quantity = _positive_finite_float("trade.quantity", trade.quantity)
        if not _crosses_limit(side=side, price=trade_price, limit_price=validated_limit_price):
            continue
        if first_fill_timestamp is None:
            first_fill_timestamp = trade.timestamp
        filled_qty += trade_quantity
        if filled_qty >= validated_quantity:
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=validated_quantity,
                filled=True,
                fill_price=validated_limit_price,
                fill_model="maker_orderbook_trade_evidence",
                execution_price_source="trade_print",
                fill_quality="evidence_backed",
                outcome="filled",
                evidence_timestamp=trade.timestamp,
                requested_quantity=validated_quantity,
                filled_quantity=validated_quantity,
                filled_notional=validated_quantity * validated_limit_price,
                unfilled_quantity=0.0,
                first_fill_timestamp=first_fill_timestamp,
                last_fill_timestamp=trade.timestamp,
            )

    sorted_books = tuple(book for book in order_books if book.symbol == symbol)
    for book in sorted_books:
        book_price = _positive_finite_float(
            "order_book.ask" if side == "buy" else "order_book.bid",
            book.ask if side == "buy" else book.bid,
        )
        if _crosses_limit(side=side, price=book_price, limit_price=validated_limit_price):
            return ExecutionFill(
                symbol=symbol,
                side=side,
                quantity=validated_quantity,
                filled=True,
                fill_price=validated_limit_price,
                fill_model="maker_orderbook_trade_evidence",
                execution_price_source="book_cross",
                fill_quality="evidence_backed",
                outcome="filled",
                evidence_timestamp=book.timestamp,
                requested_quantity=validated_quantity,
                filled_quantity=validated_quantity,
                filled_notional=validated_quantity * validated_limit_price,
                unfilled_quantity=0.0,
                first_fill_timestamp=book.timestamp,
                last_fill_timestamp=book.timestamp,
            )

    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=validated_quantity,
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
    seen_candidate_fill_ids: set[str] = set()

    for trade in (trade for trade in trades if trade.symbol == symbol):
        trade_price = _positive_finite_float("trade.price", trade.price)
        trade_quantity = _positive_finite_float("trade.quantity", trade.quantity)
        if effective_placement is not None and trade.timestamp < effective_placement:
            continue
        # Cutoff timestamps are exclusive so cancel/replace or timeout ties
        # do not get coerced into deterministic fills.
        if cutoff is not None and trade.timestamp >= cutoff:
            continue
        if not _crosses_limit(side=side, price=trade_price, limit_price=limit_price):
            continue
        if not _maker_trade_side_consumes_queue(side=side, trade_side=trade.side):
            continue
        if not isinstance(trade.fill_id, str) or not trade.fill_id.strip():
            raise ValueError("trade.fill_id is required")
        fill_id = _canonical_string("trade.fill_id", trade.fill_id)
        if fill_id in seen_candidate_fill_ids:
            raise ValueError(f"duplicate trade.fill_id: {fill_id}")
        seen_candidate_fill_ids.add(fill_id)
        remaining_print_quantity = trade_quantity
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
    elif filled_quantity > 0.0:
        status = "partial"
        if cancel_replace_timestamp is not None and (deadline is None or cancel_replace_timestamp <= deadline):
            reasons.append("cancel_replace_after_partial")
        elif deadline is not None:
            reasons.append("timeout_expired")
    elif cancel_replace_timestamp is not None and (deadline is None or cancel_replace_timestamp <= deadline):
        status = "cancelled_replaced"
        reasons.append("cancel_replace_before_fill")
    elif deadline is not None:
        status = "expired"
        reasons.append("timeout_expired")
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


def _validate_maker_queue_trade_fill_ids(
    *,
    symbol: str,
    side: OrderSide,
    limit_price: float,
    placement_timestamp: datetime | None,
    timeout_seconds: float | None,
    latency_ms: float,
    cancel_replace_timestamp: datetime | None,
    trades: tuple[TradePrint, ...],
) -> None:
    effective_placement = placement_timestamp
    if placement_timestamp is not None and latency_ms > 0.0:
        effective_placement = placement_timestamp + timedelta(milliseconds=latency_ms)
    deadline = (
        effective_placement + timedelta(seconds=timeout_seconds)
        if effective_placement is not None and timeout_seconds is not None
        else None
    )
    cutoff = _maker_cutoff(deadline=deadline, cancel_replace_timestamp=cancel_replace_timestamp)
    seen_fill_ids: set[str] = set()
    for trade in trades:
        if trade.symbol != symbol:
            continue
        trade_price = _positive_finite_float("trade.price", trade.price)
        if effective_placement is not None and trade.timestamp < effective_placement:
            continue
        if cutoff is not None and trade.timestamp >= cutoff:
            continue
        if not _crosses_limit(side=side, price=trade_price, limit_price=limit_price):
            continue
        if not _maker_trade_side_consumes_queue(side=side, trade_side=trade.side):
            continue
        if not isinstance(trade.fill_id, str) or not trade.fill_id.strip():
            raise ValueError("trade.fill_id is required")
        fill_id = _canonical_string("trade.fill_id", trade.fill_id)
        if fill_id in seen_fill_ids:
            raise ValueError(f"duplicate trade.fill_id: {fill_id}")
        seen_fill_ids.add(fill_id)


def _maker_queue_ahead(
    *,
    side: OrderSide,
    queue_ahead_quantity: float | None,
    order_books: tuple[OrderBookSnapshot, ...],
    effective_placement: datetime | None,
) -> float:
    if queue_ahead_quantity is not None:
        explicit = _non_negative_finite_float("queue_ahead_quantity", queue_ahead_quantity)
        return explicit
    if order_books and effective_placement is None:
        raise ValueError("queue_ahead_quantity inference requires placement_timestamp")
    eligible_books = [book for book in order_books if effective_placement is None or book.timestamp <= effective_placement]
    if not eligible_books:
        if effective_placement is not None:
            raise ValueError(
                "queue_ahead_quantity inference requires order book at or before placement_timestamp"
            )
        return 0.0
    book = sorted(eligible_books, key=lambda item: item.timestamp)[-1]
    size = book.bid_size if side == "buy" else book.ask_size
    if size is None:
        raise ValueError("queue_ahead_quantity inference requires visible order book size")
    return _non_negative_finite_float("order_book.bid_size" if side == "buy" else "order_book.ask_size", size)


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


def _eligible_symbol_order_books(
    symbol: str,
    order_books: tuple[OrderBookSnapshot, ...],
    *,
    placement_timestamp: datetime | None,
    max_evidence_lag: timedelta | None,
) -> list[OrderBookSnapshot]:
    eligible_books = [book for book in order_books if book.symbol == symbol]
    if placement_timestamp is not None:
        eligible_books = [book for book in eligible_books if book.timestamp >= placement_timestamp]
        if max_evidence_lag is not None:
            deadline = placement_timestamp + max_evidence_lag
            eligible_books = [book for book in eligible_books if book.timestamp <= deadline]
    return sorted(eligible_books, key=lambda item: item.timestamp)


def _eligible_symbol_trade_prints(
    symbol: str,
    trades: tuple[TradePrint, ...],
    *,
    placement_timestamp: datetime | None,
    max_evidence_lag: timedelta | None,
) -> list[TradePrint]:
    eligible_trades = [trade for trade in trades if trade.symbol == symbol]
    if placement_timestamp is not None:
        eligible_trades = [trade for trade in eligible_trades if trade.timestamp >= placement_timestamp]
        if max_evidence_lag is not None:
            deadline = placement_timestamp + max_evidence_lag
            eligible_trades = [trade for trade in eligible_trades if trade.timestamp <= deadline]
    return eligible_trades


def _side_levels(order_book: OrderBookSnapshot, *, side: OrderSide) -> tuple[DepthLevel, ...]:
    raw_levels = order_book.ask_levels if side == "buy" else order_book.bid_levels
    valid_levels = []
    for level in raw_levels:
        price = _strict_positive_finite_float("depth level price", level.price)
        quantity = _strict_positive_finite_float("depth level quantity", level.quantity)
        valid_levels.append(DepthLevel(price=price, quantity=quantity))
    if side == "buy":
        return tuple(sorted(valid_levels, key=lambda level: level.price))
    return tuple(sorted(valid_levels, key=lambda level: level.price, reverse=True))


def _side_slippage_bps(*, side: OrderSide, fill_price: float, reference_price: float) -> float | None:
    if reference_price <= 0.0 or fill_price <= 0.0:
        return None
    if side == "buy":
        return max(((float(fill_price) - float(reference_price)) / float(reference_price)) * 10_000.0, 0.0)
    return max(((float(reference_price) - float(fill_price)) / float(reference_price)) * 10_000.0, 0.0)


def _conservative_trade_print_taker_fill(
    *,
    symbol: str,
    side: OrderSide,
    quantity: float,
    trades: tuple[TradePrint, ...],
    placement_timestamp: datetime | None = None,
) -> ExecutionFill | None:
    requested_quantity = quantity
    side_known_trades: list[TradePrint] = []
    previous_trade_timestamp: datetime | None = None
    for trade in trades:
        if trade.symbol != symbol:
            continue
        if placement_timestamp is not None and trade.timestamp < placement_timestamp:
            continue
        price = _positive_finite_float("trade.price", trade.price)
        trade_quantity = _positive_finite_float("trade.quantity", trade.quantity)
        if trade.side != side:
            continue
        trade_timestamp = _timezone_aware_datetime("trade.timestamp", trade.timestamp, symbol=symbol)
        if previous_trade_timestamp is not None and trade_timestamp <= previous_trade_timestamp:
            raise ValueError(f"trade-print timestamps must be strictly increasing for {symbol} {side}")
        previous_trade_timestamp = trade_timestamp
        validated_trade = TradePrint(
            timestamp=trade_timestamp,
            symbol=trade.symbol,
            price=price,
            quantity=trade_quantity,
            side=trade.side,
            fill_id=trade.fill_id,
        )
        side_known_trades.append(validated_trade)
    symbol_trades = side_known_trades
    if not symbol_trades:
        return None
    selected_trades: list[TradePrint] = []
    filled_quantity = 0.0
    filled_notional: float | None = 0.0
    if requested_quantity <= 0.0:
        selected_trades = list(symbol_trades)
        filled_notional = None
    else:
        for trade in symbol_trades:
            if filled_quantity >= requested_quantity:
                break
            remaining_quantity = max(requested_quantity - filled_quantity, 0.0)
            fill_quantity = min(float(trade.quantity), remaining_quantity)
            selected_trades.append(trade)
            filled_quantity += fill_quantity
            filled_notional += fill_quantity * float(trade.price)
            if filled_quantity >= requested_quantity:
                break
    if requested_quantity > 0.0:
        filled_quantity = min(requested_quantity, filled_quantity)
        unfilled_quantity = max(requested_quantity - filled_quantity, 0.0)
        fill_quality: FillQuality = "evidence_backed" if unfilled_quantity <= 1e-12 else "partial_evidence_backed"
    else:
        filled_quantity = None
        unfilled_quantity = None
        fill_quality = "evidence_backed"
    if selected_trades:
        trade = max(selected_trades, key=lambda item: item.price) if side == "buy" else min(selected_trades, key=lambda item: item.price)
        evidence_timestamp = trade.timestamp
        first_fill_timestamp = selected_trades[0].timestamp
        last_fill_timestamp = selected_trades[-1].timestamp
    else:
        trade = symbol_trades[0]
        evidence_timestamp = trade.timestamp
        first_fill_timestamp = trade.timestamp
        last_fill_timestamp = trade.timestamp
    return ExecutionFill(
        symbol=symbol,
        side=side,
        quantity=requested_quantity,
        filled=True,
        fill_price=float(trade.price),
        fill_model="taker_trade_print",
        execution_price_source="trade_print",
        fill_quality=fill_quality,
        outcome="filled",
        evidence_timestamp=evidence_timestamp,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        filled_notional=filled_notional,
        unfilled_quantity=unfilled_quantity,
        first_fill_timestamp=first_fill_timestamp,
        last_fill_timestamp=last_fill_timestamp,
    )


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _finite_float(name: str, value: Any) -> float:
    if isinstance(value, (bool, str)):
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


def _depth_quantity_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    if isinstance(value, str):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    if result <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _positive_quantity_float(name: str, value: Any) -> float:
    if isinstance(value, (bool, str)):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _strict_positive_finite_float(name: str, value: Any) -> float:
    result = _finite_float(name, value)
    if result <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _non_negative_finite_float(name: str, value: Any) -> float:
    if isinstance(value, (bool, str)):
        raise ValueError(f"{name} must be a non-negative finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative finite number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return result


def _non_negative_timedelta(name: str, value: Any) -> timedelta:
    if not isinstance(value, timedelta):
        raise ValueError(f"{name} must be a non-negative timedelta")
    if value < timedelta(0):
        raise ValueError(f"{name} must be non-negative")
    return value


def _placement_timestamp_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("placement_timestamp must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("placement_timestamp must be timezone-aware")
    return value


def _cancel_replace_timestamp_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("cancel_replace_timestamp must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cancel_replace_timestamp must be timezone-aware")
    return value


def _canonical_order_side(value: Any) -> OrderSide:
    return _canonical_domain("side", value, _ORDER_SIDES)  # type: ignore[return-value]


def _canonical_domain(name: str, value: Any, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return str(value)


def _canonical_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be a canonical string")
    return value


def _canonical_symbol(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or value.upper() != value or not value.isalnum():
        raise ValueError(f"{name} must be an uppercase alphanumeric canonical string")
    return value


def _validate_maker_reasons(value: Any) -> None:
    if not isinstance(value, tuple):
        raise ValueError("maker_reasons must be a tuple")
    seen: set[str] = set()
    for reason in value:
        if not isinstance(reason, str) or not reason or reason.strip() != reason or any(char.isspace() for char in reason):
            raise ValueError("maker_reasons must contain canonical strings")
        if reason in seen:
            raise ValueError("maker_reasons must contain unique labels")
        seen.add(reason)


def _validate_depth_ladder_order(
    side_name: Literal["bid", "ask"],
    levels: tuple[DepthLevel, ...],
    *,
    symbol: str,
) -> None:
    previous_price: float | None = None
    for level in levels:
        price = _strict_positive_finite_float("depth level price", level.price)
        _strict_positive_finite_float("depth level quantity", level.quantity)
        if previous_price is not None:
            if side_name == "ask" and price <= previous_price:
                raise ValueError(f"ask depth levels must be strictly ascending by price for {symbol}")
            if side_name == "bid" and price >= previous_price:
                raise ValueError(f"bid depth levels must be strictly descending by price for {symbol}")
        previous_price = price


def _validate_depth_top_price(
    side_name: Literal["bid", "ask"],
    levels: tuple[DepthLevel, ...],
    *,
    top_price: float,
    symbol: str,
) -> None:
    if levels and levels[0].price != top_price:
        raise ValueError(f"first {side_name} depth level price must match order_book.{side_name} for {symbol}")


def _validate_depth_top_size(
    side_name: Literal["bid", "ask"],
    levels: tuple[DepthLevel, ...],
    *,
    top_size: float | None,
    symbol: str,
) -> None:
    if top_size is not None and levels and levels[0].quantity != top_size:
        raise ValueError(f"order_book.{side_name}_size must match first {side_name} depth level quantity for {symbol}")


def _timezone_aware_datetime(name: str, value: Any, *, symbol: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware for {symbol}")
    return value


def _validate_evidence_contract(
    *,
    symbol: str,
    order_books: tuple[OrderBookSnapshot, ...],
    trades: tuple[TradePrint, ...],
) -> None:
    seen_fill_ids: set[str] = set()
    previous_trade_timestamp: datetime | None = None
    validated_trades: list[tuple[datetime, float]] = []
    for trade in trades:
        if trade.symbol != symbol:
            continue
        trade_price = _positive_finite_float("trade.price", trade.price)
        _positive_finite_float("trade.quantity", trade.quantity)
        trade_timestamp = _timezone_aware_datetime("trade.timestamp", trade.timestamp, symbol=symbol)
        if trade.side is not None:
            _canonical_domain("trade.side", trade.side, _TRADE_PRINT_SIDES)
        if trade.fill_id is not None:
            fill_id = _canonical_string("trade.fill_id", trade.fill_id)
            if fill_id in seen_fill_ids:
                raise ValueError(f"duplicate trade.fill_id: {fill_id}")
            seen_fill_ids.add(fill_id)
        if previous_trade_timestamp is not None and trade_timestamp < previous_trade_timestamp:
            raise ValueError(f"trade timestamps must be monotonic for {symbol}")
        previous_trade_timestamp = trade_timestamp
        validated_trades.append((trade_timestamp, trade_price))

    previous_book_timestamp: datetime | None = None
    books_by_timestamp: dict[datetime, list[tuple[float, float]]] = {}
    for book in order_books:
        if book.symbol != symbol:
            continue
        bid = _positive_finite_float("order_book.bid", book.bid)
        ask = _positive_finite_float("order_book.ask", book.ask)
        if ask < bid:
            raise ValueError("order_book.ask must be greater than or equal to bid")
        if book.bid_size is not None:
            _non_negative_finite_float("order_book.bid_size", book.bid_size)
        if book.ask_size is not None:
            _non_negative_finite_float("order_book.ask_size", book.ask_size)
        _validate_depth_ladder_order("bid", book.bid_levels, symbol=symbol)
        _validate_depth_ladder_order("ask", book.ask_levels, symbol=symbol)
        _validate_depth_top_price("bid", book.bid_levels, top_price=bid, symbol=symbol)
        _validate_depth_top_price("ask", book.ask_levels, top_price=ask, symbol=symbol)
        _validate_depth_top_size("bid", book.bid_levels, top_size=book.bid_size, symbol=symbol)
        _validate_depth_top_size("ask", book.ask_levels, top_size=book.ask_size, symbol=symbol)
        book_timestamp = _timezone_aware_datetime("order_book.timestamp", book.timestamp, symbol=symbol)
        if previous_book_timestamp is not None and book_timestamp <= previous_book_timestamp:
            raise ValueError(f"order book timestamps must be strictly increasing for {symbol}")
        previous_book_timestamp = book_timestamp
        books_by_timestamp.setdefault(book_timestamp, []).append((bid, ask))

    for trade_timestamp, trade_price in validated_trades:
        for bid, ask in books_by_timestamp.get(trade_timestamp, ()):
            if trade_price < bid:
                raise ValueError(f"trade.price cannot be below contemporaneous order_book.bid for {symbol}")
            if trade_price > ask:
                raise ValueError(f"trade.price cannot be above contemporaneous order_book.ask for {symbol}")


def _validate_taker_evidence_timestamp_skew(
    *,
    symbol: str,
    order_books: tuple[OrderBookSnapshot, ...],
    trades: tuple[TradePrint, ...],
) -> None:
    symbol_books = [book for book in order_books if book.symbol == symbol]
    symbol_trades = [trade for trade in trades if trade.symbol == symbol]
    if not symbol_books or not symbol_trades:
        return

    evidence_timestamps = [book.timestamp for book in symbol_books]
    evidence_timestamps.extend(trade.timestamp for trade in symbol_trades)
    if max(evidence_timestamps) - min(evidence_timestamps) > _TAKER_EVIDENCE_MAX_SKEW:
        raise ValueError(f"taker evidence timestamp skew exceeds tolerance for {symbol}")


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
        return False
    return trade_side == ("sell" if side == "buy" else "buy")
