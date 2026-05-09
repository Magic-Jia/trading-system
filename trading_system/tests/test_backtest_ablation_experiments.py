from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import math

import pytest

from trading_system.app.backtest import experiments as backtest_experiments
from trading_system.app.backtest.experiments import (
    run_allocator_friction_experiment,
    run_engine_filter_ablation_experiment,
    run_long_gate_telemetry_experiment,
    run_public_strategy_factor_experiment,
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


def test_public_strategy_factor_experiment_scores_supported_factor_effectiveness() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=-0.02),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.01),
        _suppressed_rotation_row(2, link_return=0.05, ada_return=-0.01, forward_return_3d=0.05),
        _suppressed_rotation_row(3, link_return=0.03, ada_return=-0.005, forward_return_3d=0.08),
    ]
    for index, row in enumerate(rows):
        row.market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = [-0.03, 0.01, 0.04, 0.08][index]

    result = run_public_strategy_factor_experiment(
        rows,
        evaluation_window="3d",
        strategy_families=("momentum", "mean_reversion"),
        minimum_effectiveness_sample_count=4,
    )

    momentum = next(factor for factor in result["factors"] if factor["factor_name"] == "momentum_3d")
    assert momentum["supported"] is True
    assert momentum["effectiveness"]["sample_count"] == 4
    assert momentum["effectiveness"]["information_coefficient"] > 0.9
    assert momentum["effectiveness"]["top_bucket_avg_forward_return"] > momentum["effectiveness"]["bottom_bucket_avg_forward_return"]
    assert momentum["effectiveness"]["top_bucket_hit_rate"] == 1.0
    assert momentum["effectiveness"]["effectiveness_status"] == "promising_research"

    mean_reversion = next(factor for factor in result["factors"] if factor["factor_name"] == "reversal_proxy_3d")
    assert mean_reversion["effectiveness"]["information_coefficient"] < -0.9
    assert result["summary"]["effective_factor_count"] == 1
    assert result["summary"]["evaluated_factor_count"] == 2


def test_public_strategy_factor_experiment_requires_minimum_sample_count_before_promising() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=-0.02),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.01),
        _suppressed_rotation_row(2, link_return=0.05, ada_return=-0.01, forward_return_3d=0.05),
        _suppressed_rotation_row(3, link_return=0.03, ada_return=-0.005, forward_return_3d=0.08),
    ]
    for index, row in enumerate(rows):
        row.market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = [-0.03, 0.01, 0.04, 0.08][index]

    result = run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))

    momentum = result["factors"][0]
    assert momentum["effectiveness"]["sample_count"] == 4
    assert momentum["effectiveness"]["minimum_sample_count"] == 30
    assert momentum["effectiveness"]["effectiveness_status"] == "insufficient_sample"
    assert result["summary"]["effective_factor_count"] == 0


def test_public_strategy_factor_experiment_skips_non_finite_factor_values() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    for row in rows:
        for symbol in row.market["symbols"].values():
            symbol["daily"]["return_pct_7d"] = math.nan

    result = run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))

    momentum = result["factors"][0]
    assert momentum["supported"] is True
    assert "effectiveness" not in momentum
    assert result["summary"]["evaluated_factor_count"] == 0


@pytest.mark.parametrize("invalid_forward_return", [True, math.nan, math.inf, -math.inf])
def test_public_strategy_factor_experiment_rejects_invalid_effectiveness_forward_returns(
    invalid_forward_return: object,
) -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    rows[1].forward_returns["3d"] = invalid_forward_return

    with pytest.raises(ValueError, match=r"^forward_returns\.3d must be a finite number$"):
        run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))


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


def test_rotation_suppression_experiment_rejects_non_string_regime_suppression_rules(monkeypatch) -> None:
    row = _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03)
    row.meta["regime_override"]["suppression_rules"] = [True]

    def no_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(backtest_experiments, "generate_rotation_candidates", no_candidates)

    with pytest.raises(ValueError, match=r"regime\.suppression_rules"):
        run_rotation_suppression_experiment([row], evaluation_window="3d", soft_score_floor=0.72)


def test_rotation_suppression_experiment_rejects_non_string_candidate_symbol(monkeypatch) -> None:
    row = _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03)

    def patched_rotation_candidates_for_policy(_row, *, policy, soft_score_floor):
        if policy == "current":
            return [{"symbol": 123, "score": 0.9}]
        return [{"symbol": "LINKUSDT", "score": 0.9}]

    monkeypatch.setattr(
        backtest_experiments,
        "_rotation_candidates_for_policy",
        patched_rotation_candidates_for_policy,
    )

    with pytest.raises(ValueError, match=r"^current\.candidate\.symbol must be a string$"):
        run_rotation_suppression_experiment([row], evaluation_window="3d")


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


