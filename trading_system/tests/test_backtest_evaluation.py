from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.app.backtest.evaluation import (
    CostStressScenario,
    _ledger_metrics,
    build_evaluation_report,
    build_walk_forward_evaluation,
    build_walk_forward_windows,
    evaluate_regime_buckets,
    run_cost_stress_tests,
)
from trading_system.app.backtest.types import DatasetSnapshotRow, TradeLedgerRow


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _row(index: int, *, close: float = 100.0, ema_50: float = 98.0, ret: float = 0.01, atr: float = 0.02) -> DatasetSnapshotRow:
    timestamp = _ts("2026-01-01T00:00:00Z") + timedelta(days=index)
    return DatasetSnapshotRow(
        timestamp=timestamp,
        run_id=f"row-{index:03d}",
        market={
            "symbols": {
                "BTCUSDT": {
                    "daily": {
                        "close": close,
                        "ema_50": ema_50,
                        "return_pct_7d": ret,
                        "atr_pct": atr,
                    }
                }
            }
        },
        derivatives=[],
    )


def _trade(symbol: str, entry: str, *, net_pnl: float, costs: tuple[float, float, float] = (1.0, 2.0, 3.0)) -> TradeLedgerRow:
    entry_timestamp = _ts(entry)
    gross_pnl = net_pnl + sum(costs)
    return TradeLedgerRow(
        symbol=symbol,
        market_type="futures",
        base_asset=symbol.removesuffix("USDT").removesuffix("PERP"),
        side="long",
        status="accepted",
        entry_timestamp=entry_timestamp,
        exit_timestamp=entry_timestamp + timedelta(hours=4),
        entry_price=100.0,
        exit_price=105.0,
        qty=1.0,
        position_notional=100.0,
        holding_hours=4.0,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        gross_return_pct=gross_pnl / 100.0,
        net_return_pct=net_pnl / 100.0,
        fee_paid=costs[0],
        slippage_paid=costs[1],
        funding_paid=costs[2],
    )


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    (
        ({"train_size": 0, "test_size": 1, "step_size": 1}, "train_size must be positive"),
        ({"train_size": 1, "test_size": 0, "step_size": 1}, "test_size must be positive"),
        ({"train_size": 1, "test_size": 1, "step_size": 0}, "step_size must be positive"),
    ),
)
def test_window_builder_rejects_non_positive_sizes(kwargs: dict[str, int], expected_message: str) -> None:
    with pytest.raises(ValueError, match=expected_message):
        build_walk_forward_windows([_row(0), _row(1)], **kwargs)


def test_window_builder_rejects_train_rows_that_do_not_end_before_test_rows_start() -> None:
    rows = [
        dataclasses.replace(_row(0), run_id="row-a"),
        dataclasses.replace(_row(0), run_id="row-b"),
    ]

    with pytest.raises(ValueError, match="walk-forward train rows must end before test rows start"):
        build_walk_forward_windows(rows, train_size=1, test_size=1, step_size=1)


def test_window_builder_sorts_by_timestamp_and_run_id_for_non_overlapping_windows() -> None:
    rows = [
        dataclasses.replace(_row(2), run_id="row-002"),
        dataclasses.replace(_row(0), run_id="row-b"),
        dataclasses.replace(_row(1), run_id="row-001"),
        dataclasses.replace(_row(0), run_id="row-a"),
        dataclasses.replace(_row(3), run_id="row-003"),
    ]

    windows = build_walk_forward_windows(rows, train_size=2, test_size=1, step_size=1)

    assert [window.window_index for window in windows] == [1, 2, 3]
    assert [row.run_id for row in windows[0].train_rows] == ["row-a", "row-b"]
    assert [row.run_id for row in windows[0].test_rows] == ["row-001"]
    assert [row.run_id for row in windows[-1].train_rows] == ["row-001", "row-002"]
    assert [row.run_id for row in windows[-1].test_rows] == ["row-003"]
    for window in windows:
        assert window.train_end < window.test_start
        assert {row.run_id for row in window.train_rows}.isdisjoint(row.run_id for row in window.test_rows)


def test_walk_forward_evaluation_returns_insufficient_status_for_short_dataset() -> None:
    result = build_walk_forward_evaluation(
        rows=[_row(0), _row(1)],
        trade_ledger=[],
        train_size=2,
        test_size=2,
    )

    assert result.status == "insufficient_data"
    assert result.reason == "dataset shorter than train_size + test_size"
    assert result.windows == ()
    assert result.to_dict()["status"] == "insufficient_data"


