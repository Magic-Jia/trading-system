from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.app.backtest.execution_sim import (
    DepthLevel,
    ExecutionFill,
    OrderBookSnapshot,
    TradePrint,
    _conservative_trade_print_taker_fill,
    _validate_evidence_contract,
    _validate_taker_evidence_timestamp_skew,
    reference_close_fill,
    next_bar_ohlcv_fill,
    simulate_maker_limit_fill,
    simulate_taker_depth_fill,
    simulate_taker_fill,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _filled_execution_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "filled_quantity": 1.0,
        "filled_notional": 100.0,
        "unfilled_quantity": 0.0,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("symbol", ["", " BTCUSDT", "BTCUSDT ", 123])
def test_execution_fill_rejects_noncanonical_symbol(symbol: object) -> None:
    with pytest.raises(ValueError, match="symbol must be a canonical string"):
        ExecutionFill(
            symbol=symbol,
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="reference_close",
            execution_price_source="ohlcv_close",
            fill_quality="approximate",
            outcome="filled",
        )


@pytest.mark.parametrize("execution_lag_bars", [True, 1.0, "1", -1])
def test_execution_fill_rejects_invalid_execution_lag_bars(execution_lag_bars: object) -> None:
    with pytest.raises(ValueError, match="execution_lag_bars must be a non-negative integer"):
        ExecutionFill(**_filled_execution_kwargs(execution_lag_bars=execution_lag_bars))


@pytest.mark.parametrize("execution_timeframe", [" 1m", "1m ", 123])
def test_execution_fill_rejects_noncanonical_execution_timeframe(execution_timeframe: object) -> None:
    with pytest.raises(ValueError, match="execution_timeframe must be a canonical string"):
        ExecutionFill(**_filled_execution_kwargs(execution_timeframe=execution_timeframe))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("execution_timeframe", "", "next-bar OHLCV executions must include execution timeframe and positive lag"),
        ("execution_lag_bars", 0, "next-bar OHLCV executions must include execution timeframe and positive lag"),
        ("execution_lag_bars", -1, "execution_lag_bars must be a non-negative integer"),
    ],
)
def test_execution_fill_rejects_next_bar_ohlcv_without_timing_evidence(
    field: str,
    value: object,
    match: str,
) -> None:
    kwargs = _filled_execution_kwargs(
        fill_model="next_bar_ohlcv",
        execution_price_source="ohlcv_next_open",
        fill_quality="evidence_backed",
        evidence_timestamp=_ts("2026-03-10T00:01:00Z"),
        execution_timeframe="1m",
        execution_lag_bars=1,
    )
    kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_timeframe", "1m"),
        ("execution_lag_bars", 1),
    ],
)
def test_execution_fill_rejects_reference_close_with_timing_evidence_claim(
    field: str,
    value: object,
) -> None:
    kwargs = _filled_execution_kwargs()
    kwargs[field] = value

    with pytest.raises(ValueError, match="reference-close executions cannot include execution timing metadata"):
        ExecutionFill(**kwargs)


def test_execution_fill_accepts_next_bar_ohlcv_with_canonical_timeframe_and_lag() -> None:
    fill = ExecutionFill(
        **_filled_execution_kwargs(
            fill_model="next_bar_ohlcv",
            execution_price_source="ohlcv_next_open",
            fill_quality="evidence_backed",
            evidence_timestamp=_ts("2026-03-10T00:01:00Z"),
            execution_timeframe="1m",
            execution_lag_bars=1,
        )
    )

    assert fill.execution_timeframe == "1m"
    assert fill.execution_lag_bars == 1


def test_execution_fill_accepts_reference_close_without_timing_evidence_claim() -> None:
    fill = ExecutionFill(**_filled_execution_kwargs())

    assert fill.execution_timeframe == ""
    assert fill.execution_lag_bars == 0


@pytest.mark.parametrize("depth_levels_consumed", [-1, 1.5, True, "1"])
def test_execution_fill_rejects_invalid_depth_levels_consumed(depth_levels_consumed: object) -> None:
    with pytest.raises(ValueError, match="depth_levels_consumed must be a non-negative integer"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=depth_levels_consumed,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_queue_remaining_above_initial() -> None:
    with pytest.raises(ValueError, match="queue_ahead_remaining cannot exceed queue_ahead_initial"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="maker_post_only_queue",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="missed_alpha",
            maker_status="expired",
            queue_ahead_initial=1.0,
            queue_ahead_remaining=1.1,
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        (
            {"queue_ahead_initial": 1.0, "maker_status": "expired"},
            "maker queue evidence requires both queue_ahead_initial and queue_ahead_remaining",
        ),
        (
            {"queue_ahead_remaining": 0.5, "maker_status": "expired"},
            "maker queue evidence requires both queue_ahead_initial and queue_ahead_remaining",
        ),
        (
            {"queue_ahead_initial": 1.0, "queue_ahead_remaining": 0.5},
            "maker queue evidence requires maker_status",
        ),
        (
            {"maker_wait_seconds": 1.0},
            "maker_wait_seconds requires maker_status",
        ),
    ],
)
def test_execution_fill_rejects_incomplete_maker_queue_evidence(
    overrides: dict[str, object],
    match: str,
) -> None:
    kwargs: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": False,
        "fill_price": None,
        "fill_model": "maker_post_only_queue",
        "execution_price_source": "no_crossing_evidence",
        "fill_quality": "no_fill",
        "outcome": "missed_alpha",
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize("maker_field, value", [
    ("maker_status", "expired"),
    ("queue_ahead_initial", 1.0),
    ("queue_ahead_remaining", 0.5),
    ("maker_wait_seconds", 1.0),
    ("maker_reasons", ("resting",)),
])
def test_execution_fill_rejects_maker_fields_on_non_maker_models(maker_field: str, value: object) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "taker_orderbook_depth",
        "execution_price_source": "ask_depth",
        "fill_quality": "evidence_backed",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "filled_quantity": 1.0,
        "filled_notional": 100.0,
        "unfilled_quantity": 0.0,
        "evidence_timestamp": _ts("2026-03-10T00:00:01Z"),
    }
    kwargs[maker_field] = value

    with pytest.raises(ValueError, match="maker fields require maker fill model"):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("maker_reasons", "match"),
    [
        (["resting"], "maker_reasons must be a tuple"),
        (("",), "maker_reasons must contain canonical strings"),
        ((" resting",), "maker_reasons must contain canonical strings"),
        (("resting ",), "maker_reasons must contain canonical strings"),
        ((123,), "maker_reasons must contain canonical strings"),
        (("resting", "resting"), "maker_reasons must contain unique labels"),
    ],
)
def test_execution_fill_rejects_noncanonical_maker_reasons(
    maker_reasons: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="maker_post_only_queue",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="missed_alpha",
            maker_status="expired",
            queue_ahead_initial=1.0,
            queue_ahead_remaining=1.0,
            maker_reasons=maker_reasons,
        )


def test_execution_fill_accepts_canonical_unique_maker_reasons_tuple() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=False,
        fill_price=None,
        fill_model="maker_post_only_queue",
        execution_price_source="no_crossing_evidence",
        fill_quality="no_fill",
        outcome="missed_alpha",
        maker_status="expired",
        queue_ahead_initial=1.0,
        queue_ahead_remaining=1.0,
        maker_reasons=("latency_applied", "timeout_expired"),
    )

    assert fill.maker_reasons == ("latency_applied", "timeout_expired")


@pytest.mark.parametrize("fill_model, price_source", [
    ("taker_trade_print", "trade_print"),
    ("maker_post_only_queue", "no_crossing_evidence"),
    ("reference_close", "ohlcv_close"),
])
def test_execution_fill_rejects_depth_consumption_on_non_orderbook_models(fill_model: str, price_source: str) -> None:
    with pytest.raises(ValueError, match="depth_levels_consumed requires taker orderbook fill model"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model=fill_model,
            execution_price_source=price_source,
            fill_quality="evidence_backed" if fill_model != "reference_close" else "approximate",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=1,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z") if fill_model != "reference_close" else None,
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z") if fill_model == "taker_trade_print" else None,
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z") if fill_model == "taker_trade_print" else None,
        )


