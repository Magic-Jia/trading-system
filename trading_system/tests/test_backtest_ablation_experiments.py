from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from trading_system.app.backtest import experiments as backtest_experiments
from trading_system.app.backtest.experiments import (
    run_allocator_friction_experiment,
    run_engine_filter_ablation_experiment,
    run_rotation_suppression_experiment,
)
from trading_system.app.backtest.types import DatasetSnapshotRow
from trading_system.app.backtest.walk_forward import build_walk_forward_windows


def _rotation_market() -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "atr_pct": 0.035, "return_pct_7d": 0.05, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "return_pct_3d": 0.019, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 101.0, "ema_20": 100.2, "ema_50": 99.9, "return_pct_24h": 0.006, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 102.0, "ema_20": 100.8, "ema_50": 99.8, "atr_pct": 0.034, "return_pct_7d": 0.048, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 102.0, "ema_20": 100.7, "ema_50": 99.7, "return_pct_3d": 0.018, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 102.0, "ema_20": 100.8, "ema_50": 99.9, "return_pct_24h": 0.005, "volume_usdt_24h": 12_000_000_000},
            },
            "LINKUSDT": {
                "sector": "oracle",
                "liquidity_tier": "high",
                "daily": {"close": 25.0, "ema_20": 23.9, "ema_50": 22.4, "atr_pct": 0.052, "return_pct_7d": 0.082, "volume_usdt_24h": 1_050_000_000},
                "4h": {"close": 25.0, "ema_20": 24.4, "ema_50": 23.9, "return_pct_3d": 0.029, "volume_usdt_24h": 1_050_000_000},
                "1h": {"close": 25.0, "ema_20": 24.8, "ema_50": 24.4, "return_pct_24h": 0.011, "volume_usdt_24h": 1_050_000_000},
            },
            "ADAUSDT": {
                "sector": "alt_l1",
                "liquidity_tier": "high",
                "daily": {"close": 0.82, "ema_20": 0.79, "ema_50": 0.75, "atr_pct": 0.049, "return_pct_7d": 0.061, "volume_usdt_24h": 970_000_000},
                "4h": {"close": 0.82, "ema_20": 0.81, "ema_50": 0.79, "return_pct_3d": 0.017, "volume_usdt_24h": 970_000_000},
                "1h": {"close": 0.82, "ema_20": 0.818, "ema_50": 0.812, "return_pct_24h": 0.006, "volume_usdt_24h": 970_000_000},
            },
        }
    }


def _suppressed_rotation_row(
    index: int,
    *,
    link_return: float,
    ada_return: float,
    forward_return_3d: float = 0.01,
) -> DatasetSnapshotRow:
    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 10, tzinfo=UTC) + timedelta(days=index),
        run_id=f"rotation-{index}",
        market=_rotation_market(),
        derivatives=[
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.0,
                "open_interest_usdt": 10_000_000_000,
                "open_interest_change_24h_pct": 0.0,
                "mark_price_change_24h_pct": 0.0,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 0,
            }
        ],
        account={"equity": 100_000.0, "available_balance": 100_000.0, "futures_wallet_balance": 100_000.0},
        forward_returns={"3d": forward_return_3d},
        meta={
            "candidate_forward_returns": {
                "rotation": {
                    "LINKUSDT": link_return,
                    "ADAUSDT": ada_return,
                }
            },
            "regime_override": {
                "label": "RISK_OFF",
                "confidence": 0.62,
                "risk_multiplier": 0.56,
                "execution_policy": "downsize",
                "bucket_targets": {"trend": 0.25, "rotation": 0.15, "short": 0.6},
                "suppression_rules": ["rotation"],
            },
        },
    )


def test_suppression_policy_comparison() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02),
    ]

    result = run_rotation_suppression_experiment(rows, evaluation_window="3d", soft_score_floor=0.72)

    assert set(result["policies"]) == {"current", "no_suppression", "soft_suppression"}
    assert result["opportunity_kill_rate"] > 0
    assert result["avoid_loss_rate"] > 0
    assert result["policies"]["no_suppression"]["bucket_level_pnl"] != 0
    assert result["rotation_comparison_rows"]