def _majors_soft_trend_row() -> DatasetSnapshotRow:
    market = deepcopy(_rotation_market())
    market["symbols"] = {
        "BTCUSDT": deepcopy(market["symbols"]["BTCUSDT"]),
        "LINKUSDT": deepcopy(market["symbols"]["LINKUSDT"]),
    }
    market["symbols"]["BTCUSDT"]["daily"]["close"] = 104.0
    market["symbols"]["BTCUSDT"]["daily"]["ema_20"] = 102.0
    market["symbols"]["BTCUSDT"]["daily"]["ema_50"] = 103.0
    market["symbols"]["BTCUSDT"]["4h"]["close"] = 104.5
    market["symbols"]["BTCUSDT"]["4h"]["ema_20"] = 103.0
    market["symbols"]["BTCUSDT"]["4h"]["ema_50"] = 101.5
    market["symbols"]["BTCUSDT"]["1h"]["close"] = 104.3
    market["symbols"]["BTCUSDT"]["1h"]["ema_20"] = 103.6
    market["symbols"]["BTCUSDT"]["1h"]["ema_50"] = 103.0

    market["symbols"]["LINKUSDT"]["daily"]["close"] = 27.0
    market["symbols"]["LINKUSDT"]["daily"]["ema_20"] = 25.5
    market["symbols"]["LINKUSDT"]["daily"]["ema_50"] = 26.0
    market["symbols"]["LINKUSDT"]["4h"]["close"] = 27.3
    market["symbols"]["LINKUSDT"]["4h"]["ema_20"] = 26.4
    market["symbols"]["LINKUSDT"]["4h"]["ema_50"] = 25.8
    market["symbols"]["LINKUSDT"]["1h"]["close"] = 27.1
    market["symbols"]["LINKUSDT"]["1h"]["ema_20"] = 26.8
    market["symbols"]["LINKUSDT"]["1h"]["ema_50"] = 26.2

    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 14, tzinfo=UTC),
        run_id="engine-majors-soft",
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
        forward_returns={"3d": 0.012},
        meta={
            "candidate_forward_returns": {
                "trend": {
                    "BTCUSDT": 0.024,
                    "LINKUSDT": 0.017,
                }
            },
            "regime_override": {
                "label": "RISK_ON_TREND",
                "confidence": 0.86,
                "risk_multiplier": 0.92,
                "execution_policy": "normal",
                "bucket_targets": {"trend": 0.55, "rotation": 0.25, "short": 0.05},
                "suppression_rules": [],
            },
        },
    )


def _majors_reclaim_band_row() -> DatasetSnapshotRow:
    market = {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 102.5, "ema_20": 101.5, "ema_50": 103.0, "atr_pct": 0.022, "return_pct_7d": 0.041, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 103.2, "ema_20": 102.8, "ema_50": 102.1, "return_pct_3d": 0.019, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 103.0, "ema_20": 102.7, "ema_50": 102.2, "return_pct_24h": 0.006, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 101.5, "ema_20": 100.5, "ema_50": 101.0, "atr_pct": 0.024, "return_pct_7d": 0.047, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 102.1, "ema_20": 101.5, "ema_50": 100.8, "return_pct_3d": 0.018, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 102.0, "ema_20": 101.6, "ema_50": 101.2, "return_pct_24h": 0.005, "volume_usdt_24h": 12_000_000_000},
            },
            "BNBUSDT": {
                "sector": "majors",
                "liquidity_tier": "medium",
                "daily": {"close": 95.4, "ema_20": 93.8, "ema_50": 94.0, "atr_pct": 0.021, "return_pct_7d": 0.042, "volume_usdt_24h": 2_500_000_000},
                "4h": {"close": 95.6, "ema_20": 94.8, "ema_50": 94.1, "return_pct_3d": 0.016, "volume_usdt_24h": 2_500_000_000},
                "1h": {"close": 95.4, "ema_20": 95.0, "ema_50": 94.5, "return_pct_24h": 0.004, "volume_usdt_24h": 2_500_000_000},
            },
            "LINKUSDT": {
                "sector": "oracle",
                "liquidity_tier": "high",
                "daily": {"close": 27.0, "ema_20": 25.5, "ema_50": 26.0, "atr_pct": 0.03, "return_pct_7d": 0.071, "volume_usdt_24h": 1_200_000_000},
                "4h": {"close": 27.3, "ema_20": 26.4, "ema_50": 25.8, "return_pct_3d": 0.022, "volume_usdt_24h": 1_200_000_000},
                "1h": {"close": 27.1, "ema_20": 26.8, "ema_50": 26.2, "return_pct_24h": 0.007, "volume_usdt_24h": 1_200_000_000},
            },
        }
    }

    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 15, tzinfo=UTC),
        run_id="engine-majors-reclaim-band",
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
                    "BTCUSDT": 0.022,
                    "ETHUSDT": 0.011,
                    "BNBUSDT": 0.008,
                    "LINKUSDT": 0.015,
                }
            },
            "regime_override": {
                "label": "RISK_ON_TREND",
                "confidence": 0.87,
                "risk_multiplier": 0.92,
                "execution_policy": "normal",
                "bucket_targets": {"trend": 0.55, "rotation": 0.25, "short": 0.05},
                "suppression_rules": [],
            },
        },
    )


