from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_system.app.backtest import cli, reporting
from trading_system.app.backtest.types import (
    BacktestConfig,
    BacktestCosts,
    BaselineReplayResult,
    CapitalModelConfig,
    DatasetSnapshotRow,
    PortfolioDecisionLedgerRow,
    PortfolioScorecardRow,
    SampleWindow,
    TradeLedgerRow,
    UniverseFilterConfig,
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def sample_baseline_result() -> BaselineReplayResult:
    return BaselineReplayResult(
        portfolio_summary=PortfolioScorecardRow(
            experiment_name="current_system__auditable_baseline",
            total_return=0.12,
            max_drawdown=-0.08,
            sharpe=1.4,
            sortino=1.8,
            calmar=1.5,
            turnover=0.42,
            trade_count=4,
        ),
        trade_ledger=(
            TradeLedgerRow(
                symbol="BTCUSDT",
                market_type="spot",
                base_asset="BTC",
                side="long",
                status="accepted",
                entry_timestamp=_ts("2026-03-10T00:00:00Z"),
                exit_timestamp=_ts("2026-03-11T00:00:00Z"),
                entry_price=100.0,
                exit_price=110.0,
                qty=10.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=100.0,
                net_pnl=90.0,
                gross_return_pct=0.10,
                net_return_pct=0.09,
                fee_paid=1.0,
                slippage_paid=4.0,
                funding_paid=0.0,
                entry_reference_timeframe="15m",
                entry_reference_price=100.0,
                gate_timeframes=("daily", "4h", "1h"),
                trigger_timeframes=("30m", "15m"),
            ),
            TradeLedgerRow(
                symbol="BTCUSDTPERP",
                market_type="futures",
                base_asset="BTC",
                side="long",
                status="resized",
                entry_timestamp=_ts("2026-03-10T00:00:00Z"),
                exit_timestamp=_ts("2026-03-11T00:00:00Z"),
                entry_price=100.0,
                exit_price=108.0,
                qty=5.0,
                position_notional=500.0,
                holding_hours=24.0,
                gross_pnl=40.0,
                net_pnl=33.0,
                gross_return_pct=0.08,
                net_return_pct=0.066,
                fee_paid=0.5,
                slippage_paid=2.0,
                funding_paid=4.5,
            ),
            TradeLedgerRow(
                symbol="ETHUSDT",
                market_type="spot",
                base_asset="ETH",
                side="long",
                status="accepted",
                entry_timestamp=_ts("2027-01-10T00:00:00Z"),
                exit_timestamp=_ts("2027-01-11T00:00:00Z"),
                entry_price=1_000.0,
                exit_price=1_050.0,
                qty=1.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=50.0,
                net_pnl=45.0,
                gross_return_pct=0.05,
                net_return_pct=0.045,
                fee_paid=1.0,
                slippage_paid=4.0,
                funding_paid=0.0,
            ),
            TradeLedgerRow(
                symbol="SOLUSDTPERP",
                market_type="futures",
                base_asset="SOL",
                side="short",
                status="accepted",
                entry_timestamp=_ts("2027-01-10T00:00:00Z"),
                exit_timestamp=_ts("2027-01-11T00:00:00Z"),
                entry_price=50.0,
                exit_price=45.0,
                qty=20.0,
                position_notional=1_000.0,
                holding_hours=24.0,
                gross_pnl=100.0,
                net_pnl=88.0,
                gross_return_pct=0.10,
                net_return_pct=0.088,
                fee_paid=0.5,
                slippage_paid=3.0,
                funding_paid=8.5,
            ),
        ),
        rejection_ledger=(
            PortfolioDecisionLedgerRow(
                symbol="BTCUSDTPERP",
                market_type="futures",
                base_asset="BTC",
                status="rejected",
                reasons=("base_asset_same_direction_crowding",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
            PortfolioDecisionLedgerRow(
                symbol="DOGEUSDT",
                market_type="spot",
                base_asset="DOGE",
                status="rejected",
                reasons=("open_risk_limit_reached",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
            PortfolioDecisionLedgerRow(
                symbol="ADAUSDTPERP",
                market_type="futures",
                base_asset="ADA",
                status="rejected",
                reasons=("capital_usage_limit_reached",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            ),
        ),
        cost_breakdown={"fees": 3.0, "slippage": 13.0, "funding": 13.0},
        gross_period_returns=(0.08, 0.04),
        net_period_returns=(0.07, 0.04672897196261682),
    )


def _write_fixture_bundle(dataset_root: Path, *, timestamp: str, run_id: str) -> None:
    bundle_dir = dataset_root / f"{timestamp.replace(':', '-')}__{run_id}"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "metadata.json").write_text(
        json.dumps({"timestamp": timestamp, "run_id": run_id}),
        encoding="utf-8",
    )
    (bundle_dir / "market_context.json").write_text(
        json.dumps({"symbols": {}, "candidate_symbols": []}),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps({"rows": []}),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps(
            {
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            }
        ),
        encoding="utf-8",
    )


def _write_imported_public_strategy_bundle(
    dataset_root: Path,
    *,
    timestamp: str,
    run_id: str,
    momentum: float,
    forward_return: float,
) -> None:
    bundle_dir = dataset_root / f"{timestamp.replace(':', '-')}__{run_id}"
    bundle_dir.mkdir(parents=True)
    instrument_row = {
        "symbol": "BTCUSDT",
        "market_type": "futures",
        "base_asset": "BTC",
        "listing_timestamp": "2020-01-01T00:00:00Z",
        "quote_volume_usdt_24h": 1_500_000_000.0,
        "liquidity_tier": "high",
        "quantity_step": 0.001,
        "price_tick": 0.1,
        "has_complete_funding": True,
    }
    (bundle_dir / "metadata.json").write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "run_id": run_id,
                "schema_version": "phase1_import_bundle.v1",
                "forward_returns": {"3d": forward_return},
                "forward_drawdowns": {"3d": -0.02},
            }
        ),
        encoding="utf-8",
    )
    (bundle_dir / "market_context.json").write_text(
        json.dumps(
            {
                "as_of": timestamp,
                "schema_version": "imported_market_context.v1",
                "symbols": {
                    "BTCUSDT": {
                        "daily": {
                            "close": 100.0 + momentum,
                            "ema_50": 100.0,
                            "return_pct_7d": momentum,
                            "atr_pct": abs(momentum) + 0.01,
                        }
                    }
                },
                "instrument_rows": [instrument_row],
            }
        ),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps({"rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0001, "basis_bps": 0.0}]}),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps({"equity": 100_000.0, "available_balance": 100_000.0, "positions": []}),
        encoding="utf-8",
    )
    (bundle_dir / "instrument_snapshot.json").write_text(
        json.dumps({"as_of": timestamp, "schema_version": "imported_instrument_snapshot.v1", "rows": [instrument_row]}),
        encoding="utf-8",
    )


def _write_imported_public_strategy_dataset(dataset_root: Path) -> None:
    dataset_root.mkdir(parents=True)
    samples = [
        ("2025-01-01T00:00:00Z", "phase1-import-001", -0.04, -0.03),
        ("2025-01-02T00:00:00Z", "phase1-import-002", -0.01, -0.01),
        ("2025-01-03T00:00:00Z", "phase1-import-003", 0.02, 0.02),
        ("2025-01-04T00:00:00Z", "phase1-import-004", 0.05, 0.05),
    ]
    for timestamp, run_id, momentum, forward_return in samples:
        _write_imported_public_strategy_bundle(
            dataset_root,
            timestamp=timestamp,
            run_id=run_id,
            momentum=momentum,
            forward_return=forward_return,
        )
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "phase1_imported_dataset_root.v1",
                "scope": "phase1_binance_futures",
                "archive_root": "/tmp/archive",
                "dataset_root": str(dataset_root),
                "snapshot_count": len(samples),
                "symbols": ["BTCUSDT"],
                "start_timestamp": samples[0][0],
                "end_timestamp": samples[-1][0],
                "bundle_dirs": [
                    str(dataset_root / f"{timestamp.replace(':', '-')}__{run_id}")
                    for timestamp, run_id, _momentum, _forward_return in samples
                ],
                "source": {"scope": "phase1_binance_futures", "symbols": ["BTCUSDT"]},
            }
        ),
        encoding="utf-8",
    )


def _sample_dataset_rows() -> list[DatasetSnapshotRow]:
    return [
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-10T00:00:00Z"),
            run_id="row-001",
            market={"symbols": {}},
            derivatives=[],
            account={
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            },
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-12T00:00:00Z"),
            run_id="row-002",
            market={"symbols": {}},
            derivatives=[],
            account={
                "equity": 100_000.0,
                "available_balance": 100_000.0,
                "futures_wallet_balance": 100_000.0,
                "open_positions": [],
            },
        ),
    ]



