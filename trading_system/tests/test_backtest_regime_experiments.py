from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.app.backtest.experiments import _mean_mapping, run_regime_predictive_power_experiment
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


def _override_row(index: int, regime_override: dict[str, object]) -> DatasetSnapshotRow:
    row = _row(index, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.meta["regime_override"] = regime_override
    return row


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


@pytest.mark.parametrize("invalid_label", [True, 123, []])
def test_regime_predictive_power_experiment_rejects_present_non_string_regime_label(invalid_label: object) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.meta["regime_override"] = {"label": invalid_label}

    with pytest.raises(ValueError, match=r"^regime\.label must be a string when present$"):
        run_regime_predictive_power_experiment([row])


@pytest.mark.parametrize("regime_override", [{}, {"label": None}])
def test_regime_predictive_power_experiment_defaults_missing_regime_label_to_unknown(
    regime_override: dict[str, object],
) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.meta["regime_override"] = regime_override

    result = run_regime_predictive_power_experiment([row])

    assert set(result["by_regime"]) == {"UNKNOWN"}
    assert set(result["duration_stats"]) == {"UNKNOWN"}


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("confidence", True),
        ("confidence", "0.8"),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("risk_multiplier", True),
        ("risk_multiplier", "0.8"),
        ("risk_multiplier", float("nan")),
        ("risk_multiplier", float("inf")),
    ],
)
def test_regime_predictive_power_experiment_rejects_present_invalid_regime_numbers(
    field: str,
    invalid_value: object,
) -> None:
    regime = {"label": "RISK_ON_TREND", "confidence": 0.8, "risk_multiplier": 0.92}
    regime[field] = invalid_value

    with pytest.raises(ValueError, match=rf"regime\.{field} must be a finite number"):
        run_regime_predictive_power_experiment([_override_row(0, regime)])


@pytest.mark.parametrize(
    "regime_override",
    [
        {"label": "RISK_ON_TREND", "confidence": 1, "risk_multiplier": 1},
        {"label": "RISK_ON_TREND", "confidence": 0.8, "risk_multiplier": 0.92},
        {"label": "RISK_ON_TREND"},
        {"label": "RISK_ON_TREND", "confidence": None, "risk_multiplier": None},
    ],
)
def test_regime_predictive_power_experiment_preserves_valid_and_default_regime_numbers(
    regime_override: dict[str, object],
) -> None:
    result = run_regime_predictive_power_experiment([_override_row(0, regime_override)])

    assert result["metadata"] == {"snapshot_count": 1, "regime_count": 1}


@pytest.mark.parametrize("invalid_value", [True, "0.01", float("nan"), float("inf")])
def test_regime_predictive_power_experiment_rejects_invalid_forward_return_value(
    invalid_value: object,
) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.forward_returns["3d"] = invalid_value  # type: ignore[assignment]

    with pytest.raises(ValueError, match=r"^forward_returns\.3d must be a finite number$"):
        run_regime_predictive_power_experiment([row])


@pytest.mark.parametrize("invalid_value", [True, "0.02", float("nan"), float("inf")])
def test_regime_predictive_power_experiment_rejects_invalid_aggregated_forward_return_value(
    invalid_value: object,
) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.forward_returns["7d"] = invalid_value  # type: ignore[assignment]

    with pytest.raises(ValueError, match=r"^forward_returns\.7d must be a finite number$"):
        run_regime_predictive_power_experiment([row])


@pytest.mark.parametrize("invalid_value", [True, "-0.01", float("nan"), float("inf")])
def test_regime_predictive_power_experiment_rejects_invalid_forward_drawdown_value(
    invalid_value: object,
) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.forward_drawdowns["3d"] = invalid_value  # type: ignore[assignment]

    with pytest.raises(ValueError, match=r"^forward_drawdowns\.3d must be a finite number$"):
        run_regime_predictive_power_experiment([row])


@pytest.mark.parametrize("invalid_value", [True, "-0.01", float("nan"), float("inf")])
def test_regime_predictive_power_experiment_rejects_invalid_aggregated_forward_drawdown_value(
    invalid_value: object,
) -> None:
    row = _row(0, risk_on=True, forward_1d=0.018, forward_3d=0.042, drawdown_3d=-0.01)
    row.forward_drawdowns["7d"] = invalid_value  # type: ignore[assignment]

    with pytest.raises(ValueError, match=r"^forward_drawdowns\.7d must be a finite number$"):
        run_regime_predictive_power_experiment([row])


def test_regime_predictive_power_experiment_preserves_valid_forward_metrics_and_missing_windows() -> None:
    row = _row(0, risk_on=True, forward_1d=1, forward_3d=0.042, drawdown_3d=-1)
    row.forward_returns.pop("1d")
    row.forward_drawdowns.pop("3d")

    result = run_regime_predictive_power_experiment([row])

    assert result["by_regime"]["RISK_ON_TREND"]["forward_return_by_window"]["3d"] == 0.042
    assert result["by_regime"]["RISK_ON_TREND"]["forward_drawdown_by_window"] == {}


@pytest.mark.parametrize("invalid_value", [True, "0.02", float("nan"), float("inf")])
def test_mean_mapping_rejects_present_invalid_values(invalid_value: object) -> None:
    with pytest.raises(ValueError, match=r"^forward_returns\.7d must be a finite number$"):
        _mean_mapping(
            [{"7d": 0.03}, {"7d": invalid_value}],  # type: ignore[list-item]
            path="forward_returns",
        )


def test_mean_mapping_preserves_missing_default_and_valid_numeric_values() -> None:
    assert _mean_mapping([{"7d": 1}, {}], path="forward_returns") == {"7d": 0.5}



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