def test_engine_ablation_outputs_funnel_metrics() -> None:
    rows = [_bullish_ablation_row(), _bearish_short_row()]

    result = run_engine_filter_ablation_experiment(rows, evaluation_window="3d")

    assert set(result["variants"]) == {
        "trend_only",
        "majors_only_trend",
        "majors_soft_trend",
        "majors_reclaim_band_0pct",
        "majors_reclaim_band_1pct",
        "majors_reclaim_band_2pct",
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


@pytest.mark.parametrize(
    "derivatives_update,match",
    [
        ({"crowding_bias": True}, r"BTCUSDT\.derivatives\.crowding_bias"),
        ({"basis_bps": "25"}, r"BTCUSDT\.derivatives\.basis_bps"),
    ],
)
def test_engine_ablation_rejects_invalid_trend_derivatives_metadata(monkeypatch, derivatives_update, match) -> None:
    row = _bullish_ablation_row()
    original = backtest_experiments.trend_signals.symbol_derivatives_features

    def patched_symbol_derivatives_features(derivatives, symbol):
        features = original(derivatives, symbol)
        if symbol == "BTCUSDT":
            return {**features, **derivatives_update}
        return features

    monkeypatch.setattr(
        backtest_experiments.trend_signals,
        "symbol_derivatives_features",
        patched_symbol_derivatives_features,
    )

    with pytest.raises(ValueError, match=match):
        run_engine_filter_ablation_experiment([row], evaluation_window="3d")


@pytest.mark.parametrize(
    "source,match",
    [
        ("payload", r"^LINKUSDT\.sector must be a string when present$"),
        ("universe", r"^LINKUSDT\.rotation_universe\.sector must be a string when present$"),
    ],
)
def test_engine_ablation_rejects_non_string_rotation_candidate_sector(monkeypatch, source, match) -> None:
    row = _bullish_ablation_row()

    if source == "payload":
        row.market["symbols"]["LINKUSDT"]["sector"] = 123
    else:
        original_build_universes = backtest_experiments.build_universes

        def patched_build_universes(market, derivatives=None):
            universes = original_build_universes(market, derivatives=derivatives)
            for universe_row in universes.rotation_universe:
                if universe_row["symbol"] == "LINKUSDT":
                    universe_row["sector"] = 123
            return universes

        row.market["symbols"]["LINKUSDT"].pop("sector")
        monkeypatch.setattr(backtest_experiments, "build_universes", patched_build_universes)

    with pytest.raises(ValueError, match=match):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


def test_engine_ablation_rejects_non_string_trend_payload_sector() -> None:
    row = _majors_soft_trend_row()
    payload = row.market["symbols"]["BTCUSDT"]
    payload["sector"] = True

    with pytest.raises(ValueError, match=r"^BTCUSDT\.sector must be a string when present$"):
        backtest_experiments._trend_structure_intact(
            payload,
            symbol="BTCUSDT",
            soft_daily_for_majors=True,
        )


def test_majors_soft_trend_relaxes_daily_structure_only_for_majors() -> None:
    result = run_engine_filter_ablation_experiment([_majors_soft_trend_row()], evaluation_window="3d")

    trend_only = result["variants"]["trend_only"]
    majors_only = result["variants"]["majors_only_trend"]
    majors_soft = result["variants"]["majors_soft_trend"]

    assert trend_only["selected_symbols"] == []
    assert majors_only["selected_symbols"] == []
    assert majors_soft["selected_symbols"] == ["BTCUSDT"]
    assert majors_soft["accepted_symbols"] == ["BTCUSDT"]
    assert "LINKUSDT" not in majors_soft["selected_symbols"]
    assert majors_soft["funnel"]["raw_candidates"] == 1
    assert majors_soft["funnel"]["accepted_allocations"] == 1


def test_majors_reclaim_band_variants_keep_soft_major_entries_near_daily_ema50() -> None:
    result = run_engine_filter_ablation_experiment([_majors_reclaim_band_row()], evaluation_window="3d")

    majors_soft = result["variants"]["majors_soft_trend"]
    reclaim_0 = result["variants"]["majors_reclaim_band_0pct"]
    reclaim_1 = result["variants"]["majors_reclaim_band_1pct"]
    reclaim_2 = result["variants"]["majors_reclaim_band_2pct"]

    assert majors_soft["selected_symbols"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT"]
    assert reclaim_0["selected_symbols"] == ["BTCUSDT"]
    assert reclaim_1["selected_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert reclaim_2["selected_symbols"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT"]
    assert reclaim_0["funnel"]["raw_candidates"] == 1
    assert reclaim_1["funnel"]["raw_candidates"] == 2
    assert reclaim_2["funnel"]["raw_candidates"] == 3
    assert "LINKUSDT" not in majors_soft["selected_symbols"]
    assert "LINKUSDT" not in reclaim_2["selected_symbols"]


def test_engine_filter_ablation_rejects_non_string_selected_candidate_symbol(monkeypatch) -> None:
    row = _bullish_ablation_row()

    def invalid_trend_candidates_with_trace(_row, **_kwargs):
        return {
            "regime": row.meta["regime_override"],
            "input_universe": 1,
            "candidates": [{"symbol": 123, "score": 1.0}],
            "filter_counts": {"selected": 1},
        }

    monkeypatch.setattr(
        backtest_experiments,
        "_trend_candidates_with_trace",
        invalid_trend_candidates_with_trace,
    )

    with pytest.raises(ValueError, match=r"^candidates\[0\]\.symbol must be a string$"):
        run_engine_filter_ablation_experiment([row], evaluation_window="3d")


def test_engine_filter_ablation_rejects_non_mapping_traced_filter_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    def traced_with_invalid_filter_counts(_row, **_kwargs):
        return {
            "regime": _bullish_ablation_row().meta["regime_override"],
            "input_universe": 0,
            "candidates": [],
            "filter_counts": [("selected", 1)],
        }

    monkeypatch.setattr(
        backtest_experiments,
        "_trend_candidates_with_trace",
        traced_with_invalid_filter_counts,
    )

    with pytest.raises(ValueError, match=r"^traced\.filter_counts must be an object$"):
        run_engine_filter_ablation_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("candidates", "match"),
    [
        ("BTCUSDT", r"^candidates must be a list$"),
        ([object()], r"^candidates\[0\] must be an object$"),
    ],
)
def test_engine_filter_ablation_rejects_invalid_trace_candidate_shape(
    monkeypatch: pytest.MonkeyPatch,
    candidates: object,
    match: str,
) -> None:
    row = _bullish_ablation_row()

    def invalid_trend_candidates_with_trace(_row, **_kwargs):
        return {
            "regime": row.meta["regime_override"],
            "input_universe": 1,
            "candidates": candidates,
            "filter_counts": {},
        }

    monkeypatch.setattr(
        backtest_experiments,
        "_trend_candidates_with_trace",
        invalid_trend_candidates_with_trace,
    )

    with pytest.raises(ValueError, match=match):
        run_engine_filter_ablation_experiment([row], evaluation_window="3d")


@pytest.mark.parametrize("input_universe", [True, "1", 1.5])
def test_engine_filter_ablation_rejects_invalid_trace_input_universe(
    monkeypatch: pytest.MonkeyPatch,
    input_universe: object,
) -> None:
    row = _bullish_ablation_row()

    def invalid_trend_candidates_with_trace(_row, **_kwargs):
        return {
            "regime": row.meta["regime_override"],
            "input_universe": input_universe,
            "candidates": [],
            "filter_counts": {},
        }

    monkeypatch.setattr(
        backtest_experiments,
        "_trend_candidates_with_trace",
        invalid_trend_candidates_with_trace,
    )

    with pytest.raises(ValueError, match=r"^input_universe must be an integer$"):
        run_engine_filter_ablation_experiment([row], evaluation_window="3d")



def test_long_gate_telemetry_outputs_engine_blockers_and_snapshot_rows() -> None:
    rows = [_bullish_ablation_row(), _bearish_short_row()]

    result = run_long_gate_telemetry_experiment(rows, evaluation_window="3d")

    assert set(result["engines"]) == {"trend_long", "rotation_long"}
    assert result["metadata"]["snapshot_count"] == 2
    assert result["metadata"]["engine_count"] == 2
    assert result["engines"]["trend_long"]["funnel"]["raw_candidates"] > 0
    assert result["engines"]["trend_long"]["filter_counts"]["selected"] > 0
    assert result["engines"]["rotation_long"]["filter_counts"]["overheat_filtered"] > 0
    assert result["engines"]["rotation_long"]["filter_counts"]["selected"] > 0
    assert len(result["snapshot_rows"]) == 2

    bearish_row = next(row for row in result["snapshot_rows"] if row["run_id"] == "engine-bear")
    assert bearish_row["total_long_accepted_allocations"] == 0
    assert bearish_row["engines"]["trend_long"]["filter_counts"]["trend_filtered"] > 0
    assert bearish_row["engines"]["rotation_long"]["funnel"]["input_universe"] == 0
    assert bearish_row["engines"]["rotation_long"]["funnel"]["raw_candidates"] == 0


def test_long_gate_telemetry_breaks_trend_eligibility_into_specific_reasons() -> None:
    row = _bullish_ablation_row()
    market = deepcopy(row.market)
    market["symbols"]["ADAUSDT"]["liquidity_tier"] = "medium"
    market["symbols"]["ADAUSDT"]["daily"]["return_pct_7d"] = 0.08
    market["symbols"]["ADAUSDT"]["4h"]["return_pct_3d"] = 0.03
    market["symbols"]["ADAUSDT"]["daily"]["close"] = 0.84
    market["symbols"]["ADAUSDT"]["daily"]["ema_20"] = 0.81
    market["symbols"]["ADAUSDT"]["daily"]["ema_50"] = 0.78
    market["symbols"]["ADAUSDT"]["4h"]["close"] = 0.84
    market["symbols"]["ADAUSDT"]["4h"]["ema_20"] = 0.82
    market["symbols"]["ADAUSDT"]["4h"]["ema_50"] = 0.80
    market["symbols"]["ADAUSDT"]["1h"]["close"] = 0.84
    market["symbols"]["ADAUSDT"]["1h"]["ema_20"] = 0.83
    market["symbols"]["ADAUSDT"]["1h"]["ema_50"] = 0.82

    market["symbols"]["LINKUSDT"]["daily"]["return_pct_7d"] = -0.01
    market["symbols"]["LINKUSDT"]["4h"]["return_pct_3d"] = 0.03
    market["symbols"]["LINKUSDT"]["daily"]["close"] = 26.0
    market["symbols"]["LINKUSDT"]["daily"]["ema_20"] = 24.4
    market["symbols"]["LINKUSDT"]["daily"]["ema_50"] = 23.0
    market["symbols"]["LINKUSDT"]["4h"]["close"] = 26.5
    market["symbols"]["LINKUSDT"]["4h"]["ema_20"] = 25.0
    market["symbols"]["LINKUSDT"]["4h"]["ema_50"] = 24.0
    market["symbols"]["LINKUSDT"]["1h"]["close"] = 25.5
    market["symbols"]["LINKUSDT"]["1h"]["ema_20"] = 25.1
    market["symbols"]["LINKUSDT"]["1h"]["ema_50"] = 24.7

    market["symbols"]["XRPUSDT"] = {
        "sector": "payments",
        "liquidity_tier": "high",
        "daily": {"close": 2.12, "ema_20": 2.0, "ema_50": 1.92, "atr_pct": 0.038, "return_pct_7d": 0.05, "volume_usdt_24h": 2_000_000_000},
        "4h": {"close": 2.05, "ema_20": 2.01, "ema_50": 1.98, "return_pct_3d": -0.01, "volume_usdt_24h": 2_000_000_000},
        "1h": {"close": 2.03, "ema_20": 2.02, "ema_50": 2.0, "return_pct_24h": 0.004, "volume_usdt_24h": 2_000_000_000},
    }

    custom_row = DatasetSnapshotRow(
        timestamp=row.timestamp,
        run_id="engine-eligibility-breakdown",
        market=market,
        derivatives=row.derivatives,
        account=row.account,
        forward_returns=row.forward_returns,
        meta=row.meta,
    )

    result = run_long_gate_telemetry_experiment([custom_row], evaluation_window="3d")
    counts = result["engines"]["trend_long"]["filter_counts"]

    assert counts["eligibility_filtered"] >= 3
    assert counts["eligibility_liquidity_tier_filtered"] >= 1
    assert counts["eligibility_daily_return_filtered"] >= 1
    assert counts["eligibility_h4_return_filtered"] >= 1
    assert counts["eligibility_pretrend_filtered"] == 0



def test_long_gate_telemetry_outputs_symbol_and_regime_breakdowns() -> None:
    rows = [_bullish_ablation_row(), _bearish_short_row()]

    result = run_long_gate_telemetry_experiment(rows, evaluation_window="3d")

    assert set(result["regime_breakdown"]) == {"RISK_ON_TREND", "RISK_OFF"}
    assert result["regime_breakdown"]["RISK_ON_TREND"]["snapshot_count"] == 1
    assert result["regime_breakdown"]["RISK_OFF"]["snapshot_count"] == 1
    assert result["regime_breakdown"]["RISK_ON_TREND"]["engines"]["trend_long"]["funnel"]["accepted_allocations"] > 0
    assert result["regime_breakdown"]["RISK_OFF"]["engines"]["trend_long"]["filter_counts"]["trend_filtered"] > 0

    trend_symbols = result["symbol_breakdown"]["trend_long"]
    assert trend_symbols["BTCUSDT"]["snapshot_count"] == 2
    assert trend_symbols["BTCUSDT"]["filter_counts"]["selected"] > 0
    assert trend_symbols["BTCUSDT"]["filter_counts"]["trend_filtered"] > 0
    assert trend_symbols["BTCUSDT"]["funnel"]["raw_candidates"] > 0


@pytest.mark.parametrize(
    ("source", "match"),
    [
        ({123: {"snapshot_count": 1, "funnel": {}, "filter_counts": {}}}, r"^symbol_breakdown key must be a string$"),
        ({"BTCUSDT": [("snapshot_count", 1), ("funnel", {}), ("filter_counts", {})]}, r"^symbol_breakdown\.BTCUSDT must be an object$"),
        ({"BTCUSDT": {"snapshot_count": 1, "funnel": [("raw_candidates", 1)], "filter_counts": {}}}, r"^symbol_breakdown\.BTCUSDT\.funnel must be an object$"),
        ({"BTCUSDT": {"snapshot_count": 1, "funnel": {}, "filter_counts": [("selected", 1)]}}, r"^symbol_breakdown\.BTCUSDT\.filter_counts must be an object$"),
    ],
)
def test_long_gate_telemetry_rejects_non_mapping_symbol_breakdown_shapes(
    source: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._merge_symbol_breakdown({}, source)


def test_long_gate_telemetry_rejects_present_non_string_regime_label() -> None:
    row = _bullish_ablation_row()
    row.meta["regime_override"]["label"] = True

    with pytest.raises(ValueError, match=r"^regime\.label must be a string when present$"):
        run_long_gate_telemetry_experiment([row], evaluation_window="3d")


def test_long_gate_telemetry_rejects_non_string_symbol_rows_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def traced_with_invalid_symbol_key(_row):
        return {
            "input_universe": 1,
            "candidates": [],
            "filter_counts": {},
            "symbol_rows": {
                123: {"snapshot_count": 1, "funnel": {"raw_candidates": 1}, "filter_counts": {"selected": 1}},
            },
        }

    monkeypatch.setattr(backtest_experiments, "_trend_candidates_with_trace", traced_with_invalid_symbol_key)

    with pytest.raises(ValueError, match=r"^symbol_rows key must be a string$"):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_long_gate_telemetry_rejects_non_mapping_traced_filter_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    def traced_with_invalid_filter_counts(_row):
        return {
            "input_universe": 0,
            "candidates": [],
            "filter_counts": [("selected", 1)],
            "symbol_rows": {},
        }

    monkeypatch.setattr(backtest_experiments, "_trend_candidates_with_trace", traced_with_invalid_filter_counts)

    with pytest.raises(ValueError, match=r"^traced\.filter_counts must be an object$"):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("symbol_rows", "match"),
    [
        ([("BTCUSDT", {"snapshot_count": 1, "funnel": {}, "filter_counts": {}})], r"^symbol_rows must be an object$"),
        ({"BTCUSDT": [("snapshot_count", 1), ("funnel", {}), ("filter_counts", {})]}, r"^symbol_rows\.BTCUSDT must be an object$"),
        ({"BTCUSDT": {"snapshot_count": 1, "funnel": [("raw_candidates", 1)], "filter_counts": {}}}, r"^symbol_rows\.BTCUSDT\.funnel must be an object$"),
        ({"BTCUSDT": {"snapshot_count": 1, "funnel": {}, "filter_counts": [("selected", 1)]}}, r"^symbol_rows\.BTCUSDT\.filter_counts must be an object$"),
    ],
)
def test_long_gate_telemetry_rejects_non_mapping_symbol_row_shapes(
    monkeypatch: pytest.MonkeyPatch,
    symbol_rows: object,
    match: str,
) -> None:
    def traced_with_invalid_symbol_rows(_row):
        return {
            "input_universe": 1,
            "candidates": [],
            "filter_counts": {},
            "symbol_rows": symbol_rows,
        }

    monkeypatch.setattr(backtest_experiments, "_trend_candidates_with_trace", traced_with_invalid_symbol_rows)

    with pytest.raises(ValueError, match=match):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("candidates", "match"),
    [
        ("BTCUSDT", r"^candidates must be a list$"),
        ([object()], r"^candidates\[0\] must be an object$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_trace_candidate_shape(
    monkeypatch: pytest.MonkeyPatch,
    candidates: object,
    match: str,
) -> None:
    def traced_with_invalid_candidates(_row):
        return {
            "input_universe": 1,
            "candidates": candidates,
            "filter_counts": {},
            "symbol_rows": {},
        }

    monkeypatch.setattr(backtest_experiments, "_trend_candidates_with_trace", traced_with_invalid_candidates)

    with pytest.raises(ValueError, match=match):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize("input_universe", [True, "1", 1.5])
def test_long_gate_telemetry_rejects_invalid_trace_input_universe(
    monkeypatch: pytest.MonkeyPatch,
    input_universe: object,
) -> None:
    def traced_with_invalid_input_universe(_row):
        return {
            "input_universe": input_universe,
            "candidates": [],
            "filter_counts": {},
            "symbol_rows": {},
        }

    monkeypatch.setattr(backtest_experiments, "_trend_candidates_with_trace", traced_with_invalid_input_universe)

    with pytest.raises(ValueError, match=r"^input_universe must be an integer$"):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_long_gate_telemetry_rejects_non_string_validated_candidate_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def pipeline_with_invalid_validated_symbol(*_args, **_kwargs):
        return {
            "funnel": {
                "input_universe": 1,
                "raw_candidates": 1,
                "validated_candidates": 1,
                "allocation_decisions": 0,
                "accepted_allocations": 0,
            },
            "validated_candidates": [{"symbol": 123}],
            "allocation_rows": [],
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_validated_symbol)

    with pytest.raises(ValueError, match=r"^validated_candidates\[0\]\.symbol must be a string$"):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize("funnel", [[("raw_candidates", 1)], object()])
def test_long_gate_telemetry_rejects_non_mapping_pipeline_funnel(
    monkeypatch: pytest.MonkeyPatch,
    funnel: object,
) -> None:
    def pipeline_with_invalid_funnel(*_args, **_kwargs):
        return {
            "funnel": funnel,
            "validated_candidates": [],
            "allocation_rows": [],
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_funnel)

    with pytest.raises(ValueError, match=r"^pipeline\.funnel must be an object$"):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("counter", "value", "match"),
    [
        ("raw_candidates", True, r"^pipeline\.funnel\.raw_candidates must be an integer$"),
        ("raw_candidates", "1", r"^pipeline\.funnel\.raw_candidates must be an integer$"),
        ("raw_candidates", 1.0, r"^pipeline\.funnel\.raw_candidates must be an integer$"),
        ("accepted_allocations", True, r"^pipeline\.funnel\.accepted_allocations must be an integer$"),
        ("accepted_allocations", "1", r"^pipeline\.funnel\.accepted_allocations must be an integer$"),
        ("accepted_allocations", 1.0, r"^pipeline\.funnel\.accepted_allocations must be an integer$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_pipeline_funnel_counter(
    monkeypatch: pytest.MonkeyPatch,
    counter: str,
    value: object,
    match: str,
) -> None:
    funnel = {
        "input_universe": 1,
        "raw_candidates": 1,
        "validated_candidates": 0,
        "allocation_decisions": 0,
        "accepted_allocations": 0,
        counter: value,
    }

    def pipeline_with_invalid_funnel_counter(*_args, **_kwargs):
        return {
            "funnel": funnel,
            "validated_candidates": [],
            "allocation_rows": [],
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_funnel_counter)

    with pytest.raises(ValueError, match=match):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_long_gate_telemetry_rejects_non_string_trend_symbol_key() -> None:
    row = _bullish_ablation_row()
    row.market["symbols"] = {123: row.market["symbols"]["BTCUSDT"]}

    with pytest.raises(ValueError, match=r"market\.symbols key must be a string"):
        backtest_experiments._trend_candidates_with_trace(row)


def test_long_gate_telemetry_rejects_non_string_trend_candidate_symbol(monkeypatch) -> None:
    row = _bullish_ablation_row()
    original_symbol_key = backtest_experiments.trend_signals._market_symbol_key

    def patched_symbol_key(symbol):
        if symbol == "BTCUSDT":
            return 123
        return original_symbol_key(symbol)

    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})
    monkeypatch.setattr(backtest_experiments.trend_signals, "_market_symbol_key", patched_symbol_key)

    with pytest.raises(ValueError, match=r"^candidates\[0\]\.symbol must be a string$"):
        backtest_experiments._trend_candidates_with_trace(row)


def test_long_gate_telemetry_rejects_non_string_rotation_symbol_key() -> None:
    row = _supportive_soft_long_gate_row()
    row.market["symbols"] = {
        "BTCUSDT": row.market["symbols"]["BTCUSDT"],
        "ETHUSDT": row.market["symbols"]["ETHUSDT"],
        123: row.market["symbols"]["LINKUSDT"],
    }

    with pytest.raises(ValueError, match=r"market\.symbols key must be a string"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


def _supportive_soft_long_gate_row(regime_label: str = "RISK_ON_ROTATION") -> DatasetSnapshotRow:
    market = {
        "symbols": {
            "BTCUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 100.0, "ema_20": 99.2, "ema_50": 98.5, "atr_pct": 0.032, "return_pct_7d": 0.012, "volume_usdt_24h": 20_000_000_000},
                "4h": {"close": 100.0, "ema_20": 99.4, "ema_50": 98.7, "return_pct_3d": 0.004, "volume_usdt_24h": 20_000_000_000},
                "1h": {"close": 100.0, "ema_20": 99.6, "ema_50": 99.0, "return_pct_24h": 0.001, "volume_usdt_24h": 20_000_000_000},
            },
            "ETHUSDT": {
                "sector": "majors",
                "liquidity_tier": "top",
                "daily": {"close": 101.0, "ema_20": 100.1, "ema_50": 99.4, "atr_pct": 0.033, "return_pct_7d": 0.011, "volume_usdt_24h": 12_000_000_000},
                "4h": {"close": 101.0, "ema_20": 100.3, "ema_50": 99.7, "return_pct_3d": 0.003, "volume_usdt_24h": 12_000_000_000},
                "1h": {"close": 101.0, "ema_20": 100.5, "ema_50": 100.0, "return_pct_24h": 0.001, "volume_usdt_24h": 12_000_000_000},
            },
            "LINKUSDT": {
                "sector": "oracle",
                "liquidity_tier": "high",
                "daily": {"close": 100.5, "ema_20": 99.5, "ema_50": 100.0, "atr_pct": 0.055, "return_pct_7d": 0.061, "volume_usdt_24h": 1_450_000_000},
                "4h": {"close": 101.0, "ema_20": 100.1, "ema_50": 99.0, "return_pct_3d": 0.024, "volume_usdt_24h": 1_450_000_000},
                "1h": {"close": 101.0, "ema_20": 100.6, "ema_50": 100.0, "return_pct_24h": 0.008, "volume_usdt_24h": 1_450_000_000},
            },
        }
    }
    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 16, tzinfo=UTC),
        run_id=f"soft-long-gate-{regime_label.lower()}",
        market=market,
        derivatives=[
            {
                "symbol": "LINKUSDT",
                "funding_rate": 0.00002,
                "open_interest_usdt": 1_300_000_000,
                "open_interest_change_24h_pct": 0.006,
                "mark_price_change_24h_pct": 0.009,
                "taker_buy_sell_ratio": 1.02,
                "basis_bps": 8,
            }
        ],
        account=_engine_account(),
        forward_returns={"3d": 0.018},
        meta={
            "candidate_forward_returns": {
                "trend": {"LINKUSDT": 0.018},
                "rotation": {"LINKUSDT": 0.026},
            },
            "regime_override": {
                "label": regime_label,
                "confidence": 0.78,
                "risk_multiplier": 0.9,
                "execution_policy": "normal",
                "bucket_targets": {"trend": 0.45, "rotation": 0.45, "short": 0.1},
                "suppression_rules": [],
            },
        },
    )


