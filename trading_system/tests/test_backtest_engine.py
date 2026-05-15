from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from trading_system.app.backtest import cli as backtest_cli
from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest import engine as backtest_engine
from trading_system.app.backtest import reporting as backtest_reporting
from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.backtest.types import (
    BacktestConfig,
    BacktestCosts,
    CapitalModelConfig,
    DatasetSnapshotRow,
    ExperimentParams,
    InstrumentSnapshotRow,
    PortfolioCandidate,
    PortfolioDecision,
    SampleWindow,
    UniverseFilterConfig,
    WalkForwardConfig,
)
from trading_system.app.types import AllocationDecision, EngineCandidate, RegimeSnapshot
from trading_system.app.universe.builder import UniverseBuildResult


def _ts(value: str) -> Any:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_engine_rejects_coerced_futures_context_fields() -> None:
    with pytest.raises(ValueError, match="invalid futures context numeric field funding_rate"):
        backtest_engine._optional_futures_float("0.001", "funding_rate")

    with pytest.raises(ValueError, match="invalid futures context numeric field funding_rate: True"):
        backtest_engine._optional_futures_float(True, "funding_rate")

    with pytest.raises(ValueError, match="invalid futures context numeric field funding_rate: non-finite value"):
        backtest_engine._optional_futures_float(float("nan"), "funding_rate")

    with pytest.raises(ValueError, match="invalid futures context integer field funding_age_seconds"):
        backtest_engine._optional_futures_int(1.5, "funding_age_seconds")

    with pytest.raises(ValueError, match="invalid futures context integer field funding_age_seconds: True"):
        backtest_engine._optional_futures_int(True, "funding_age_seconds")


def test_engine_rejects_negative_futures_context_age_seconds() -> None:
    with pytest.raises(ValueError, match="invalid futures context integer field funding_age_seconds: -1"):
        backtest_engine._optional_futures_int(-1, "funding_age_seconds")


def test_engine_optional_int_rejects_present_invalid_evidence_values() -> None:
    assert backtest_engine._optional_int(None) is None

    for value in (True, "1", 1.5, float("nan")):
        with pytest.raises(ValueError, match="invalid integer field execution_lag_bars"):
            backtest_engine._optional_int(value, "execution_lag_bars")


@pytest.mark.parametrize("value", [" 1h", ""])
def test_timeframe_metadata_rejects_non_canonical_string(value: str) -> None:
    with pytest.raises(ValueError, match="timeframe metadata must contain only canonical strings"):
        backtest_engine._string_tuple(value)


@pytest.mark.parametrize("value", [["1h", True], ["1h", ""], ["1h", " 4h"]])
def test_timeframe_metadata_rejects_invalid_list_entries(value: list[Any]) -> None:
    with pytest.raises(ValueError, match="timeframe metadata must contain only strings"):
        backtest_engine._string_tuple(value)


@pytest.mark.parametrize("value", [True, {"timeframe": "1h"}])
def test_timeframe_metadata_rejects_invalid_container(value: Any) -> None:
    with pytest.raises(ValueError, match="timeframe metadata must be a string or list of strings"):
        backtest_engine._string_tuple(value)


def test_entry_reference_timeframes_prefers_valid_explicit_metadata() -> None:
    assert (
        backtest_engine._entry_reference_timeframes(
            {"timeframe_meta": {"entry_reference_timeframes": ["4h", "1h"], "trigger_timeframes": ["15m"]}}
        )
        == ("4h", "1h")
    )


def test_entry_reference_timeframes_expands_intraday_triggers() -> None:
    assert (
        backtest_engine._entry_reference_timeframes({"timeframe_meta": {"trigger_timeframes": ["30m"]}})
        == ("15m", "30m", "1h", "4h", "daily")
    )


@pytest.mark.parametrize(
    "candidate_row",
    [
        {"timeframe_meta": {"trigger_timeframes": ["15m"]}},
        {"timeframe_meta": {"entry_reference_timeframes": ["30m"]}},
    ],
)
def test_has_intraday_entry_metadata_uses_timeframe_metadata(candidate_row: dict[str, Any]) -> None:
    assert backtest_engine._has_intraday_entry_metadata(candidate_row, "daily") is True


@pytest.mark.parametrize("candidate_row", [{"execution_policy": True}, {"timeframe_meta": {"execution_policy": True}}])
def test_entry_execution_policy_rejects_invalid_raw_policy(candidate_row: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="execution_policy must be a string"):
        backtest_engine._entry_execution_policy(candidate_row)


@pytest.mark.parametrize("value", [True, "", " BTCUSDT"])
def test_candidate_canonical_string_rejects_invalid_symbol(value: Any) -> None:
    with pytest.raises(ValueError, match="candidate symbol must be a canonical string"):
        backtest_engine._candidate_canonical_string({"symbol": value}, "symbol")


@pytest.mark.parametrize("value", [True, "0.7", float("nan"), float("inf")])
def test_candidate_finite_number_rejects_invalid_score(value: Any) -> None:
    with pytest.raises(ValueError, match="candidate score must be a finite number"):
        backtest_engine._candidate_finite_number({"score": value}, "score")


def test_suppression_payload_rejects_coerced_regime_fields() -> None:
    with pytest.raises(ValueError, match="suppression_rules must be a list"):
        backtest_engine._suppression_payload({"suppression_rules": "rotation"})
    with pytest.raises(ValueError, match="suppression_rules entries must be canonical strings"):
        backtest_engine._suppression_payload({"suppression_rules": ["rotation", True]})
    with pytest.raises(ValueError, match="execution_policy must be a canonical string"):
        backtest_engine._suppression_payload({"suppression_rules": [], "execution_policy": True})


def test_candidate_row_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(ValueError, match="candidate keys must be strings"):
        backtest_engine._candidate_row({True: "BTCUSDT", "symbol": "ETHUSDT"})

    with pytest.raises(TypeError, match="unsupported candidate type"):
        backtest_engine._candidate_row([("symbol", "BTCUSDT")])


@pytest.mark.parametrize(
    "override",
    [
        [("label", "RISK_ON")],
        {True: "RISK_ON", "label": "MIXED"},
    ],
)
def test_regime_dict_rejects_invalid_regime_override_shapes(override: Any) -> None:
    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:00:00+00:00"),
        run_id="run-1",
        market={},
        derivatives=[],
        meta={"regime_override": override},
    )

    with pytest.raises(ValueError, match="regime_override"):
        backtest_engine._regime_dict(row)


def test_engine_funding_rate_rejects_coerced_derivative_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={},
        derivatives=["BTCUSDT"],
    )
    with pytest.raises(ValueError, match="derivative row must be an object"):
        backtest_engine._funding_rate(row, "BTCUSDT")

    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={},
        derivatives=[{"symbol": True, "funding_rate": 0.001}],
    )
    with pytest.raises(ValueError, match="derivative symbol must be a canonical string"):
        backtest_engine._funding_rate(row, "BTCUSDT")

    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={},
        derivatives=[{"symbol": "BTCUSDT", "funding_rate": True}],
    )
    with pytest.raises(ValueError, match="invalid futures context numeric field funding_rate"):
        backtest_engine._funding_rate(row, "BTCUSDT")


def test_execution_evidence_rejects_coerced_order_book_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "order_book": {"timestamp": "2026-03-10T00:00:00Z", "bid": "1", "ask": 2.0}
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="order_book.bid must be a positive number"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_invalid_single_order_book_container() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"execution": {"order_book": [("bid", 1.0), ("ask", 2.0)]}}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match="execution.order_book must be an object when present"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_coerced_trade_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "trades": [
                            {"timestamp": "2026-03-10T00:00:00Z", "price": "1", "quantity": 2.0, "side": "buy"}
                        ]
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="trade.price must be a positive number"):
        backtest_engine._execution_evidence(row, "BTCUSDT")

    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "trades": [
                            {"timestamp": "2026-03-10T00:00:00Z", "price": 1.0, "quantity": 2.0, "side": True}
                        ]
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="trade.side must be buy or sell when present"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_invalid_trade_side_value() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "trades": [
                            {"timestamp": "2026-03-10T00:00:00Z", "price": 1.0, "quantity": 2.0, "side": "hold"}
                        ]
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="trade.side must be buy or sell when present"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_coerced_depth_level_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "order_book": {
                            "timestamp": "2026-03-10T00:00:00Z",
                            "bid": 1.0,
                            "ask": 2.0,
                            "bids": [["1", 2.0]],
                        }
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="depth_level.price must be a positive number"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_invalid_depth_level_row_shape() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "order_book": {
                            "timestamp": "2026-03-10T00:00:00Z",
                            "bid": 1.0,
                            "ask": 2.0,
                            "asks": [[2.0]],
                        }
                    }
                }
            }
        },
        derivatives=[],
    )

    with pytest.raises(ValueError, match="depth_level row must be an object or pair"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_accepts_valid_books_and_trades_for_taker_fill() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "execution": {
                        "order_book": {
                            "timestamp": "2026-03-10T00:00:01Z",
                            "bid": 99.5,
                            "ask": 100.5,
                            "bid_size": 4.0,
                            "ask_size": 5.0,
                            "bids": [{"price": 99.5, "quantity": 4.0}],
                            "asks": [[100.5, 5.0]],
                        },
                        "order_books": [
                            {
                                "timestamp": "2026-03-10T00:00:02Z",
                                "bid": 99.75,
                                "ask": 100.25,
                                "bids": [[99.75, 3.0]],
                                "asks": [{"price": 100.25, "quantity": 3.0}],
                            }
                        ],
                        "trades": [
                            {
                                "timestamp": "2026-03-10T00:00:01.500000Z",
                                "price": 100.4,
                                "quantity": 1.0,
                                "side": "buy",
                            }
                        ],
                    }
                }
            }
        },
        derivatives=[],
    )

    fill = backtest_engine._entry_execution_fill(
        row=row,
        symbol="BTCUSDT",
        order_side="buy",
        entry_price=100.0,
        candidate_row={},
        entry_reference_timeframe="daily",
    )

    assert fill.fill_model == "taker_orderbook_depth"
    assert fill.execution_price_source == "ask_depth"
    assert fill.fill_quality == "evidence_backed"
    assert fill.fill_price == pytest.approx(100.5)
    assert fill.evidence_timestamp == _ts("2026-03-10T00:00:01Z")