def _write_experiment_fixture_config(tmp_path: Path, fixture_name: str) -> Path:
    fixture_path = Path("trading_system/tests/fixtures/backtest") / fixture_name
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir(parents=True, exist_ok=True)
    raw["dataset_root"] = str(dataset_root)
    output_path = tmp_path / fixture_name
    output_path.write_text(json.dumps(raw), encoding="utf-8")
    return output_path



def sample_full_market_config(tmp_path: Path) -> Path:
    dataset_root = tmp_path / "dataset"
    _write_fixture_bundle(dataset_root, timestamp="2026-03-10T00:00:00Z", run_id="row-001")
    _write_fixture_bundle(dataset_root, timestamp="2026-03-11T00:00:00Z", run_id="row-002")

    config = BacktestConfig(
        dataset_root=dataset_root,
        experiment_kind="full_market_baseline",
        sample_windows=(
            SampleWindow(
                name="full_history",
                start=_ts("2026-03-10T00:00:00Z"),
                end=_ts("2026-03-11T00:00:00Z"),
            ),
        ),
        forward_return_windows=(),
        costs=BacktestCosts(
            fee_bps_by_market={"spot": 10.0, "futures": 5.0},
            slippage_bps_by_tier={"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0},
            funding_mode="historical_series",
        ),
        baseline_name="current_system",
        variant_name="auditable_baseline",
        universe=UniverseFilterConfig(
            listing_age_days=30,
            min_quote_volume_usdt_24h={"spot": 1_000_000.0, "futures": 1_000_000.0},
            require_complete_funding=True,
        ),
        capital=CapitalModelConfig(
            model="shared_pool",
            initial_equity=100_000.0,
            risk_per_trade=0.02,
            max_open_risk=0.03,
        ),
    )

    config_path = tmp_path / "full_market_baseline.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": str(config.dataset_root),
                "experiment_kind": config.experiment_kind,
                "sample_windows": [
                    {
                        "name": config.sample_windows[0].name,
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-11T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "costs": {
                    "fee_bps": config.costs.fee_bps_by_market,
                    "slippage_tiers": config.costs.slippage_bps_by_tier,
                    "funding_mode": config.costs.funding_mode,
                },
                "baseline_name": config.baseline_name,
                "variant_name": config.variant_name,
                "universe": {
                    "listing_age_days": config.universe.listing_age_days,
                    "min_quote_volume_usdt_24h": config.universe.min_quote_volume_usdt_24h,
                    "require_complete_funding": config.universe.require_complete_funding,
                },
                "capital": {
                    "model": config.capital.model,
                    "initial_equity": config.capital.initial_equity,
                    "risk_per_trade": config.capital.risk_per_trade,
                    "max_open_risk": config.capital.max_open_risk,
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_render_full_market_baseline_report_contains_summary_breakdowns_and_audit_counts() -> None:
    renderer = getattr(reporting, "render_full_market_baseline_report", None)
    assert callable(renderer), "render_full_market_baseline_report is missing"

    report = renderer(sample_baseline_result())

    assert report["summary"]["total_return"] == pytest.approx(0.12)
    assert "by_market" in report["breakdowns"]
    assert "by_year" in report["breakdowns"]
    assert report["audit"]["rejection_count"] == 3
    first_trade = report["trades"][0]
    assert first_trade["entry_reference_timeframe"] == "15m"
    assert first_trade["entry_reference_price"] == pytest.approx(100.0)
    assert first_trade["gate_timeframes"] == ["daily", "4h", "1h"]
    assert first_trade["trigger_timeframes"] == ["30m", "15m"]
    assert first_trade["fill_model"] == "reference_close"
    assert first_trade["execution_price_source"] == "ohlcv_close"
    assert first_trade["fill_quality"] == "approximate"


def test_backtest_cli_writes_full_market_baseline_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "replay_full_market_baseline", lambda config: sample_baseline_result(), raising=False)

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(sample_full_market_config(tmp_path)),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "full_market_baseline__current_system__auditable_baseline"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "breakdowns.json").exists()
    assert (bundle_dir / "audit.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "breakdowns.json",
        "audit.json",
        "trades.json",
        "trade_postmortem.md",
    ]
    trades = json.loads((bundle_dir / "trades.json").read_text(encoding="utf-8"))["trades"]
    assert trades and "exit_reason" in trades[0]
    assert trades[0]["entry_reference_timeframe"] == "15m"
    assert trades[0]["fill_model"] == "reference_close"
    assert trades[0]["fill_quality"] == "approximate"
    assert "逐单复盘" in (bundle_dir / "trade_postmortem.md").read_text(encoding="utf-8")



def test_backtest_cli_writes_rotation_suppression_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_rotation_suppression_experiment",
        lambda rows, *, evaluation_window, soft_score_floor: {
            "policies": {
                "current": {"bucket_level_pnl": 0.08, "trade_count": 2},
                "no_suppression": {"bucket_level_pnl": 0.12, "trade_count": 3},
                "soft_suppression": {"bucket_level_pnl": 0.1, "trade_count": 2},
            },
            "opportunity_kill_rate": 0.25,
            "avoid_loss_rate": 0.75,
            "rotation_comparison_rows": [{"symbol": "LINKUSDT", "current": "suppressed", "soft_suppression": "selected"}],
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "rotation_suppression_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "rotation_suppression__current_policy__soft_suppression"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "comparison_rows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "comparison_rows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_writes_allocator_friction_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_allocator_friction_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "variant_count": 3, "evaluation_window": evaluation_window},
            "variants": {
                "current_allocator": {
                    "allocation_summary": {"accepted_allocations": 2, "total_risk_budget": 0.02},
                    "frictions": {
                        "base": {"net_bucket_pnl": 0.03, "cost_drag": 0.004, "trade_count": 2},
                        "low": {"net_bucket_pnl": 0.031, "cost_drag": 0.003, "trade_count": 2},
                        "stressed": {"net_bucket_pnl": 0.02, "cost_drag": 0.008, "trade_count": 2},
                    },
                },
                "equal_weight_baseline": {
                    "allocation_summary": {"accepted_allocations": 3, "total_risk_budget": 0.03},
                    "frictions": {
                        "base": {"net_bucket_pnl": 0.04, "cost_drag": 0.005, "trade_count": 3},
                        "low": {"net_bucket_pnl": 0.041, "cost_drag": 0.004, "trade_count": 3},
                        "stressed": {"net_bucket_pnl": 0.025, "cost_drag": 0.01, "trade_count": 3},
                    },
                },
            },
            "comparison_rows": [{"allocator_variant": "current_allocator", "friction_scenario": "base", "net_bucket_pnl": 0.03}],
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "allocator_friction_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "allocator_friction__current_policy__allocator_fee_drag"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "comparison_rows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "comparison_rows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_writes_engine_filter_ablation_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_engine_filter_ablation_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "variant_count": 4, "evaluation_window": evaluation_window},
            "variants": {
                "trend_only": {"funnel": {"accepted_allocations": 2}, "performance": {"bucket_level_pnl": 0.02, "trade_count": 2}},
                "rotation_only": {"funnel": {"accepted_allocations": 1}, "performance": {"bucket_level_pnl": 0.01, "trade_count": 1}},
            },
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "engine_filter_ablation_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "engine_filter_ablation__current_policy__no_engine_filter"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "1d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }



def test_backtest_cli_filters_engine_filter_ablation_rows_to_sample_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    captured: dict[str, object] = {}

    def _fake_engine_filter(rows, *, evaluation_window):
        captured["run_ids"] = [row.run_id for row in rows]
        captured["evaluation_window"] = evaluation_window
        return {
            "metadata": {"snapshot_count": len(rows), "variant_count": 1, "evaluation_window": evaluation_window},
            "variants": {
                "trend_only": {
                    "funnel": {"accepted_allocations": len(rows)},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": len(rows)},
                }
            },
        }

    monkeypatch.setattr(cli, "run_engine_filter_ablation_experiment", _fake_engine_filter, raising=False)

    config_path = _write_experiment_fixture_config(tmp_path, "engine_filter_ablation_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train_only",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-10T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "engine_filter_ablation__current_policy__no_engine_filter"
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured["run_ids"] == ["row-001"]
    assert captured["evaluation_window"] == "1d"
    assert summary["metadata"]["snapshot_count"] == 1
    assert summary["metadata"]["sample_period"] == {
        "start": "2026-03-10T00:00:00+00:00",
        "end": "2026-03-10T00:00:00+00:00",
    }
    assert summary["metadata"]["window_counts"] == {"train_only": 1}
    assert scorecard["key_metrics"]["snapshot_count"] == 1



def test_backtest_cli_writes_long_gate_telemetry_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_long_gate_telemetry_experiment",
        lambda rows, *, evaluation_window: {
            "metadata": {"snapshot_count": 2, "engine_count": 2, "evaluation_window": evaluation_window},
            "engines": {
                "trend_long": {
                    "funnel": {"raw_candidates": 3, "accepted_allocations": 1},
                    "filter_counts": {"trend_filtered": 2, "selected": 1},
                    "performance": {"bucket_level_pnl": 0.01, "trade_count": 1},
                },
                "rotation_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {"overheat_filtered": 4, "selected": 0},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
            },
            "symbol_breakdown": {
                "trend_long": {
                    "BTCUSDT": {
                        "snapshot_count": 1,
                        "funnel": {"raw_candidates": 1, "validated_candidates": 1, "allocation_decisions": 1, "accepted_allocations": 1},
                        "filter_counts": {"selected": 1},
                    }
                },
                "rotation_long": {},
            },
            "regime_breakdown": {
                "RISK_ON_TREND": {
                    "snapshot_count": 2,
                    "engines": {
                        "trend_long": {
                            "funnel": {"raw_candidates": 1, "validated_candidates": 1, "allocation_decisions": 1, "accepted_allocations": 1},
                            "filter_counts": {"selected": 1},
                            "performance": {"bucket_level_pnl": 0.01, "trade_count": 1},
                        },
                        "rotation_long": {
                            "funnel": {"raw_candidates": 0, "validated_candidates": 0, "allocation_decisions": 0, "accepted_allocations": 0},
                            "filter_counts": {"trend_filtered": 1, "selected": 0},
                            "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                        },
                    },
                }
            },
            "snapshot_rows": [
                {
                    "timestamp": "2026-03-10T00:00:00+00:00",
                    "run_id": "row-001",
                    "regime_label": "RISK_ON_TREND",
                    "total_long_raw_candidates": 1,
                    "total_long_accepted_allocations": 1,
                    "engines": {},
                }
            ],
        },
        raising=False,
    )

    config_path = _write_experiment_fixture_config(tmp_path, "long_gate_telemetry_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-12T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "long_gate_telemetry__current_policy__long_gate_diagnostic"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "snapshot_rows.json").exists()
    assert (bundle_dir / "symbol_breakdown.json").exists()
    assert (bundle_dir / "regime_breakdown.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "snapshot_rows.json",
        "symbol_breakdown.json",
        "regime_breakdown.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] == "keep_researching"
    assert scorecard["key_metrics"]["dominant_blocker_gate"] in {"trend_filtered", "overheat_filtered"}
    snapshot_rows = json.loads((bundle_dir / "snapshot_rows.json").read_text(encoding="utf-8"))
    assert snapshot_rows["rows"][0]["run_id"] == "row-001"
    symbol_breakdown = json.loads((bundle_dir / "symbol_breakdown.json").read_text(encoding="utf-8"))
    assert symbol_breakdown["engines"]["trend_long"]["BTCUSDT"]["funnel"]["accepted_allocations"] == 1
    regime_breakdown = json.loads((bundle_dir / "regime_breakdown.json").read_text(encoding="utf-8"))
    assert regime_breakdown["regimes"]["RISK_ON_TREND"]["engines"]["trend_long"]["funnel"]["accepted_allocations"] == 1



def test_render_long_gate_telemetry_prefers_specific_eligibility_blocker_in_scorecard() -> None:
    report = cli.render_long_gate_telemetry_report(
        experiment_name="long_gate_telemetry",
        metadata={"snapshot_count": 1, "evaluation_window": "3d"},
        experiment={
            "engines": {
                "trend_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {
                        "eligibility_filtered": 6,
                        "eligibility_liquidity_tier_filtered": 2,
                        "eligibility_pretrend_filtered": 1,
                        "eligibility_daily_return_filtered": 3,
                        "eligibility_h4_return_filtered": 1,
                        "selected": 0,
                    },
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
                "rotation_long": {
                    "funnel": {"raw_candidates": 0, "accepted_allocations": 0},
                    "filter_counts": {"trend_filtered": 2, "selected": 0},
                    "performance": {"bucket_level_pnl": 0.0, "trade_count": 0},
                },
            },
            "snapshot_rows": [],
        },
    )

    assert report["scorecard"]["key_metrics"]["dominant_blocker_engine"] == "trend_long"
    assert report["scorecard"]["key_metrics"]["dominant_blocker_gate"] == "eligibility_daily_return_filtered"



def test_backtest_cli_writes_walk_forward_validation_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    monkeypatch.setattr(
        cli,
        "run_walk_forward_validation_experiment",
        lambda rows, *, evaluation_window, in_sample_size, out_of_sample_size, step_size, config=None: {
            "metadata": {
                "snapshot_count": 2,
                "window_count": 1,
                "evaluation_window": evaluation_window,
                "in_sample_size": in_sample_size,
                "out_of_sample_size": out_of_sample_size,
                "step_size": step_size,
            },
            "windows": [
                {
                    "window_index": 1,
                    "out_of_sample": {
                        "scorecard": {"total_return": 0.03, "trade_count": 1},
                        "run_ids": ["row-002"],
                    },
                }
            ],
            "robustness_summary": {
                "out_of_sample_scorecard": {"total_return": 0.03, "trade_count": 1},
                "performance_dispersion": {"positive_window_ratio": 1.0},
            },
            "parameter_stability": {"parameter_stability_score": 0.8},
        },
        raising=False,
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(_write_experiment_fixture_config(tmp_path, "walk_forward_validation_config.json")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "walk_forward_validation__current_policy__rolling_walk_forward"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "windows.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "windows.json",
        "scorecard.json",
    ]
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }

def test_public_strategy_factor_report_surfaces_effectiveness_counts() -> None:
    report = reporting.render_public_strategy_factor_report(
        experiment_name="public_strategy_factors",
        experiment={
            "summary": {
                "supported_factor_count": 2,
                "unsupported_factor_count": 1,
                "data_gap_count": 1,
                "evaluated_factor_count": 2,
                "effective_factor_count": 1,
            },
            "factors": [
                {
                    "source_strategy_family": "momentum",
                    "factor_name": "momentum_3d",
                    "supported": True,
                    "effectiveness": {"effectiveness_status": "promising_research", "information_coefficient": 0.9},
                }
            ],
        },
        metadata={
            "snapshot_count": 8,
            "evaluation_window": "3d",
            "baseline_name": "public_strategy_scan",
            "variant_name": "factor_catalog_v1",
        },
    )

    assert report["scorecard"]["key_metrics"]["evaluated_factor_count"] == 2
    assert report["scorecard"]["key_metrics"]["effective_factor_count"] == 1
    assert report["scorecard"]["decision_summary"]["decision"] == "keep_researching"


def test_backtest_cli_writes_public_strategy_factor_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = [
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-10T00:00:00Z"),
            run_id="row-001",
            market={"symbols": {"BTCUSDT": {"close": 100.0}}},
            derivatives=[{"symbol": "BTCUSDT", "funding_rate": 0.0001}],
            account={"equity": 100_000.0},
            forward_returns={"3d": 0.04},
            forward_drawdowns={"3d": -0.03},
        ),
        DatasetSnapshotRow(
            timestamp=_ts("2026-03-12T00:00:00Z"),
            run_id="row-002",
            market={"symbols": {"ETHUSDT": {"close": 110.0}}},
            derivatives=[],
            account={"equity": 100_000.0},
            forward_returns={"3d": -0.02},
            forward_drawdowns={"3d": -0.05},
        ),
    ]
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: rows, raising=False)

    exit_code = cli.main([
        "run",
        "--config",
        str(_write_experiment_fixture_config(tmp_path, "public_strategy_factors_config.json")),
        "--output-dir",
        str(tmp_path / "out"),
    ])

    bundle_dir = tmp_path / "out" / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert exit_code == 0
    assert (bundle_dir / "manifest.json").exists()
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "factor_catalog.json").exists()
    assert (bundle_dir / "scorecard.json").exists()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == [
        "manifest.json",
        "summary.json",
        "factor_catalog.json",
        "scorecard.json",
    ]
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))
    factors = {item["factor_name"]: item for item in catalog["factors"]}
    assert factors["momentum_3d"]["source_strategy_family"] == "momentum"
    assert factors["momentum_3d"]["supported"] is True
    assert factors["funding_basis"]["supported"] is False
    assert factors["funding_basis"]["unsupported_reason"] == "insufficient_funding_or_basis_fields"

    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["metadata"]["evaluation_window"] == "3d"
    assert scorecard["key_metrics"]["snapshot_count"] == 2
    assert scorecard["key_metrics"]["supported_factor_count"] >= 2
    assert scorecard["decision_summary"]["decision"] == "keep_researching"