def test_long_gate_telemetry_reflects_supportive_soft_long_selection() -> None:
    supportive = run_long_gate_telemetry_experiment([_supportive_soft_long_gate_row()], evaluation_window="3d")
    defensive = run_long_gate_telemetry_experiment([_supportive_soft_long_gate_row("RISK_OFF")], evaluation_window="3d")

    assert supportive["engines"]["trend_long"]["filter_counts"]["selected"] == 1
    assert supportive["engines"]["rotation_long"]["filter_counts"]["selected"] == 1
    assert supportive["symbol_breakdown"]["trend_long"]["LINKUSDT"]["filter_counts"]["selected"] == 1
    assert supportive["symbol_breakdown"]["rotation_long"]["LINKUSDT"]["filter_counts"]["selected"] == 1
    assert defensive["engines"]["trend_long"]["filter_counts"]["selected"] == 0
    assert defensive["engines"]["rotation_long"]["filter_counts"]["selected"] == 0



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


@pytest.mark.parametrize("input_universe", [True, "1", 1.5])
def test_allocator_friction_experiment_rejects_invalid_engine_only_input_universe(
    monkeypatch: pytest.MonkeyPatch,
    input_universe: object,
) -> None:
    def invalid_engine_only_candidates(_row, *, engine: str):
        return {
            "regime": _bullish_ablation_row().meta["regime_override"],
            "input_universe": input_universe,
            "candidates": [],
            "filter_counts": {},
        }

    monkeypatch.setattr(backtest_experiments, "_engine_only_candidates", invalid_engine_only_candidates)

    with pytest.raises(ValueError, match=r"^engine_only\.input_universe must be an integer$"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("candidates", "match"),
    [
        ("BTCUSDT", r"^engine_only\.candidates must be a list$"),
        ([object()], r"^engine_only\.candidates\[0\] must be an object$"),
    ],
)
def test_allocator_friction_experiment_rejects_invalid_engine_only_candidate_shape(
    monkeypatch: pytest.MonkeyPatch,
    candidates: object,
    match: str,
) -> None:
    def invalid_engine_only_candidates(_row, *, engine: str):
        return {
            "regime": _bullish_ablation_row().meta["regime_override"],
            "input_universe": 1,
            "candidates": candidates,
            "filter_counts": {},
        }

    monkeypatch.setattr(backtest_experiments, "_engine_only_candidates", invalid_engine_only_candidates)

    with pytest.raises(ValueError, match=match):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize("regime", [[("label", "RISK_ON_TREND")], "RISK_ON_TREND"])