def test_execution_evidence_rejects_invalid_evidence_containers() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"execution": {"order_books": {"bad": "container"}}}}},
        derivatives=[],
    )
    with pytest.raises(ValueError, match="execution.order_books must be a list when present"):
        backtest_engine._execution_evidence(row, "BTCUSDT")

    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"execution": {"trades": {"bad": "container"}}}}},
        derivatives=[],
    )
    with pytest.raises(ValueError, match="execution.trades must be a list when present"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_execution_evidence_rejects_invalid_evidence_rows() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"execution": {"order_books": [True]}}}},
        derivatives=[],
    )
    with pytest.raises(ValueError, match="execution.order_books entries must be objects"):
        backtest_engine._execution_evidence(row, "BTCUSDT")

    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"execution": {"trades": [True]}}}},
        derivatives=[],
    )
    with pytest.raises(ValueError, match="execution.trades entries must be objects"):
        backtest_engine._execution_evidence(row, "BTCUSDT")


def test_intraday_path_rejects_coerced_high_low_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"1m": {"high": "2", "low": 1.0}}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match="path.1m.high must be a positive number"):
        backtest_engine._path_high_low(row, "BTCUSDT")


@pytest.mark.parametrize("path_row", [{"high": 2.0}, {"low": 1.0}])
def test_intraday_path_rejects_incomplete_high_low_pairs(path_row: dict[str, float]) -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"1m": path_row}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match=r"path\.1m high/low must both be present"):
        backtest_engine._path_high_low(row, "BTCUSDT")


def test_reference_price_rejects_coerced_close_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"daily": {"close": "123"}}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match="reference_price.daily.close must be a positive number"):
        backtest_engine._reference_price_with_timeframe(row, "BTCUSDT")


def test_futures_context_rejects_coerced_derivative_symbol_fields() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {}}},
        derivatives=[{"symbol": True, "funding_rate": 0.001}],
    )

    with pytest.raises(ValueError, match="derivative.symbol must be a canonical string"):
        backtest_engine._futures_context(row, "BTCUSDT")


def test_futures_context_rejects_present_invalid_symbol_context() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"futures_context": "invalid"}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match="futures_context must be an object when present"):
        backtest_engine._futures_context(row, "BTCUSDT")


def test_futures_context_rejects_non_string_symbol_context_keys() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"futures_context": {True: 0.001}}}},
        derivatives=[],
    )

    with pytest.raises(ValueError, match="futures_context keys must be strings"):
        backtest_engine._futures_context(row, "BTCUSDT")


def test_futures_context_rejects_non_string_derivative_context_keys() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {}}},
        derivatives=[{"symbol": "BTCUSDT", "funding_rate": 0.001, True: 123}],
    )

    with pytest.raises(ValueError, match="derivative futures context keys must be strings"):
        backtest_engine._futures_context(row, "BTCUSDT")


def test_futures_context_combines_symbol_overrides_with_derivative_context() -> None:
    row = DatasetSnapshotRow(
        timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={
            "symbols": {
                "BTCUSDT": {
                    "futures_context": {
                        "funding_rate": 0.0003,
                        "mark_price": 101.0,
                    }
                }
            }
        },
        derivatives=[
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.0001,
                "mark_price": 100.5,
                "open_interest_usdt": 25_000_000.0,
                "open_interest_age_seconds": 4,
            }
        ],
    )

    context = backtest_engine._futures_context(row, "BTCUSDT")

    assert context["funding_rate"] == pytest.approx(0.0003)
    assert context["mark_price"] == pytest.approx(101.0)
    assert context["open_interest_usdt"] == pytest.approx(25_000_000.0)
    assert context["open_interest_age_seconds"] == 4


def test_candidate_setup_type_rejects_coerced_fields() -> None:
    with pytest.raises(ValueError, match="candidate setup_type must be a canonical string"):
        backtest_engine._candidate_setup_type({"setup_type": True})


def test_execution_fill_rejects_coerced_depth_fill_fields() -> None:
    with pytest.raises(ValueError, match="filled_quantity must be a non-negative finite number"):
        backtest_engine.ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price=100.0,
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="partial_evidence_backed",
            outcome="filled",
            filled_quantity="1",
            filled_notional=100.0,
        )


def test_candidate_take_profit_rejects_coerced_prices() -> None:
    with pytest.raises(ValueError, match="entry_price must be a positive number"):
        backtest_engine._candidate_take_profit_price("100", 90.0, "long")

    with pytest.raises(ValueError, match="stop_loss must be a positive number"):
        backtest_engine._candidate_take_profit_price(100.0, "90", "long")


def test_candidate_execution_fill_rejects_coerced_fill_price() -> None:
    with pytest.raises(ValueError, match="fill_price must be a positive finite number"):
        backtest_engine.ExecutionFill(
            symbol="BTCUSDT",
            side="buy",
            quantity=1.0,
            filled=True,
            fill_price="101",
            fill_model="taker_orderbook_depth",
            execution_price_source="ask_depth",
            fill_quality="evidence_backed",
            outcome="filled",
        )


def test_candidate_cost_coverage_rejects_coerced_prices() -> None:
    candidate = PortfolioCandidate(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        side="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit="110",
    )
    instrument = InstrumentSnapshotRow(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        listing_timestamp=_ts("2026-03-01T00:00:00Z"),
        quote_volume_usdt_24h=1_000_000.0,
        liquidity_tier="tier1",
        quantity_step=0.001,
        price_tick=0.1,
        has_complete_funding=True,
    )
    costs = BacktestCosts(fee_bps_by_market={"futures": 1.0}, slippage_bps_by_tier={"tier1": 1.0})

    with pytest.raises(ValueError, match="take_profit must be a positive number"):
        backtest_engine._candidate_cost_coverage_ratio(candidate, instrument=instrument, costs=costs)


def test_candidate_cost_coverage_ok_rejects_coerced_threshold() -> None:
    candidate = PortfolioCandidate(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        side="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=110.0,
    )
    instrument = InstrumentSnapshotRow(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        listing_timestamp=_ts("2026-03-01T00:00:00Z"),
        quote_volume_usdt_24h=1_000_000.0,
        liquidity_tier="tier1",
        quantity_step=0.001,
        price_tick=0.1,
        has_complete_funding=True,
    )
    costs = BacktestCosts(fee_bps_by_market={"futures": 1.0}, slippage_bps_by_tier={"tier1": 1.0})

    with pytest.raises(ValueError, match="minimum_cost_coverage_ratio must be a finite number"):
        backtest_engine._candidate_cost_coverage_ok(
            candidate,
            instrument=instrument,
            costs=costs,
            minimum_cost_coverage_ratio="1.5",
        )


def test_exit_slippage_rejects_coerced_prices() -> None:
    with pytest.raises(ValueError, match="fill_price must be a positive number"):
        backtest_engine._exit_slippage_vs_reference_bps(side="long", fill_price="101", reference_price=100.0)

    with pytest.raises(ValueError, match="reference_price must be a positive number"):
        backtest_engine._exit_slippage_vs_reference_bps(side="short", fill_price=99.0, reference_price="100")


def test_depth_fill_adjustment_rejects_coerced_risk_fields() -> None:
    decision = PortfolioDecision(
        status="accepted",
        reasons=(),
        final_risk_budget="0.02",
        position_notional=1000.0,
        qty=1.0,
    )
    fill = backtest_engine.ExecutionFill(
        symbol="BTCUSDT",
        side="buy",
        quantity=1.0,
        filled=True,
        fill_price=100.0,
        fill_model="taker_orderbook_depth",
        execution_price_source="ask_depth",
        fill_quality="partial_evidence_backed",
        outcome="filled",
        evidence_timestamp=backtest_engine._datetime_or_none("2026-03-10T00:00:01Z"),
        filled_quantity=1.0,
        filled_notional=100.0,
    )
    candidate = PortfolioCandidate(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        side="long",
        entry_price="100",
        stop_loss=90.0,
    )

    with pytest.raises(ValueError, match="entry_price must be a positive number"):
        backtest_engine._decision_with_depth_fill(
            decision=decision,
            fill=fill,
            candidate=candidate,
            equity=10_000.0,
        )


def test_intraday_exit_rejects_coerced_take_profit() -> None:
    with pytest.raises(ValueError, match="take_profit must be a positive number"):
        backtest_engine._simulate_intraday_exit(
            side="long",
            entry_price=100.0,
            fixed_exit_price=101.0,
            stop_loss=90.0,
            take_profit="110",
            path_high=111.0,
            path_low=95.0,
        )


def test_funding_rate_rejects_present_null_derivative_field() -> None:
    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:00:00Z"),
        run_id="run-1",
        market={},
        derivatives=[{"symbol": "BTCUSDT", "funding_rate": None}],
    )

    with pytest.raises(ValueError, match="invalid futures context numeric field funding_rate"):
        backtest_engine._funding_rate(row, "BTCUSDT")


def test_engine_rejects_coerced_portfolio_candidate_fields(fixture_dir: Path) -> None:
    row = load_historical_dataset(fixture_dir / "backtest" / "sample_dataset")[0]
    instrument = InstrumentSnapshotRow(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        listing_timestamp=row.timestamp,
        quote_volume_usdt_24h=1_000_000.0,
        liquidity_tier="high",
        quantity_step=0.001,
        price_tick=0.1,
        has_complete_funding=True,
    )
    base_candidate = {
        "symbol": instrument.symbol,
        "side": "LONG",
        "stop_loss": 1.0,
        "take_profit": 2.0,
        "score": 0.7,
        "engine": "trend",
        "setup_type": "BREAKOUT",
    }

    bad_side = dict(base_candidate)
    bad_side["side"] = True
    with pytest.raises(ValueError, match="candidate side must be LONG or SHORT"):
        backtest_engine._portfolio_candidate(bad_side, instrument=instrument, row=row)

    bad_stop = dict(base_candidate)
    bad_stop["stop_loss"] = True
    with pytest.raises(ValueError, match="candidate stop_loss must be a finite number"):
        backtest_engine._portfolio_candidate(bad_stop, instrument=instrument, row=row)

    bad_take_profit = dict(base_candidate)
    bad_take_profit["take_profit"] = True
    with pytest.raises(ValueError, match="candidate take_profit must be a finite number"):
        backtest_engine._portfolio_candidate(bad_take_profit, instrument=instrument, row=row)