@pytest.mark.parametrize("price_source", ["best_ask", "best_bid"])
def test_execution_fill_rejects_depth_consumption_on_best_top_of_book_source(price_source: str) -> None:
    with pytest.raises(ValueError, match="depth_levels_consumed requires depth price source"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook",
            execution_price_source=price_source,
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=1,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_depth_consumption_on_top_of_book_fill_model() -> None:
    with pytest.raises(ValueError, match="depth_levels_consumed requires taker orderbook depth fill model"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=1,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


@pytest.mark.parametrize("depth_levels_consumed", [True, "1", 1.0, -1])
def test_execution_fill_rejects_invalid_depth_levels_consumed_scalar(depth_levels_consumed: object) -> None:
    with pytest.raises(ValueError, match="depth_levels_consumed must be a non-negative integer"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            depth_levels_consumed=depth_levels_consumed,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_accepts_depth_consumption_on_depth_price_source() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.0,
        fill_model="taker_orderbook_depth",
        execution_price_source="ask_depth",
        fill_quality="evidence_backed",
        outcome="filled",
        requested_quantity=1.0,
        filled_quantity=1.0,
        filled_notional=100.0,
        unfilled_quantity=0.0,
        depth_levels_consumed=1,
        evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
    )

    assert fill.depth_levels_consumed == 1


@pytest.mark.parametrize("evidence_timestamp", [
    _ts("2026-03-10T00:00:00Z"),
    _ts("2026-03-10T00:00:04Z"),
])
def test_execution_fill_rejects_trade_print_evidence_timestamp_outside_fill_interval(evidence_timestamp: datetime) -> None:
    with pytest.raises(ValueError, match="evidence_timestamp must fall within fill timestamp interval"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_trade_print",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            evidence_timestamp=evidence_timestamp,
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:03Z"),
        )


@pytest.mark.parametrize(
    ("fill_model", "execution_price_source"),
    [
        ("maker_orderbook_trade_evidence", "book_cross"),
        ("maker_post_only_queue", "trade_print"),
    ],
)
@pytest.mark.parametrize("evidence_timestamp", [
    _ts("2026-03-10T00:00:00Z"),
    _ts("2026-03-10T00:00:04Z"),
])
def test_execution_fill_rejects_maker_evidence_timestamp_outside_fill_interval(
    fill_model: str,
    execution_price_source: str,
    evidence_timestamp: datetime,
) -> None:
    with pytest.raises(ValueError, match="evidence_timestamp must fall within fill timestamp interval"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model=fill_model,
            execution_price_source=execution_price_source,
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            evidence_timestamp=evidence_timestamp,
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:03Z"),
            maker_status="filled",
        )


@pytest.mark.parametrize(
    ("first_fill_timestamp", "last_fill_timestamp"),
    [
        (_ts("2026-03-10T00:00:01Z"), None),
        (None, _ts("2026-03-10T00:00:01Z")),
    ],
)
def test_execution_fill_rejects_partial_fill_timestamp_pair(
    first_fill_timestamp: datetime | None,
    last_fill_timestamp: datetime | None,
) -> None:
    with pytest.raises(ValueError, match="fill timestamps must be provided as a pair"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="reference_close",
            execution_price_source="ohlcv_close",
            fill_quality="approximate",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            first_fill_timestamp=first_fill_timestamp,
            last_fill_timestamp=last_fill_timestamp,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"filled": False, "outcome": "missed_alpha"},
        {"filled": False, "outcome": "missed_alpha", "fill_price": None},
        {"fill_quality": "no_fill", "filled": False, "fill_price": None, "outcome": "missed_alpha"},
    ],
)
def test_execution_fill_rejects_fill_timestamps_on_non_filled_states(override: dict[str, object]) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "filled_quantity": 0.0,
        "filled_notional": 0.0,
        "unfilled_quantity": 1.0,
        "first_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
        "last_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
    }
    kwargs.update(override)

    with pytest.raises(ValueError, match="fill timestamps require a filled execution state|no-fill execution"):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filled_quantity", None),
        ("filled_notional", None),
    ],
)
def test_execution_fill_rejects_fill_timestamps_without_complete_positive_fill_accounting(
    field: str,
    value: object,
) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "filled_quantity": 1.0,
        "filled_notional": 100.0,
        "unfilled_quantity": 0.0,
        "first_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
        "last_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="fill timestamps require complete positive fill accounting"):
        ExecutionFill(**kwargs)


def test_execution_fill_accepts_trade_print_fill_with_timestamp_interval() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.0,
        fill_model="taker_trade_print",
        execution_price_source="trade_print",
        fill_quality="evidence_backed",
        outcome="filled",
        requested_quantity=1.0,
        filled_quantity=1.0,
        filled_notional=100.0,
        unfilled_quantity=0.0,
        evidence_timestamp=_ts("2026-03-10T00:00:02Z"),
        first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        last_fill_timestamp=_ts("2026-03-10T00:00:03Z"),
    )

    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:03Z")


def test_execution_fill_rejects_filled_state_with_missed_alpha_outcome() -> None:
    with pytest.raises(ValueError, match="filled executions must have filled outcome"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook",
            execution_price_source="best_ask",
            fill_quality="evidence_backed",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_no_fill_state_with_filled_outcome() -> None:
    with pytest.raises(ValueError, match="unfilled executions cannot have filled outcome"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="maker_post_only_queue",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="filled",
            maker_status="expired",
            queue_ahead_initial=1.0,
            queue_ahead_remaining=1.0,
        )


@pytest.mark.parametrize(("field", "value", "match"), [
    ("filled_quantity", 0.01, "unfilled executions cannot include positive filled quantity"),
    ("filled_notional", 1.0, "unfilled executions cannot include positive filled notional"),
])
def test_execution_fill_rejects_unfilled_state_with_positive_accounting(field: str, value: float, match: str) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": False,
        "fill_price": None,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "missed_alpha",
        "requested_quantity": 1.0,
        "filled_quantity": 0.0,
        "filled_notional": 0.0,
        "unfilled_quantity": 1.0,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


def test_execution_fill_rejects_no_fill_with_positive_accounting() -> None:
    with pytest.raises(ValueError, match="no-fill execution cannot include filled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="no_fill",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=0.1,
            filled_notional=0.0,
            unfilled_quantity=0.9,
        )

    with pytest.raises(ValueError, match="no-fill execution cannot include filled notional"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="no_fill",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=10.0,
            unfilled_quantity=1.0,
        )

    with pytest.raises(ValueError, match="no-fill execution cannot consume depth levels"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="no_fill",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=1.0,
            depth_levels_consumed=1,
        )


@pytest.mark.parametrize("field", ["execution_impact_bps", "slippage_bps"])
def test_execution_fill_rejects_no_fill_with_impact_or_slippage_evidence(field: str) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": False,
        "fill_price": None,
        "fill_model": "taker_orderbook_depth",
        "execution_price_source": "ask_depth",
        "fill_quality": "no_fill",
        "outcome": "missed_alpha",
        "evidence_timestamp": _ts("2026-03-10T00:00:01Z"),
        "requested_quantity": 1.0,
        "filled_quantity": 0.0,
        "filled_notional": 0.0,
        "unfilled_quantity": 1.0,
        "depth_levels_consumed": 0,
        field: 0.0,
    }

    with pytest.raises(ValueError, match=rf"{field} requires an evidence-backed filled execution"):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize("field", ["execution_impact_bps", "slippage_bps"])
def test_execution_fill_rejects_reference_close_with_impact_or_slippage_evidence(field: str) -> None:
    with pytest.raises(ValueError, match=rf"{field} requires an evidence-backed filled execution"):
        ExecutionFill(**_filled_execution_kwargs(**{field: 0.0}))


def test_execution_fill_accepts_evidence_backed_taker_depth_impact_and_slippage() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.5,
        fill_model="taker_orderbook_depth",
        execution_price_source="ask_depth",
        fill_quality="evidence_backed",
        outcome="filled",
        evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        requested_quantity=1.0,
        filled_quantity=1.0,
        filled_notional=100.5,
        unfilled_quantity=0.0,
        depth_levels_consumed=1,
        execution_impact_bps=5.0,
        slippage_bps=2.5,
    )

    assert fill.execution_impact_bps == pytest.approx(5.0)
    assert fill.slippage_bps == pytest.approx(2.5)


def test_execution_fill_rejects_negative_taker_depth_slippage_bps() -> None:
    with pytest.raises(ValueError, match="slippage_bps must be a non-negative finite number"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="sell",
            quantity=1.0,
            filled=True,
            fill_price=99.5,
            fill_model="taker_orderbook_depth",
            execution_price_source="bid_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=99.5,
            unfilled_quantity=0.0,
            depth_levels_consumed=1,
            execution_impact_bps=5.0,
            slippage_bps=-5.0,
        )


def test_execution_fill_rejects_no_fill_with_fill_timestamps_or_filled_flag() -> None:
    timestamp = _ts("2026-03-10T00:00:01Z")
    with pytest.raises(ValueError, match="no-fill execution cannot include fill timestamps"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="no_fill",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=1.0,
            first_fill_timestamp=timestamp,
            last_fill_timestamp=timestamp,
        )

    with pytest.raises(ValueError, match="filled executions cannot have no_fill quality"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="no_fill",
            outcome="missed_alpha",
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=1.0,
        )


