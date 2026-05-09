from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
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


@pytest.mark.parametrize("strategy_family", [123, True])
def test_public_strategy_factor_experiment_rejects_non_string_strategy_families(strategy_family: object) -> None:
    rows = [_suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01)]

    with pytest.raises(ValueError, match=r"^strategy_families\[0\] must be a string$"):
        run_public_strategy_factor_experiment(
            rows,
            evaluation_window="3d",
            strategy_families=(strategy_family,),
        )


def test_public_strategy_factor_experiment_preserves_unknown_string_strategy_family_identity() -> None:
    rows = [_suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01)]

    result = run_public_strategy_factor_experiment(
        rows,
        evaluation_window="3d",
        strategy_families=("custom_family",),
    )

    factor = result["factors"][0]
    assert factor["source_strategy_family"] == "custom_family"
    assert factor["factor_name"] == "custom_family"
    assert factor["supported"] is False
    assert factor["unsupported_reason"] == "unknown_strategy_family"


@pytest.mark.parametrize("supported", ["false", 1])
def test_public_strategy_factor_experiment_rejects_non_bool_family_spec_supported(
    monkeypatch: pytest.MonkeyPatch,
    supported: object,
) -> None:
    def has_forward_window(_rows, _evaluation_window):
        return supported

    monkeypatch.setattr(backtest_experiments, "_has_forward_window", has_forward_window)

    with pytest.raises(ValueError, match=r"^family_specs\.momentum\.supported must be a bool$"):
        run_public_strategy_factor_experiment(
            [_suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01)],
            evaluation_window="3d",
            strategy_families=("momentum",),
        )


def test_public_strategy_factor_experiment_skips_missing_daily_factor_fields() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    for row in rows:
        for symbol in row.market["symbols"].values():
            del symbol["daily"]["return_pct_7d"]

    result = run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))

    momentum = result["factors"][0]
    assert momentum["supported"] is True
    assert "effectiveness" not in momentum
    assert result["summary"]["evaluated_factor_count"] == 0


@pytest.mark.parametrize("invalid_factor_value", [True, "0.05", math.nan, math.inf, -math.inf])
def test_public_strategy_factor_experiment_rejects_invalid_present_daily_return_factor_fields(
    invalid_factor_value: object,
) -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    rows[0].market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = invalid_factor_value

    with pytest.raises(ValueError, match=r"^BTCUSDT\.daily\.return_pct_7d must be a finite number$"):
        run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))


@pytest.mark.parametrize("invalid_factor_value", [True, "0.05", math.nan, math.inf, -math.inf])
def test_public_strategy_factor_experiment_rejects_invalid_present_daily_atr_factor_fields(
    invalid_factor_value: object,
) -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    for row in rows:
        row.forward_drawdowns["3d"] = -0.01
    rows[0].market["symbols"]["BTCUSDT"]["daily"]["atr_pct"] = invalid_factor_value

    with pytest.raises(ValueError, match=r"^BTCUSDT\.daily\.atr_pct must be a finite number$"):
        run_public_strategy_factor_experiment(
            rows,
            evaluation_window="3d",
            strategy_families=("volatility_breakout",),
        )


@pytest.mark.parametrize(
    ("field", "invalid_factor_value", "match"),
    [
        ("close", True, r"^BTCUSDT\.daily\.close must be a finite number$"),
        ("close", "101", r"^BTCUSDT\.daily\.close must be a finite number$"),
        ("close", math.nan, r"^BTCUSDT\.daily\.close must be a finite number$"),
        ("close", math.inf, r"^BTCUSDT\.daily\.close must be a finite number$"),
        ("ema_50", True, r"^BTCUSDT\.daily\.ema_50 must be a finite number$"),
        ("ema_50", "100", r"^BTCUSDT\.daily\.ema_50 must be a finite number$"),
        ("ema_50", math.nan, r"^BTCUSDT\.daily\.ema_50 must be a finite number$"),
        ("ema_50", math.inf, r"^BTCUSDT\.daily\.ema_50 must be a finite number$"),
    ],
)
def test_public_strategy_factor_experiment_rejects_invalid_present_trend_daily_factor_fields(
    field: str,
    invalid_factor_value: object,
    match: str,
) -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    rows[0].market["symbols"]["BTCUSDT"]["daily"][field] = invalid_factor_value

    with pytest.raises(ValueError, match=match):
        run_public_strategy_factor_experiment(
            rows,
            evaluation_window="3d",
            strategy_families=("trend_following",),
        )


@pytest.mark.parametrize("field", ["close", "ema_50"])
def test_public_strategy_factor_experiment_skips_missing_trend_daily_factor_fields(field: str) -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    for row in rows:
        for symbol in row.market["symbols"].values():
            del symbol["daily"][field]

    result = run_public_strategy_factor_experiment(
        rows,
        evaluation_window="3d",
        strategy_families=("trend_following",),
    )

    trend = result["factors"][0]
    assert trend["supported"] is True
    assert "effectiveness" not in trend
    assert result["summary"]["evaluated_factor_count"] == 0


def test_public_strategy_factor_experiment_preserves_valid_trend_daily_factor_numbers() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    rows[0].market["symbols"]["BTCUSDT"]["daily"]["close"] = 101
    rows[0].market["symbols"]["BTCUSDT"]["daily"]["ema_50"] = 100
    rows[1].market["symbols"]["BTCUSDT"]["daily"]["close"] = 102.0
    rows[1].market["symbols"]["BTCUSDT"]["daily"]["ema_50"] = 100.0

    result = run_public_strategy_factor_experiment(
        rows,
        evaluation_window="3d",
        strategy_families=("trend_following",),
    )

    trend = result["factors"][0]
    assert trend["effectiveness"]["sample_count"] == 2


def test_public_strategy_factor_experiment_preserves_valid_daily_factor_numbers() -> None:
    rows = [
        _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03, forward_return_3d=0.01),
        _suppressed_rotation_row(1, link_return=0.04, ada_return=-0.02, forward_return_3d=0.02),
    ]
    rows[0].market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 1
    rows[1].market["symbols"]["BTCUSDT"]["daily"]["return_pct_7d"] = 0.05

    result = run_public_strategy_factor_experiment(rows, evaluation_window="3d", strategy_families=("momentum",))

    momentum = result["factors"][0]
    assert momentum["effectiveness"]["sample_count"] == 2


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


@pytest.mark.parametrize("invalid_forward_return", [True, "0.01", math.nan, math.inf, -math.inf])
def test_rotation_suppression_experiment_rejects_invalid_candidate_forward_returns(
    invalid_forward_return: object,
) -> None:
    row = _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03)
    row.meta["candidate_forward_returns"]["rotation"]["LINKUSDT"] = invalid_forward_return

    with pytest.raises(
        ValueError,
        match=r"^candidate_forward_returns\.rotation\.LINKUSDT must be a finite number$",
    ):
        run_rotation_suppression_experiment([row], evaluation_window="3d", soft_score_floor=0.72)