def test_full_market_replay_rejects_coerced_candidate_ledger_fields(
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "full_market_baseline.json")
    rows = load_historical_dataset(fixture_dir / "backtest" / "full_market_baseline_dataset")
    symbol = rows[0].instrument_rows[0].symbol
    candidate = {
        "symbol": symbol,
        "side": "LONG",
        "stop_loss": 1.0,
        "take_profit": 2.0,
        "score": True,
        "engine": "trend",
        "setup_type": "BREAKOUT",
    }

    monkeypatch.setattr(backtest_engine, "_raw_full_market_candidates", lambda *args, **kwargs: [candidate])

    with pytest.raises(ValueError, match="candidate score must be a finite number"):
        backtest_engine._replay_full_market_baseline_rows(config, rows)


def test_full_market_replay_rejects_coerced_candidate_symbol(
    fixture_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "full_market_baseline.json")
    rows = load_historical_dataset(fixture_dir / "backtest" / "full_market_baseline_dataset")
    candidate = {
        "symbol": True,
        "side": "LONG",
        "stop_loss": 1.0,
        "take_profit": 2.0,
        "score": 0.7,
        "engine": "trend",
        "setup_type": "BREAKOUT",
    }

    monkeypatch.setattr(backtest_engine, "_raw_full_market_candidates", lambda *args, **kwargs: [candidate])

    with pytest.raises(ValueError, match="candidate symbol must be a canonical string"):
        backtest_engine._replay_full_market_baseline_rows(config, rows)


def test_full_market_replay_rejects_coerced_initial_equity(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "full_market_baseline.json")
    rows = load_historical_dataset(fixture_dir / "backtest" / "full_market_baseline_dataset")
    assert config.capital is not None
    config = replace(config, capital=replace(config.capital, initial_equity="100000"))

    with pytest.raises(ValueError, match="initial_equity must be a positive number"):
        backtest_engine._replay_full_market_baseline_rows(config, rows)


def test_exit_execution_fill_rejects_coerced_trade_print_price(monkeypatch: pytest.MonkeyPatch) -> None:
    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:01:00Z"),
        run_id="run-1",
        market={},
        derivatives=[],
    )
    open_trade = backtest_engine._OpenTrade(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        side="long",
        status="accepted",
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        entry_price=100.0,
        qty=1.0,
        position_notional=100.0,
        liquidity_tier="high",
        funding_rate=0.0,
    )
    trade = backtest_engine.TradePrint(
        timestamp=row.timestamp,
        symbol="BTCUSDT",
        price="101",
        quantity=1.0,
        side="sell",
    )
    monkeypatch.setattr(backtest_engine, "_execution_evidence", lambda *_args, **_kwargs: ((), (trade,)))

    with pytest.raises(ValueError, match="trade.price must be a positive number"):
        backtest_engine._exit_execution_fill(row, open_trade, 100.0)


@pytest.mark.parametrize(
    ("qty", "position_notional", "message"),
    [
        (0.0, 100.0, "open_trade.qty must be a positive number"),
        (-1.0, 100.0, "open_trade.qty must be a positive number"),
        (1.0, 0.0, "open_trade.position_notional must be a positive number"),
        (1.0, -100.0, "open_trade.position_notional must be a positive number"),
        (float("nan"), 100.0, "open_trade.qty must be a positive number"),
    ],
)
def test_trade_row_rejects_invalid_open_trade_exposure(
    qty: float,
    position_notional: float,
    message: str,
) -> None:
    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:01:00Z"),
        run_id="run-1",
        market={"symbols": {"BTCUSDT": {"close": 101.0}}},
        derivatives=[],
    )
    open_trade = backtest_engine._OpenTrade(
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        side="long",
        status="accepted",
        entry_timestamp=_ts("2026-03-10T00:00:00Z"),
        entry_price=100.0,
        qty=qty,
        position_notional=position_notional,
        liquidity_tier="high",
        funding_rate=0.0,
    )

    with pytest.raises(ValueError, match=message):
        backtest_engine._trade_row(open_trade, exit_row=row, costs=BacktestCosts())


def test_replay_snapshot_records_layer_artifacts(fixture_dir: Path) -> None:
    rows = load_historical_dataset(fixture_dir / "backtest" / "sample_dataset")

    result = replay_snapshot(rows[0])

    assert result["regime"]["label"].startswith("RISK_")
    assert "rotation_suppressed" in result["suppression"]
    assert result["universes"]["rotation_count"] >= 0
    assert set(result["raw_candidates"]) == {"trend", "rotation", "short"}
    assert isinstance(result["validated_candidates"], list)
    assert isinstance(result["allocations"], list)
    assert result["execution_assumptions"]["fee_bps"] == 0.0


def test_replay_snapshot_preserves_missing_account_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:00:00+00:00"),
        run_id="run-1",
        market={},
        derivatives=[],
        account=None,
    )

    result = replay_snapshot(row)

    assert result["validated_candidates"] == []
    assert result["allocations"] == []