def test_execution_fill_rejects_filled_state_without_price_or_quantity_accounting() -> None:
    with pytest.raises(ValueError, match="filled executions must include fill_price"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=None,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
        )

    with pytest.raises(ValueError, match="filled executions must include positive filled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=100.0,
            unfilled_quantity=1.0,
        )

    with pytest.raises(ValueError, match="filled executions must include positive filled notional"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=0.0,
            unfilled_quantity=0.0,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("filled_quantity", "omit", "filled executions must include positive filled quantity"),
        ("filled_quantity", None, "filled executions must include positive filled quantity"),
        ("filled_quantity", 0.0, "filled executions must include positive filled quantity"),
        ("filled_quantity", -1.0, "filled_quantity must be a non-negative finite number"),
        ("filled_quantity", math.nan, "filled_quantity must be a non-negative finite number"),
        ("filled_quantity", math.inf, "filled_quantity must be a non-negative finite number"),
        ("filled_notional", "omit", "filled executions must include positive filled notional"),
        ("filled_notional", None, "filled executions must include positive filled notional"),
        ("filled_notional", 0.0, "filled executions must include positive filled notional"),
        ("filled_notional", -100.0, "filled_notional must be a non-negative finite number"),
        ("filled_notional", math.nan, "filled_notional must be a non-negative finite number"),
        ("filled_notional", math.inf, "filled_notional must be a non-negative finite number"),
    ],
)
def test_execution_fill_rejects_filled_state_without_positive_accounting(
    field: str,
    value: object,
    match: str,
) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "filled_quantity": 1.0,
        "filled_notional": 100.0,
        "unfilled_quantity": 0.0,
    }
    if value == "omit":
        kwargs.pop(field)
    else:
        kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("filled_quantity", 1.1, "fill quantities must conserve requested quantity"),
        ("unfilled_quantity", None, "fill quantities must include unfilled quantity"),
        ("unfilled_quantity", 0.2, "fill quantities must conserve requested quantity"),
        ("unfilled_quantity", -0.1, "unfilled_quantity must be a non-negative finite number"),
        ("unfilled_quantity", math.nan, "unfilled_quantity must be a non-negative finite number"),
        ("unfilled_quantity", math.inf, "unfilled_quantity must be a non-negative finite number"),
    ],
)
def test_execution_fill_rejects_non_conserved_requested_fill_quantities(
    field: str,
    value: object,
    match: str,
) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "requested_notional": 100.0,
        "filled_quantity": 0.75,
        "filled_notional": 75.0,
        "unfilled_quantity": 0.25,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("filled_quantity", "unfilled_quantity"),
    [
        (1.0, 0.0),
        (0.75, 0.25),
    ],
)
def test_execution_fill_accepts_conserved_full_and_partial_requested_fill_quantities(
    filled_quantity: float,
    unfilled_quantity: float,
) -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.0,
        fill_model="reference_close",
        execution_price_source="ohlcv_close",
        fill_quality="approximate",
        outcome="filled",
        requested_quantity=1.0,
        requested_notional=100.0,
        filled_quantity=filled_quantity,
        filled_notional=filled_quantity * 100.0,
        unfilled_quantity=unfilled_quantity,
        unfilled_notional=unfilled_quantity * 100.0,
    )

    assert fill.filled_quantity == pytest.approx(filled_quantity)
    assert fill.unfilled_quantity == pytest.approx(unfilled_quantity)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("filled_notional", 100.01, "fill notionals must conserve requested notional"),
        ("unfilled_notional", "omit", "fill notionals must include unfilled notional"),
        ("unfilled_notional", None, "fill notionals must include unfilled notional"),
        ("unfilled_notional", 24.0, "fill notionals must conserve requested notional"),
        ("unfilled_notional", -0.1, "unfilled_notional must be a non-negative finite number"),
        ("unfilled_notional", math.nan, "unfilled_notional must be a non-negative finite number"),
        ("unfilled_notional", math.inf, "unfilled_notional must be a non-negative finite number"),
    ],
)
def test_execution_fill_rejects_non_conserved_requested_fill_notionals(
    field: str,
    value: object,
    match: str,
) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "reference_close",
        "execution_price_source": "ohlcv_close",
        "fill_quality": "approximate",
        "outcome": "filled",
        "requested_quantity": 1.0,
        "requested_notional": 100.0,
        "filled_quantity": 0.75,
        "filled_notional": 75.0,
        "unfilled_quantity": 0.25,
        "unfilled_notional": 25.0,
    }
    if value == "omit":
        kwargs.pop(field)
    else:
        kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**kwargs)


@pytest.mark.parametrize(
    ("filled_notional", "unfilled_notional"),
    [
        (100.0, 0.0),
        (75.0, 25.0),
    ],
)
def test_execution_fill_accepts_conserved_full_and_partial_requested_fill_notionals(
    filled_notional: float,
    unfilled_notional: float,
) -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.0,
        fill_model="reference_close",
        execution_price_source="ohlcv_close",
        fill_quality="approximate",
        outcome="filled",
        requested_quantity=1.0,
        requested_notional=100.0,
        filled_quantity=filled_notional / 100.0,
        filled_notional=filled_notional,
        unfilled_quantity=unfilled_notional / 100.0,
        unfilled_notional=unfilled_notional,
    )

    assert fill.filled_notional == pytest.approx(filled_notional)
    assert fill.unfilled_notional == pytest.approx(unfilled_notional)


def test_execution_fill_rejects_orderbook_depth_accounting_price_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="filled notional must equal filled quantity times fill_price"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=99.0,
            unfilled_quantity=0.0,
        )


def test_execution_fill_rejects_full_quality_taker_depth_with_depth_exhaustion() -> None:
    with pytest.raises(ValueError, match="evidence-backed orderbook-depth fills cannot leave unfilled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=3.0,
            filled=True,
            fill_price=100.5,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=3.0,
            requested_notional=301.5,
            filled_quantity=2.0,
            filled_notional=201.0,
            unfilled_quantity=1.0,
            unfilled_notional=100.5,
            depth_levels_consumed=2,
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_evidence_backed_fill_without_evidence_timestamp() -> None:
    with pytest.raises(ValueError, match="evidence-backed executions must include evidence_timestamp"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            evidence_timestamp=None,
        )


def test_execution_fill_rejects_trade_print_evidence_without_fill_timestamps() -> None:
    with pytest.raises(ValueError, match="trade-print evidence-backed executions must include fill timestamps"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_trade_print",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            first_fill_timestamp=None,
            last_fill_timestamp=None,
        )


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
    with pytest.raises(ValueError, match="order book timestamps must be strictly increasing for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=99.9, ask=100.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.8, ask=100.0),
            ),
            trades=(),
        )


def test_evidence_contract_rejects_duplicate_same_symbol_order_book_timestamps() -> None:
    with pytest.raises(ValueError, match="order book timestamps must be strictly increasing for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=199.9, ask=200.1),
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.8, ask=100.2),
            ),
            trades=(),
        )


def test_evidence_contract_accepts_equal_order_book_timestamps_for_different_symbols() -> None:
    _validate_evidence_contract(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=199.9, ask=200.1),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        ),
        trades=(),
    )


def test_evidence_contract_accepts_strictly_increasing_same_symbol_order_book_timestamps() -> None:
    _validate_evidence_contract(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="ETHUSDT", bid=199.9, ask=200.1),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", bid=99.8, ask=100.2),
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


@pytest.mark.parametrize(
    ("levels", "match"),
    [
        (
            (DepthLevel(price=100.1, quantity=1.0), DepthLevel(price=100.0, quantity=1.0)),
            "ask depth levels must be strictly ascending by price for BTCUSDT",
        ),
        (
            (DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=100.0, quantity=1.0)),
            "ask depth levels must be strictly ascending by price for BTCUSDT",
        ),
    ],
)
def test_evidence_contract_rejects_non_canonical_ask_depth_prices(
    levels: tuple[DepthLevel, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.0,
                    ask_levels=levels,
                ),
            ),
            trades=(),
        )


def test_evidence_contract_rejects_ask_depth_top_price_mismatch() -> None:
    with pytest.raises(ValueError, match="first ask depth level price must match order_book.ask for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.0,
                    ask_levels=(DepthLevel(price=100.1, quantity=1.0),),
                ),
            ),
            trades=(),
        )


@pytest.mark.parametrize(
    ("levels", "match"),
    [
        (
            (DepthLevel(price=99.9, quantity=1.0), DepthLevel(price=100.0, quantity=1.0)),
            "bid depth levels must be strictly descending by price for BTCUSDT",
        ),
        (
            (DepthLevel(price=99.9, quantity=1.0), DepthLevel(price=99.9, quantity=1.0)),
            "bid depth levels must be strictly descending by price for BTCUSDT",
        ),
    ],
)
def test_evidence_contract_rejects_non_canonical_bid_depth_prices(
    levels: tuple[DepthLevel, ...],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.0,
                    bid_levels=levels,
                ),
            ),
            trades=(),
        )


def test_evidence_contract_rejects_bid_depth_top_price_mismatch() -> None:
    with pytest.raises(ValueError, match="first bid depth level price must match order_book.bid for BTCUSDT"):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.0,
                    bid_levels=(DepthLevel(price=99.8, quantity=1.0),),
                ),
            ),
            trades=(),
        )


@pytest.mark.parametrize(
    ("size_field", "levels_field", "levels", "match"),
    [
        (
            "bid_size",
            "bid_levels",
            (DepthLevel(price=99.9, quantity=1.0),),
            "order_book.bid_size must match first bid depth level quantity for BTCUSDT",
        ),
        (
            "ask_size",
            "ask_levels",
            (DepthLevel(price=100.0, quantity=1.0),),
            "order_book.ask_size must match first ask depth level quantity for BTCUSDT",
        ),
    ],
)
def test_evidence_contract_rejects_depth_top_size_mismatch(
    size_field: str,
    levels_field: str,
    levels: tuple[DepthLevel, ...],
    match: str,
) -> None:
    book_kwargs = {size_field: 2.0, levels_field: levels}

    with pytest.raises(ValueError, match=match):
        _validate_evidence_contract(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    bid=99.9,
                    ask=100.0,
                    **book_kwargs,
                ),
            ),
            trades=(),
        )


def test_evidence_contract_accepts_depth_top_prices_matching_best_bid_ask() -> None:
    _validate_evidence_contract(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                bid_levels=(DepthLevel(price=99.9, quantity=1.0), DepthLevel(price=99.8, quantity=1.0)),
                ask_levels=(DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=100.1, quantity=1.0)),
            ),
        ),
        trades=(),
    )


def test_evidence_contract_accepts_depth_top_sizes_matching_first_level_quantities() -> None:
    _validate_evidence_contract(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                bid_size=1.5,
                ask_size=2.5,
                bid_levels=(DepthLevel(price=99.9, quantity=1.5), DepthLevel(price=99.8, quantity=1.0)),
                ask_levels=(DepthLevel(price=100.0, quantity=2.5), DepthLevel(price=100.1, quantity=1.0)),
            ),
        ),
        trades=(),
    )


def test_evidence_contract_accepts_missing_top_size_or_empty_depth_side_without_size_match() -> None:
    _validate_evidence_contract(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                bid_size=None,
                ask_size=2.0,
                bid_levels=(DepthLevel(price=99.9, quantity=1.0),),
                ask_levels=(),
            ),
        ),
        trades=(),
    )


