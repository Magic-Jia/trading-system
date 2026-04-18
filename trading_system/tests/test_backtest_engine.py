from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest import engine as backtest_engine
from trading_system.app.backtest.dataset import load_historical_dataset
from trading_system.app.backtest.engine import replay_snapshot
from trading_system.app.types import EngineCandidate, RegimeSnapshot
from trading_system.app.universe.builder import UniverseBuildResult


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
            "BTCUSDTPERP": _sample_symbol(close=100.0),
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
