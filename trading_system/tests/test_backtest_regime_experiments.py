from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_system.app.backtest.experiments import run_regime_predictive_power_experiment
from trading_system.app.backtest.reporting import render_regime_scorecard
from trading_system.app.backtest.types import DatasetSnapshotRow


def _regime_market(*, risk_on: bool) -> dict[str, object]:
    if risk_on:
        return {
            "symbols": {
                "BTCUSDT": {
                    "daily": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "atr_pct": 0.035, "return_pct_7d": 0.05},
                    "4h": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "return_pct_3d": 0.02},
                    "1h": {"close": 101.0, "ema_20": 100.2, "ema_50": 99.8, "return_pct_24h": 0.006},
                },
                "ETHUSDT": {
                    "daily": {"close": 102.0, "ema_20": 100.5, "ema_50": 99.5, "atr_pct": 0.034, "return_pct_7d": 0.048},
                    "4h": {"close": 102.0, "ema_20": 100.4, "ema_50": 99.6, "return_pct_3d": 0.019},
                    "1h": {"close": 102.0, "ema_20": 100.5, "ema_50": 99.7, "return_pct_24h": 0.005},
                },
                "SOLUSDT": {
                    "sector": "alt_l1",
                    "daily": {"close": 152.0, "ema_20": 145.0, "ema_50": 138.0, "atr_pct": 0.05, "return_pct_7d": 0.08},
                    "4h": {"close": 152.0, "ema_20": 149.0, "ema_50": 144.0, "return_pct_3d": 0.028},
                    "1h": {"close": 152.0, "ema_20": 150.0, "ema_50": 148.0, "return_pct_24h": 0.01},
                },
            }
        }
    return {
        "symbols": {
            "BTCUSDT": {
                "daily": {"close": 96.0, "ema_20": 98.0, "ema_50": 100.0, "atr_pct": 0.04, "return_pct_7d": -0.04},
                "4h": {"close": 96.0, "ema_20": 97.0, "ema_50": 99.0, "return_pct_3d": -0.02},
                "1h": {"close": 96.0, "ema_20": 96.5, "ema_50": 97.0, "return_pct_24h": -0.008},
            },
            "ETHUSDT": {
                "daily": {"close": 95.0, "ema_20": 97.0, "ema_50": 99.0, "atr_pct": 0.041, "return_pct_7d": -0.045},
                "4h": {"close": 95.0, "ema_20": 96.0, "ema_50": 98.0, "return_pct_3d": -0.018},
                "1h": {"close": 95.0, "ema_20": 95.8, "ema_50": 96.4, "return_pct_24h": -0.007},
            },
            "SOLUSDT": {
                "sector": "alt_l1",
                "daily": {"close": 132.0, "ema_20": 136.0, "ema_50": 140.0, "atr_pct": 0.055, "return_pct_7d": -0.06},
                "4h": {"close": 132.0, "ema_20": 134.0, "ema_50": 138.0, "return_pct_3d": -0.024},
                "1h": {"close": 132.0, "ema_20": 133.0, "ema_50": 134.0, "return_pct_24h": -0.01},
            },
        }
    }


def _regime_derivatives(*, crowded_short: bool) -> list[dict[str, object]]:
    ratio = 0.92 if crowded_short else 1.05
    oi_change = -0.05 if crowded_short else 0.04
    rows = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        rows.append(
            {
                "symbol": symbol,
                "funding_rate": -0.00008 if crowded_short else 0.00012,
                "open_interest_usdt": 1_000_000_000,
                "open_interest_change_24h_pct": oi_change,
                "mark_price_change_24h_pct": -0.03 if crowded_short else 0.02,
                "taker_buy_sell_ratio": ratio,
                "basis_bps": 8 if crowded_short else 18,
            }
        )
    return rows


def _row(index: int, *, risk_on: bool, forward_1d: float, forward_3d: float, drawdown_3d: float) -> DatasetSnapshotRow:
    return DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 10, tzinfo=UTC) + timedelta(days=index),
        run_id=f"row-{index}",
        market=_regime_market(risk_on=risk_on),
        derivatives=_regime_derivatives(crowded_short=not risk_on),
        account={"equity": 100_000.0, "available_balance": 100_000.0, "futures_wallet_balance": 100_000.0},
        forward_returns={"1d": forward_1d, "3d": forward_3d},
        forward_drawdowns={"3d": drawdown_3d},
    )


def test_regime_predictive_power_experiment_emits_expected_sections() -> None:
    rows = [
        _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01),
        _row(1, risk_on=True, forward_1d=0.016, forward_3d=0.039, drawdown_3d=-0.012),
        _row(2, risk_on=False, forward_1d=-0.012, forward_3d=-0.027, drawdown_3d=-0.035),
        _row(3, risk_on=False, forward_1d=-0.009, forward_3d=-0.021, drawdown_3d=-0.03),
    ]

    result = run_regime_predictive_power_experiment(rows)

    assert "by_regime" in result
    assert "duration_stats" in result
    assert "confidence_aggression_summary" in result
    assert result["by_regime"]["RISK_ON_TREND"]["forward_return_by_window"]["3d"] > 0
    assert result["by_regime"]["RISK_OFF"]["forward_drawdown_by_window"]["3d"] < 0


def test_regime_scorecard_rendering(tmp_path: Path) -> None:
    rows = [
        _row(0, risk_on=True, forward_1d=0.02, forward_3d=0.05, drawdown_3d=-0.012),
        _row(1, risk_on=False, forward_1d=-0.01, forward_3d=-0.025, drawdown_3d=-0.03),
    ]
    experiment = run_regime_predictive_power_experiment(rows)

    scorecard = render_regime_scorecard(
        experiment_name="regime-predictive-power",
        experiment=experiment,
        metadata={
            "dataset_root": str(tmp_path / "dataset"),
            "baseline_name": "current_policy",
            "variant_name": "no_rotation_suppression",
            "sample_period": "2026-03-10 → 2026-03-11",
        },
    )

    assert set(scorecard) == {"metadata", "key_metrics", "decision_summary", "promotion_gate"}
    assert scorecard["metadata"]["experiment_name"] == "regime-predictive-power"
    assert "status" in scorecard["promotion_gate"]
    assert scorecard["decision_summary"]["summary"]