def test_maker_buy_limit_fill_records_multi_print_fill_interval() -> None:
    first_contributing_trade_timestamp = _ts("2026-03-10T00:00:03Z")
    final_contributing_trade_timestamp = _ts("2026-03-10T00:00:05Z")
    requested_quantity = 2.0
    limit_price = 99.5
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=limit_price,
        quantity=requested_quantity,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.4, ask=100.0),
        ),
        trades=(
            TradePrint(timestamp=_ts("2026-03-10T00:00:02Z"), symbol="BTCUSDT", price=99.6, quantity=100.0),
            TradePrint(timestamp=first_contributing_trade_timestamp, symbol="BTCUSDT", price=99.5, quantity=0.75),
            TradePrint(timestamp=_ts("2026-03-10T00:00:04Z"), symbol="ETHUSDT", price=99.4, quantity=100.0),
            TradePrint(timestamp=final_contributing_trade_timestamp, symbol="BTCUSDT", price=99.4, quantity=1.5),
            TradePrint(timestamp=_ts("2026-03-10T00:00:06Z"), symbol="BTCUSDT", price=99.4, quantity=100.0),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(limit_price)
    assert fill.fill_model == "maker_orderbook_trade_evidence"
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.outcome == "filled"
    assert fill.evidence_timestamp == final_contributing_trade_timestamp
    assert fill.first_fill_timestamp == first_contributing_trade_timestamp
    assert fill.last_fill_timestamp == final_contributing_trade_timestamp
    assert fill.filled_quantity == pytest.approx(requested_quantity)
    assert fill.filled_notional == pytest.approx(requested_quantity * limit_price)
    assert fill.unfilled_quantity == pytest.approx(0.0)


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
                fill_id="maker-print-001",
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


def test_maker_queue_full_fill_conserves_limit_price_notional_identity() -> None:
    limit_price = 99.5
    requested_quantity = 1.0

    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=limit_price,
        quantity=requested_quantity,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=limit_price,
                quantity=3.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.fill_price == pytest.approx(limit_price)
    assert fill.filled_quantity == pytest.approx(requested_quantity)
    assert fill.filled_notional == pytest.approx(fill.filled_quantity * fill.fill_price)
    assert fill.unfilled_quantity == pytest.approx(0.0)


def test_maker_queue_full_fill_evidence_timestamp_is_last_actual_fill_print() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        queue_ahead_quantity=0.5,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=2.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:03Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == fill.evidence_timestamp
    assert fill.first_fill_timestamp <= fill.last_fill_timestamp


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status", "expected_reason"),
    [
        (None, 2.0, "partial", "timeout_expired"),
        (_ts("2026-03-10T00:00:02Z"), 10.0, "partial", "cancel_replace_after_partial"),
    ],
)
def test_maker_queue_partial_fill_conserves_limit_price_notional_and_unfilled_identity(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
    expected_reason: str,
) -> None:
    limit_price = 99.5
    requested_quantity = 2.0

    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=limit_price,
        quantity=requested_quantity,
        queue_ahead_quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=limit_price,
                quantity=2.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=limit_price,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.fill_price == pytest.approx(limit_price)
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.filled_notional == pytest.approx(fill.filled_quantity * fill.fill_price)
    assert fill.unfilled_quantity == pytest.approx(requested_quantity - fill.filled_quantity)
    assert expected_reason in fill.maker_reasons


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status"),
    [
        (_ts("2026-03-10T00:00:02Z"), 10.0, "partial"),
        (None, 2.0, "partial"),
    ],
)
def test_maker_queue_partial_fill_evidence_timestamp_remains_last_actual_fill_print(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        queue_ahead_quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=2.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == fill.evidence_timestamp
    assert fill.first_fill_timestamp <= fill.last_fill_timestamp


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status", "expected_reason"),
    [
        (None, 2.0, "expired", "timeout_expired"),
        (_ts("2026-03-10T00:00:02Z"), 10.0, "cancelled_replaced", "cancel_replace_before_fill"),
    ],
)
def test_maker_queue_no_fill_conserves_zero_fill_accounting_identity(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
    expected_reason: str,
) -> None:
    requested_quantity = 1.0

    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=requested_quantity,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.filled_notional == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(requested_quantity)
    assert expected_reason in fill.maker_reasons


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status"),
    [
        (_ts("2026-03-10T00:00:02Z"), 10.0, "cancelled_replaced"),
        (None, 2.0, "expired"),
    ],
)
def test_maker_queue_no_fill_evidence_timestamps_are_empty(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.evidence_timestamp is None
    assert fill.first_fill_timestamp is None
    assert fill.last_fill_timestamp is None


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status", "expected_reasons"),
    [
        (
            _ts("2026-03-10T00:00:00.400000Z"),
            1.0,
            "cancelled_replaced",
            ("latency_applied", "queue_depleted", "cancel_replace_before_fill"),
        ),
        (
            None,
            0.4,
            "expired",
            ("latency_applied", "queue_depleted", "timeout_expired"),
        ),
    ],
)
def test_maker_reasons_preserve_order_from_latency_queue_depletion_to_terminal_no_fill(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
    expected_reasons: tuple[str, ...],
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=100,
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.200000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.300000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="buy",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.filled is False
    assert fill.maker_reasons == expected_reasons
    assert len(fill.maker_reasons) == len(set(fill.maker_reasons))


def test_maker_reasons_are_duplicate_free_when_multiple_prints_deplete_empty_queue() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=2.0,
        queue_ahead_quantity=0.5,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=100,
        timeout_seconds=1.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.200000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.300000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == "partial"
    assert fill.filled_quantity == pytest.approx(1.5)
    assert fill.maker_reasons == ("latency_applied", "queue_depleted", "timeout_expired")
    assert len(fill.maker_reasons) == len(set(fill.maker_reasons))


def test_maker_reasons_include_stable_terminal_reason_for_no_crossing_evidence() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=0.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=100,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.200000Z"),
                symbol="BTCUSDT",
                price=100.5,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "no_fill"
    assert fill.maker_reasons == ("latency_applied", "no_crossing_evidence")
    assert len(fill.maker_reasons) == len(set(fill.maker_reasons))


@pytest.mark.parametrize(
    ("side", "limit_price", "signed_trade_side"),
    [
        ("buy", 99.5, "sell"),
        ("sell", 100.5, "buy"),
    ],
)
def test_maker_queue_ignores_unsigned_trade_prints_before_signed_fill(
    side: str,
    limit_price: float,
    signed_trade_side: str,
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side=side,
        limit_price=limit_price,
        quantity=1.0,
        queue_ahead_quantity=2.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=limit_price,
                quantity=10.0,
                side=None,
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=limit_price,
                quantity=3.0,
                side=signed_trade_side,
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.queue_ahead_initial == pytest.approx(2.0)
    assert fill.queue_ahead_remaining == pytest.approx(0.0)
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:02Z")
    assert "queue_depleted" in fill.maker_reasons
    assert "ambiguous_trade_side_assumed" not in fill.maker_reasons


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
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:03Z")


def test_maker_queue_rejects_missing_trade_fill_id_before_consuming_queue() -> None:
    with pytest.raises(ValueError, match="trade.fill_id is required"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            queue_ahead_quantity=2.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            timeout_seconds=10.0,
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


def test_maker_queue_rejects_blank_trade_fill_id_before_consuming_queue() -> None:
    trade = TradePrint(
        timestamp=_ts("2026-03-10T00:00:01Z"),
        symbol="BTCUSDT",
        price=99.5,
        quantity=3.0,
        side="sell",
        fill_id="maker-print-001",
    )
    object.__setattr__(trade, "fill_id", " ")

    with pytest.raises(ValueError, match="trade.fill_id is required"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            queue_ahead_quantity=2.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            timeout_seconds=10.0,
            trades=(trade,),
        )


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
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.4,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == "partial"
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
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:00.060000Z")
    assert fill.maker_wait_seconds == pytest.approx(0.01)
    assert "latency_applied" in fill.maker_reasons


@pytest.mark.parametrize(
    ("quantity", "extra_trades", "expected_status"),
    [
        (
            1.0,
            (
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:00.750000Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                    fill_id="maker-print-002",
                ),
            ),
            "filled",
        ),
        (2.0, (), "partial"),
    ],
)
def test_maker_queue_wait_seconds_for_fills_uses_first_fill_after_effective_placement(
    quantity: float,
    extra_trades: tuple[TradePrint, ...],
    expected_status: str,
) -> None:
    placement = _ts("2026-03-10T00:00:00Z")
    first_fill = _ts("2026-03-10T00:00:00.250000Z")

    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=quantity,
        queue_ahead_quantity=0.0,
        placement_timestamp=placement,
        latency_ms=100,
        cancel_replace_timestamp=_ts("2026-03-10T00:00:01Z"),
        trades=(
            TradePrint(
                timestamp=first_fill,
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        )
        + extra_trades,
    )

    assert fill.maker_status == expected_status
    assert fill.first_fill_timestamp == first_fill
    assert fill.maker_wait_seconds == pytest.approx(0.15)


@pytest.mark.parametrize(
    ("cancel_replace_timestamp", "timeout_seconds", "expected_status", "expected_wait_seconds"),
    [
        (_ts("2026-03-10T00:00:00.400000Z"), 1.0, "cancelled_replaced", 0.15),
        (None, 0.4, "expired", 0.4),
    ],
)
def test_maker_queue_wait_seconds_for_no_fill_uses_cutoff_after_effective_placement(
    cancel_replace_timestamp: datetime | None,
    timeout_seconds: float,
    expected_status: str,
    expected_wait_seconds: float,
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=250,
        cancel_replace_timestamp=cancel_replace_timestamp,
        timeout_seconds=timeout_seconds,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.300000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=0.5,
                side="sell",
                fill_id="maker-print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=10.0,
                side="sell",
                fill_id="maker-print-002",
            ),
        ),
    )

    assert fill.maker_status == expected_status
    assert fill.first_fill_timestamp is None
    assert fill.maker_wait_seconds == pytest.approx(expected_wait_seconds)