def _engine_account() -> dict[str, float | list[object]]:
    return {
        "equity": 100_000.0,
        "available_balance": 100_000.0,
        "futures_wallet_balance": 100_000.0,
        "open_positions": [],
    }


def _bullish_ablation_row() -> DatasetSnapshotRow:
    market = deepcopy(_rotation_market())
    market["symbols"]["LINKUSDT"]["daily"]["close"] = 26.0
    market["symbols"]["LINKUSDT"]["daily"]["ema_20"] = 24.4
    market["symbols"]["LINKUSDT"]["daily"]["ema_50"] = 23.0
    market["symbols"]["LINKUSDT"]["4h"]["close"] = 26.5
    market["symbols"]["LINKUSDT"]["4h"]["ema_20"] = 25.0
    market["symbols"]["LINKUSDT"]["4h"]["ema_50"] = 24.0
    market["symbols"]["LINKUSDT"]["1h"]["close"] = 25.5
    market["symbols"]["LINKUSDT"]["1h"]["ema_20"] = 25.1
    market["symbols"]["LINKUSDT"]["1h"]["ema_50"] = 24.7

    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 12, tzinfo=UTC),
        run_id="engine-bull",
        market=market,
        derivatives=[
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.0,
                "open_interest_usdt": 10_000_000_000,
                "open_interest_change_24h_pct": 0.0,
                "mark_price_change_24h_pct": 0.0,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 0,
            }
        ],
        account=_engine_account(),
        forward_returns={"3d": 0.01},
        meta={
            "candidate_forward_returns": {
                "trend": {
                    "BTCUSDT": 0.02,
                    "ETHUSDT": 0.018,
                    "LINKUSDT": 0.014,
                    "ADAUSDT": 0.011,
                },
                "rotation": {
                    "LINKUSDT": 0.05,
                    "ADAUSDT": 0.02,
                },
            },
            "regime_override": {
                "label": "RISK_ON_TREND",
                "confidence": 0.88,
                "risk_multiplier": 0.92,
                "execution_policy": "normal",
                "bucket_targets": {"trend": 0.55, "rotation": 0.25, "short": 0.05},
                "suppression_rules": [],
            },
        },
    )


def _bearish_short_market() -> dict[str, object]:
    return {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 96.0, "ema_20": 98.0, "ema_50": 100.0, "atr_pct": 0.04, "return_pct_7d": -0.04, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 96.0, "ema_20": 97.0, "ema_50": 99.0, "return_pct_3d": -0.02, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 96.0, "ema_20": 96.5, "ema_50": 97.0, "return_pct_24h": -0.008, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 95.0, "ema_20": 97.0, "ema_50": 99.0, "atr_pct": 0.041, "return_pct_7d": -0.045, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 95.0, "ema_20": 96.0, "ema_50": 98.0, "return_pct_3d": -0.018, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 95.0, "ema_20": 95.8, "ema_50": 96.4, "return_pct_24h": -0.007, "volume_usdt_24h": 12_000_000_000},
            },
        }
    }


def _bearish_short_row() -> DatasetSnapshotRow:
    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 13, tzinfo=UTC),
        run_id="engine-bear",
        market=_bearish_short_market(),
        derivatives=[
            {
                "symbol": "BTCUSDT",
                "funding_rate": -0.00008,
                "open_interest_usdt": 1_000_000_000,
                "open_interest_change_24h_pct": -0.05,
                "mark_price_change_24h_pct": -0.03,
                "taker_buy_sell_ratio": 0.92,
                "basis_bps": 8,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": -0.00008,
                "open_interest_usdt": 1_000_000_000,
                "open_interest_change_24h_pct": -0.05,
                "mark_price_change_24h_pct": -0.03,
                "taker_buy_sell_ratio": 0.92,
                "basis_bps": 8,
            },
        ],
        account=_engine_account(),
        forward_returns={"3d": -0.01},
        meta={
            "candidate_forward_returns": {
                "short": {
                    "BTCUSDT": 0.03,
                    "ETHUSDT": 0.018,
                }
            },
            "regime_override": {
                "label": "RISK_OFF",
                "confidence": 0.7,
                "risk_multiplier": 0.6,
                "execution_policy": "downsize",
                "bucket_targets": {"trend": 0.15, "rotation": 0.05, "short": 0.6},
                "suppression_rules": [],
            },
        },
    )


