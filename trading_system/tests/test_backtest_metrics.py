from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trading_system.app.backtest import engine as backtest_engine
from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.metrics import (
    calmar_ratio,
    cost_drag,
    expectancy,
    max_drawdown,
    payoff_ratio,
    sharpe_ratio,
    sortino_ratio,
    total_return,
    turnover,
    win_rate,
)
from trading_system.app.types import EngineCandidate, RegimeSnapshot
from trading_system.app.universe.builder import UniverseBuildResult


def test_backtest_metrics_are_deterministic() -> None:
    returns = [0.05, -0.02, 0.03, -0.01]
    trade_returns = [0.08, -0.03, 0.04, -0.02]

    assert total_return(returns) == pytest.approx(0.04927, rel=1e-4)
    assert max_drawdown(returns) == pytest.approx(-0.02, rel=1e-4)
    assert sharpe_ratio(returns, periods_per_year=4) == pytest.approx(0.8737, rel=1e-3)
    assert sortino_ratio(returns, periods_per_year=4) == pytest.approx(2.2361, rel=1e-3)
    assert calmar_ratio(returns, periods_per_year=4) == pytest.approx(2.4636, rel=1e-3)
    assert win_rate(trade_returns) == pytest.approx(0.5)
    assert payoff_ratio(trade_returns) == pytest.approx(2.4)
    assert expectancy(trade_returns) == pytest.approx(0.0175)
    assert turnover([12_000.0, -4_500.0, 3_000.0], average_equity=100_000.0) == pytest.approx(0.195)
    assert cost_drag([0.06, -0.01, 0.04], [0.05, -0.015, 0.032]) == pytest.approx(0.02403, rel=1e-4)


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


def _sample_symbol(*, close: float, liquidity_tier: str = "top") -> dict[str, Any]:
    return {
        "sector": "majors",
        "liquidity_tier": liquidity_tier,
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
            "BTCUSDTPERP": _sample_symbol(close=100.0),
            "ETHUSDT": _sample_symbol(close=1000.0, liquidity_tier="low"),
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
            "ETHUSDT": _sample_symbol(close=980.0, liquidity_tier="low"),
            "SOLUSDTPERP": _sample_symbol(close=50.0, liquidity_tier="medium"),
        },
        derivatives_rows=[{"symbol": "SOLUSDTPERP", "funding_rate": 0.0002}],
        instrument_rows=row2_instruments,
        candidate_symbols=["SOLUSDTPERP"],
    )
    _write_market_bundle(
        dataset_root,
        timestamp="2026-03-12T00:00:00Z",
        run_id="row-003",
        market_symbols={"SOLUSDTPERP": _sample_symbol(close=55.0, liquidity_tier="medium")},
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


def test_full_market_baseline_replay_metrics_capture_cost_drag_and_turnover(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = load_backtest_config(_baseline_config_path(tmp_path))
    _install_replay_candidates(monkeypatch)

    replay = getattr(backtest_engine, "replay_full_market_baseline", None)
    assert replay is not None

    result = replay(config)

    assert cost_drag(result.gross_period_returns, result.net_period_returns) > 0.0
    assert result.portfolio_summary.turnover > 0.0
    assert result.portfolio_summary.total_return == pytest.approx(total_return(result.net_period_returns))

    trade_by_symbol = {row.symbol: row for row in result.trade_ledger}
    assert trade_by_symbol["BTCUSDT"].funding_paid == pytest.approx(0.0)
    assert trade_by_symbol["ETHUSDT"].funding_paid == pytest.approx(0.0)
    assert trade_by_symbol["SOLUSDTPERP"].funding_paid > 0.0