@pytest.mark.parametrize(
    "account",
    [
        [("equity", 1000.0)],
        {True: 1000.0, "open_positions": []},
    ],
)
def test_replay_snapshot_rejects_invalid_present_account_shapes(account: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    row = DatasetSnapshotRow(
        timestamp=_ts("2026-03-10T00:00:00+00:00"),
        run_id="run-1",
        market={},
        derivatives=[],
        account=account,
    )

    with pytest.raises(ValueError, match="account"):
        replay_snapshot(row)


@pytest.mark.parametrize(
    "meta",
    [
        [("rank_score", 0.9)],
        {True: 0.9, "rank_score": 0.8},
    ],
)
def test_allocation_rows_rejects_invalid_decision_meta_shapes(meta: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def allocate_with_bad_meta(**_kwargs: Any) -> list[AllocationDecision]:
        return [
            AllocationDecision(
                status="ACCEPTED",
                engine="trend",
                reasons=[],
                meta=meta,
                final_risk_budget=0.01,
                rank=1,
            )
        ]

    monkeypatch.setattr(backtest_engine, "allocate_candidates", allocate_with_bad_meta)

    with pytest.raises(ValueError, match="decision.meta"):
        backtest_engine._allocation_rows(
            {"equity": 1000.0, "open_positions": []},
            [
                {
                    "symbol": "BTCUSDT",
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "score": 0.95,
                }
            ],
            {"label": "MIXED", "confidence": 0.5, "risk_multiplier": 1.0},
            app_config=backtest_engine.DEFAULT_CONFIG,
        )


def test_backtest_cli_runs_fixture_experiment(
    fixture_dir: Path,
    tmp_path: Path,
) -> None:
    config_path = fixture_dir / "backtest" / "minimal_config.json"
    output_dir = tmp_path / "research-output"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    bundle_dir = output_dir / "regime_research__current_policy__no_rotation_suppression"
    summary_path = bundle_dir / "summary.json"
    scorecard_path = bundle_dir / "scorecard.json"
    manifest_path = bundle_dir / "manifest.json"
    assert summary_path.exists()
    assert scorecard_path.exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["experiment_kind"] == "regime_research"
    assert manifest["dataset_root"].endswith("sample_dataset")
    assert manifest["snapshot_count"] == 3

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 3
    assert summary["metadata"]["baseline_name"] == "current_policy"
    assert summary["metadata"]["variant_name"] == "no_rotation_suppression"


def test_backtest_cli_runs_full_market_baseline_smoke_fixture(
    fixture_dir: Path,
    tmp_path: Path,
) -> None:
    config_path = fixture_dir / "backtest" / "full_market_baseline.json"
    output_dir = tmp_path / "smoke-output"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    bundle_dir = output_dir / "full_market_baseline__current_system__auditable_baseline"
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "breakdowns.json").exists()
    assert (bundle_dir / "audit.json").exists()
    assert (bundle_dir / "trades.json").exists()
    assert (bundle_dir / "exit_path_replay.json").exists()
    assert (bundle_dir / "trade_postmortem.md").exists()
    postmortem = (bundle_dir / "trade_postmortem.md").read_text(encoding="utf-8")
    assert "逐单复盘" in postmortem
    assert "exit_reason" in postmortem
    trades_path = bundle_dir / "trades.json"
    assert trades_path.exists()
    trades = json.loads(trades_path.read_text(encoding="utf-8"))["trades"]
    exit_path_payload = json.loads((bundle_dir / "exit_path_replay.json").read_text(encoding="utf-8"))
    assert exit_path_payload["metadata"]["experiment_kind"] == "full_market_baseline"
    assert exit_path_payload["exit_path_replay"]["schema_version"] == "exit_path_replay_audit.v1"
    assert exit_path_payload["exit_path_replay"]["counts"]
    assert len(exit_path_payload["exit_path_replay"]["trades"]) == len(trades)
    assert "simulated_exit_ordering" in exit_path_payload["exit_path_replay"]["trades"][0]
    summary_payload = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary_payload["metadata"]["experiment_params"] == {
        "disabled_engines": [],
        "allowed_short_setup_types": [],
        "quarantined_setup_types": [],
        "quarantined_short_setup_types": [],
    }
    assert trades
    assert {
        "engine",
        "setup_type",
        "score",
        "stop_loss",
        "take_profit",
        "exit_reason",
        "mfe_pct",
        "mae_pct",
        "exit_move_pct",
        "simulated_exit_reason",
        "simulated_exit_price",
        "simulated_exit_move_pct",
        "simulated_gross_pnl",
        "simulated_net_pnl",
        "cost_coverage_ratio",
        "execution_price_source",
        "fill_model",
        "fill_quality",
        "exit_fill_model",
        "exit_price_source",
        "exit_fill_quality",
        "exit_fill_timestamp",
        "exit_slippage_vs_reference_bps",
    }.issubset(trades[0])
    assert trades[0]["fill_model"] == "reference_close"
    assert trades[0]["execution_price_source"] == "ohlcv_close"
    assert trades[0]["fill_quality"] == "approximate"


def test_full_market_baseline_ledger_exposes_entry_futures_context(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = load_backtest_config(_baseline_config_path(tmp_path))
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(config)

    futures_trade = next(row for row in result.trade_ledger if row.symbol == "SOLUSDTPERP")
    assert futures_trade.mark_price == pytest.approx(50.25)
    assert futures_trade.mark_price_timestamp == _ts("2026-03-11T00:00:00Z")
    assert futures_trade.mark_price_age_seconds == 0
    assert futures_trade.funding_rate == pytest.approx(0.0002)
    assert futures_trade.funding_timestamp == _ts("2026-03-11T00:00:00Z")
    assert futures_trade.funding_age_seconds == 0
    assert futures_trade.open_interest_usdt == pytest.approx(80_000_000.0)
    assert futures_trade.open_interest_timestamp == _ts("2026-03-11T00:00:00Z")
    assert futures_trade.open_interest_age_seconds == 0


def test_full_market_baseline_rejects_invalid_futures_context_numeric(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    market_path = config_path.parent / "baseline_dataset" / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["SOLUSDTPERP"]["futures_context"]["mark_price_age_seconds"] = True
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    config = load_backtest_config(config_path)
    _install_replay_candidates(monkeypatch)

    with pytest.raises(ValueError, match="mark_price_age_seconds"):
        backtest_engine.replay_full_market_baseline(config)


def test_full_market_baseline_uses_trade_print_execution_evidence_for_entry_fill(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    dataset_root = tmp_path / "baseline_dataset"
    first_bundle = dataset_root / "2026-03-10T00-00-00Z__row-001"
    market_path = first_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["execution"] = {
        "trades": [
            {
                "timestamp": "2026-03-10T00:00:03Z",
                "price": 101.25,
                "quantity": 2.0,
                "side": "buy",
            }
        ]
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")

    config = load_backtest_config(config_path)
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(config)

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.entry_price == pytest.approx(101.25)
    assert btc_trade.fill_model == "taker_trade_print"
    assert btc_trade.execution_price_source == "trade_print"
    assert btc_trade.fill_quality == "evidence_backed"


def test_full_market_baseline_ignores_trade_print_evidence_before_fixed_horizon_exit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    dataset_root = tmp_path / "baseline_dataset"
    first_bundle = dataset_root / "2026-03-10T00-00-00Z__row-001"
    first_market_path = first_bundle / "market_context.json"
    first_market_payload = json.loads(first_market_path.read_text(encoding="utf-8"))
    first_market_payload["symbols"]["BTCUSDT"]["execution"] = {
        "trades": [
            {
                "timestamp": "2026-03-10T00:00:03Z",
                "price": 101.25,
                "quantity": 2.0,
                "side": "buy",
            }
        ]
    }
    first_market_path.write_text(json.dumps(first_market_payload), encoding="utf-8")

    exit_bundle = dataset_root / "2026-03-11T00-00-00Z__row-002"
    exit_market_path = exit_bundle / "market_context.json"
    exit_market_payload = json.loads(exit_market_path.read_text(encoding="utf-8"))
    exit_market_payload["symbols"]["BTCUSDT"]["execution"] = {
        "trades": [
            {
                "timestamp": "2026-03-10T23:59:58Z",
                "price": 109.5,
                "quantity": 1.0,
                "side": "sell",
            },
            {
                "timestamp": "2026-03-11T00:00:02Z",
                "price": 108.75,
                "quantity": 1.0,
                "side": "sell",
            },
        ]
    }
    exit_market_path.write_text(json.dumps(exit_market_payload), encoding="utf-8")

    config = load_backtest_config(config_path)
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(config)

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_timestamp == _ts("2026-03-11T00:00:00Z")
    assert btc_trade.exit_price == pytest.approx(108.75)
    assert btc_trade.exit_fill_model == "taker_trade_print"
    assert btc_trade.exit_price_source == "trade_print"
    assert btc_trade.exit_fill_quality == "partial_evidence_backed"
    assert btc_trade.exit_fill_timestamp == _ts("2026-03-11T00:00:02Z")
    assert btc_trade.exit_slippage_vs_reference_bps == pytest.approx(((108.75 - 110.0) / 110.0) * 10_000.0)


def test_full_market_baseline_ignores_nearby_pre_exit_trade_print_without_post_exit_print(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    dataset_root = tmp_path / "baseline_dataset"
    exit_bundle = dataset_root / "2026-03-11T00-00-00Z__row-002"
    exit_market_path = exit_bundle / "market_context.json"
    exit_market_payload = json.loads(exit_market_path.read_text(encoding="utf-8"))
    exit_market_payload["symbols"]["BTCUSDT"]["execution"] = {
        "trades": [
            {
                "timestamp": "2026-03-10T23:59:59.700Z",
                "price": 109.25,
                "quantity": 1.0,
                "side": "sell",
            }
        ]
    }
    exit_market_path.write_text(json.dumps(exit_market_payload), encoding="utf-8")

    config = load_backtest_config(config_path)
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(config)

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_timestamp == _ts("2026-03-11T00:00:00Z")
    assert btc_trade.exit_price == pytest.approx(110.0)
    assert btc_trade.exit_fill_model == "reference_close"
    assert btc_trade.exit_price_source == "ohlcv_close"
    assert btc_trade.exit_fill_quality == "approximate"
    assert btc_trade.exit_fill_timestamp is None


def test_backtest_cli_rejects_invalid_config(
    fixture_dir: Path,
    tmp_path: Path,
) -> None:
    invalid_config_path = tmp_path / "invalid_backtest_config.json"
    invalid_config_path.write_text(
        json.dumps({"experiment_kind": "regime_research"}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_system.app.backtest.cli",
            "run",
            "--config",
            str(invalid_config_path),
            "--output-dir",
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing required field" in result.stderr


def _write_market_bundle(
    dataset_root: Path,
    *,
    timestamp: str,
    run_id: str,
    market_symbols: dict[str, dict[str, Any]],
    derivatives_rows: list[dict[str, Any]],
    instrument_rows: list[dict[str, Any]],
    candidate_symbols: list[str] | None = None,
) -> None:
    bundle = dataset_root / f"{timestamp.replace(':', '-')}__{run_id}"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(json.dumps({"timestamp": timestamp, "run_id": run_id}), encoding="utf-8")
    (bundle / "market_context.json").write_text(
        json.dumps({"symbols": market_symbols, "candidate_symbols": candidate_symbols or sorted(market_symbols)}),
        encoding="utf-8",
    )
    (bundle / "derivatives_snapshot.json").write_text(json.dumps({"rows": derivatives_rows}), encoding="utf-8")
    (bundle / "account_snapshot.json").write_text(
        json.dumps(
            {
                "equity": 100000.0,
                "available_balance": 100000.0,
                "futures_wallet_balance": 100000.0,
                "open_positions": [],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "instrument_snapshot.json").write_text(
        json.dumps(
            {
                "as_of": timestamp,
                "schema_version": "imported_instrument_snapshot.v1",
                "rows": instrument_rows,
            }
        ),
        encoding="utf-8",
    )


def _sample_symbol(*, close: float) -> dict[str, Any]:
    return {
        "sector": "majors",
        "liquidity_tier": "top",
        "daily": {
            "close": close,
            "ema_20": close * 0.98,
            "ema_50": close * 0.95,
            "return_pct_7d": 0.05,
            "volume_usdt_24h": 50_000_000.0,
            "atr_pct": 0.03,
        },
        "4h": {
            "close": close,
            "ema_20": close * 0.985,
            "ema_50": close * 0.96,
            "return_pct_3d": 0.03,
        },
        "1h": {
            "close": close,
            "ema_20": close * 0.99,
            "ema_50": close * 0.97,
            "return_pct_24h": 0.01,
        },
    }


def _baseline_config_path(tmp_path: Path) -> Path:
    dataset_root = tmp_path / "baseline_dataset"
    row1_instruments = [
        {
            "symbol": "BTCUSDT",
            "market_type": "spot",
            "base_asset": "BTC",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 50_000_000.0,
            "liquidity_tier": "top",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
        {
            "symbol": "BTCUSDTPERP",
            "market_type": "futures",
            "base_asset": "BTC",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 80_000_000.0,
            "liquidity_tier": "high",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
        {
            "symbol": "ETHUSDT",
            "market_type": "spot",
            "base_asset": "ETH",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 20_000_000.0,
            "liquidity_tier": "low",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
    ]
    row2_instruments = [
        {
            "symbol": "SOLUSDTPERP",
            "market_type": "futures",
            "base_asset": "SOL",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 35_000_000.0,
            "liquidity_tier": "medium",
            "quantity_step": 0.01,
            "price_tick": 0.01,
            "has_complete_funding": True,
        }
    ]
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-10T00:00:00Z",
        run_id="row-001",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=100.0),
            "BTCUSDTPERP": {
                **_sample_symbol(close=100.0),
                "futures_context": {
                    "mark_price": 100.25,
                    "mark_price_timestamp": "2026-03-10T00:00:00Z",
                    "mark_price_age_seconds": 0,
                    "funding_rate": 0.0004,
                    "funding_timestamp": "2026-03-10T00:00:00Z",
                    "funding_age_seconds": 0,
                    "open_interest_usdt": 120_000_000.0,
                    "open_interest_timestamp": "2026-03-10T00:00:00Z",
                    "open_interest_age_seconds": 0,
                },
            },
            "ETHUSDT": {
                **_sample_symbol(close=1000.0),
                "liquidity_tier": "low",
            },
        },
        derivatives_rows=[{"symbol": "BTCUSDTPERP", "funding_rate": 0.0004}],
        instrument_rows=row1_instruments,
        candidate_symbols=["BTCUSDT", "BTCUSDTPERP", "ETHUSDT"],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-11T00:00:00Z",
        run_id="row-002",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=110.0),
            "ETHUSDT": {
                **_sample_symbol(close=980.0),
                "liquidity_tier": "low",
            },
            "SOLUSDTPERP": {
                **_sample_symbol(close=50.0),
                "liquidity_tier": "medium",
                "futures_context": {
                    "mark_price": 50.25,
                    "mark_price_timestamp": "2026-03-11T00:00:00Z",
                    "mark_price_age_seconds": 0,
                    "funding_rate": 0.0002,
                    "funding_timestamp": "2026-03-11T00:00:00Z",
                    "funding_age_seconds": 0,
                    "open_interest_usdt": 80_000_000.0,
                    "open_interest_timestamp": "2026-03-11T00:00:00Z",
                    "open_interest_age_seconds": 0,
                },
            },
        },
        derivatives_rows=[{"symbol": "SOLUSDTPERP", "funding_rate": 0.0002}],
        instrument_rows=row2_instruments,
        candidate_symbols=["SOLUSDTPERP"],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-12T00:00:00Z",
        run_id="row-003",
        market_symbols={"SOLUSDTPERP": {**_sample_symbol(close=55.0), "liquidity_tier": "medium"}},
        derivatives_rows=[{"symbol": "SOLUSDTPERP", "funding_rate": 0.0002}],
        instrument_rows=row2_instruments,
        candidate_symbols=[],
    )

    config_path = tmp_path / "full_market_baseline.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "experiment_kind": "full_market_baseline",
                "sample_windows": [
                    {
                        "name": "full_history",
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-12T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "universe": {
                    "listing_age_days": 30,
                    "min_quote_volume_usdt_24h": {"spot": 1_000_000.0, "futures": 1_000_000.0},
                    "require_complete_funding": True,
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100_000.0,
                    "risk_per_trade": 0.02,
                    "max_open_risk": 0.03,
                },
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0},
                    "funding_mode": "historical_series",
                },
                "baseline_name": "current_system",
                "variant_name": "task5-replay",
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _install_replay_candidates(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())

    def trend_candidates(market: dict[str, Any], **_kwargs: Any) -> list[EngineCandidate]:
        symbols = set(market.get("candidate_symbols") or [])
        rows: list[EngineCandidate] = []
        if "BTCUSDT" in symbols:
            rows.append(
                EngineCandidate(
                    engine="trend",
                    setup_type="BREAKOUT_CONTINUATION",
                    symbol="BTCUSDT",
                    side="LONG",
                    score=0.95,
                    stop_loss=90.0,
                )
            )
            rows.append(
                EngineCandidate(
                    engine="trend",
                    setup_type="BREAKOUT_CONTINUATION",
                    symbol="BTCUSDTPERP",
                    side="LONG",
                    score=0.94,
                    stop_loss=90.0,
                )
            )
        return rows

    def rotation_candidates(market: dict[str, Any], **_kwargs: Any) -> list[EngineCandidate]:
        symbols = set(market.get("candidate_symbols") or [])
        if "ETHUSDT" in symbols:
            return [
                EngineCandidate(
                    engine="rotation",
                    setup_type="RS_PULLBACK",
                    symbol="ETHUSDT",
                    side="LONG",
                    score=0.90,
                    stop_loss=999.0,
                )
            ]
        if "SOLUSDTPERP" in symbols:
            return [
                EngineCandidate(
                    engine="rotation",
                    setup_type="RS_REACCELERATION",
                    symbol="SOLUSDTPERP",
                    side="LONG",
                    score=0.88,
                    stop_loss=47.0,
                )
            ]
        return []

    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", trend_candidates)
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", rotation_candidates)
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])


def _walk_forward_strategy_dataset_root(tmp_path: Path) -> Path:
    dataset_root = tmp_path / "walk_forward_strategy_dataset"
    instrument_rows = [
        {
            "symbol": "BTCUSDT",
            "market_type": "spot",
            "base_asset": "BTC",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 50_000_000.0,
            "liquidity_tier": "top",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
        {
            "symbol": "BTCUSDTPERP",
            "market_type": "futures",
            "base_asset": "BTC",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 80_000_000.0,
            "liquidity_tier": "high",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
        {
            "symbol": "ETHUSDTPERP",
            "market_type": "futures",
            "base_asset": "ETH",
            "listing_timestamp": "2020-01-01T00:00:00Z",
            "quote_volume_usdt_24h": 60_000_000.0,
            "liquidity_tier": "high",
            "quantity_step": 0.001,
            "price_tick": 0.1,
            "has_complete_funding": True,
        },
    ]

    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-10T00:00:00Z",
        run_id="wf-row-001",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=100.0),
            "BTCUSDTPERP": _sample_symbol(close=100.0),
            "ETHUSDTPERP": _sample_symbol(close=50.0),
        },
        derivatives_rows=[
            {"symbol": "BTCUSDTPERP", "funding_rate": 0.0002},
            {"symbol": "ETHUSDTPERP", "funding_rate": 0.0001},
        ],
        instrument_rows=instrument_rows,
        candidate_symbols=["BTCUSDT", "BTCUSDTPERP", "ETHUSDTPERP"],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-11T00:00:00Z",
        run_id="wf-row-002",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=110.0),
            "BTCUSDTPERP": _sample_symbol(close=115.0),
            "ETHUSDTPERP": _sample_symbol(close=40.0),
        },
        derivatives_rows=[
            {"symbol": "BTCUSDTPERP", "funding_rate": 0.0002},
            {"symbol": "ETHUSDTPERP", "funding_rate": 0.0001},
        ],
        instrument_rows=instrument_rows,
        candidate_symbols=[],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-12T00:00:00Z",
        run_id="wf-row-003",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=120.0),
            "BTCUSDTPERP": _sample_symbol(close=120.0),
            "ETHUSDTPERP": _sample_symbol(close=60.0),
        },
        derivatives_rows=[
            {"symbol": "BTCUSDTPERP", "funding_rate": 0.0002},
            {"symbol": "ETHUSDTPERP", "funding_rate": 0.0001},
        ],
        instrument_rows=instrument_rows,
        candidate_symbols=["BTCUSDT", "BTCUSDTPERP", "ETHUSDTPERP"],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-13T00:00:00Z",
        run_id="wf-row-004",
        market_symbols={
            "BTCUSDT": _sample_symbol(close=130.0),
            "BTCUSDTPERP": _sample_symbol(close=140.0),
            "ETHUSDTPERP": _sample_symbol(close=45.0),
        },
        derivatives_rows=[
            {"symbol": "BTCUSDTPERP", "funding_rate": 0.0002},
            {"symbol": "ETHUSDTPERP", "funding_rate": 0.0001},
        ],
        instrument_rows=instrument_rows,
        candidate_symbols=[],
    )
    return dataset_root


def _walk_forward_strategy_config(
    dataset_root: Path,
    *,
    disabled_engines: tuple[str, ...] = (),
    allowed_short_setup_types: tuple[str, ...] = (),
) -> BacktestConfig:
    rows = load_historical_dataset(dataset_root)
    return BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind="walk_forward_validation",
        sample_windows=(
            SampleWindow(
                name="history",
                start=rows[0].timestamp,
                end=rows[-1].timestamp,
            ),
        ),
        forward_return_windows=(),
        costs=BacktestCosts(
            fee_bps_by_market={"spot": 10.0, "futures": 5.0},
            slippage_bps_by_tier={"top": 2.0, "high": 8.0},
            funding_mode="historical_series",
        ),
        baseline_name="current_system",
        variant_name="wf_no_short" if disabled_engines else "wf_current_system",
        universe=UniverseFilterConfig(
            listing_age_days=30,
            min_quote_volume_usdt_24h={"spot": 1_000_000.0, "futures": 1_000_000.0},
            require_complete_funding=True,
        ),
        capital=CapitalModelConfig(
            model="shared_pool",
            initial_equity=100_000.0,
            risk_per_trade=0.02,
            max_open_risk=0.10,
        ),
        experiment_params=ExperimentParams(
            evaluation_window="3d",
            walk_forward=WalkForwardConfig(in_sample_size=2, out_of_sample_size=2, step_size=2),
            disabled_engines=disabled_engines,
            allowed_short_setup_types=allowed_short_setup_types,
        ),
    )


