from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_system.app.backtest.experiments import run_rotation_suppression_experiment
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