def test_engine_ablation_outputs_funnel_metrics() -> None:
    rows = [_bullish_ablation_row(), _bearish_short_row()]

    result = run_engine_filter_ablation_experiment(rows, evaluation_window="3d")

    assert set(result["variants"]) == {
        "trend_only",
        "rotation_only",
        "short_only",
        "rotation_without_overheat_filter",
    }
    assert result["metadata"]["snapshot_count"] == 2
    assert result["variants"]["trend_only"]["funnel"]["raw_candidates"] > 0
    assert result["variants"]["trend_only"]["funnel"]["validated_candidates"] >= 0
    assert result["variants"]["rotation_only"]["funnel"]["raw_candidates"] > 0
    assert result["variants"]["rotation_only"]["filter_counts"]["overheat_filtered"] > 0
    assert result["variants"]["short_only"]["funnel"]["raw_candidates"] > 0
    assert result["variants"]["rotation_without_overheat_filter"]["filter_counts"]["overheat_filtered"] == 0
    assert (
        result["variants"]["rotation_without_overheat_filter"]["funnel"]["raw_candidates"]
        > result["variants"]["rotation_only"]["funnel"]["raw_candidates"]
    )
    assert (
        result["variants"]["rotation_without_overheat_filter"]["funnel"]["accepted_allocations"]
        >= result["variants"]["rotation_only"]["funnel"]["accepted_allocations"]
    )


def test_allocator_and_friction_comparisons() -> None:
    rows = [_bullish_ablation_row(), _bearish_short_row()]

    result = run_allocator_friction_experiment(rows, evaluation_window="3d")

    assert set(result["variants"]) == {
        "current_allocator",
        "equal_weight_baseline",
        "fixed_risk_baseline",
    }
    assert result["metadata"]["snapshot_count"] == 2
    assert result["metadata"]["evaluation_window"] == "3d"

    current = result["variants"]["current_allocator"]
    equal_weight = result["variants"]["equal_weight_baseline"]
    fixed_risk = result["variants"]["fixed_risk_baseline"]

    assert current["allocation_summary"]["accepted_allocations"] < equal_weight["allocation_summary"]["accepted_allocations"]
    assert current["allocation_summary"]["total_risk_budget"] < fixed_risk["allocation_summary"]["total_risk_budget"]
    assert fixed_risk["allocation_summary"]["total_risk_budget"] < equal_weight["allocation_summary"]["total_risk_budget"]

    for variant in (current, equal_weight, fixed_risk):
        low = variant["frictions"]["low"]
        base = variant["frictions"]["base"]
        stressed = variant["frictions"]["stressed"]

        assert low["cost_drag"] < base["cost_drag"] < stressed["cost_drag"]
        assert low["net_bucket_pnl"] > stressed["net_bucket_pnl"]
        assert base["trade_count"] == variant["allocation_summary"]["accepted_allocations"]
        assert pytest.approx(
            base["cost_attribution"]["fee_drag"]
            + base["cost_attribution"]["slippage_drag"]
            + base["cost_attribution"]["funding_drag"],
            rel=0,
            abs=1e-9,
        ) == base["cost_drag"]

    assert result["comparison_rows"]