def test_maker_queue_wait_seconds_is_none_without_placement_anchor() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=0.0,
        cancel_replace_timestamp=_ts("2026-03-10T00:00:01Z"),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.500000Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:00.500000Z")
    assert fill.maker_wait_seconds is None


def test_maker_queue_inference_requires_placement_timestamp_anchor() -> None:
    with pytest.raises(ValueError, match="queue_ahead_quantity inference requires placement_timestamp"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            timeout_seconds=10.0,
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:05Z"),
                    symbol="BTCUSDT",
                    bid=99.5,
                    ask=99.7,
                    bid_size=4.0,
                ),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:06Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=5.0,
                    side="sell",
                ),
            ),
        )


def test_maker_queue_explicit_queue_ahead_does_not_require_placement_timestamp() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=4.0,
        timeout_seconds=10.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:05Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=99.0,
            ),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:06Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=5.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.queue_ahead_initial == pytest.approx(4.0)
    assert fill.filled_quantity == pytest.approx(1.0)


def test_maker_queue_infers_queue_ahead_from_book_at_effective_placement() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=100,
        timeout_seconds=10.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.050000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=2.0,
            ),
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.100000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=3.0,
            ),
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.200000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=99.0,
            ),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=4.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.queue_ahead_initial == pytest.approx(3.0)
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.maker_wait_seconds == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("side", "book_kwargs", "trade_side"),
    [
        ("buy", {"bid_size": None, "ask_size": 5.0}, "sell"),
        ("sell", {"bid_size": 5.0, "ask_size": None}, "buy"),
    ],
)
def test_maker_queue_inference_requires_visible_size_for_relevant_side(
    side: str,
    book_kwargs: dict[str, float | None],
    trade_side: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="queue_ahead_quantity inference requires visible order book size",
    ):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side=side,
            limit_price=99.5 if side == "buy" else 100.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            timeout_seconds=10.0,
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:00Z"),
                    symbol="BTCUSDT",
                    bid=99.5,
                    ask=100.5,
                    **book_kwargs,
                ),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5 if side == "buy" else 100.5,
                    quantity=1.0,
                    side=trade_side,
                ),
            ),
        )


def test_maker_queue_explicit_queue_ahead_allows_missing_visible_size() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        queue_ahead_quantity=0.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=100.5,
            ),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=1.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.queue_ahead_initial == pytest.approx(0.0)
    assert fill.filled_quantity == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("side", "book_kwargs", "trade_side"),
    [
        ("buy", {"bid_size": 0.0}, "sell"),
        ("sell", {"ask_size": 0.0}, "buy"),
    ],
)
def test_maker_queue_inference_accepts_zero_visible_size(
    side: str,
    book_kwargs: dict[str, float],
    trade_side: str,
) -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side=side,
        limit_price=99.5 if side == "buy" else 100.5,
        quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        timeout_seconds=10.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=100.5,
                **book_kwargs,
            ),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5 if side == "buy" else 100.5,
                quantity=1.0,
                side=trade_side,
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.queue_ahead_initial == pytest.approx(0.0)
    assert fill.filled_quantity == pytest.approx(1.0)


def test_maker_queue_inference_rejects_future_only_books_after_effective_placement() -> None:
    with pytest.raises(
        ValueError,
        match="queue_ahead_quantity inference requires order book at or before placement_timestamp",
    ):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            latency_ms=100,
            timeout_seconds=10.0,
            order_books=(
                OrderBookSnapshot(
                    timestamp=_ts("2026-03-10T00:00:00.101000Z"),
                    symbol="BTCUSDT",
                    bid=99.5,
                    ask=99.7,
                    bid_size=99.0,
                ),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=100.0,
                    side="sell",
                ),
            ),
        )


def test_maker_queue_inference_rejects_missing_order_books_at_placement() -> None:
    with pytest.raises(
        ValueError,
        match="queue_ahead_quantity inference requires order book at or before placement_timestamp",
    ):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            timeout_seconds=10.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


def test_maker_queue_inference_uses_latest_book_before_effective_placement() -> None:
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="buy",
        limit_price=99.5,
        quantity=1.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        latency_ms=100,
        timeout_seconds=10.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.050000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=2.0,
            ),
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.099000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=3.0,
            ),
            OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:00.101000Z"),
                symbol="BTCUSDT",
                bid=99.5,
                ask=99.7,
                bid_size=99.0,
            ),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.5,
                quantity=4.0,
                side="sell",
                fill_id="maker-print-001",
            ),
        ),
    )

    assert fill.maker_status == "filled"
    assert fill.queue_ahead_initial == pytest.approx(3.0)
    assert fill.filled_quantity == pytest.approx(1.0)


def test_maker_limit_rejects_naive_placement_timestamp_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be timezone-aware"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=datetime(2026, 3, 10, 0, 0, 0),
            timeout_seconds=10.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


@pytest.mark.parametrize("placement_timestamp", ["2026-03-10T00:00:00Z", 1, True])
def test_maker_limit_rejects_non_datetime_placement_timestamp_before_using_evidence(
    placement_timestamp: object,
) -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be a timezone-aware datetime"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=placement_timestamp,
            timeout_seconds=10.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


def test_maker_limit_rejects_naive_cancel_replace_timestamp_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="cancel_replace_timestamp must be timezone-aware"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            cancel_replace_timestamp=datetime(2026, 3, 10, 0, 0, 2),
            timeout_seconds=10.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


@pytest.mark.parametrize("cancel_replace_timestamp", ["2026-03-10T00:00:02Z", 1, True])
def test_maker_limit_rejects_non_datetime_cancel_replace_timestamp_before_using_evidence(
    cancel_replace_timestamp: object,
) -> None:
    with pytest.raises(ValueError, match="cancel_replace_timestamp must be a timezone-aware datetime"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            cancel_replace_timestamp=cancel_replace_timestamp,
            timeout_seconds=10.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=99.5,
                    quantity=1.0,
                    side="sell",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("latency_ms", "cancel_replace_timestamp"),
    [
        (0, _ts("2026-03-09T23:59:59.999000Z")),
        (50, _ts("2026-03-10T00:00:00.049000Z")),
    ],
)
def test_maker_limit_rejects_cancel_replace_timestamp_before_effective_placement(
    latency_ms: int,
    cancel_replace_timestamp: datetime,
) -> None:
    with pytest.raises(ValueError, match="cancel_replace_timestamp cannot be before placement_timestamp"):
        simulate_maker_limit_fill(
            symbol="BTCUSDT",
            side="buy",
            limit_price=99.5,
            quantity=1.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            latency_ms=latency_ms,
            cancel_replace_timestamp=cancel_replace_timestamp,
            timeout_seconds=10.0,
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
                fill_id="maker-print-001",
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
    assert fill.first_fill_timestamp is None
    assert fill.last_fill_timestamp is None


def test_maker_sell_limit_fills_when_bid_crosses_limit_with_orderbook_evidence() -> None:
    crossing_book_timestamp = _ts("2026-03-10T00:00:01Z")
    fill = simulate_maker_limit_fill(
        symbol="BTCUSDT",
        side="sell",
        limit_price=100.5,
        quantity=1.0,
        order_books=(
            OrderBookSnapshot(timestamp=crossing_book_timestamp, symbol="BTCUSDT", bid=100.6, ask=100.8),
        ),
        trades=(),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.5)
    assert fill.fill_model == "maker_orderbook_trade_evidence"
    assert fill.execution_price_source == "book_cross"
    assert fill.fill_quality == "evidence_backed"
    assert fill.evidence_timestamp == crossing_book_timestamp
    assert fill.first_fill_timestamp == crossing_book_timestamp
    assert fill.last_fill_timestamp == crossing_book_timestamp


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


@pytest.mark.parametrize(
    ("side", "expected_price", "expected_source"),
    [
        ("buy", 100.1, "best_ask"),
        ("sell", 99.9, "best_bid"),
    ],
)
def test_taker_top_of_book_fallback_does_not_claim_consumed_depth_without_side_ladder(
    side: str,
    expected_price: float,
    expected_source: str,
) -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=1.0,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(expected_price)
    assert fill.fill_model == "taker_orderbook"
    assert fill.execution_price_source == expected_source
    assert fill.depth_levels_consumed is None


@pytest.mark.parametrize(
    ("side", "top_size_field", "top_size", "expected_price", "expected_source"),
    [
        ("buy", "ask_size", 0.4, 100.1, "best_ask"),
        ("sell", "bid_size", 0.6, 99.9, "best_bid"),
    ],
)
def test_taker_top_of_book_fallback_is_partial_when_visible_top_size_is_smaller_than_request(
    side: str,
    top_size_field: str,
    top_size: float,
    expected_price: float,
    expected_source: str,
) -> None:
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    book_kwargs = {top_size_field: top_size}

    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=1.0,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=book_timestamp,
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.1,
                **book_kwargs,
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(expected_price)
    assert fill.fill_model == "taker_orderbook"
    assert fill.execution_price_source == expected_source
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_quantity == pytest.approx(1.0)
    assert fill.filled_quantity == pytest.approx(top_size)
    assert fill.unfilled_quantity == pytest.approx(1.0 - top_size)
    assert fill.filled_notional == pytest.approx(top_size * expected_price)
    assert fill.depth_levels_consumed is None
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp


@pytest.mark.parametrize(
    ("side", "top_size_field"),
    [
        ("buy", "ask_size"),
        ("sell", "bid_size"),
    ],
)
def test_taker_top_of_book_fallback_is_no_fill_when_visible_top_size_is_zero(
    side: str,
    top_size_field: str,
) -> None:
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    requested_quantity = 1.0
    book_kwargs = {top_size_field: 0.0}

    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=requested_quantity,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=book_timestamp,
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.1,
                **book_kwargs,
            ),
        ),
    )

    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.fill_model == "taker_orderbook"
    assert fill.execution_price_source == "no_crossing_evidence"
    assert fill.fill_quality == "no_fill"
    assert fill.outcome == "missed_alpha"
    assert fill.requested_quantity == pytest.approx(requested_quantity)
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.filled_notional == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(requested_quantity)
    assert fill.depth_levels_consumed is None
    assert fill.evidence_timestamp == book_timestamp