@pytest.mark.parametrize("invalid_forward_return", [True, "0.01", math.nan, math.inf, -math.inf])
def test_rotation_suppression_experiment_rejects_invalid_fallback_forward_returns(
    invalid_forward_return: object,
) -> None:
    row = _suppressed_rotation_row(0, link_return=0.06, ada_return=-0.03)
    del row.meta["candidate_forward_returns"]["rotation"]["LINKUSDT"]
    row.forward_returns["3d"] = invalid_forward_return

    with pytest.raises(ValueError, match=r"^forward_returns\.3d must be a finite number$"):
        run_rotation_suppression_experiment([row], evaluation_window="3d", soft_score_floor=0.72)


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


@pytest.mark.parametrize("soft_score_floor", [True, "0.72", float("nan")])
def test_rotation_suppression_experiment_rejects_invalid_soft_score_floor(
    soft_score_floor: object,
) -> None:
    with pytest.raises(ValueError, match=r"^soft_score_floor must be a finite number$"):
        run_rotation_suppression_experiment(
            [_bullish_ablation_row()],
            evaluation_window="3d",
            soft_score_floor=soft_score_floor,  # type: ignore[arg-type]
        )


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


@pytest.mark.parametrize(
    ("allocation_rows", "match"),
    [
        ("BTCUSDT", r"^pipeline\.allocation_rows must be a list$"),
        ([object()], r"^pipeline\.allocation_rows\[0\] must be an object$"),
    ],
)
def test_engine_filter_ablation_rejects_invalid_pipeline_allocation_rows(
    monkeypatch: pytest.MonkeyPatch,
    allocation_rows: object,
    match: str,
) -> None:
    def pipeline_with_invalid_allocation_rows(*_args, **_kwargs):
        return {
            "funnel": {
                "input_universe": 1,
                "raw_candidates": 1,
                "validated_candidates": 0,
                "allocation_decisions": 1,
                "accepted_allocations": 0,
            },
            "validated_candidates": [],
            "allocation_rows": allocation_rows,
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_allocation_rows)

    with pytest.raises(ValueError, match=match):
        run_engine_filter_ablation_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (None, []),
        ([], []),
        (["some reason"], ["some reason"]),
    ],
)
def test_baseline_allocation_row_preserves_valid_reasons(reasons: object, expected: list[str]) -> None:
    row = backtest_experiments._baseline_allocation_row(
        {"symbol": "BTCUSDT", "engine": "trend_long", "setup_type": "breakout", "score": 1.25},
        rank=1,
        status="ACCEPTED",
        final_risk_budget=0.1234567,
        reasons=reasons,
        baseline_name="equal_weight",
    )

    assert row["reasons"] == expected