def test_load_backtest_config_walk_forward_validation_supports_strategy_replay_fields(tmp_path: Path) -> None:
    dataset_root = _walk_forward_strategy_dataset_root(tmp_path)
    config_path = tmp_path / "walk_forward_strategy_config.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "experiment_kind": "walk_forward_validation",
                "sample_windows": [
                    {
                        "name": "history",
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-13T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {"top": 2.0, "high": 8.0},
                    "funding_mode": "historical_series",
                },
                "universe": {
                    "listing_age_days": 30,
                    "min_quote_volume_usdt_24h": {"spot": 1000000.0, "futures": 1000000.0},
                    "require_complete_funding": True
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100000.0,
                    "risk_per_trade": 0.02,
                    "max_open_risk": 0.10
                },
                "baseline_name": "current_system",
                "variant_name": "wf_current_system",
                "experiment_params": {
                    "evaluation_window": "3d",
                    "walk_forward": {
                        "in_sample_size": 2,
                        "out_of_sample_size": 2,
                        "step_size": 2
                    },
                    "disabled_engines": ["short"]
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.universe is not None
    assert config.capital is not None
    assert config.costs.fee_bps_by_market == {"spot": 10.0, "futures": 5.0}
    assert config.costs.slippage_bps_by_tier == {"top": 2.0, "high": 8.0}
    assert config.costs.funding_mode == "historical_series"
    assert config.experiment_params is not None
    assert config.experiment_params.disabled_engines == ("short",)


def test_walk_forward_validation_respects_disabled_short_engine(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    dataset_root = _walk_forward_strategy_dataset_root(tmp_path)
    current_config = _walk_forward_strategy_config(dataset_root)
    no_short_config = _walk_forward_strategy_config(dataset_root, disabled_engines=("short",))
    rows = load_historical_dataset(dataset_root)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="BREAKOUT_CONTINUATION",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90.0,
            )
        ]
        if "BTCUSDT" in set(market.get("candidate_symbols") or [])
        else [],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=110.0,
            )
        ]
        if "BTCUSDTPERP" in set(market.get("candidate_symbols") or [])
        else [],
    )

    _current_manifest, current_artifacts = backtest_cli._walk_forward_validation_outputs(current_config, rows)
    _no_short_manifest, no_short_artifacts = backtest_cli._walk_forward_validation_outputs(no_short_config, rows)

    assert (
        current_artifacts["scorecard.json"]["key_metrics"]["out_of_sample_total_return"]
        != no_short_artifacts["scorecard.json"]["key_metrics"]["out_of_sample_total_return"]
    )