@pytest.mark.parametrize(
    ("side", "top_size_field", "top_size", "expected_price", "expected_source"),
    [
        ("buy", "ask_size", 1.0, 100.1, "best_ask"),
        ("buy", "ask_size", 1.5, 100.1, "best_ask"),
        ("sell", "bid_size", 1.0, 99.9, "best_bid"),
        ("sell", "bid_size", 1.5, 99.9, "best_bid"),
    ],
)
def test_taker_top_of_book_fallback_is_full_when_visible_top_size_covers_request(
    side: str,
    top_size_field: str,
    top_size: float,
    expected_price: float,
    expected_source: str,
) -> None:
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    book_kwargs = {top_size_field: top_size}

    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=1.0,
        reference_price=100.0,
        order_books=(
            OrderBookSnapshot(
                timestamp=book_timestamp,
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.1,
                **book_kwargs,
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(expected_price)
    assert fill.fill_model == "taker_orderbook"
    assert fill.execution_price_source == expected_source
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_quantity == pytest.approx(1.0)
    assert fill.filled_quantity == pytest.approx(1.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.filled_notional == pytest.approx(expected_price)
    assert fill.depth_levels_consumed is None
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp


def test_taker_fill_ignores_evidence_before_placement_timestamp() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        placement_timestamp=_ts("2026-03-10T00:00:02Z"),
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.1,
                quantity=1.0,
                side="buy",
            ),
        ),
    )

    assert fill.fill_model == "taker_ohlcv_approx"
    assert fill.fill_quality == "approximate"
    assert fill.evidence_timestamp is None


def test_taker_fill_rejects_naive_placement_timestamp_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be timezone-aware"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=datetime(2026, 3, 10, 0, 0, 1),
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
        )


@pytest.mark.parametrize("placement_timestamp", ["2026-03-10T00:00:01Z", 1, True])
def test_taker_fill_rejects_non_datetime_placement_timestamp_before_using_evidence(
    placement_timestamp: object,
) -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be a timezone-aware datetime"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=placement_timestamp,
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
        )


def test_taker_fill_rejects_unanchored_max_evidence_lag_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="max_evidence_lag requires placement_timestamp"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            max_evidence_lag=timedelta(seconds=1),
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=100.1,
                    quantity=1.0,
                    side="buy",
                ),
            ),
        )


def test_taker_fill_rejects_negative_max_evidence_lag_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="max_evidence_lag must be non-negative"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=_ts("2026-03-10T00:00:01Z"),
            max_evidence_lag=timedelta(microseconds=-1),
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=100.1,
                    quantity=1.0,
                    side="buy",
                ),
            ),
        )


@pytest.mark.parametrize("max_evidence_lag", [True, 1, 1.0, "PT1S"])
def test_taker_fill_rejects_non_timedelta_max_evidence_lag_before_using_evidence(
    max_evidence_lag: object,
) -> None:
    with pytest.raises(ValueError, match="max_evidence_lag must be a non-negative timedelta"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=_ts("2026-03-10T00:00:01Z"),
            max_evidence_lag=max_evidence_lag,
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
        )


@pytest.mark.parametrize(
    "evidence_kind",
    [
        "order_book",
        "trade_print",
    ],
)
def test_taker_fill_ignores_evidence_after_placement_window(evidence_kind: str) -> None:
    placement_timestamp = _ts("2026-03-10T00:00:00Z")
    order_books = ()
    trades = ()
    if evidence_kind == "order_book":
        order_books = (
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01.001Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        )
    else:
        trades = (
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01.001Z"),
                symbol="BTCUSDT",
                price=100.1,
                quantity=1.0,
                side="buy",
            ),
        )

    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        placement_timestamp=placement_timestamp,
        max_evidence_lag=timedelta(seconds=1),
        order_books=order_books,
        trades=trades,
    )

    assert fill.fill_model == "taker_ohlcv_approx"
    assert fill.fill_quality == "approximate"
    assert fill.evidence_timestamp is None


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
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=3.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=book_timestamp,
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
    assert fill.fill_price == pytest.approx(fill.filled_notional / fill.filled_quantity)
    assert fill.unfilled_quantity == pytest.approx(fill.requested_quantity - fill.filled_quantity)
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp
    assert fill.execution_impact_bps == pytest.approx(((302.0 / 3.0) - 100.0) / 100.0 * 10_000.0)
    assert fill.slippage_bps == pytest.approx(((302.0 / 3.0) - 100.0) / 100.0 * 10_000.0)


def test_taker_depth_rejects_order_book_symbol_mismatch_before_using_depth() -> None:
    with pytest.raises(ValueError, match="order_book.symbol ETHUSDT does not match requested symbol BTCUSDT"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="ETHUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


def test_taker_depth_rejects_order_book_symbol_mismatch_before_lag_validation() -> None:
    with pytest.raises(ValueError, match="order_book.symbol ETHUSDT does not match requested symbol BTCUSDT"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            max_evidence_lag=timedelta(seconds=1),
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="ETHUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


def test_taker_depth_sell_consumes_multiple_bid_levels_with_weighted_average() -> None:
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=4.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=book_timestamp,
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.2,
            bid_levels=(DepthLevel(price=100.0, quantity=1.5), DepthLevel(price=99.5, quantity=2.5)),
        ),
    )

    expected_fill_price = (100.0 * 1.5 + 99.5 * 2.5) / 4.0
    assert fill.filled is True
    assert fill.fill_price == pytest.approx(expected_fill_price)
    assert fill.fill_model == "taker_orderbook_depth"
    assert fill.execution_price_source == "bid_depth"
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_quantity == pytest.approx(4.0)
    assert fill.filled_quantity == pytest.approx(4.0)
    assert fill.filled_notional == pytest.approx(100.0 * 1.5 + 99.5 * 2.5)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.depth_levels_consumed == 2
    assert fill.fill_price == pytest.approx(fill.filled_notional / fill.filled_quantity)
    assert fill.unfilled_quantity == pytest.approx(fill.requested_quantity - fill.filled_quantity)
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp
    assert fill.execution_impact_bps == pytest.approx((100.0 - fill.fill_price) / 100.0 * 10_000.0)
    assert fill.slippage_bps == pytest.approx((100.0 - fill.fill_price) / 100.0 * 10_000.0)


@pytest.mark.parametrize(
    ("side", "book_kwargs", "match"),
    [
        (
            "buy",
            {
                "bid": 99.9,
                "ask": 100.0,
                "ask_levels": (DepthLevel(price=101.0, quantity=1.0), DepthLevel(price=100.0, quantity=1.0)),
            },
            "ask depth levels must be strictly ascending by price for BTCUSDT",
        ),
        (
            "sell",
            {
                "bid": 99.9,
                "ask": 100.0,
                "bid_levels": (DepthLevel(price=99.9, quantity=1.0), DepthLevel(price=100.0, quantity=1.0)),
            },
            "bid depth levels must be strictly descending by price for BTCUSDT",
        ),
    ],
)
def test_taker_depth_rejects_non_canonical_depth_before_sorting(
    side: str,
    book_kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side=side,
            quantity=1.0,
            reference_price=100.0,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                **book_kwargs,
            ),
        )