def _walk_forward_rows() -> list[DatasetSnapshotRow]:
    return [
        _suppressed_rotation_row(2, link_return=0.04, ada_return=-0.01, forward_return_3d=0.04),
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.06),
        _suppressed_rotation_row(1, link_return=0.05, ada_return=-0.02, forward_return_3d=0.05),
        _suppressed_rotation_row(5, link_return=0.02, ada_return=-0.01, forward_return_3d=0.02),
        _suppressed_rotation_row(3, link_return=0.03, ada_return=-0.005, forward_return_3d=0.03),
        _suppressed_rotation_row(4, link_return=0.05, ada_return=-0.015, forward_return_3d=0.05),
    ]


def test_walk_forward_splits_and_outputs() -> None:
    rows = _walk_forward_rows()

    windows = build_walk_forward_windows(
        rows,
        in_sample_size=2,
        out_of_sample_size=1,
        step_size=1,
    )

    assert len(windows) == 4
    assert [window.window_index for window in windows] == [1, 2, 3, 4]
    assert [row.run_id for row in windows[0].in_sample] == ["rotation-0", "rotation-1"]
    assert [row.run_id for row in windows[0].out_of_sample] == ["rotation-2"]
    assert [row.run_id for row in windows[-1].in_sample] == ["rotation-3", "rotation-4"]
    assert [row.run_id for row in windows[-1].out_of_sample] == ["rotation-5"]

    for window in windows:
        in_sample_ids = {row.run_id for row in window.in_sample}
        out_of_sample_ids = {row.run_id for row in window.out_of_sample}
        assert not (in_sample_ids & out_of_sample_ids)
        assert max(row.timestamp for row in window.in_sample) < min(row.timestamp for row in window.out_of_sample)

    result = backtest_experiments.run_walk_forward_validation_experiment(
        rows,
        evaluation_window="3d",
        in_sample_size=2,
        out_of_sample_size=1,
        step_size=1,
    )

    assert result["metadata"] == {
        "snapshot_count": 6,
        "window_count": 4,
        "evaluation_window": "3d",
        "in_sample_size": 2,
        "out_of_sample_size": 1,
        "step_size": 1,
    }
    assert len(result["windows"]) == 4

    scorecard_keys = {
        "total_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "win_rate",
        "payoff_ratio",
        "expectancy",
        "trade_count",
    }

    first_window = result["windows"][0]
    assert first_window["window_index"] == 1
    assert first_window["in_sample"]["run_ids"] == ["rotation-0", "rotation-1"]
    assert first_window["in_sample"]["snapshot_count"] == 2
    assert first_window["in_sample"]["start_timestamp"] == "2026-03-10T00:00:00+00:00"
    assert first_window["in_sample"]["end_timestamp"] == "2026-03-11T00:00:00+00:00"
    assert first_window["in_sample"]["scorecard"]["total_return"] == pytest.approx(0.113)
    assert first_window["in_sample"]["scorecard"]["trade_count"] == 2
    assert first_window["out_of_sample"]["run_ids"] == ["rotation-2"]
    assert first_window["out_of_sample"]["snapshot_count"] == 1
    assert first_window["out_of_sample"]["start_timestamp"] == "2026-03-12T00:00:00+00:00"
    assert first_window["out_of_sample"]["end_timestamp"] == "2026-03-12T00:00:00+00:00"
    assert first_window["out_of_sample"]["scorecard"]["total_return"] == pytest.approx(0.04)
    assert first_window["out_of_sample"]["scorecard"]["trade_count"] == 1

    for window in result["windows"]:
        assert set(window["in_sample"]["run_ids"]).isdisjoint(window["out_of_sample"]["run_ids"])
        assert window["in_sample"]["end_timestamp"] < window["out_of_sample"]["start_timestamp"]
        assert set(window["in_sample"]["scorecard"]) == scorecard_keys
        assert window["out_of_sample"]["scorecard"]["total_return"] > 0
        assert set(window["out_of_sample"]["scorecard"]) == scorecard_keys