def test_allocator_friction_experiment_rejects_invalid_candidate_bundle_regime(
    monkeypatch: pytest.MonkeyPatch,
    regime: object,
) -> None:
    def invalid_all_engine_candidates(_row):
        return {
            "regime": regime,
            "input_universe": 0,
            "candidates": [],
        }

    monkeypatch.setattr(backtest_experiments, "_all_engine_candidates", invalid_all_engine_candidates)

    with pytest.raises(ValueError, match=r"^candidate_bundle\.regime must be an object$"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize("invalid_budget", [True, float("nan"), float("inf")])
def test_allocator_friction_experiment_rejects_invalid_present_final_risk_budget(
    monkeypatch: pytest.MonkeyPatch,
    invalid_budget: object,
) -> None:
    def invalid_allocation_rows(_account, _validated_candidates, _regime, *, app_config=None):
        return [
            {
                "symbol": "BTCUSDT",
                "engine": "trend",
                "status": "ACCEPTED",
                "final_risk_budget": invalid_budget,
            }
        ]

    monkeypatch.setattr(backtest_experiments, "_allocation_rows", invalid_allocation_rows)

    with pytest.raises(ValueError, match=r"allocations\[0\]\.final_risk_budget"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    "field,invalid_value",
    [
        ("engine", 123),
        ("symbol", True),
        ("status", True),
    ],
)
def test_allocator_friction_experiment_rejects_non_string_allocation_identity_status(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid_value: object,
) -> None:
    def invalid_allocation_rows(_account, _validated_candidates, _regime, *, app_config=None):
        allocation = {
            "symbol": "BTCUSDT",
            "engine": "trend",
            "status": "ACCEPTED",
            "final_risk_budget": 10.0,
        }
        allocation[field] = invalid_value
        return [allocation]

    monkeypatch.setattr(backtest_experiments, "_allocation_rows", invalid_allocation_rows)

    with pytest.raises(ValueError, match=rf"allocations\[0\]\.{field} must be a string"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    "field,invalid_value",
    [
        ("engine", 123),
        ("symbol", True),
        ("status", True),
    ],
)
def test_engine_filter_ablation_rejects_non_string_allocation_identity_status(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid_value: object,
) -> None:
    def invalid_allocation_rows(_account, _validated_candidates, _regime, *, app_config=None):
        allocation = {
            "symbol": "BTCUSDT",
            "engine": "trend",
            "status": "ACCEPTED",
            "final_risk_budget": 10.0,
        }
        allocation[field] = invalid_value
        return [allocation]

    monkeypatch.setattr(backtest_experiments, "_allocation_rows", invalid_allocation_rows)

    with pytest.raises(ValueError, match=rf"allocations\[0\]\.{field} must be a string"):
        run_engine_filter_ablation_experiment([_bullish_ablation_row()], evaluation_window="3d")


def _walk_forward_rows() -> list[DatasetSnapshotRow]:
    return [
        _suppressed_rotation_row(2, link_return=0.04, ada_return=-0.01, forward_return_3d=0.04),
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.06),
        _suppressed_rotation_row(1, link_return=0.05, ada_return=-0.02, forward_return_3d=0.05),
        _suppressed_rotation_row(5, link_return=0.02, ada_return=-0.01, forward_return_3d=0.02),
        _suppressed_rotation_row(3, link_return=0.03, ada_return=-0.005, forward_return_3d=0.03),
        _suppressed_rotation_row(4, link_return=0.05, ada_return=-0.015, forward_return_3d=0.05),
    ]