def test_public_strategy_factor_cli_surfaces_flat_tiny_sample_effectiveness_fields(
    fixture_dir: Path, tmp_path: Path
) -> None:
    exit_code = cli.main(
        [
            "run",
            "--config",
            str(fixture_dir / "backtest" / "public_strategy_factors_config.json"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert exit_code == 0
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "scorecard.json").exists()
    assert (bundle_dir / "factor_catalog.json").exists()

    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))

    assert summary["sample_count"] == 3
    assert summary["minimum_sample_count"] == 30
    assert summary["effective_factor_count"] == 0
    assert summary["decision"] == "keep_researching"
    assert scorecard["decision_summary"]["decision"] == "keep_researching"

    evaluated_tiny_sample_factors = [
        factor
        for factor in catalog["factors"]
        if factor["supported"] and factor["sample_count"] == 3
    ]
    assert evaluated_tiny_sample_factors
    for factor in evaluated_tiny_sample_factors:
        assert factor["effectiveness_status"] == "insufficient_sample"
        assert factor["sample_count"] == 3
        assert (
            "information_coefficient" in factor or "rank_correlation" in factor
        )
        assert "top_minus_bottom_forward_return" in factor
        assert "top_bucket_hit_rate" in factor


def test_public_strategy_factor_cli_generates_config_for_imported_dataset_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "imported_dataset"
    config_path = tmp_path / "public_strategy_factors_real_history.json"
    output_dir = tmp_path / "out"
    _write_imported_public_strategy_dataset(dataset_root)

    generate_exit_code = cli.main(
        [
            "write-public-strategy-factors-config",
            "--dataset-root",
            str(dataset_root),
            "--output-config",
            str(config_path),
            "--minimum-effectiveness-sample-count",
            "4",
        ]
    )
    run_exit_code = cli.main(["run", "--config", str(config_path), "--output-dir", str(output_dir)])

    bundle_dir = output_dir / "public_strategy_factors__public_strategy_scan__factor_catalog_v1"
    assert generate_exit_code == 0
    assert run_exit_code == 0
    generated = json.loads(config_path.read_text(encoding="utf-8"))
    assert generated["dataset_root"] == str(dataset_root)
    assert generated["sample_windows"] == [
        {
            "name": "imported_history",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-04T00:00:00Z",
            "split": "in_sample",
        }
    ]

    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    catalog = json.loads((bundle_dir / "factor_catalog.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 4
    assert summary["metadata"]["imported_dataset"]["import_manifest"]["manifest_snapshot_count"] == 4
    assert summary["metadata"]["minimum_effectiveness_sample_count"] == 4
    assert catalog["metadata"]["imported_dataset"]["dataset_root_type"] == "imported_archive"
    momentum = next(item for item in catalog["factors"] if item["factor_name"] == "momentum_3d")
    assert momentum["effectiveness"]["sample_count"] == 4
    assert momentum["effectiveness"]["minimum_sample_count"] == 4
    assert momentum["effectiveness"]["effectiveness_status"] == "promising_research"


def test_full_market_baseline_runbook_documents_required_inputs_outputs_and_limitations() -> None:
    runbook = Path("trading_system/docs/BACKTEST_RUNBOOK.md").read_text(encoding="utf-8")

    assert "instrument_snapshot.json" in runbook
    assert "full_market_baseline" in runbook
    assert "listing_age_days" in runbook
    assert "min_quote_volume_usdt_24h" in runbook
    assert "risk_per_trade" in runbook
    assert "max_open_risk" in runbook
    assert "manifest.json" in runbook
    assert "summary.json" in runbook
    assert "breakdowns.json" in runbook
    assert "audit.json" in runbook
    assert "rejection_reasons" in runbook
    assert "parameter search" in runbook
    assert "walk-forward" in runbook
    assert "order-book simulation" in runbook



def test_backtest_docs_cover_run_compare_gate_and_roadmap() -> None:
    runbook = Path("trading_system/docs/BACKTEST_RUNBOOK.md").read_text(encoding="utf-8")
    promotion_gate = Path("trading_system/docs/BACKTEST_PROMOTION_GATE.md").read_text(encoding="utf-8")
    roadmap = Path("trading_system/docs/BACKTEST_ROADMAP.md").read_text(encoding="utf-8")

    assert "python -m trading_system.app.backtest.cli run" in runbook
    assert "python -m trading_system.app.backtest.cli compare" in runbook
    assert "promotion_gate.json" in runbook
    assert "decision_summary.json" in runbook
    assert "scorecard.json" in runbook
    assert "什么时候允许进入“改交易程序”阶段" in runbook or "什么时候允许改交易程序" in runbook
    assert "必须继续研究，不准改源码" in runbook or "继续研究，不进入主线" in runbook
    assert "rotation_suppression" in runbook
    assert "allocator_friction" in runbook
    assert "engine_filter_ablation" in runbook
    assert "walk_forward_validation" in runbook

    assert '"decision": "hold"' in promotion_gate
    assert '"has_runtime_observability_plan": false' in promotion_gate
    assert '"has_rollback_plan": false' in promotion_gate
    assert '"why": ["missing out-of-sample evidence"]' in promotion_gate

    assert "已实现 experiment" in roadmap
    assert "已接入 CLI" in roadmap
    assert "已接入 gate" in roadmap
    assert "rotation suppression" in roadmap
    assert "allocator" in roadmap
    assert "walk-forward" in roadmap

def test_render_llm_trend_breakout_report_builds_summary_candidate_rows_and_scorecard() -> None:
    experiment = {
        "summary": {
            "snapshot_count": 2,
            "technical_candidate_count": 2,
            "accepted_candidate_count": 1,
            "rejected_candidate_count": 1,
            "acceptance_rate": 0.5,
            "rejection_reasons": {"high_event_risk": 1},
        },
        "candidate_rows": [
            {"symbol": "SOLUSDT", "decision": "accepted", "reasons": ["llm_filter_passed"]},
            {"symbol": "BTCUSDT", "decision": "rejected", "reasons": ["high_event_risk"]},
        ],
    }

    report = reporting.render_llm_trend_breakout_report(
        experiment_name="llm_trend_breakout",
        experiment=experiment,
        metadata={
            "dataset_root": "sample_dataset",
            "baseline_name": "trend-breakout",
            "variant_name": "llm-filtered",
            "evaluation_window": "1d",
            "snapshot_count": 2,
        },
    )

    assert set(report) == {"summary", "candidate_rows", "scorecard"}
    assert report["summary"]["summary"]["accepted_candidate_count"] == 1
    assert report["candidate_rows"]["rows"] == experiment["candidate_rows"]
    assert report["scorecard"]["key_metrics"] == {
        "snapshot_count": 2,
        "technical_candidate_count": 2,
        "accepted_candidate_count": 1,
        "rejected_candidate_count": 1,
        "acceptance_rate": 0.5,
    }
    assert report["scorecard"]["decision_summary"]["decision"] == "keep_researching"


def test_backtest_cli_writes_llm_trend_breakout_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "load_historical_dataset", lambda _dataset_root: _sample_dataset_rows(), raising=False)
    captured: dict[str, object] = {}

    def _fake_llm_runner(rows, *, params):
        captured["run_ids"] = [row.run_id for row in rows]
        captured["params"] = params
        return {
            "summary": {
                "snapshot_count": len(rows),
                "technical_candidate_count": 2,
                "accepted_candidate_count": 1,
                "rejected_candidate_count": 1,
                "acceptance_rate": 0.5,
                "rejection_reasons": {"missing_llm_label": 1},
            },
            "candidate_rows": [
                {
                    "timestamp": "2026-03-10T00:00:00+00:00",
                    "symbol": "SOLUSDT",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "technical_score": 0.8,
                    "sentiment_score": 0.1,
                    "final_score": 0.9,
                    "decision": "accepted",
                    "reasons": ["llm_filter_passed"],
                    "event_risk": "low",
                    "fomo_risk": "low",
                    "label_confidence": 0.8,
                }
            ],
        }

    monkeypatch.setattr(cli, "run_llm_trend_breakout_experiment", _fake_llm_runner, raising=False)
    config_path = _write_experiment_fixture_config(tmp_path, "llm_trend_breakout_config.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["sample_windows"] = [
        {
            "name": "train_only",
            "start": "2026-03-10T00:00:00Z",
            "end": "2026-03-10T00:00:00Z",
            "split": "in_sample",
        }
    ]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    bundle_dir = tmp_path / "out" / "llm_trend_breakout__trend-breakout__llm-filtered"
    assert exit_code == 0
    assert captured["run_ids"] == ["row-001"]
    assert captured["params"].llm_label_path == str(config_path.parent / "llm_labels/sample_labels.json")
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == ["manifest.json", "summary.json", "candidate_rows.json", "scorecard.json"]
    summary = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    candidate_rows = json.loads((bundle_dir / "candidate_rows.json").read_text(encoding="utf-8"))
    scorecard = json.loads((bundle_dir / "scorecard.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["snapshot_count"] == 1
    assert candidate_rows["rows"][0]["symbol"] == "SOLUSDT"
    assert scorecard["metadata"]["evaluation_window"] == "1d"
    assert scorecard["decision_summary"]["decision"] in {
        "keep_researching",
        "candidate_for_promotion",
        "reject",
    }