def test_walk_forward_metrics_use_train_and_test_period_trades_separately() -> None:
    rows = [_row(index) for index in range(5)]
    trades = (
        _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=10.0),
        _trade("ETHUSDT", "2026-01-02T12:00:00Z", net_pnl=-3.0),
        _trade("SOLUSDT", "2026-01-03T12:00:00Z", net_pnl=7.0),
        _trade("BNBUSDT", "2026-01-04T12:00:00Z", net_pnl=5.0),
    )

    result = build_walk_forward_evaluation(
        rows=rows,
        trade_ledger=trades,
        train_size=2,
        test_size=1,
        step_size=2,
    )

    assert result.status == "ok"
    assert len(result.windows) == 2
    assert result.windows[0].in_sample_metrics["trade_count"] == 2
    assert result.windows[0].in_sample_metrics["net_pnl"] == pytest.approx(7.0)
    assert result.windows[0].out_of_sample_metrics["trade_count"] == 1
    assert result.windows[0].out_of_sample_metrics["net_pnl"] == pytest.approx(7.0)
    assert result.windows[1].in_sample_trade_ids == ("SOLUSDT@2026-01-03T12:00:00+00:00", "BNBUSDT@2026-01-04T12:00:00+00:00")
    assert result.windows[1].out_of_sample_metrics["trade_count"] == 0


def test_cost_stress_scenarios_do_not_mutate_original_trades() -> None:
    trades = (_trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=20.0, costs=(1.0, 2.0, 3.0)),)
    scenario = CostStressScenario(name="double_costs", fee_multiplier=2.0, slippage_multiplier=2.0, funding_multiplier=2.0)

    stressed = run_cost_stress_tests(trades, [scenario])

    assert trades[0].net_pnl == pytest.approx(20.0)
    assert stressed[0].scenario.name == "double_costs"
    assert stressed[0].base_metrics["net_pnl"] == pytest.approx(20.0)
    assert stressed[0].stressed_metrics["net_pnl"] == pytest.approx(14.0)
    assert stressed[0].base_metrics["total_net_return"] == pytest.approx(0.20)
    assert stressed[0].stressed_metrics["total_net_return"] == pytest.approx(0.14)
    assert stressed[0].stressed_trades[0]["base_net_pnl"] == pytest.approx(20.0)
    assert stressed[0].stressed_trades[0]["stressed_net_pnl"] == pytest.approx(14.0)


@pytest.mark.parametrize(
    ("scenario_name", "expected_message"),
    (
        ("", "cost stress scenario name must be a canonical non-empty string"),
        (" fees_2x", "cost stress scenario name must be a canonical non-empty string"),
        ("fees 2x", "cost stress scenario name must be a canonical non-empty string"),
        ("fees_2X", "cost stress scenario name must be a canonical non-empty string"),
        (True, "cost stress scenario name must be a canonical non-empty string"),
    ),
)
def test_cost_stress_scenario_rejects_non_canonical_names(scenario_name: object, expected_message: str) -> None:
    with pytest.raises(ValueError, match=expected_message):
        CostStressScenario(name=scenario_name)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "bad_value", "expected_message"),
    (
        ("fee_multiplier", True, "fee_multiplier must be a finite non-bool non-negative number"),
        ("slippage_multiplier", float("nan"), "slippage_multiplier must be a finite non-bool non-negative number"),
        ("funding_multiplier", float("inf"), "funding_multiplier must be a finite non-bool non-negative number"),
        ("fee_multiplier", -0.01, "fee_multiplier must be a finite non-bool non-negative number"),
    ),
)
def test_cost_stress_scenario_rejects_invalid_multipliers(
    field_name: str,
    bad_value: object,
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        CostStressScenario(name="fees_2x", **{field_name: bad_value})  # type: ignore[arg-type]


def test_cost_stress_rejects_duplicate_scenario_names() -> None:
    trades = (_trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=20.0),)

    with pytest.raises(ValueError, match="duplicate cost stress scenario name: fees_2x"):
        run_cost_stress_tests(
            trades,
            (
                CostStressScenario(name="fees_2x", fee_multiplier=2.0),
                CostStressScenario(name="fees_2x", slippage_multiplier=2.0),
            ),
        )


def test_cost_stress_rejects_boolean_position_notional_before_stressed_risk_metrics() -> None:
    trade = dataclasses.replace(
        _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=20.0),
        position_notional=True,
    )

    with pytest.raises(ValueError, match=r"trades\[0\]\.position_notional must be a finite number"):
        run_cost_stress_tests(
            (trade,),
            (CostStressScenario(name="fees_2x", fee_multiplier=2.0),),
        )