def _walk_forward_robustness_rows() -> list[DatasetSnapshotRow]:
    returns = [0.06, 0.05, 0.04, -0.01, 0.03, 0.02, -0.02, 0.01]
    return [
        _suppressed_rotation_row(
            index,
            link_return=0.04,
            ada_return=-0.01,
            forward_return_3d=forward_return,
        )
        for index, forward_return in enumerate(returns)
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


def test_walk_forward_outputs_robustness_summary_and_parameter_stability() -> None:
    rows = _walk_forward_robustness_rows()

    result = backtest_experiments.run_walk_forward_validation_experiment(
        rows,
        evaluation_window="3d",
        in_sample_size=2,
        out_of_sample_size=2,
        step_size=2,
    )

    assert result["metadata"]["window_count"] == 3

    robustness = result["robustness_summary"]
    assert robustness["in_sample_scorecard"]["total_return"] == pytest.approx(0.20393)
    assert robustness["out_of_sample_scorecard"]["total_return"] == pytest.approx(0.070664)
    assert robustness["out_of_sample_scorecard"]["trade_count"] == 3
    assert robustness["performance_dispersion"]["window_count"] == 3
    assert robustness["performance_dispersion"]["positive_window_ratio"] == pytest.approx(2 / 3)
    assert robustness["performance_dispersion"]["average_out_of_sample_return"] == pytest.approx(0.023333)
    assert robustness["performance_dispersion"]["return_std_dev"] == pytest.approx(0.025214, abs=1e-6)
    assert robustness["performance_dispersion"]["best_window_total_return"] == pytest.approx(0.0506)
    assert robustness["performance_dispersion"]["worst_window_total_return"] == pytest.approx(-0.0102)
    assert robustness["worst_window"] == {
        "window_index": 3,
        "start_timestamp": "2026-03-16T00:00:00+00:00",
        "end_timestamp": "2026-03-17T00:00:00+00:00",
        "scorecard": result["windows"][2]["out_of_sample"]["scorecard"],
    }

    parameter_stability = result["parameter_stability"]
    assert parameter_stability["edge_retention_ratio"] == pytest.approx(0.362319, abs=1e-6)
    assert parameter_stability["worst_window_retention_ratio"] == pytest.approx(-0.158385, abs=1e-6)
    assert parameter_stability["positive_window_ratio"] == pytest.approx(2 / 3)
    assert parameter_stability["parameter_stability_score"] == pytest.approx(0.342995, abs=1e-6)
    assert set(parameter_stability["sensitivity_bands"]) == {
        "out_of_sample_total_return",
        "out_of_sample_sharpe",
        "out_of_sample_calmar",
    }
    total_return_band = parameter_stability["sensitivity_bands"]["out_of_sample_total_return"]
    assert total_return_band["min"] == pytest.approx(-0.0102)
    assert total_return_band["median"] == pytest.approx(0.0296)
    assert total_return_band["max"] == pytest.approx(0.0506)
    sharpe_band = parameter_stability["sensitivity_bands"]["out_of_sample_sharpe"]
    assert sharpe_band["min"] < 0.0
    assert sharpe_band["median"] > 0.0
    assert sharpe_band["max"] > sharpe_band["median"]
    calmar_band = parameter_stability["sensitivity_bands"]["out_of_sample_calmar"]
    assert calmar_band["min"] < 0.0
    assert calmar_band["max"] >= 0.0
