from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.dataset import load_historical_dataset, split_rows_by_windows
from trading_system.app.backtest.types import DatasetSnapshotRow, ExperimentMetadata, ForwardReturnWindow


def test_backtest_shared_types_can_be_instantiated() -> None:
    metadata = ExperimentMetadata(
        name="phase0-foundation",
        experiment_kind="regime_research",
        dataset_root=Path("sample"),
        baseline_name="current_policy",
        variant_name="no_rotation_suppression",
    )
    row = DatasetSnapshotRow(
        timestamp=datetime(2026, 3, 10, tzinfo=UTC),
        run_id="sample-001",
        market={"symbols": {}},
        derivatives=[],
        account={"equity": 100_000.0},
        forward_returns={"1d": 0.02},
    )
    window = ForwardReturnWindow(name="3d", hours=72)

    assert metadata.variant_name == "no_rotation_suppression"
    assert row.run_id == "sample-001"
    assert window.hours == 72


def test_load_backtest_config(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "minimal_config.json")

    assert config.dataset_root == fixture_dir / "backtest" / "sample_dataset"
    assert config.experiment_kind == "regime_research"
    assert [window.name for window in config.sample_windows] == ["train", "validation"]
    assert config.costs.fee_bps == pytest.approx(4.0)
    assert config.costs.slippage_bps == pytest.approx(6.0)
    assert config.baseline_name == "current_policy"
    assert config.variant_name == "no_rotation_suppression"


def test_load_backtest_config_requires_dataset_root(tmp_path: Path) -> None:
    config_path = tmp_path / "broken_config.json"
    config_path.write_text(
        '{"experiment_kind": "regime_research", "sample_windows": [], "costs": {}, "baseline_name": "a", "variant_name": "b"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required field: dataset_root"):
        load_backtest_config(config_path)


def test_load_backtest_config_parses_full_market_baseline_contract(tmp_path: Path) -> None:
    config_path = tmp_path / "full_market_baseline.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": "./dataset",
                "experiment_kind": "full_market_baseline",
                "baseline_name": "current_system",
                "variant_name": "auditable_baseline",
                "sample_windows": [
                    {
                        "name": "full_history",
                        "start": "2021-01-01T00:00:00Z",
                        "end": "2026-01-01T00:00:00Z",
                        "split": "in_sample",
                    }
                ],
                "markets": ["spot", "futures"],
                "universe": {
                    "listing_age_days": 90,
                    "min_quote_volume_usdt_24h": {"spot": 5_000_000, "futures": 20_000_000},
                    "require_complete_funding": True,
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100000.0,
                    "risk_per_trade": 0.005,
                    "max_open_risk": 0.03,
                },
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {
                        "top": 4.0,
                        "high": 8.0,
                        "medium": 15.0,
                        "low": 30.0,
                    },
                    "funding_mode": "historical_series",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = load_backtest_config(config_path)

    assert loaded.experiment_kind == "full_market_baseline"
    assert loaded.markets == ("spot", "futures")
    assert loaded.capital.model == "shared_pool"
    assert loaded.capital.risk_per_trade == pytest.approx(0.005)
    assert loaded.universe.require_complete_funding is True
    assert loaded.costs.fee_bps_by_market["futures"] == pytest.approx(5.0)
    assert loaded.costs.slippage_bps_by_tier["medium"] == pytest.approx(15.0)


def test_load_historical_dataset_orders_rows_and_applies_baseline_account(fixture_dir: Path) -> None:
    rows = load_historical_dataset(fixture_dir / "backtest" / "sample_dataset")

    assert [row.run_id for row in rows] == ["sample-001", "sample-002", "sample-003"]
    assert rows[0].timestamp < rows[1].timestamp < rows[2].timestamp
    assert rows[1].account is not None
    assert rows[1].account["meta"]["account_type"] == "paper"


def test_load_historical_dataset_fails_when_required_snapshot_is_missing(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text('{"timestamp": "2026-03-10T00:00:00Z", "run_id": "broken"}', encoding="utf-8")
    (bundle / "market_context.json").write_text('{"symbols": {}}', encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="derivatives_snapshot.json"):
        load_historical_dataset(dataset_root)


def test_split_rows_by_windows_is_deterministic(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "minimal_config.json")
    rows = load_historical_dataset(config.dataset_root)

    split = split_rows_by_windows(rows, config.sample_windows)

    assert [row.run_id for row in split["train"]] == ["sample-001", "sample-002"]
    assert [row.run_id for row in split["validation"]] == ["sample-003"]