def test_taker_depth_buy_can_consume_by_requested_notional() -> None:
    requested_notional = 251.0

    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=requested_notional,
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
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_notional == pytest.approx(requested_notional)
    assert fill.filled_notional == pytest.approx(requested_notional)
    assert fill.filled_quantity == pytest.approx(1.0 + 151.0 / 101.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.unfilled_notional == pytest.approx(0.0)
    assert fill.filled_notional + fill.unfilled_notional == pytest.approx(requested_notional)
    assert fill.depth_levels_consumed == 2


def test_taker_depth_requested_notional_shortfall_uses_total_executable_side_depth() -> None:
    requested_notional = 305.0

    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=requested_notional,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(
                DepthLevel(price=100.0, quantity=1.0),
                DepthLevel(price=101.0, quantity=2.0),
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_notional == pytest.approx(requested_notional)
    assert fill.filled_notional == pytest.approx(302.0)
    assert fill.filled_notional < requested_notional
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.unfilled_notional == pytest.approx(3.0)
    assert fill.filled_notional + fill.unfilled_notional == pytest.approx(requested_notional)
    assert fill.unfilled_quantity == pytest.approx(3.0 / 101.0)
    assert fill.unfilled_quantity > 0.0
    assert fill.depth_levels_consumed == 2


def test_taker_depth_requested_notional_exact_depth_satisfaction_is_evidence_backed() -> None:
    requested_notional = 302.0

    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=requested_notional,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(
                DepthLevel(price=100.0, quantity=1.0),
                DepthLevel(price=101.0, quantity=2.0),
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_notional == pytest.approx(requested_notional)
    assert fill.filled_notional == pytest.approx(requested_notional)
    assert fill.filled_notional <= requested_notional + 1e-12
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.unfilled_notional == pytest.approx(0.0)
    assert fill.filled_notional + fill.unfilled_notional == pytest.approx(requested_notional)
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
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=5.0,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=book_timestamp,
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
    assert fill.filled_notional == pytest.approx(302.0)
    assert fill.unfilled_quantity == pytest.approx(2.0)
    assert fill.depth_levels_consumed == 2
    assert fill.fill_price == pytest.approx(fill.filled_notional / fill.filled_quantity)
    assert fill.unfilled_quantity == pytest.approx(fill.requested_quantity - fill.filled_quantity)
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp


@pytest.mark.parametrize(
    ("side", "levels", "reference_price", "expected_source", "expected_fill_price", "expected_cost_bps"),
    [
        (
            "buy",
            (DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=101.0, quantity=2.0)),
            100.0,
            "ask_depth",
            302.0 / 3.0,
            ((302.0 / 3.0) - 100.0) / 100.0 * 10_000.0,
        ),
        (
            "sell",
            (DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=99.0, quantity=2.0)),
            100.0,
            "bid_depth",
            298.0 / 3.0,
            (100.0 - (298.0 / 3.0)) / 100.0 * 10_000.0,
        ),
    ],
)
def test_taker_depth_partial_fill_uses_side_correct_adverse_cost_sign(
    side: str,
    levels: tuple[DepthLevel, ...],
    reference_price: float,
    expected_source: str,
    expected_fill_price: float,
    expected_cost_bps: float,
) -> None:
    book_timestamp = _ts("2026-03-10T00:00:01Z")
    book_kwargs = {
        "timestamp": book_timestamp,
        "symbol": "BTCUSDT",
        "bid": 100.0,
        "ask": levels[0].price if side == "buy" else 100.2,
        "bid_levels" if side == "sell" else "ask_levels": levels,
    }

    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=5.0,
        reference_price=reference_price,
        order_book=OrderBookSnapshot(**book_kwargs),
    )

    assert fill.filled is True
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.execution_price_source == expected_source
    assert fill.fill_price == pytest.approx(expected_fill_price)
    assert fill.fill_price == pytest.approx(fill.filled_notional / fill.filled_quantity)
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.unfilled_quantity == pytest.approx(2.0)
    assert fill.depth_levels_consumed == 2
    assert fill.execution_impact_bps == pytest.approx(expected_cost_bps)
    assert fill.slippage_bps == pytest.approx(expected_cost_bps)
    assert fill.execution_impact_bps > 0.0
    assert fill.slippage_bps > 0.0


def test_taker_depth_requested_notional_partial_fill_preserves_accounting_identity() -> None:
    requested_notional = 350.0
    book_timestamp = _ts("2026-03-10T00:00:01Z")

    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        requested_notional=requested_notional,
        reference_price=100.0,
        order_book=OrderBookSnapshot(
            timestamp=book_timestamp,
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=100.0, quantity=1.0), DepthLevel(price=101.0, quantity=2.0)),
        ),
    )

    assert fill.filled is True
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_notional == pytest.approx(requested_notional)
    assert fill.filled_quantity == pytest.approx(3.0)
    assert fill.filled_notional == pytest.approx(302.0)
    assert fill.filled_notional <= requested_notional + 1e-12
    assert fill.unfilled_notional == pytest.approx(48.0)
    assert fill.filled_notional + fill.unfilled_notional == pytest.approx(requested_notional)
    assert fill.unfilled_quantity == pytest.approx(48.0 / 101.0)
    assert fill.unfilled_quantity > 0.0
    assert fill.fill_price == pytest.approx(fill.filled_notional / fill.filled_quantity)
    assert fill.depth_levels_consumed == 2
    assert fill.evidence_timestamp == book_timestamp
    assert fill.first_fill_timestamp == book_timestamp
    assert fill.last_fill_timestamp == book_timestamp


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
    assert fill.filled_notional == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.depth_levels_consumed == 0
    assert fill.first_fill_timestamp is None
    assert fill.last_fill_timestamp is None


def test_taker_depth_returns_no_fill_after_placement_window() -> None:
    fill = simulate_taker_depth_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        reference_price=100.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        max_evidence_lag=timedelta(seconds=1),
        order_book=OrderBookSnapshot(
            timestamp=_ts("2026-03-10T00:00:01.001Z"),
            symbol="BTCUSDT",
            bid=99.9,
            ask=100.0,
            ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
        ),
    )

    assert fill.filled is False
    assert fill.fill_price is None
    assert fill.fill_model == "taker_orderbook_depth"
    assert fill.execution_price_source == "no_crossing_evidence"
    assert fill.fill_quality == "no_fill"
    assert fill.outcome == "missed_alpha"
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01.001Z")
    assert fill.filled_quantity == pytest.approx(0.0)
    assert fill.filled_notional == pytest.approx(0.0)
    assert fill.unfilled_quantity == pytest.approx(1.0)
    assert fill.depth_levels_consumed == 0
    assert fill.first_fill_timestamp is None
    assert fill.last_fill_timestamp is None


def test_taker_depth_rejects_naive_placement_timestamp_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be timezone-aware"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=datetime(2026, 3, 10, 0, 0, 1),
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


@pytest.mark.parametrize("placement_timestamp", ["2026-03-10T00:00:01Z", 1, True])
def test_taker_depth_rejects_non_datetime_placement_timestamp_before_using_evidence(
    placement_timestamp: object,
) -> None:
    with pytest.raises(ValueError, match="placement_timestamp must be a timezone-aware datetime"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=placement_timestamp,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


def test_taker_depth_rejects_unanchored_max_evidence_lag_before_using_evidence() -> None:
    with pytest.raises(ValueError, match="max_evidence_lag requires placement_timestamp"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            max_evidence_lag=timedelta(seconds=1),
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


@pytest.mark.parametrize("max_evidence_lag", [timedelta(microseconds=-1), True, 1, 1.0, "PT1S"])
def test_taker_depth_rejects_invalid_max_evidence_lag_before_using_evidence(max_evidence_lag: object) -> None:
    with pytest.raises(ValueError, match="max_evidence_lag must be"):
        simulate_taker_depth_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            reference_price=100.0,
            placement_timestamp=_ts("2026-03-10T00:00:00Z"),
            max_evidence_lag=max_evidence_lag,
            order_book=OrderBookSnapshot(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                bid=99.9,
                ask=100.0,
                ask_levels=(DepthLevel(price=100.0, quantity=1.0),),
            ),
        )


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
            ask=99.99,
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
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(2.0)
    assert fill.filled_notional == pytest.approx(2.0 * 100.2)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


@pytest.mark.parametrize(
    ("side", "trade_side"),
    [
        ("buy", "sell"),
        ("buy", None),
        ("sell", "buy"),
        ("sell", None),
    ],
)
def test_taker_trade_print_fallback_requires_directional_aggressor_evidence(
    side: str,
    trade_side: str | None,
) -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side=side,
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=10.0,
                side=trade_side,
                fill_id="print-001",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_price == pytest.approx(100.0)
    assert fill.fill_model == "taker_ohlcv_approx"
    assert fill.execution_price_source == "ohlcv_reference"
    assert fill.fill_quality == "approximate"
    assert fill.evidence_timestamp is None
    assert fill.first_fill_timestamp is None
    assert fill.last_fill_timestamp is None


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


def test_taker_trade_print_fallback_rejects_out_of_order_same_symbol_side_prints() -> None:
    with pytest.raises(ValueError, match="trade-print timestamps must be strictly increasing for BTCUSDT buy"):
        _conservative_trade_print_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="BTCUSDT",
                    price=100.2,
                    quantity=1.0,
                    side="buy",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=100.6,
                    quantity=1.0,
                    side="buy",
                ),
            ),
        )


def test_taker_trade_print_fallback_ordering_ignores_other_symbols_and_opposite_side() -> None:
    fill = _conservative_trade_print_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=1.0,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="ETHUSDT",
                price=200.0,
                quantity=10.0,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.6,
                quantity=10.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=100.4,
                quantity=1.0,
                side="buy",
            ),
        ),
    )

    assert fill is not None
    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_price == pytest.approx(100.4)
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:02Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:03Z")


def test_taker_trade_print_fill_selects_earliest_eligible_directional_print_identity() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.500000Z"),
                symbol="ETHUSDT",
                price=200.0,
                quantity=10.0,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.8,
                quantity=1.0,
                side="buy",
                fill_id="print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.2,
                quantity=1.0,
                side="buy",
                fill_id="print-002",
            ),
        ),
    )

    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.fill_price == pytest.approx(100.8)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:02Z")


def test_taker_trade_print_fill_ignores_earlier_ineligible_prints_before_selected_identity() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=1.0,
        reference_price=100.0,
        placement_timestamp=_ts("2026-03-10T00:00:00Z"),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.250000Z"),
                symbol="ETHUSDT",
                price=100.9,
                quantity=10.0,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.500000Z"),
                symbol="BTCUSDT",
                price=100.7,
                quantity=10.0,
                side="buy",
                fill_id="print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.750000Z"),
                symbol="BTCUSDT",
                price=100.5,
                quantity=10.0,
                side=None,
                fill_id="print-002",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.8,
                quantity=1.0,
                side="sell",
                fill_id="print-003",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=99.2,
                quantity=1.0,
                side="sell",
                fill_id="print-004",
            ),
        ),
    )

    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.fill_price == pytest.approx(99.8)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:01Z")


def test_taker_trade_print_fill_selects_earliest_in_window_directional_print_identity() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        placement_timestamp=_ts("2026-03-10T00:00:01Z"),
        max_evidence_lag=timedelta(seconds=1),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:00.999999Z"),
                symbol="BTCUSDT",
                price=101.5,
                quantity=10.0,
                side="buy",
                fill_id="print-001",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01.250000Z"),
                symbol="BTCUSDT",
                price=100.9,
                quantity=1.0,
                side="buy",
                fill_id="print-002",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01.750000Z"),
                symbol="BTCUSDT",
                price=100.4,
                quantity=1.0,
                side="buy",
                fill_id="print-003",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02.000001Z"),
                symbol="BTCUSDT",
                price=99.9,
                quantity=10.0,
                side="buy",
                fill_id="print-004",
            ),
        ),
    )

    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.fill_price == pytest.approx(100.9)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01.250000Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01.250000Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:01.750000Z")