def test_walk_forward_validation_respects_allowed_short_setup_types(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    dataset_root = _walk_forward_strategy_dataset_root(tmp_path)
    current_config = _walk_forward_strategy_config(dataset_root)
    filtered_config = _walk_forward_strategy_config(
        dataset_root,
        allowed_short_setup_types=("FAILED_BOUNCE_SHORT",),
    )
    rows = load_historical_dataset(dataset_root)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="BREAKOUT_CONTINUATION",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90.0,
            )
        ]
        if "BTCUSDT" in set(market.get("candidate_symbols") or [])
        else [],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=110.0,
            ),
            EngineCandidate(
                engine="short",
                setup_type="FAILED_BOUNCE_SHORT",
                symbol="ETHUSDTPERP",
                side="SHORT",
                score=0.96,
                stop_loss=60.0,
            ),
        ]
        if "BTCUSDTPERP" in set(market.get("candidate_symbols") or [])
        else [],
    )

    _current_manifest, current_artifacts = backtest_cli._walk_forward_validation_outputs(current_config, rows)
    _filtered_manifest, filtered_artifacts = backtest_cli._walk_forward_validation_outputs(filtered_config, rows)

    assert (
        filtered_artifacts["scorecard.json"]["key_metrics"]["out_of_sample_total_return"]
        > current_artifacts["scorecard.json"]["key_metrics"]["out_of_sample_total_return"]
    )


