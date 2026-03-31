from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from trading_system.app.backtest.experiments import (
    run_engine_filter_ablation_experiment,
    run_rotation_suppression_experiment,
)
from trading_system.app.backtest.types import DatasetSnapshotRow


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


def _suppressed_rotation_row(index: int, *, link_return: float, ada_return: float) -> DatasetSnapshotRow:
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
        forward_returns={"3d": 0.01},
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