def test_regime_buckets_are_deterministic_and_report_per_regime_metrics() -> None:
    rows = [
        _row(0, close=110.0, ema_50=100.0, ret=0.06, atr=0.02),
        _row(1, close=90.0, ema_50=100.0, ret=-0.06, atr=0.07),
        _row(2, close=101.0, ema_50=100.0, ret=0.001, atr=0.015),
    ]
    trades = (
        _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=9.0),
        _trade("ETHUSDT", "2026-01-02T12:00:00Z", net_pnl=-4.0),
    )

    buckets = evaluate_regime_buckets(rows, trades)

    assert [bucket.label for bucket in buckets] == ["high_vol_downtrend", "low_vol_range", "low_vol_uptrend"]
    by_label = {bucket.label: bucket for bucket in buckets}
    assert by_label["low_vol_uptrend"].row_count == 1
    assert by_label["low_vol_uptrend"].metrics["trade_count"] == 1
    assert by_label["low_vol_uptrend"].metrics["net_pnl"] == pytest.approx(9.0)
    assert by_label["high_vol_downtrend"].metrics["net_pnl"] == pytest.approx(-4.0)


@pytest.mark.parametrize(
    ("label", "expected_message"),
    (
        ("", "row-000 regime label must be a canonical non-empty string"),
        ("RISK ON", "row-000 regime label must be a canonical non-empty string"),
        (" risk_on", "row-000 regime label must be a canonical non-empty string"),
        (True, "row-000 regime label must be a canonical non-empty string"),
    ),
)
def test_regime_buckets_reject_non_canonical_explicit_labels(label: object, expected_message: str) -> None:
    row = dataclasses.replace(_row(0), meta={"regime_label": label})

    with pytest.raises(ValueError, match=expected_message):
        evaluate_regime_buckets((row,), ())


def test_regime_buckets_reject_rows_that_reuse_run_ids_with_different_labels() -> None:
    rows = (
        dataclasses.replace(_row(0), meta={"regime_label": "risk_on"}),
        dataclasses.replace(_row(1), run_id="row-000", meta={"regime_label": "risk_off"}),
    )

    with pytest.raises(ValueError, match="duplicate regime row id with conflicting label: row-000"):
        evaluate_regime_buckets(rows, ())


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("return_pct_7d", True),
        ("atr_pct", "0.07"),
        ("close", float("nan")),
        ("ema_50", float("inf")),
    ),
)
def test_regime_buckets_reject_invalid_present_daily_regime_metrics(
    field_name: str,
    bad_value: object,
) -> None:
    row = _row(0)
    corrupted_row = dataclasses.replace(row)
    corrupted_row.market["symbols"]["BTCUSDT"]["daily"][field_name] = bad_value

    with pytest.raises(ValueError, match=rf"row-000\.BTCUSDT\.daily\.{field_name} must be a finite number"):
        evaluate_regime_buckets((corrupted_row,), ())


def test_evaluation_report_labels_walk_forward_regimes_and_cost_stress() -> None:
    rows = [_row(index) for index in range(4)]
    trades = (
        _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=9.0),
        _trade("ETHUSDT", "2026-01-03T12:00:00Z", net_pnl=5.0),
    )

    report = build_evaluation_report(
        rows=rows,
        trade_ledger=trades,
        train_size=2,
        test_size=1,
        cost_scenarios=(CostStressScenario(name="fees_2x", fee_multiplier=2.0),),
    )

    assert report["walk_forward"]["status"] == "ok"
    assert report["walk_forward"]["windows"][0]["splits"]["in_sample"]["label"] == "IS"
    assert report["walk_forward"]["windows"][0]["splits"]["out_of_sample"]["label"] == "OOS"
    assert report["regimes"]["buckets"]
    assert report["cost_stress"]["scenarios"][0]["scenario"]["name"] == "fees_2x"
    assert report["cost_stress"]["scenarios"][0]["label"] == "cost_stress:fees_2x"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("net_pnl", True),
        ("net_return_pct", float("nan")),
        ("gross_return_pct", float("inf")),
        ("fee_paid", float("-inf")),
    ),
)
def test_ledger_metrics_rejects_invalid_trade_metric_numbers(field_name: str, bad_value: object) -> None:
    trade = _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=10.0)
    corrupted_trade = dataclasses.replace(trade, **{field_name: bad_value})

    with pytest.raises(ValueError, match=rf"trades\[0\]\.{field_name}"):
        _ledger_metrics((corrupted_trade,))


@pytest.mark.parametrize(
    ("override_name", "override_values", "expected_path"),
    (
        ("net_pnls", (10.0, True), r"net_pnls\[1\]"),
        ("net_returns", (0.10, float("nan")), r"net_returns\[1\]"),
    ),
)
def test_ledger_metrics_rejects_invalid_override_metric_numbers(
    override_name: str,
    override_values: tuple[object, ...],
    expected_path: str,
) -> None:
    trade = _trade("BTCUSDT", "2026-01-01T12:00:00Z", net_pnl=10.0)

    with pytest.raises(ValueError, match=expected_path):
        _ledger_metrics((trade,), **{override_name: override_values})