def test_replay_full_market_baseline_emits_trades_rejections_and_cost_drag(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = load_backtest_config(_baseline_config_path(tmp_path))
    _install_replay_candidates(monkeypatch)

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    result = replay(config)

    assert result.portfolio_summary.trade_count == 3
    assert result.portfolio_summary.max_drawdown <= 0.0
    assert [row.symbol for row in result.trade_ledger] == ["BTCUSDT", "ETHUSDT", "SOLUSDTPERP"]
    assert [row.status for row in result.trade_ledger] == ["accepted", "resized", "accepted"]
    assert [row.symbol for row in result.rejection_ledger] == ["BTCUSDTPERP"]
    assert result.rejection_ledger[0].status == "rejected"
    assert result.cost_breakdown["fees"] > 0.0
    assert result.cost_breakdown["slippage"] > 0.0
    assert result.cost_breakdown["funding"] > 0.0

    trade_by_symbol = {row.symbol: row for row in result.trade_ledger}
    assert trade_by_symbol["BTCUSDT"].fee_paid > trade_by_symbol["SOLUSDTPERP"].fee_paid
    assert trade_by_symbol["ETHUSDT"].slippage_paid > trade_by_symbol["BTCUSDT"].slippage_paid
    assert trade_by_symbol["BTCUSDT"].funding_paid == pytest.approx(0.0)
    assert trade_by_symbol["ETHUSDT"].funding_paid == pytest.approx(0.0)
    assert trade_by_symbol["SOLUSDTPERP"].funding_paid > 0.0


def test_replay_full_market_baseline_rejects_duplicate_snapshot_timestamps(tmp_path: Path) -> None:
    timestamp = _ts("2026-03-10T00:00:00+00:00")
    config = BacktestConfig(
        dataset_root=tmp_path,
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=BacktestCosts(),
        baseline_name="current_system",
        variant_name="duplicate_timestamp_contract",
        universe=UniverseFilterConfig(
            listing_age_days=30,
            min_quote_volume_usdt_24h={"spot": 1_000_000.0, "futures": 1_000_000.0},
        ),
        capital=CapitalModelConfig(
            model="shared_pool",
            initial_equity=100_000.0,
            risk_per_trade=0.02,
            max_open_risk=0.03,
        ),
    )
    rows = [
        DatasetSnapshotRow(timestamp=timestamp, run_id="row-001", market={}, derivatives=[]),
        DatasetSnapshotRow(timestamp=timestamp, run_id="row-002", market={}, derivatives=[]),
    ]

    with pytest.raises(ValueError, match="full-market replay snapshot timestamps must be strictly increasing"):
        backtest_engine._replay_full_market_baseline_rows(config, rows)


def test_replay_full_market_baseline_rejects_naive_snapshot_timestamps(tmp_path: Path) -> None:
    config = BacktestConfig(
        dataset_root=tmp_path,
        experiment_kind="full_market_baseline",
        sample_windows=(),
        forward_return_windows=(),
        costs=BacktestCosts(),
        baseline_name="current_system",
        variant_name="timezone_aware_timestamp_contract",
        universe=UniverseFilterConfig(
            listing_age_days=30,
            min_quote_volume_usdt_24h={"spot": 1_000_000.0, "futures": 1_000_000.0},
        ),
        capital=CapitalModelConfig(
            model="shared_pool",
            initial_equity=100_000.0,
            risk_per_trade=0.02,
            max_open_risk=0.03,
        ),
    )
    rows = [
        DatasetSnapshotRow(timestamp=datetime(2026, 3, 10), run_id="row-001", market={}, derivatives=[]),
        DatasetSnapshotRow(timestamp=_ts("2026-03-11T00:00:00+00:00"), run_id="row-002", market={}, derivatives=[]),
    ]

    with pytest.raises(ValueError, match="full-market replay snapshot timestamps must be timezone-aware"):
        backtest_engine._replay_full_market_baseline_rows(config, rows)


def test_replay_full_market_baseline_rejects_candidates_below_minimum_cost_coverage(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["experiment_params"] = {"minimum_cost_coverage_ratio": 2.0}
    raw["costs"]["fee_bps"] = {"spot": 10.0, "futures": 5.0}
    raw["costs"]["slippage_tiers"] = {"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_backtest_config(config_path)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    def trend_candidates(market: dict[str, Any], **_kwargs: Any) -> list[EngineCandidate]:
        symbols = set(market.get("candidate_symbols") or [])
        if "BTCUSDT" not in symbols:
            return []
        return [
            EngineCandidate(
                engine="trend",
                setup_type="LOW_COVERAGE",
                symbol="BTCUSDT",
                side="LONG",
                score=0.99,
                stop_loss=90.0,
                take_profit=100.1,
            ),
            EngineCandidate(
                engine="trend",
                setup_type="ENOUGH_COVERAGE",
                symbol="BTCUSDTPERP",
                side="LONG",
                score=0.98,
                stop_loss=90.0,
                take_profit=101.0,
            ),
        ]

    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", trend_candidates)

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    result = replay(config)

    assert [row.symbol for row in result.trade_ledger] == ["BTCUSDTPERP"]
    rejected = {row.symbol: row for row in result.rejection_ledger}
    assert rejected["BTCUSDT"].reasons == ("minimum_cost_coverage_not_met",)


def test_replay_full_market_baseline_simulates_take_profit_exit_from_intraday_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["1h"] = {
        **market_payload["symbols"]["BTCUSDT"].get("1h", {}),
        "high": 116.0,
        "low": 99.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_reason == "fixed_horizon"
    assert btc_trade.exit_price == pytest.approx(110.0)
    assert btc_trade.simulated_exit_reason == "take_profit"
    assert btc_trade.simulated_exit_price == pytest.approx(115.0)
    assert btc_trade.simulated_exit_move_pct == pytest.approx(0.15)
    assert btc_trade.simulated_gross_pnl > btc_trade.gross_pnl


def test_replay_full_market_baseline_simulates_short_stop_loss_conservatively(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDTPERP"] = {
        **_sample_symbol(close=95.0),
        "liquidity_tier": "high",
        "1h": {
            **_sample_symbol(close=95.0)["1h"],
            "high": 112.0,
            "low": 80.0,
            "close": 95.0,
        },
    }
    market_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])

    def short_candidates(market: dict[str, Any], **_kwargs: Any) -> list[EngineCandidate]:
        if "BTCUSDTPERP" not in set(market.get("candidate_symbols") or []):
            return []
        return [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=110.0,
                take_profit=85.0,
            )
        ]

    monkeypatch.setattr(backtest_engine, "generate_short_candidates", short_candidates)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    short_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDTPERP")
    assert short_trade.side == "short"
    assert short_trade.exit_price == pytest.approx(95.0)
    assert short_trade.simulated_exit_reason == "stop_loss"
    assert short_trade.simulated_exit_price == pytest.approx(110.0)
    assert short_trade.simulated_exit_move_pct == pytest.approx(-0.10)
    assert short_trade.simulated_exit_ordering == "ambiguous_conservative_stop"
    assert short_trade.simulated_gross_pnl < short_trade.gross_pnl


def test_replay_full_market_baseline_marks_same_bar_stop_and_target_as_ambiguous_conservative(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["1h"] = {
        **market_payload["symbols"]["BTCUSDT"].get("1h", {}),
        "high": 116.0,
        "low": 89.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.simulated_exit_reason == "stop_loss"
    assert btc_trade.simulated_exit_price == pytest.approx(90.0)
    assert btc_trade.simulated_exit_ordering == "ambiguous_conservative_stop"


def test_replay_full_market_baseline_does_not_simulate_stop_or_target_without_intraday_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    for timeframe in ("daily", "4h", "1h"):
        market_payload["symbols"]["BTCUSDT"][timeframe] = {
            **market_payload["symbols"]["BTCUSDT"].get(timeframe, {}),
            "close": 120.0,
        }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.take_profit == pytest.approx(115.0)
    assert btc_trade.exit_price == pytest.approx(120.0)
    assert btc_trade.simulated_exit_reason == "fixed_horizon"
    assert btc_trade.simulated_exit_price == pytest.approx(btc_trade.exit_price)
    assert btc_trade.simulated_exit_move_pct == pytest.approx(btc_trade.exit_move_pct)
    assert btc_trade.simulated_gross_pnl == pytest.approx(btc_trade.gross_pnl)
    assert btc_trade.simulated_net_pnl == pytest.approx(btc_trade.net_pnl)


def test_replay_full_market_baseline_trade_ledger_keeps_candidate_explanation_fields(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.engine == "trend"
    assert btc_trade.setup_type == "BREAKOUT_CONTINUATION"
    assert btc_trade.score == pytest.approx(0.95)
    assert btc_trade.stop_loss == pytest.approx(90.0)
    assert btc_trade.take_profit == pytest.approx(115.0)
    assert btc_trade.exit_reason == "fixed_horizon"
    assert btc_trade.mfe_pct == pytest.approx(0.10)
    assert btc_trade.mae_pct == pytest.approx(0.0)
    assert btc_trade.exit_move_pct == pytest.approx(0.10)
    assert btc_trade.cost_coverage_ratio > 0.0


def test_replay_full_market_baseline_uses_intraday_path_for_trade_mfe_mae(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["15m"] = {
        **market_payload["symbols"]["BTCUSDT"].get("15m", {}),
        "high": 112.0,
        "low": 96.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_move_pct == pytest.approx(0.10)
    assert btc_trade.mfe_pct == pytest.approx(0.12)
    assert btc_trade.mae_pct == pytest.approx(0.04)


def test_replay_full_market_baseline_prefers_finest_available_intraday_path_for_mfe_mae(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["15m"] = {
        **market_payload["symbols"]["BTCUSDT"].get("15m", {}),
        "high": 130.0,
        "low": 70.0,
        "close": 110.0,
    }
    market_payload["symbols"]["BTCUSDT"]["5m"] = {
        **market_payload["symbols"]["BTCUSDT"].get("5m", {}),
        "high": 112.0,
        "low": 96.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_move_pct == pytest.approx(0.10)
    assert btc_trade.mfe_pct == pytest.approx(0.12)
    assert btc_trade.mae_pct == pytest.approx(0.04)
    assert btc_trade.simulated_exit_reason == "fixed_horizon"


def test_replay_full_market_baseline_envelopes_intraday_path_with_entry_and_exit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["15m"] = {
        **market_payload["symbols"]["BTCUSDT"].get("15m", {}),
        "high": 105.0,
        "low": 99.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_move_pct == pytest.approx(0.10)
    assert btc_trade.mfe_pct == pytest.approx(0.10)
    assert btc_trade.mae_pct == pytest.approx(0.01)


def test_replay_full_market_baseline_falls_back_to_exit_move_when_path_high_low_is_missing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    market_path = row2_bundle / "market_context.json"
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    market_payload["symbols"]["BTCUSDT"]["daily"] = {
        **market_payload["symbols"]["BTCUSDT"].get("daily", {}),
        "high": 150.0,
        "low": 50.0,
        "close": 110.0,
    }
    market_payload["symbols"]["BTCUSDT"]["4h"] = {
        **market_payload["symbols"]["BTCUSDT"].get("4h", {}),
        "high": 140.0,
        "low": 60.0,
        "close": 110.0,
    }
    market_path.write_text(json.dumps(market_payload), encoding="utf-8")
    _install_replay_candidates(monkeypatch)

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    btc_trade = next(row for row in result.trade_ledger if row.symbol == "BTCUSDT")
    assert btc_trade.exit_move_pct == pytest.approx(0.10)
    assert btc_trade.mfe_pct == pytest.approx(0.10)
    assert btc_trade.mae_pct == pytest.approx(0.0)


def test_replay_full_market_baseline_allows_positive_reward_candidate_when_costs_are_zero(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["experiment_params"] = {"minimum_cost_coverage_ratio": 2.0}
    raw["costs"]["fee_bps"] = {"spot": 0.0, "futures": 0.0}
    raw["costs"]["slippage_tiers"] = {"top": 0.0, "high": 0.0, "medium": 0.0, "low": 0.0}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_backtest_config(config_path)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    def trend_candidates(market: dict[str, Any], **_kwargs: Any) -> list[EngineCandidate]:
        symbols = set(market.get("candidate_symbols") or [])
        if "BTCUSDT" not in symbols:
            return []
        return [
            EngineCandidate(
                engine="trend",
                setup_type="ZERO_COST_COVERAGE",
                symbol="BTCUSDT",
                side="LONG",
                score=0.99,
                stop_loss=90.0,
                take_profit=101.0,
            )
        ]

    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", trend_candidates)

    result = backtest_engine.replay_full_market_baseline(config)

    assert len(result.trade_ledger) == 1
    assert result.trade_ledger[0].cost_coverage_ratio is None
    assert result.rejection_ledger == ()


def test_replay_full_market_baseline_uses_intraday_entry_reference_for_short_term_short(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_bundle = config.dataset_root / "2026-03-10T00-00-00Z__row-001"
    row1_market_path = row1_bundle / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    row1_payload["symbols"]["BTCUSDTPERP"]["daily"]["close"] = 100.0
    row1_payload["symbols"]["BTCUSDTPERP"]["1h"]["close"] = 99.0
    row1_payload["symbols"]["BTCUSDTPERP"]["30m"] = {"close": 96.0, "high": 98.0, "low": 94.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["15m"] = {"close": 94.0, "high": 95.0, "low": 93.5}
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    row2_market_path = row2_bundle / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDTPERP"] = {**_sample_symbol(close=90.0), "liquidity_tier": "high"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=101.0,
                timeframe_meta={
                    "gate_timeframes": ["daily", "4h", "1h"],
                    "trigger_timeframes": ["30m", "15m"],
                },
            )
        ],
    )

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(94.0)
    assert trade.entry_reference_timeframe == "15m"
    assert trade.entry_reference_price == pytest.approx(94.0)
    assert trade.gate_timeframes == ("daily", "4h", "1h")
    assert trade.trigger_timeframes == ("30m", "15m")


def test_replay_full_market_baseline_executes_intraday_entry_on_next_finer_bar(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_bundle = config.dataset_root / "2026-03-10T00-00-00Z__row-001"
    row1_market_path = row1_bundle / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    row1_payload["symbols"]["BTCUSDTPERP"]["daily"]["close"] = 100.0
    row1_payload["symbols"]["BTCUSDTPERP"]["1h"]["close"] = 99.0
    row1_payload["symbols"]["BTCUSDTPERP"]["30m"] = {"close": 96.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["15m"] = {"close": 94.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["5m"] = {
        "close": 95.0,
        "next_bar": {"open": 92.5, "timestamp": "2026-03-10T00:05:00Z"},
    }
    row1_payload["symbols"]["BTCUSDTPERP"]["1m"] = {
        "close": 94.4,
        "next_bar": {"open": 93.25, "timestamp": "2026-03-10T00:01:00Z"},
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    row2_market_path = row2_bundle / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDTPERP"] = {**_sample_symbol(close=90.0), "liquidity_tier": "high"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=101.0,
                timeframe_meta={"trigger_timeframes": ["30m", "15m"]},
            )
        ],
    )

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_reference_timeframe == "15m"
    assert trade.entry_reference_price == pytest.approx(94.0)
    assert trade.entry_price == pytest.approx(93.25)
    assert trade.fill_model == "next_bar_ohlcv"
    assert trade.execution_price_source == "ohlcv_next_open"
    assert trade.fill_quality == "evidence_backed"
    assert trade.execution_timeframe == "1m"
    assert trade.execution_lag_bars == 1


def test_replay_full_market_baseline_short_entry_uses_orderbook_bid_before_next_bar(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    row1_payload["symbols"]["BTCUSDTPERP"]["daily"]["close"] = 100.0
    row1_payload["symbols"]["BTCUSDTPERP"]["30m"] = {"close": 96.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["15m"] = {"close": 94.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["1m"] = {
        "next_bar": {"open": 93.25, "timestamp": "2026-03-10T00:01:00Z"}
    }
    row1_payload["symbols"]["BTCUSDTPERP"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:05Z",
            "bid": 99.9,
            "ask": 100.1,
            "bid_size": 10,
            "ask_size": 12,
        }
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDTPERP"] = {**_sample_symbol(close=90.0), "liquidity_tier": "high"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=105.0,
                timeframe_meta={"trigger_timeframes": ["30m", "15m"]},
            )
        ],
    )

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(99.9)
    assert trade.fill_model == "taker_orderbook"
    assert trade.execution_price_source == "best_bid"
    assert trade.fill_quality == "evidence_backed"


def test_replay_full_market_baseline_long_entry_uses_orderbook_ask(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:05Z",
            "bid": 99.9,
            "ask": 100.1,
            "bid_size": 10,
            "ask_size": 12,
        }
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDT"] = {**_sample_symbol(close=110.0), "liquidity_tier": "top"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(100.1)
    assert trade.fill_model == "taker_orderbook"
    assert trade.execution_price_source == "best_ask"
    assert trade.fill_quality == "evidence_backed"


def test_replay_full_market_baseline_long_entry_uses_depth_when_levels_exist(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:05Z",
            "bid": 99.9,
            "ask": 100.0,
            "bid_size": 10,
            "ask_size": 1,
            "bids": [[99.9, 10.0]],
            "asks": [[100.0, 1.0], [101.0, 500.0]],
        }
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDT"] = {**_sample_symbol(close=110.0), "liquidity_tier": "top"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.fill_model == "taker_orderbook_depth"
    assert trade.execution_price_source == "ask_depth"
    assert trade.fill_quality == "evidence_backed"
    assert trade.requested_quantity == pytest.approx(400.0)
    assert trade.filled_quantity == pytest.approx(400.0)
    assert trade.unfilled_quantity == pytest.approx(0.0)
    assert trade.depth_levels_consumed == 2
    assert trade.entry_price > 100.0
    assert trade.execution_impact_bps is not None


def test_replay_full_market_baseline_resizes_when_depth_fill_is_partial(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:05Z",
            "bid": 99.9,
            "ask": 100.0,
            "asks": [[100.0, 1.0], [101.0, 2.0]],
        }
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDT"] = {**_sample_symbol(close=110.0), "liquidity_tier": "top"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.status == "resized"
    assert trade.fill_quality == "partial_evidence_backed"
    assert trade.requested_quantity == pytest.approx(400.0)
    assert trade.filled_quantity == pytest.approx(3.0)
    assert trade.unfilled_quantity == pytest.approx(397.0)
    assert trade.qty == pytest.approx(3.0)
    assert trade.position_notional == pytest.approx(302.0)


def test_replay_full_market_baseline_uses_conservative_trade_print_when_orderbook_missing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "trades": [
            {"timestamp": "2026-03-10T00:00:06Z", "price": 100.05, "quantity": 0.5, "side": "buy"},
            {"timestamp": "2026-03-10T00:00:07Z", "price": 100.20, "quantity": 0.3, "side": "buy"},
        ],
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDT"] = {**_sample_symbol(close=110.0), "liquidity_tier": "top"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(100.20)
    assert trade.fill_model == "taker_trade_print"
    assert trade.execution_price_source == "trade_print"
    assert trade.fill_quality == "evidence_backed"

    report = backtest_reporting.render_full_market_baseline_report(result)
    reported_trade = report["trades"][0]
    assert reported_trade["fill_model"] == "taker_trade_print"
    assert reported_trade["execution_price_source"] == "trade_print"
    assert reported_trade["fill_quality"] == "evidence_backed"


def test_replay_full_market_baseline_post_only_without_crossing_evidence_is_rejected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:00Z",
            "bid": 99.8,
            "ask": 100.2,
            "bid_size": 10,
            "ask_size": 12,
        },
        "trades": [
            {"timestamp": "2026-03-10T00:00:06Z", "price": 100.1, "quantity": 0.5, "side": "buy"}
        ],
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
                timeframe_meta={"execution_policy": "post_only"},
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    assert result.trade_ledger == ()
    assert len(result.rejection_ledger) == 1
    rejection = result.rejection_ledger[0]
    assert rejection.symbol == "BTCUSDT"
    assert rejection.status == "rejected"
    assert "maker_no_fill_evidence" in rejection.reasons


def test_replay_full_market_baseline_post_only_uses_queue_evidence_fields(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_market_path = config.dataset_root / "2026-03-10T00-00-00Z__row-001" / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDT"]
    row1_payload["symbols"]["BTCUSDT"]["execution"] = {
        "order_book": {
            "timestamp": "2026-03-10T00:00:00Z",
            "bid": 100.0,
            "ask": 100.2,
            "bid_size": 2.0,
            "ask_size": 12.0,
        },
        "trades": [
            {
                "timestamp": "2026-03-10T00:00:01Z",
                "price": 100.0,
                "quantity": 3.0,
                "side": "sell",
                "fill_id": "maker-print-001",
            }
        ],
    }
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_market_path = config.dataset_root / "2026-03-11T00-00-00Z__row-002" / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDT"] = {**_sample_symbol(close=110.0), "liquidity_tier": "top"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="TREND_PULLBACK",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=95.0,
                timeframe_meta={"execution_policy": "post_only"},
            )
        ],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_short_candidates", lambda *_args, **_kwargs: [])

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.fill_model == "maker_post_only_queue"
    assert trade.execution_price_source == "trade_print"
    assert trade.fill_quality == "partial_evidence_backed"
    assert trade.filled_quantity == pytest.approx(1.0)
    assert trade.unfilled_quantity == pytest.approx(399.0)
    assert trade.maker_status == "partial"
    assert trade.queue_ahead_initial == pytest.approx(2.0)
    assert trade.queue_ahead_remaining == pytest.approx(0.0)
    assert trade.maker_wait_seconds == pytest.approx(1.0)
    assert "timeout_expired" in trade.maker_reasons


def test_replay_full_market_baseline_labels_intraday_entry_reference_fallback_as_approximate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_bundle = config.dataset_root / "2026-03-10T00-00-00Z__row-001"
    row1_market_path = row1_bundle / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    row1_payload["symbols"]["BTCUSDTPERP"]["daily"]["close"] = 100.0
    row1_payload["symbols"]["BTCUSDTPERP"]["1h"]["close"] = 99.0
    row1_payload["symbols"]["BTCUSDTPERP"]["30m"] = {"close": 96.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["15m"] = {"close": 94.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["5m"] = {"close": 92.5}
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    row2_market_path = row2_bundle / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDTPERP"] = {**_sample_symbol(close=90.0), "liquidity_tier": "high"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=101.0,
                timeframe_meta={"trigger_timeframes": ["30m", "15m"]},
            )
        ],
    )

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(94.0)
    assert trade.fill_model == "reference_close"
    assert trade.execution_price_source == "ohlcv_close"
    assert trade.fill_quality == "approximate"
    assert trade.execution_timeframe == ""
    assert trade.execution_lag_bars == 0


def test_replay_full_market_baseline_generates_take_profit_from_intraday_entry_reference(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    config = load_backtest_config(config_path)
    row1_bundle = config.dataset_root / "2026-03-10T00-00-00Z__row-001"
    row1_market_path = row1_bundle / "market_context.json"
    row1_payload = json.loads(row1_market_path.read_text(encoding="utf-8"))
    row1_payload["candidate_symbols"] = ["BTCUSDTPERP"]
    row1_payload["symbols"]["BTCUSDTPERP"]["daily"]["close"] = 100.0
    row1_payload["symbols"]["BTCUSDTPERP"]["1h"]["close"] = 99.0
    row1_payload["symbols"]["BTCUSDTPERP"]["30m"] = {"close": 96.0}
    row1_payload["symbols"]["BTCUSDTPERP"]["15m"] = {"close": 94.0}
    row1_market_path.write_text(json.dumps(row1_payload), encoding="utf-8")

    row2_bundle = config.dataset_root / "2026-03-11T00-00-00Z__row-002"
    row2_market_path = row2_bundle / "market_context.json"
    row2_payload = json.loads(row2_market_path.read_text(encoding="utf-8"))
    row2_payload["symbols"]["BTCUSDTPERP"] = {**_sample_symbol(close=90.0), "liquidity_tier": "high"}
    row2_payload["candidate_symbols"] = []
    row2_market_path.write_text(json.dumps(row2_payload), encoding="utf-8")

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(backtest_engine, "generate_trend_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda *_args, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=101.0,
                timeframe_meta={"trigger_timeframes": ["30m", "15m"]},
            )
        ],
    )

    result = backtest_engine.replay_full_market_baseline(load_backtest_config(config_path))

    trade = result.trade_ledger[0]
    assert trade.entry_price == pytest.approx(94.0)
    assert trade.take_profit == pytest.approx(83.5)


def test_replay_full_market_baseline_respects_disabled_short_engine(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["experiment_params"] = {"disabled_engines": ["short"]}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_backtest_config(config_path)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="BREAKOUT_CONTINUATION",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90.0,
            )
        ]
        if "BTCUSDT" in set(market.get("candidate_symbols") or [])
        else [],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=110.0,
            )
        ]
        if "BTCUSDTPERP" in set(market.get("candidate_symbols") or [])
        else [],
    )

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    result = replay(config)

    assert [row.symbol for row in result.trade_ledger] == ["BTCUSDT"]
    assert all(row.side == "long" for row in result.trade_ledger)
    assert all(row.symbol != "BTCUSDTPERP" for row in result.trade_ledger)


def test_replay_full_market_baseline_respects_allowed_short_setup_types(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["experiment_params"] = {"allowed_short_setup_types": ["BREAKDOWN_SHORT"]}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_backtest_config(config_path)

    monkeypatch.setattr(
        backtest_engine,
        "classify_regime",
        lambda *_args, **_kwargs: RegimeSnapshot(label="MIXED", confidence=0.5, risk_multiplier=1.0),
    )
    monkeypatch.setattr(backtest_engine, "build_universes", lambda *_args, **_kwargs: UniverseBuildResult())
    monkeypatch.setattr(
        backtest_engine,
        "generate_trend_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="trend",
                setup_type="BREAKOUT_CONTINUATION",
                symbol="BTCUSDT",
                side="LONG",
                score=0.95,
                stop_loss=90.0,
            )
        ]
        if "BTCUSDT" in set(market.get("candidate_symbols") or [])
        else [],
    )
    monkeypatch.setattr(backtest_engine, "generate_rotation_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        backtest_engine,
        "generate_short_candidates",
        lambda market, **_kwargs: [
            EngineCandidate(
                engine="short",
                setup_type="BREAKDOWN_SHORT",
                symbol="BTCUSDTPERP",
                side="SHORT",
                score=0.97,
                stop_loss=110.0,
            ),
            EngineCandidate(
                engine="short",
                setup_type="FAILED_BOUNCE_SHORT",
                symbol="ETHUSDTPERP",
                side="SHORT",
                score=0.96,
                stop_loss=120.0,
            ),
        ]
        if "BTCUSDTPERP" in set(market.get("candidate_symbols") or [])
        else [],
    )

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    result = replay(config)

    assert [row.symbol for row in result.trade_ledger] == ["BTCUSDTPERP", "BTCUSDT"]
    assert [row.side for row in result.trade_ledger] == ["short", "long"]
    assert all(row.symbol != "ETHUSDTPERP" for row in result.trade_ledger)


def test_full_market_baseline_replay_is_deterministic_for_same_dataset_and_config(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_path = _baseline_config_path(tmp_path)
    _install_replay_candidates(monkeypatch)

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    first = replay(load_backtest_config(config_path))
    second = replay(load_backtest_config(config_path))

    assert first.portfolio_summary == second.portfolio_summary
    assert first.trade_ledger == second.trade_ledger
    assert first.rejection_ledger == second.rejection_ledger