@pytest.mark.parametrize(
    ("reasons", "match"),
    [
        ("bad", r"^reasons must be a list$"),
        ([1], r"^reasons\[0\] must be a string$"),
    ],
)
def test_baseline_allocation_row_rejects_invalid_reasons(reasons: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._baseline_allocation_row(
            {"symbol": "BTCUSDT", "engine": "trend_long", "setup_type": "breakout", "score": 1.25},
            rank=1,
            status="REJECTED",
            final_risk_budget=0.0,
            reasons=reasons,
            baseline_name="equal_weight",
        )



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


def test_long_gate_telemetry_finalizes_symbol_breakdown_output() -> None:
    result = backtest_experiments._finalize_symbol_breakdown(
        {
            "BTCUSDT": {
                "snapshot_count": 2,
                "funnel": {"raw_candidates": 3},
                "filter_counts": {"selected": 1},
            }
        },
        filter_keys=("selected", "trend_filtered"),
    )

    assert result["BTCUSDT"]["snapshot_count"] == 2
    assert result["BTCUSDT"]["funnel"]["raw_candidates"] == 3
    assert result["BTCUSDT"]["funnel"]["accepted_allocations"] == 0
    assert result["BTCUSDT"]["filter_counts"] == {"selected": 1, "trend_filtered": 0}


def test_long_gate_telemetry_finalizes_engine_results_output() -> None:
    result = backtest_experiments._finalize_engine_results(
        {
            "trend_long": {
                "funnel_counts": {"raw_candidates": 3},
                "filter_counts": {"selected": 1},
                "accepted_returns": [0.1, -0.02],
            }
        },
        {
            "trend_long": {"filter_keys": ("selected", "trend_filtered")},
        },
    )

    assert result["trend_long"]["funnel"]["raw_candidates"] == 3
    assert result["trend_long"]["funnel"]["accepted_allocations"] == 0
    assert result["trend_long"]["filter_counts"] == {"selected": 1, "trend_filtered": 0}
    assert result["trend_long"]["performance"]["trade_count"] == 2


def test_long_gate_telemetry_defaults_missing_counters_and_preserves_valid_ints() -> None:
    result = backtest_experiments._with_zero_defaults(
        {"selected": 1},
        ("selected", "trend_filtered"),
        path="filter_counts",
    )

    assert result == {"selected": 1, "trend_filtered": 0}


@pytest.mark.parametrize("invalid_counter", [True, "1", 1.0])
def test_long_gate_telemetry_rejects_invalid_zero_default_counter(invalid_counter: object) -> None:
    with pytest.raises(ValueError, match=r"^filter_counts\.score_filtered must be an integer counter$"):
        backtest_experiments._with_zero_defaults(
            {"score_filtered": invalid_counter},
            ("score_filtered",),
            path="filter_counts",
        )


@pytest.mark.parametrize("invalid_counter", [True, "1", 1.0])
def test_long_gate_telemetry_rejects_invalid_merge_counter(invalid_counter: object) -> None:
    target = {"raw_candidates": 1}

    with pytest.raises(ValueError, match=r"^funnel\.raw_candidates must be an integer counter$"):
        backtest_experiments._merge_counts(
            target,
            {"raw_candidates": invalid_counter},
            path="funnel",
        )


@pytest.mark.parametrize("invalid_counter", [True, "1", 1.0])
def test_long_gate_telemetry_rejects_invalid_existing_symbol_funnel_counter(invalid_counter: object) -> None:
    symbol_rows = {
        "BTCUSDT": {
            "snapshot_count": 1,
            "funnel": {"raw_candidates": invalid_counter},
            "filter_counts": {},
        }
    }

    with pytest.raises(ValueError, match=r"^symbol_rows\.BTCUSDT\.funnel\.raw_candidates must be an integer counter$"):
        backtest_experiments._bump_symbol_funnel(symbol_rows, "BTCUSDT", "raw_candidates")


@pytest.mark.parametrize("invalid_counter", [True, "1", 1.0])
def test_long_gate_telemetry_rejects_invalid_existing_symbol_filter_counter(invalid_counter: object) -> None:
    symbol_rows = {
        "BTCUSDT": {
            "snapshot_count": 1,
            "funnel": {},
            "filter_counts": {"score_filtered": invalid_counter},
        }
    }

    with pytest.raises(
        ValueError,
        match=r"^symbol_rows\.BTCUSDT\.filter_counts\.score_filtered must be an integer counter$",
    ):
        backtest_experiments._bump_symbol_filter(symbol_rows, "BTCUSDT", "score_filtered")


def test_long_gate_telemetry_finalizes_valid_accepted_returns() -> None:
    assert backtest_experiments._finalize_returns(
        [0, 0.1, -0.02],
        path="engines.trend_long.accepted_returns",
    ) == [0.0, 0.1, -0.02]


@pytest.mark.parametrize("invalid_return", [True, "0.1"])
def test_long_gate_telemetry_rejects_non_numeric_final_accepted_returns(
    invalid_return: object,
) -> None:
    with pytest.raises(ValueError, match=r"^engines\.trend_long\.accepted_returns\[0\] must be numeric$"):
        backtest_experiments._finalize_returns(
            [invalid_return],
            path="engines.trend_long.accepted_returns",
        )


@pytest.mark.parametrize("invalid_return", [math.nan, math.inf])
def test_long_gate_telemetry_rejects_non_finite_final_accepted_returns(
    invalid_return: float,
) -> None:
    with pytest.raises(ValueError, match=r"^engines\.trend_long\.accepted_returns\[0\] must be finite$"):
        backtest_experiments._finalize_returns(
            [invalid_return],
            path="engines.trend_long.accepted_returns",
        )


@pytest.mark.parametrize(
    ("engine_payload", "match"),
    [
        (
            {"funnel_counts": [("raw_candidates", 1)], "filter_counts": {}, "accepted_returns": []},
            r"^engines\.trend_long\.funnel_counts must be an object$",
        ),
        (
            {"funnel_counts": {}, "filter_counts": [("selected", 1)], "accepted_returns": []},
            r"^engines\.trend_long\.filter_counts must be an object$",
        ),
        (
            {"funnel_counts": {}, "filter_counts": {}, "accepted_returns": "0.1"},
            r"^engines\.trend_long\.accepted_returns must be a list$",
        ),
        (
            {"funnel_counts": {}, "filter_counts": {}, "accepted_returns": ["not-a-number"]},
            r"^engines\.trend_long\.accepted_returns\[0\] must be numeric$",
        ),
    ],
)
def test_long_gate_telemetry_rejects_invalid_final_engine_results_aggregates(
    engine_payload: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._finalize_engine_results(
            {
                "trend_long": engine_payload,
            },
            {
                "trend_long": {"filter_keys": ("selected",)},
            },
        )


@pytest.mark.parametrize("snapshot_count", [True, "1", 1.5])
def test_long_gate_telemetry_rejects_invalid_final_symbol_breakdown_snapshot_count(
    snapshot_count: object,
) -> None:
    with pytest.raises(ValueError, match=r"^symbol_breakdown\.BTCUSDT\.snapshot_count must be an integer$"):
        backtest_experiments._finalize_symbol_breakdown(
            {
                "BTCUSDT": {
                    "snapshot_count": snapshot_count,
                    "funnel": {},
                    "filter_counts": {},
                }
            },
            filter_keys=("selected",),
        )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"snapshot_count": 1, "funnel": [("raw_candidates", 1)], "filter_counts": {}},
            r"^symbol_breakdown\.BTCUSDT\.funnel must be an object$",
        ),
        (
            {"snapshot_count": 1, "funnel": {}, "filter_counts": [("selected", 1)]},
            r"^symbol_breakdown\.BTCUSDT\.filter_counts must be an object$",
        ),
    ],
)
def test_long_gate_telemetry_rejects_invalid_final_symbol_breakdown_nested_maps(
    payload: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._finalize_symbol_breakdown({"BTCUSDT": payload}, filter_keys=("selected",))


def test_long_gate_telemetry_rejects_invalid_final_regime_breakdown_engines() -> None:
    with pytest.raises(ValueError, match=r"^regime_breakdown\.RISK_ON_TREND\.engines must be an object$"):
        backtest_experiments._finalize_regime_breakdown(
            {"RISK_ON_TREND": {"snapshot_count": 1, "engines": [("trend_long", {})]}},
            {
                "trend_long": {"filter_keys": ("selected",)},
            },
        )


@pytest.mark.parametrize(
    ("engine_payload", "match"),
    [
        (
            [("funnel_counts", {})],
            r"^regime_breakdown\.RISK_ON_TREND\.engines\.trend_long must be an object$",
        ),
        (
            {"funnel_counts": [("raw_candidates", 1)], "filter_counts": {}, "accepted_returns": []},
            r"^regime_breakdown\.RISK_ON_TREND\.engines\.trend_long\.funnel_counts must be an object$",
        ),
        (
            {"funnel_counts": {}, "filter_counts": [("selected", 1)], "accepted_returns": []},
            r"^regime_breakdown\.RISK_ON_TREND\.engines\.trend_long\.filter_counts must be an object$",
        ),
    ],
)
def test_long_gate_telemetry_rejects_invalid_final_regime_breakdown_engine_maps(
    engine_payload: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._finalize_regime_breakdown(
            {
                "RISK_ON_TREND": {
                    "snapshot_count": 1,
                    "engines": {"trend_long": engine_payload},
                }
            },
            {
                "trend_long": {"filter_keys": ("selected",)},
            },
        )


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


@pytest.mark.parametrize("snapshot_count", [True, "1", 1.5])
def test_long_gate_telemetry_rejects_invalid_symbol_breakdown_snapshot_count(snapshot_count: object) -> None:
    source = {"BTCUSDT": {"snapshot_count": snapshot_count, "funnel": {}, "filter_counts": {}}}

    with pytest.raises(ValueError, match=r"^symbol_breakdown\.BTCUSDT\.snapshot_count must be an integer$"):
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
        (
            {"BTCUSDT": {"snapshot_count": 1, "funnel": {123: 1}, "filter_counts": {}}},
            r"^symbol_rows\.BTCUSDT\.funnel key must be a string$",
        ),
        (
            {"BTCUSDT": {"snapshot_count": 1, "funnel": {"raw_candidates": True}, "filter_counts": {}}},
            r"^symbol_rows\.BTCUSDT\.funnel\.raw_candidates must be an integer counter$",
        ),
        (
            {"BTCUSDT": {"snapshot_count": 1, "funnel": {}, "filter_counts": {123: 1}}},
            r"^symbol_rows\.BTCUSDT\.filter_counts key must be a string$",
        ),
        (
            {"BTCUSDT": {"snapshot_count": 1, "funnel": {}, "filter_counts": {"selected": 1.0}}},
            r"^symbol_rows\.BTCUSDT\.filter_counts\.selected must be an integer counter$",
        ),
    ],
)
def test_long_gate_telemetry_rejects_invalid_symbol_row_count_maps(
    symbol_rows: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        backtest_experiments._normalize_symbol_rows(symbol_rows)


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


@pytest.mark.parametrize("snapshot_count", [True, "1", 1.5])
def test_long_gate_telemetry_rejects_invalid_symbol_row_snapshot_count(
    monkeypatch: pytest.MonkeyPatch,
    snapshot_count: object,
) -> None:
    def traced_with_invalid_symbol_row_snapshot_count(_row):
        return {
            "input_universe": 1,
            "candidates": [],
            "filter_counts": {},
            "symbol_rows": {
                "BTCUSDT": {"snapshot_count": snapshot_count, "funnel": {}, "filter_counts": {}},
            },
        }

    monkeypatch.setattr(
        backtest_experiments,
        "_trend_candidates_with_trace",
        traced_with_invalid_symbol_row_snapshot_count,
    )

    with pytest.raises(ValueError, match=r"^symbol_rows\.BTCUSDT\.snapshot_count must be an integer$"):
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


def test_long_gate_telemetry_rejects_non_string_trace_candidate_key() -> None:
    with pytest.raises(ValueError, match=r"^candidates\[0\] key must be a string$"):
        backtest_experiments._trace_candidate_rows({"candidates": [{123: "bad", "symbol": "BTCUSDT"}]})


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


@pytest.mark.parametrize(
    ("validated_candidates", "match"),
    [
        ("BTCUSDT", r"^pipeline\.validated_candidates must be a list$"),
        ([object()], r"^pipeline\.validated_candidates\[0\] must be an object$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_pipeline_validated_candidate_rows(
    monkeypatch: pytest.MonkeyPatch,
    validated_candidates: object,
    match: str,
) -> None:
    def pipeline_with_invalid_validated_candidates(*_args, **_kwargs):
        return {
            "funnel": {
                "input_universe": 1,
                "raw_candidates": 1,
                "validated_candidates": 1,
                "allocation_decisions": 0,
                "accepted_allocations": 0,
            },
            "validated_candidates": validated_candidates,
            "allocation_rows": [],
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_validated_candidates)

    with pytest.raises(ValueError, match=match):
        run_long_gate_telemetry_experiment([_bullish_ablation_row()], evaluation_window="3d")


@pytest.mark.parametrize(
    ("allocation_rows", "match"),
    [
        ("BTCUSDT", r"^pipeline\.allocation_rows must be a list$"),
        ([object()], r"^pipeline\.allocation_rows\[0\] must be an object$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_pipeline_allocation_rows(
    monkeypatch: pytest.MonkeyPatch,
    allocation_rows: object,
    match: str,
) -> None:
    def pipeline_with_invalid_allocation_rows(*_args, **_kwargs):
        return {
            "funnel": {
                "input_universe": 1,
                "raw_candidates": 1,
                "validated_candidates": 0,
                "allocation_decisions": 1,
                "accepted_allocations": 0,
            },
            "validated_candidates": [],
            "allocation_rows": allocation_rows,
            "returns": [],
        }

    monkeypatch.setattr(backtest_experiments, "_run_candidate_pipeline", pipeline_with_invalid_allocation_rows)

    with pytest.raises(ValueError, match=match):
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


def test_long_gate_telemetry_rejects_non_string_rotation_candidate_symbol(monkeypatch) -> None:
    row = _supportive_soft_long_gate_row()
    original_build_universes = backtest_experiments.build_universes
    original_symbol_key = backtest_experiments.rotation_signals._market_symbol_key
    link_payload = row.market["symbols"]["LINKUSDT"]

    class CandidatePayloadMap(dict):
        def get(self, key, default=None):
            if key == 123:
                return link_payload
            return super().get(key, default)

    def patched_rotation_symbols(rotation_universe):
        universes = original_build_universes(row.market, derivatives=row.derivatives)
        link_universe_row = next(
            universe_row for universe_row in universes.rotation_universe if universe_row["symbol"] == "LINKUSDT"
        )
        return {123: link_universe_row}

    def patched_symbol_key(symbol):
        if symbol == 123:
            return symbol
        return original_symbol_key(symbol)

    row.market["symbols"] = CandidatePayloadMap(row.market["symbols"])
    monkeypatch.setattr(backtest_experiments.rotation_signals, "_rotation_symbols", patched_rotation_symbols)
    monkeypatch.setattr(backtest_experiments.rotation_signals, "_market_symbol_key", patched_symbol_key)
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^rotation candidates\[0\]\.symbol must be a string$"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


@pytest.mark.parametrize("invalid_return", [True, "0.05", math.nan, math.inf])
def test_long_gate_telemetry_rejects_invalid_present_trend_daily_return(
    invalid_return: object,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["LINKUSDT"]["liquidity_tier"] = "medium"
    row.market["symbols"]["LINKUSDT"]["daily"]["return_pct_7d"] = invalid_return

    with pytest.raises(ValueError, match=r"^LINKUSDT\.daily\.return_pct_7d must be a finite number$"):
        backtest_experiments._trend_candidates_with_trace(row)


@pytest.mark.parametrize("invalid_return", [True, "0.05", math.nan, math.inf])
def test_long_gate_telemetry_rejects_invalid_present_trend_h4_return(
    invalid_return: object,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["LINKUSDT"]["liquidity_tier"] = "medium"
    row.market["symbols"]["LINKUSDT"]["4h"]["return_pct_3d"] = invalid_return

    with pytest.raises(ValueError, match=r"^LINKUSDT\.4h\.return_pct_3d must be a finite number$"):
        backtest_experiments._trend_candidates_with_trace(row)


def test_long_gate_telemetry_preserves_valid_trend_eligibility_return_numbers() -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["LINKUSDT"]["liquidity_tier"] = "medium"
    row.market["symbols"]["LINKUSDT"]["daily"]["return_pct_7d"] = 1
    row.market["symbols"]["LINKUSDT"]["4h"]["return_pct_3d"] = 0.03

    result = backtest_experiments._trend_candidates_with_trace(row)

    link_filters = result["symbol_rows"]["LINKUSDT"]["filter_counts"]
    assert link_filters["eligibility_filtered"] == 1
    assert link_filters.get("eligibility_daily_return_filtered", 0) == 0
    assert link_filters.get("eligibility_h4_return_filtered", 0) == 0


@pytest.mark.parametrize(
    ("timeframe", "field", "reason"),
    [
        ("daily", "return_pct_7d", "eligibility_daily_return_filtered"),
        ("4h", "return_pct_3d", "eligibility_h4_return_filtered"),
    ],
)
def test_long_gate_telemetry_preserves_missing_trend_eligibility_return_default(
    timeframe: str,
    field: str,
    reason: str,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["LINKUSDT"]["liquidity_tier"] = "medium"
    del row.market["symbols"]["LINKUSDT"][timeframe][field]

    result = backtest_experiments._trend_candidates_with_trace(row)

    link_filters = result["symbol_rows"]["LINKUSDT"]["filter_counts"]
    assert link_filters["eligibility_filtered"] == 1
    assert link_filters[reason] == 1


def _preserve_trace_score(monkeypatch: pytest.MonkeyPatch, engine_module: object, invalid_score: object) -> None:
    original_to_float = engine_module._to_float
    original_strict_finite_number = backtest_experiments._strict_finite_number

    def patched_to_float(value: object) -> object:
        if value is invalid_score:
            return value
        return original_to_float(value)

    def patched_strict_finite_number(value: object, *, field_name: str) -> float:
        if field_name == "rotation score total" and value is invalid_score:
            return invalid_score
        return original_strict_finite_number(value, field_name=field_name)

    monkeypatch.setattr(engine_module, "_to_float", patched_to_float)
    monkeypatch.setattr(backtest_experiments, "_strict_finite_number", patched_strict_finite_number)


@pytest.mark.parametrize(
    ("invalid_score", "match"),
    [
        (True, r"^trend score total must be a finite number$"),
        (float("nan"), r"^trend score total must be a finite number$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_trend_candidate_sort_score(
    monkeypatch: pytest.MonkeyPatch,
    invalid_score: object,
    match: str,
) -> None:
    row = _bullish_ablation_row()
    _preserve_trace_score(monkeypatch, backtest_experiments.trend_signals, invalid_score)
    monkeypatch.setattr(
        backtest_experiments.trend_signals,
        "score_trend_candidate",
        lambda _features: {"total": invalid_score, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=match):
        backtest_experiments._trend_candidates_with_trace(row)


@pytest.mark.parametrize("invalid_total", [True, "0.9", float("nan"), float("inf")])
def test_long_gate_telemetry_rejects_invalid_trend_scorer_total(
    monkeypatch: pytest.MonkeyPatch,
    invalid_total: object,
) -> None:
    row = _bullish_ablation_row()
    monkeypatch.setattr(
        backtest_experiments.trend_signals,
        "score_trend_candidate",
        lambda _features: {"total": invalid_total, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^trend score total must be a finite number$"):
        backtest_experiments._trend_candidates_with_trace(row)


@pytest.mark.parametrize("valid_total", [1, 0.9])
def test_long_gate_telemetry_preserves_valid_trend_scorer_total(
    monkeypatch: pytest.MonkeyPatch,
    valid_total: object,
) -> None:
    row = _bullish_ablation_row()
    monkeypatch.setattr(
        backtest_experiments.trend_signals,
        "score_trend_candidate",
        lambda _features: {"total": valid_total, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._trend_candidates_with_trace(row)

    assert result["candidates"][0]["score"] == float(valid_total)


@pytest.mark.parametrize("invalid_volume", [True, "123", math.nan, math.inf])
def test_long_gate_telemetry_rejects_invalid_selected_trend_daily_volume(
    monkeypatch: pytest.MonkeyPatch,
    invalid_volume: object,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["BTCUSDT"]["daily"]["volume_usdt_24h"] = invalid_volume
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^BTCUSDT\.daily\.volume_usdt_24h must be a finite number$"):
        backtest_experiments._trend_candidates_with_trace(row)


@pytest.mark.parametrize("volume", [0, 20_000_000_000])
def test_long_gate_telemetry_preserves_valid_selected_trend_daily_volume(
    monkeypatch: pytest.MonkeyPatch,
    volume: int,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["BTCUSDT"]["daily"]["volume_usdt_24h"] = volume
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._trend_candidates_with_trace(row)

    btc_candidate = next(candidate for candidate in result["candidates"] if candidate["symbol"] == "BTCUSDT")
    assert btc_candidate["liquidity_meta"]["volume_usdt_24h"] == float(volume)


def test_long_gate_telemetry_preserves_missing_selected_trend_daily_volume_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _bullish_ablation_row()
    del row.market["symbols"]["BTCUSDT"]["daily"]["volume_usdt_24h"]
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._trend_candidates_with_trace(row)

    btc_candidate = next(candidate for candidate in result["candidates"] if candidate["symbol"] == "BTCUSDT")
    assert btc_candidate["liquidity_meta"]["volume_usdt_24h"] == 0.0


@pytest.mark.parametrize(
    ("timeframe", "field", "invalid_value", "match"),
    [
        ("daily", "close", True, r"^BTCUSDT\.daily\.close must be a finite number$"),
        ("daily", "ema_20", "101", r"^BTCUSDT\.daily\.ema_20 must be a finite number$"),
        ("4h", "ema_50", math.nan, r"^BTCUSDT\.4h\.ema_50 must be a finite number$"),
        ("1h", "ema_20", math.inf, r"^BTCUSDT\.1h\.ema_20 must be a finite number$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_selected_trend_structure_fields(
    monkeypatch: pytest.MonkeyPatch,
    timeframe: str,
    field: str,
    invalid_value: object,
    match: str,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["BTCUSDT"][timeframe][field] = invalid_value
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=match):
        backtest_experiments._trend_candidates_with_trace(row)


@pytest.mark.parametrize(
    ("timeframe", "field", "valid_value"),
    [
        ("daily", "close", 101),
        ("daily", "ema_20", 100),
        ("4h", "ema_50", 99),
        ("1h", "ema_20", 100),
    ],
)
def test_long_gate_telemetry_preserves_valid_selected_trend_structure_fields(
    monkeypatch: pytest.MonkeyPatch,
    timeframe: str,
    field: str,
    valid_value: int,
) -> None:
    row = _bullish_ablation_row()
    row.market["symbols"]["BTCUSDT"][timeframe][field] = valid_value
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._trend_candidates_with_trace(row)

    assert any(candidate["symbol"] == "BTCUSDT" for candidate in result["candidates"])


@pytest.mark.parametrize(
    ("timeframe", "field"),
    [
        ("daily", "close"),
        ("daily", "ema_20"),
        ("4h", "ema_50"),
        ("1h", "ema_20"),
    ],
)
def test_long_gate_telemetry_preserves_missing_selected_trend_structure_field_filtering(
    monkeypatch: pytest.MonkeyPatch,
    timeframe: str,
    field: str,
) -> None:
    row = _bullish_ablation_row()
    del row.market["symbols"]["BTCUSDT"][timeframe][field]
    monkeypatch.setattr(backtest_experiments.trend_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._trend_candidates_with_trace(row)

    assert "BTCUSDT" not in {candidate["symbol"] for candidate in result["candidates"]}
    assert sum(result["symbol_rows"]["BTCUSDT"]["filter_counts"].values()) == 1


@pytest.mark.parametrize(
    ("invalid_score", "match"),
    [
        (True, r"^rotation candidates\[0\]\.score must be numeric$"),
        (float("inf"), r"^rotation candidates\[0\]\.score must be finite$"),
    ],
)
def test_long_gate_telemetry_rejects_invalid_rotation_candidate_sort_score(
    monkeypatch: pytest.MonkeyPatch,
    invalid_score: object,
    match: str,
) -> None:
    row = _supportive_soft_long_gate_row()
    _preserve_trace_score(monkeypatch, backtest_experiments.rotation_signals, invalid_score)
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": invalid_score, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=match):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


@pytest.mark.parametrize("invalid_total", [True, "0.9", float("nan"), float("inf")])
def test_long_gate_telemetry_rejects_invalid_rotation_scorer_total(
    monkeypatch: pytest.MonkeyPatch,
    invalid_total: object,
) -> None:
    row = _supportive_soft_long_gate_row()
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": invalid_total, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^rotation score total must be a finite number$"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


@pytest.mark.parametrize("valid_total", [1, 0.9])
def test_long_gate_telemetry_accepts_valid_rotation_scorer_total(
    monkeypatch: pytest.MonkeyPatch,
    valid_total: int | float,
) -> None:
    row = _supportive_soft_long_gate_row()
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": valid_total, "components": {}},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    trace = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    assert trace["candidates"]
    assert {candidate["score"] for candidate in trace["candidates"]} == {float(valid_total)}


@pytest.mark.parametrize("invalid_volume", [True, "123", math.nan, math.inf])
def test_long_gate_telemetry_rejects_invalid_selected_rotation_daily_volume(
    monkeypatch: pytest.MonkeyPatch,
    invalid_volume: object,
) -> None:
    row = _supportive_soft_long_gate_row()
    row.market["symbols"]["LINKUSDT"]["daily"]["volume_usdt_24h"] = invalid_volume
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^LINKUSDT\.daily\.volume_usdt_24h must be a finite number$"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


@pytest.mark.parametrize("volume", [0, 1_450_000_000])
def test_long_gate_telemetry_preserves_valid_selected_rotation_daily_volume(
    monkeypatch: pytest.MonkeyPatch,
    volume: int,
) -> None:
    row = _supportive_soft_long_gate_row()
    row.market["symbols"]["LINKUSDT"]["daily"]["volume_usdt_24h"] = volume
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    assert result["candidates"][0]["liquidity_meta"]["volume_usdt_24h"] == float(volume)


def test_long_gate_telemetry_preserves_missing_selected_rotation_daily_volume_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _supportive_soft_long_gate_row()
    del row.market["symbols"]["LINKUSDT"]["daily"]["volume_usdt_24h"]
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    assert result["candidates"][0]["liquidity_meta"]["volume_usdt_24h"] == 0.0


@pytest.mark.parametrize("invalid_liquidity_meta", [[("liquidity_tier", "high")], "not-object"])
def test_long_gate_telemetry_rejects_invalid_rotation_universe_liquidity_meta(
    monkeypatch: pytest.MonkeyPatch,
    invalid_liquidity_meta: object,
) -> None:
    row = _supportive_soft_long_gate_row()
    original_build_universes = backtest_experiments.build_universes

    def patched_build_universes(market, derivatives=None):
        universes = original_build_universes(market, derivatives=derivatives)
        for universe_row in universes.rotation_universe:
            if universe_row["symbol"] == "LINKUSDT":
                universe_row["liquidity_meta"] = invalid_liquidity_meta
        return universes

    monkeypatch.setattr(backtest_experiments, "build_universes", patched_build_universes)
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(
        ValueError,
        match=r"^LINKUSDT\.rotation_universe\.liquidity_meta must be an object$",
    ):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


def test_long_gate_telemetry_rejects_rotation_universe_liquidity_meta_non_string_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _supportive_soft_long_gate_row()
    original_build_universes = backtest_experiments.build_universes

    def patched_build_universes(market, derivatives=None):
        universes = original_build_universes(market, derivatives=derivatives)
        for universe_row in universes.rotation_universe:
            if universe_row["symbol"] == "LINKUSDT":
                universe_row["liquidity_meta"] = {123: "bad"}
        return universes

    monkeypatch.setattr(backtest_experiments, "build_universes", patched_build_universes)
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(
        ValueError,
        match=r"^LINKUSDT\.rotation_universe\.liquidity_meta key must be a string$",
    ):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


@pytest.mark.parametrize("liquidity_meta", [None, {}])
def test_long_gate_telemetry_preserves_missing_rotation_universe_liquidity_meta(
    monkeypatch: pytest.MonkeyPatch,
    liquidity_meta: object,
) -> None:
    row = _supportive_soft_long_gate_row()
    original_build_universes = backtest_experiments.build_universes

    def patched_build_universes(market, derivatives=None):
        universes = original_build_universes(market, derivatives=derivatives)
        for universe_row in universes.rotation_universe:
            if universe_row["symbol"] == "LINKUSDT":
                if liquidity_meta is None:
                    universe_row.pop("liquidity_meta", None)
                else:
                    universe_row["liquidity_meta"] = liquidity_meta
        return universes

    monkeypatch.setattr(backtest_experiments, "build_universes", patched_build_universes)
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    result = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    assert result["candidates"][0]["liquidity_meta"] == {
        "liquidity_tier": "high",
        "volume_usdt_24h": 1_450_000_000.0,
    }


@pytest.mark.parametrize("invalid_components", ["bad", [("x", 1)], True])
def test_long_gate_telemetry_rejects_invalid_rotation_score_components(
    monkeypatch: pytest.MonkeyPatch,
    invalid_components: object,
) -> None:
    row = _supportive_soft_long_gate_row()
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": 0.9, "components": invalid_components},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^rotation candidates\[0\]\.score_components must be an object$"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


def test_long_gate_telemetry_rejects_rotation_score_component_non_string_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _supportive_soft_long_gate_row()
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": 0.9, "components": {123: 0.4}},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    with pytest.raises(ValueError, match=r"^rotation candidates\[0\]\.score_components key must be a string$"):
        backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())


def test_long_gate_telemetry_preserves_rotation_score_component_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _supportive_soft_long_gate_row()
    components = {"relative_strength": 0.4, "persistence": 0.2}
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: {"total": 0.9, "components": components},
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    trace = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    score_components = trace["candidates"][0]["timeframe_meta"]["score_components"]
    assert score_components == components
    assert score_components is not components


@pytest.mark.parametrize("scored", [{"total": 0.9}, {"total": 0.9, "components": None}])
def test_long_gate_telemetry_defaults_missing_rotation_score_components_to_empty_object(
    monkeypatch: pytest.MonkeyPatch,
    scored: dict[str, object],
) -> None:
    row = _supportive_soft_long_gate_row()
    monkeypatch.setattr(
        backtest_experiments.rotation_signals,
        "score_rotation_candidate",
        lambda _features: scored,
    )
    monkeypatch.setattr(backtest_experiments.rotation_signals, "symbol_derivatives_features", lambda *_args: {})

    trace = backtest_experiments._rotation_candidates_with_trace(row, disabled_filters=frozenset())

    assert trace["candidates"][0]["timeframe_meta"]["score_components"] == {}



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


def _accepted_trend_btc_allocation_rows(*_args, **_kwargs) -> list[dict[str, object]]:
    return [
        {
            "symbol": "BTCUSDT",
            "engine": "trend",
            "status": "ACCEPTED",
            "final_risk_budget": 0.01,
        }
    ]


@pytest.mark.parametrize("invalid_return", [True, "0.01", math.nan, math.inf])
def test_allocator_friction_experiment_rejects_invalid_candidate_forward_returns(
    monkeypatch: pytest.MonkeyPatch,
    invalid_return: object,
) -> None:
    row = _bullish_ablation_row()
    row.meta["candidate_forward_returns"]["trend"]["BTCUSDT"] = invalid_return
    monkeypatch.setattr(backtest_experiments, "_allocation_rows", _accepted_trend_btc_allocation_rows)

    with pytest.raises(
        ValueError,
        match=r"^candidate_forward_returns\.trend\.BTCUSDT must be a finite number$",
    ):
        run_allocator_friction_experiment([row], evaluation_window="3d")


@pytest.mark.parametrize("invalid_return", [True, "0.01", math.nan, math.inf])
def test_allocator_friction_experiment_rejects_invalid_fallback_forward_returns(
    monkeypatch: pytest.MonkeyPatch,
    invalid_return: object,
) -> None:
    row = _bullish_ablation_row()
    row.meta["candidate_forward_returns"] = {"trend": {}}
    row.forward_returns["3d"] = invalid_return
    monkeypatch.setattr(backtest_experiments, "_allocation_rows", _accepted_trend_btc_allocation_rows)

    with pytest.raises(ValueError, match=r"^forward_returns\.3d must be a finite number$"):
        run_allocator_friction_experiment([row], evaluation_window="3d")


@pytest.mark.parametrize("candidate_return", [1, 0.01])
def test_allocator_friction_experiment_preserves_valid_candidate_forward_returns(
    monkeypatch: pytest.MonkeyPatch,
    candidate_return: int | float,
) -> None:
    row = _bullish_ablation_row()
    row.meta["candidate_forward_returns"]["trend"]["BTCUSDT"] = candidate_return
    monkeypatch.setattr(backtest_experiments, "_allocation_rows", _accepted_trend_btc_allocation_rows)

    result = run_allocator_friction_experiment([row], evaluation_window="3d")

    assert result["variants"]["current_allocator"]["frictions"]["base"]["trade_count"] == 1


def test_allocator_friction_experiment_defaults_missing_fallback_forward_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _bullish_ablation_row()
    row.meta["candidate_forward_returns"] = {"trend": {}}
    row.forward_returns.clear()
    monkeypatch.setattr(backtest_experiments, "_allocation_rows", _accepted_trend_btc_allocation_rows)

    result = run_allocator_friction_experiment([row], evaluation_window="3d")

    assert result["variants"]["current_allocator"]["frictions"]["base"]["gross_bucket_pnl"] == 0.0


def test_friction_summary_preserves_valid_cost_attribution_outputs() -> None:
    summary = backtest_experiments._friction_summary(
        [
            {"gross_pnl": 2, "net_pnl": 1.25, "fee_drag": 0.1, "slippage_drag": 0.2, "funding_drag": 0.05},
            {"gross_pnl": -1.0, "net_pnl": -1.35, "fee_drag": 1, "slippage_drag": 0.25, "funding_drag": 0},
        ]
    )

    assert summary["gross_bucket_pnl"] == 1.0
    assert summary["net_bucket_pnl"] == -0.1
    assert summary["cost_drag"] == 1.6
    assert summary["cost_attribution"] == {
        "fee_drag": 1.1,
        "slippage_drag": 0.45,
        "funding_drag": 0.05,
    }


@pytest.mark.parametrize("field", ["fee_drag", "slippage_drag", "funding_drag"])
@pytest.mark.parametrize("invalid_drag", [True, "1.0", float("nan"), float("inf")])
def test_friction_summary_rejects_invalid_cost_drag_fields(field: str, invalid_drag: object) -> None:
    performance_rows = [
        {
            "gross_pnl": 1.0,
            "net_pnl": 0.9,
            "fee_drag": 0.05,
            "slippage_drag": 0.03,
            "funding_drag": 0.02,
        }
    ]
    performance_rows[0][field] = invalid_drag

    with pytest.raises(ValueError, match=rf"^performance_rows\[0\]\.{field} must be a finite number$"):
        backtest_experiments._friction_summary(performance_rows)


def test_allocation_summary_preserves_valid_accepted_budget_outputs() -> None:
    summary = backtest_experiments._allocation_summary(
        [
            {"status": "ACCEPTED", "final_risk_budget": 0.015},
            {"status": "DOWNSIZED", "final_risk_budget": 0.01},
            {"status": "REJECTED", "final_risk_budget": 0.5},
            {"status": "ACCEPTED"},
        ]
    )

    assert summary == {
        "accepted_allocations": 2,
        "total_risk_budget": 0.025,
        "avg_risk_budget": 0.0125,
        "max_risk_budget": 0.015,
        "status_breakdown": {
            "accepted": 2,
            "downsized": 1,
            "rejected": 1,
        },
    }


@pytest.mark.parametrize("invalid_budget", [True, "0.1", float("nan"), float("inf")])
def test_allocation_summary_rejects_invalid_present_accepted_final_risk_budget(invalid_budget: object) -> None:
    allocations = [{"status": "ACCEPTED", "final_risk_budget": invalid_budget}]

    with pytest.raises(ValueError, match=r"^allocations\[0\]\.final_risk_budget must be a finite number$"):
        backtest_experiments._allocation_summary(allocations)


@pytest.mark.parametrize("invalid_score", [True, "1"])
def test_baseline_allocation_row_rejects_non_numeric_present_rank_score(invalid_score: object) -> None:
    candidate = {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "breakout", "score": invalid_score}

    with pytest.raises(ValueError, match=r"^candidate\.score must be numeric$"):
        backtest_experiments._baseline_allocation_row(
            candidate,
            rank=1,
            status="ACCEPTED",
            final_risk_budget=0.01,
            baseline_name="equal_weight_baseline",
        )


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf")])
def test_baseline_allocation_row_rejects_non_finite_present_rank_score(invalid_score: float) -> None:
    candidate = {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "breakout", "score": invalid_score}

    with pytest.raises(ValueError, match=r"^candidate\.score must be finite$"):
        backtest_experiments._baseline_allocation_row(
            candidate,
            rank=1,
            status="ACCEPTED",
            final_risk_budget=0.01,
            baseline_name="equal_weight_baseline",
        )


@pytest.mark.parametrize("invalid_budget", [True, "0.01"])
def test_baseline_allocation_row_rejects_non_numeric_final_risk_budget(invalid_budget: object) -> None:
    candidate = {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "breakout", "score": 0.9}

    with pytest.raises(ValueError, match=r"^final_risk_budget must be a finite number$"):
        backtest_experiments._baseline_allocation_row(
            candidate,
            rank=1,
            status="ACCEPTED",
            final_risk_budget=invalid_budget,
            baseline_name="equal_weight_baseline",
        )


@pytest.mark.parametrize("invalid_budget", [float("nan"), float("inf")])
def test_baseline_allocation_row_rejects_non_finite_final_risk_budget(invalid_budget: float) -> None:
    candidate = {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "breakout", "score": 0.9}

    with pytest.raises(ValueError, match=r"^final_risk_budget must be a finite number$"):
        backtest_experiments._baseline_allocation_row(
            candidate,
            rank=1,
            status="ACCEPTED",
            final_risk_budget=invalid_budget,
            baseline_name="equal_weight_baseline",
        )


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (1, 1.0),
        (0.01234567, 0.012346),
    ],
)
def test_baseline_allocation_row_preserves_valid_final_risk_budget_rounding(
    budget: int | float,
    expected: float,
) -> None:
    candidate = {"symbol": "BTCUSDT", "engine": "trend", "setup_type": "breakout", "score": 0.9}

    row = backtest_experiments._baseline_allocation_row(
        candidate,
        rank=1,
        status="ACCEPTED",
        final_risk_budget=budget,
        baseline_name="equal_weight_baseline",
    )

    assert row["final_risk_budget"] == expected


@pytest.mark.parametrize("candidate", [{"symbol": "BTCUSDT"}, {"symbol": "BTCUSDT", "score": None}])
def test_baseline_allocation_row_defaults_missing_rank_score(candidate: dict[str, object]) -> None:
    row = backtest_experiments._baseline_allocation_row(
        candidate,
        rank=1,
        status="ACCEPTED",
        final_risk_budget=0.01,
        baseline_name="equal_weight_baseline",
    )

    assert row["meta"]["rank_score"] == 0.0


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


def test_allocator_friction_experiment_rejects_non_string_engine_only_candidate_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_engine_only_candidates(_row, *, engine: str):
        return {
            "regime": {},
            "input_universe": 1,
            "candidates": [{123: "bad", "symbol": "BTCUSDT"}],
            "filter_counts": {},
        }

    monkeypatch.setattr(backtest_experiments, "_engine_only_candidates", invalid_engine_only_candidates)

    with pytest.raises(ValueError, match=r"^engine_only\.candidates\[0\] key must be a string$"):
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


def test_allocator_friction_experiment_rejects_non_string_candidate_bundle_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_all_engine_candidates(_row):
        return {
            "regime": {},
            "input_universe": 1,
            "candidates": [{123: "bad", "symbol": "BTCUSDT"}],
        }

    monkeypatch.setattr(backtest_experiments, "_all_engine_candidates", invalid_all_engine_candidates)

    with pytest.raises(ValueError, match=r"^candidate_bundle\.candidates\[0\] key must be a string$"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_allocator_friction_experiment_rejects_non_string_candidate_bundle_regime_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_all_engine_candidates(_row):
        return {
            "regime": {123: "bad"},
            "input_universe": 1,
            "candidates": [],
            "filter_counts": {},
        }

    monkeypatch.setattr(backtest_experiments, "_all_engine_candidates", invalid_all_engine_candidates)

    with pytest.raises(ValueError, match=r"^candidate_bundle\.regime key must be a string$"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_allocator_friction_experiment_rejects_list_of_pairs_row_account() -> None:
    row = replace(_bullish_ablation_row(), account=[("equity", 100_000.0)])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"^row\.account must be an object$"):
        run_allocator_friction_experiment([row], evaluation_window="3d")


def test_allocator_friction_experiment_rejects_non_string_row_account_key() -> None:
    row = replace(_bullish_ablation_row(), account={123: "bad", "equity": 100_000.0})  # type: ignore[dict-item]

    with pytest.raises(ValueError, match=r"^row\.account key must be a string$"):
        run_allocator_friction_experiment([row], evaluation_window="3d")


def test_experiment_count_merge_rejects_non_string_source_key() -> None:
    with pytest.raises(ValueError, match=r"^filter_counts key must be a string$"):
        backtest_experiments._merge_counts({}, {123: 1}, path="filter_counts")  # type: ignore[dict-item]


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


@pytest.mark.parametrize("invalid_budget", [True, "0.1", float("nan"), float("inf")])
def test_allocator_friction_experiment_rejects_invalid_performance_risk_budget(
    monkeypatch: pytest.MonkeyPatch,
    invalid_budget: object,
) -> None:
    def invalid_performance_rows(*_args, **_kwargs):
        return [
            {
                "symbol": "BTCUSDT",
                "engine": "trend",
                "status": "ACCEPTED",
                "risk_budget": invalid_budget,
                "gross_pnl": 0.02,
                "net_pnl": 0.019,
                "fee_drag": 0.0004,
                "slippage_drag": 0.0002,
                "funding_drag": 0.0001,
            }
        ]

    monkeypatch.setattr(backtest_experiments, "_allocation_performance_rows", invalid_performance_rows)

    with pytest.raises(ValueError, match=r"^performance_rows\[0\]\.risk_budget must be a finite number$"):
        run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")


def test_allocator_friction_experiment_preserves_valid_performance_risk_budget_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def valid_performance_rows(*_args, **_kwargs):
        return [
            {
                "symbol": "BTCUSDT",
                "engine": "trend",
                "status": "ACCEPTED",
                "risk_budget": 1,
                "gross_pnl": 0.02,
                "net_pnl": 0.019,
                "fee_drag": 0.0004,
                "slippage_drag": 0.0002,
                "funding_drag": 0.0001,
            },
            {
                "symbol": "ETHUSDT",
                "engine": "trend",
                "status": "ACCEPTED",
                "risk_budget": 0.01234567,
                "gross_pnl": 0.01,
                "net_pnl": 0.009,
                "fee_drag": 0.0003,
                "slippage_drag": 0.0002,
                "funding_drag": 0.0001,
            },
        ]

    monkeypatch.setattr(backtest_experiments, "_allocation_performance_rows", valid_performance_rows)

    result = run_allocator_friction_experiment([_bullish_ablation_row()], evaluation_window="3d")

    assert result["comparison_rows"][0]["total_risk_budget"] == 1.012346


def _valid_friction_performance_row() -> dict[str, object]:
    return {
        "gross_pnl": 2,
        "net_pnl": 1.5,
        "fee_drag": 0.1,
        "slippage_drag": 0.2,
        "funding_drag": 0.3,
    }


def test_friction_summary_preserves_valid_pnl_numbers() -> None:
    summary = backtest_experiments._friction_summary([_valid_friction_performance_row()])

    assert summary["gross_bucket_pnl"] == 2.0
    assert summary["net_bucket_pnl"] == 1.5
    assert summary["trade_count"] == 1
    assert summary["cost_drag"] == 0.6


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("gross_pnl", True),
        ("gross_pnl", "1.0"),
        ("net_pnl", True),
        ("net_pnl", "1.0"),
    ],
)
def test_friction_summary_rejects_non_numeric_pnl_fields(field: str, invalid_value: object) -> None:
    row = _valid_friction_performance_row()
    row[field] = invalid_value

    with pytest.raises(ValueError, match=rf"performance_rows\[0\]\.{field} must be a finite number"):
        backtest_experiments._friction_summary([row])


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("gross_pnl", float("nan")),
        ("gross_pnl", float("inf")),
        ("net_pnl", float("nan")),
        ("net_pnl", float("inf")),
    ],
)
def test_friction_summary_rejects_non_finite_pnl_fields(field: str, invalid_value: float) -> None:
    row = _valid_friction_performance_row()
    row[field] = invalid_value

    with pytest.raises(ValueError, match=rf"performance_rows\[0\]\.{field} must be a finite number"):
        backtest_experiments._friction_summary([row])


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