def test_taker_trade_print_fill_does_not_use_unsigned_print_to_complete_side_known_fill() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=100.4,
                quantity=0.75,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.1,
                quantity=10.0,
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_price == pytest.approx(100.4)
    assert fill.fill_quality == "partial_evidence_backed"
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(0.75)
    assert fill.filled_notional == pytest.approx(75.3)
    assert fill.unfilled_quantity == pytest.approx(1.25)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:01Z")


def test_taker_trade_print_fill_rejects_duplicate_trade_print_identity_before_aggregation() -> None:
    with pytest.raises(ValueError, match="duplicate trade.fill_id: print-A"):
        simulate_taker_fill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            reference_price=100.0,
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01Z"),
                    symbol="BTCUSDT",
                    price=100.2,
                    quantity=1.25,
                    side="buy",
                    fill_id="print-A",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:02Z"),
                    symbol="BTCUSDT",
                    price=100.2,
                    quantity=1.25,
                    side="buy",
                    fill_id="print-A",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:03Z"),
                    symbol="BTCUSDT",
                    price=100.6,
                    quantity=1.0,
                    side="buy",
                    fill_id="print-B",
                ),
            ),
        )


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


def test_taker_sell_trade_print_fill_uses_worst_selected_price_with_clipped_final_print() -> None:
    fill = simulate_taker_fill(
        symbol="BTCUSDT",
        side="sell",
        quantity=2.0,
        reference_price=100.0,
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01Z"),
                symbol="BTCUSDT",
                price=99.8,
                quantity=1.25,
                side="sell",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:02Z"),
                symbol="BTCUSDT",
                price=100.4,
                quantity=10.0,
                side="buy",
            ),
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:03Z"),
                symbol="BTCUSDT",
                price=99.2,
                quantity=1.0,
                side="sell",
            ),
        ),
    )

    assert fill.filled is True
    assert fill.fill_model == "taker_trade_print"
    assert fill.fill_price == pytest.approx(99.2)
    assert fill.execution_price_source == "trade_print"
    assert fill.fill_quality == "evidence_backed"
    assert fill.requested_quantity == pytest.approx(2.0)
    assert fill.filled_quantity == pytest.approx(2.0)
    assert fill.filled_notional == pytest.approx(99.8 * 1.25 + 99.2 * 0.75)
    assert fill.unfilled_quantity == pytest.approx(0.0)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:03Z")
    assert fill.first_fill_timestamp == _ts("2026-03-10T00:00:01Z")
    assert fill.last_fill_timestamp == _ts("2026-03-10T00:00:03Z")


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


def test_taker_evidence_timestamp_skew_rejects_stale_member_despite_near_pair() -> None:
    with pytest.raises(ValueError, match="taker evidence timestamp skew exceeds tolerance for BTCUSDT"):
        _validate_taker_evidence_timestamp_skew(
            symbol="BTCUSDT",
            order_books=(
                OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            ),
            trades=(
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:01.500000Z"),
                    symbol="BTCUSDT",
                    price=100.0,
                    quantity=1.0,
                    side="buy",
                ),
                TradePrint(
                    timestamp=_ts("2026-03-10T00:00:03Z"),
                    symbol="BTCUSDT",
                    price=100.0,
                    quantity=1.0,
                    side="buy",
                ),
            ),
        )


def test_taker_evidence_timestamp_skew_accepts_compact_same_symbol_evidence_set() -> None:
    _validate_taker_evidence_timestamp_skew(
        symbol="BTCUSDT",
        order_books=(
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
            OrderBookSnapshot(timestamp=_ts("2026-03-10T00:00:01.400000Z"), symbol="BTCUSDT", bid=99.9, ask=100.1),
        ),
        trades=(
            TradePrint(
                timestamp=_ts("2026-03-10T00:00:01.900000Z"),
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


@pytest.mark.parametrize("maker_status", ["no_fill", "expired", "cancelled_replaced"])
def test_execution_fill_rejects_full_maker_fill_with_non_filled_status(maker_status: str) -> None:
    with pytest.raises(ValueError, match="maker_status must agree with filled execution state"):
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
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=1.0,
            filled_quantity=1.0,
            filled_notional=100.0,
            unfilled_quantity=0.0,
            maker_status=maker_status,
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


@pytest.mark.parametrize("maker_status", ["filled", "partial"])
def test_execution_fill_rejects_unfilled_maker_no_fill_with_filled_status(maker_status: str) -> None:
    with pytest.raises(ValueError, match="maker_status must agree with filled execution state"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="maker_post_only_queue",
            execution_price_source="no_crossing_evidence",
            fill_quality="no_fill",
            outcome="missed_alpha",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=1.0,
            maker_status=maker_status,
        )


def test_execution_fill_accepts_partial_maker_fill_with_partial_status() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        filled=True,
        fill_price=100.0,
        fill_model="maker_post_only_queue",
        execution_price_source="trade_print",
        fill_quality="partial_evidence_backed",
        outcome="filled",
        evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        requested_quantity=2.0,
        filled_quantity=0.75,
        filled_notional=75.0,
        unfilled_quantity=1.25,
        maker_status="partial",
        first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
    )

    assert fill.maker_status == "partial"
    assert fill.fill_quality == "partial_evidence_backed"


def test_execution_fill_rejects_partial_maker_status_with_full_fill_quality() -> None:
    with pytest.raises(ValueError, match="maker_status must agree with fill_quality"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=2.0,
            filled_quantity=0.75,
            filled_notional=75.0,
            unfilled_quantity=1.25,
            maker_status="partial",
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_filled_maker_status_with_partial_fill_quality() -> None:
    with pytest.raises(ValueError, match="maker_status must agree with fill_quality"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="partial_evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=2.0,
            filled_quantity=2.0,
            filled_notional=200.0,
            unfilled_quantity=0.0,
            maker_status="filled",
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


@pytest.mark.parametrize("maker_status", ["expired", "cancelled_replaced"])
def test_execution_fill_rejects_terminal_maker_no_fill_status_with_non_no_fill_quality(maker_status: str) -> None:
    with pytest.raises(ValueError, match="maker_status must agree with fill_quality"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=False,
            fill_price=None,
            fill_model="maker_post_only_queue",
            execution_price_source="no_crossing_evidence",
            fill_quality="evidence_backed",
            outcome="missed_alpha",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=1.0,
            filled_quantity=0.0,
            filled_notional=0.0,
            unfilled_quantity=1.0,
            maker_status=maker_status,
        )


def test_execution_fill_rejects_partial_maker_status_without_unfilled_quantity() -> None:
    with pytest.raises(ValueError, match="partial maker fills require positive unfilled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="partial_evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=2.0,
            filled_quantity=2.0,
            filled_notional=200.0,
            unfilled_quantity=0.0,
            maker_status="partial",
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_rejects_filled_maker_status_with_unfilled_quantity() -> None:
    with pytest.raises(ValueError, match="filled maker status requires zero unfilled quantity"):
        ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=2.0,
            filled=True,
            fill_price=100.0,
            fill_model="maker_post_only_queue",
            execution_price_source="trade_print",
            fill_quality="partial_evidence_backed",
            outcome="filled",
            evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
            requested_quantity=2.0,
            filled_quantity=0.75,
            filled_notional=75.0,
            unfilled_quantity=1.25,
            maker_status="filled",
            first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
            last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        )


def test_execution_fill_accepts_filled_maker_status_with_zero_unfilled_quantity() -> None:
    fill = ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=2.0,
        filled=True,
        fill_price=100.0,
        fill_model="maker_post_only_queue",
        execution_price_source="trade_print",
        fill_quality="evidence_backed",
        outcome="filled",
        evidence_timestamp=_ts("2026-03-10T00:00:01Z"),
        requested_quantity=2.0,
        filled_quantity=2.0,
        filled_notional=200.0,
        unfilled_quantity=0.0,
        maker_status="filled",
        first_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
        last_fill_timestamp=_ts("2026-03-10T00:00:01Z"),
    )

    assert fill.maker_status == "filled"
    assert fill.unfilled_quantity == pytest.approx(0.0)


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
            filled_notional=filled_quantity * 100.0,
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


@pytest.mark.parametrize("field", ["evidence_timestamp", "first_fill_timestamp", "last_fill_timestamp"])
def test_execution_fill_rejects_naive_evidence_timestamps(field: str) -> None:
    kwargs = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 1.0,
        "filled": True,
        "fill_price": 100.0,
        "fill_model": "maker_post_only_queue",
        "execution_price_source": "trade_print",
        "fill_quality": "evidence_backed",
        "outcome": "filled",
        "evidence_timestamp": _ts("2026-03-10T00:00:01Z"),
        "first_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
        "last_fill_timestamp": _ts("2026-03-10T00:00:01Z"),
    }
    kwargs[field] = datetime(2026, 3, 10, 0, 0, 1)

    with pytest.raises(ValueError, match=rf"{field} must be timezone-aware for BTCUSDT"):
        ExecutionFill(**kwargs)


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
            evidence_timestamp=_ts("2026-03-10T00:00:02Z"),
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
        "requested_quantity": 1.0,
        "filled_quantity": 1.0,
        "filled_notional": 100.0,
        "unfilled_quantity": 0.0,
    }
    fill_kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        ExecutionFill(**fill_kwargs)
