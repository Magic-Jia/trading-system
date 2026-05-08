from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.app.backtest.config import load_backtest_config
from trading_system.app.backtest.dataset import (
    load_dataset_root_metadata,
    load_historical_dataset,
    split_rows_by_windows,
)
from trading_system.app.backtest.types import (
    DatasetSnapshotRow,
    ExperimentMetadata,
    ForwardReturnWindow,
    InstrumentSnapshotRow,
    PromotionMetadata,
)


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


def test_load_historical_dataset_loads_normalized_instrument_rows(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": [{"symbol": "BTCUSDT"}]}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")
    (bundle / "instrument_snapshot.json").write_text(
        """
        {
          "as_of": "2026-03-10T00:00:00Z",
          "schema_version": "imported_instrument_snapshot.v1",
          "rows": [
            {
              "symbol": "BTCUSDT",
              "market_type": "futures",
              "base_asset": "BTC",
              "listing_timestamp": "2020-01-01T00:00:00Z",
              "quote_volume_usdt_24h": 250000000.0,
              "liquidity_tier": "high",
              "quantity_step": 0.001,
              "price_tick": 0.1,
              "has_complete_funding": true
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    rows = load_historical_dataset(dataset_root)

    assert len(rows) == 1
    assert rows[0].instrument_rows == (
        InstrumentSnapshotRow(
            symbol="BTCUSDT",
            market_type="futures",
            base_asset="BTC",
            listing_timestamp=datetime(2020, 1, 1, tzinfo=UTC),
            quote_volume_usdt_24h=250_000_000.0,
            liquidity_tier="high",
            quantity_step=0.001,
            price_tick=0.1,
            has_complete_funding=True,
        ),
    )


def test_load_historical_dataset_rejects_non_object_account_snapshot(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text(json.dumps([["equity", 100000.0]]), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset bundle has invalid account snapshot"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_noncanonical_metadata_identity_fields(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        json.dumps({"timestamp": "2026-03-10T00:00:00Z", "run_id": True}),
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")

    with pytest.raises(ValueError, match="metadata.run_id must be a canonical string"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_non_boolean_instrument_funding_flag(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")
    (bundle / "instrument_snapshot.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "market_type": "futures",
                        "base_asset": "BTC",
                        "listing_timestamp": "2020-01-01T00:00:00Z",
                        "quote_volume_usdt_24h": 250000000.0,
                        "liquidity_tier": "high",
                        "quantity_step": 0.001,
                        "price_tick": 0.1,
                        "has_complete_funding": "false",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="instrument has_complete_funding must be a boolean"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_noncanonical_instrument_identity_fields(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")
    (bundle / "instrument_snapshot.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "symbol": True,
                        "market_type": "futures",
                        "base_asset": "BTC",
                        "listing_timestamp": "2020-01-01T00:00:00Z",
                        "quote_volume_usdt_24h": 250000000.0,
                        "liquidity_tier": "high",
                        "quantity_step": 0.001,
                        "price_tick": 0.1,
                        "has_complete_funding": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="instrument symbol must be a canonical string"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_boolean_instrument_numeric_fields(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")
    (bundle / "instrument_snapshot.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "symbol": "BTCUSDT",
                        "market_type": "futures",
                        "base_asset": "BTC",
                        "listing_timestamp": "2020-01-01T00:00:00Z",
                        "quote_volume_usdt_24h": 250000000.0,
                        "liquidity_tier": "high",
                        "quantity_step": True,
                        "price_tick": 0.1,
                        "has_complete_funding": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="instrument quantity_step must be a positive finite number"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_non_object_derivatives_rows(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        '{"timestamp": "2026-03-10T00:00:00Z", "run_id": "sample-001"}',
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text(
        json.dumps({"rows": [[("symbol", "BTCUSDT")]]}), encoding="utf-8"
    )
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")

    with pytest.raises(ValueError, match="dataset bundle has invalid derivatives row payload"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_non_object_forward_returns(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-03-10T00:00:00Z",
                "run_id": "sample-001",
                "forward_returns": [],
                "forward_drawdowns": {},
            }
        ),
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")

    with pytest.raises(ValueError, match="metadata.forward_returns must be an object"):
        load_historical_dataset(dataset_root)


def test_load_historical_dataset_rejects_noncanonical_forward_return_keys(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    bundle = dataset_root / "2026-03-10T00-00-00Z__sample-001"
    bundle.mkdir(parents=True)
    (bundle / "metadata.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-03-10T00:00:00Z",
                "run_id": "sample-001",
                "forward_returns": {" 1d": 0.02},
                "forward_drawdowns": {},
            }
        ),
        encoding="utf-8",
    )
    (bundle / "market_context.json").write_text('{"symbols": {"BTCUSDT": {}}}', encoding="utf-8")
    (bundle / "derivatives_snapshot.json").write_text('{"rows": []}', encoding="utf-8")
    (bundle / "account_snapshot.json").write_text('{"equity": 100000.0}', encoding="utf-8")

    with pytest.raises(ValueError, match="metadata.forward_returns key must be a canonical string"):
        load_historical_dataset(dataset_root)


def test_load_dataset_root_metadata_surfaces_imported_manifest_summary(tmp_path: Path) -> None:
    dataset_root = tmp_path / "imported_dataset"
    dataset_root.mkdir()
    (dataset_root / "import_manifest.json").write_text(
        """
        {
          "schema_version": "phase1_imported_dataset_root.v1",
          "scope": "phase1_binance_futures",
          "archive_root": "/tmp/archive",
          "dataset_root": "/tmp/imported_dataset",
          "snapshot_count": 42,
          "symbols": ["BTCUSDT", "ETHUSDT"],
          "start_timestamp": "2025-01-01T00:00:00Z",
          "end_timestamp": "2025-02-11T00:00:00Z",
          "bundle_dirs": ["bundle-a", "bundle-b"],
          "source": {"scope": "phase1_binance_futures"},
          "coverage": {
            "ohlcv_timeframes": {
              "available": ["1h", "5m"],
              "materialized": ["1h", "5m"],
              "missing_optional": ["1m", "15m", "30m"],
              "not_materialized": {}
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    metadata = load_dataset_root_metadata(dataset_root)

    assert metadata == {
        "dataset_root_type": "imported_archive",
        "import_manifest_path": str(dataset_root / "import_manifest.json"),
        "import_manifest": {
            "schema_version": "phase1_imported_dataset_root.v1",
            "scope": "phase1_binance_futures",
            "archive_root": "/tmp/archive",
            "dataset_root": "/tmp/imported_dataset",
            "manifest_snapshot_count": 42,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "start_timestamp": "2025-01-01T00:00:00Z",
            "end_timestamp": "2025-02-11T00:00:00Z",
            "bundle_count": 2,
            "source": {"scope": "phase1_binance_futures"},
            "coverage": {
                "ohlcv_timeframes": {
                    "available": ["1h", "5m"],
                    "materialized": ["1h", "5m"],
                    "missing_optional": ["1m", "15m", "30m"],
                    "not_materialized": {},
                }
            },
        },
    }


def test_load_dataset_root_metadata_rejects_non_object_manifest_source(tmp_path: Path) -> None:
    dataset_root = tmp_path / "imported_dataset"
    dataset_root.mkdir()
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "phase1_imported_dataset_root.v1",
                "scope": "phase1_binance_futures",
                "archive_root": "/tmp/archive",
                "dataset_root": "/tmp/imported_dataset",
                "snapshot_count": 0,
                "symbols": [],
                "bundle_dirs": [],
                "source": [],
                "coverage": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="import manifest source must be an object"):
        load_dataset_root_metadata(dataset_root)


def test_load_dataset_root_metadata_rejects_non_strict_snapshot_count(tmp_path: Path) -> None:
    dataset_root = tmp_path / "imported_dataset"
    dataset_root.mkdir()
    (dataset_root / "import_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "phase1_imported_dataset_root.v1",
                "scope": "phase1_binance_futures",
                "archive_root": "/tmp/archive",
                "dataset_root": "/tmp/imported_dataset",
                "snapshot_count": True,
                "symbols": [],
                "bundle_dirs": [],
                "source": {},
                "coverage": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="import manifest snapshot_count must be a non-negative integer"):
        load_dataset_root_metadata(dataset_root)


def test_load_backtest_config(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "minimal_config.json")

    assert config.dataset_root == fixture_dir / "backtest" / "sample_dataset"
    assert config.experiment_kind == "regime_research"
    assert [window.name for window in config.sample_windows] == ["train", "validation"]
    assert config.costs.fee_bps == pytest.approx(4.0)
    assert config.costs.slippage_bps == pytest.approx(6.0)
    assert config.baseline_name == "current_policy"
    assert config.variant_name == "no_rotation_suppression"
    assert config.experiment_params is None


def test_load_backtest_config_requires_dataset_root(tmp_path: Path) -> None:
    config_path = tmp_path / "broken_config.json"
    config_path.write_text(
        '{"experiment_kind": "regime_research", "sample_windows": [], "costs": {}, "baseline_name": "a", "variant_name": "b"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required field: dataset_root"):
        load_backtest_config(config_path)


def test_load_backtest_config_parses_full_market_baseline_contract(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    config_path = tmp_path / "full_market_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "forward_return_windows": [
            {
              "name": "1d",
              "hours": 24
            }
          ],
          "universe": {
            "listing_age_days": 45,
            "min_quote_volume_usdt_24h": {
              "spot": 1000000.0,
              "futures": 5000000.0
            },
            "require_complete_funding": true
          },
          "capital": {
            "model": "shared_pool",
            "initial_equity": 250000.0,
            "risk_per_trade": 0.01,
            "max_open_risk": 0.05
          },
          "costs": {
            "fee_bps": {
              "spot": 10.0,
              "futures": 5.0
            },
            "slippage_tiers": {
              "deep": 2.5,
              "mid": 6.0,
              "thin": 12.0
            },
            "funding_mode": "historical_series"
          },
          "baseline_name": "market-wide",
          "variant_name": "baseline-v1"
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.dataset_root == dataset_root
    assert config.experiment_kind == "full_market_baseline"
    assert config.universe is not None
    assert config.universe.listing_age_days == 45
    assert config.universe.min_quote_volume_usdt_24h == {"spot": 1000000.0, "futures": 5000000.0}
    assert config.universe.require_complete_funding is True
    assert config.capital is not None
    assert config.capital.model == "shared_pool"
    assert config.capital.initial_equity == pytest.approx(250000.0)
    assert config.capital.risk_per_trade == pytest.approx(0.01)
    assert config.capital.max_open_risk == pytest.approx(0.05)
    assert config.costs.fee_bps_by_market == {"spot": 10.0, "futures": 5.0}
    assert config.costs.slippage_bps_by_tier == {"deep": 2.5, "mid": 6.0, "thin": 12.0}
    assert config.costs.funding_mode == "historical_series"
    assert config.experiment_params is None


def test_load_backtest_config_parses_full_market_baseline_disabled_engines(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    config_path = tmp_path / "full_market_disabled_engines_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "forward_return_windows": [],
          "universe": {
            "listing_age_days": 30,
            "min_quote_volume_usdt_24h": {
              "spot": 1000000.0,
              "futures": 1000000.0
            },
            "require_complete_funding": true
          },
          "capital": {
            "model": "shared_pool",
            "initial_equity": 100000.0,
            "risk_per_trade": 0.01,
            "max_open_risk": 0.03
          },
          "costs": {
            "fee_bps": {
              "spot": 10.0,
              "futures": 5.0
            },
            "slippage_tiers": {
              "top": 2.0,
              "high": 8.0,
              "medium": 15.0,
              "low": 30.0
            },
            "funding_mode": "historical_series"
          },
          "baseline_name": "market-wide",
          "variant_name": "baseline-no-short",
          "experiment_params": {
            "disabled_engines": ["short"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.dataset_root == dataset_root
    assert config.experiment_kind == "full_market_baseline"
    assert config.experiment_params is not None
    assert config.experiment_params.disabled_engines == ("short",)
    assert config.experiment_params.allowed_short_setup_types == ()


def test_load_backtest_config_parses_full_market_baseline_allowed_short_setup_types(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sample_dataset"
    config_path = tmp_path / "full_market_allowed_short_setup_types_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "forward_return_windows": [],
          "universe": {
            "listing_age_days": 30,
            "min_quote_volume_usdt_24h": {
              "spot": 1000000.0,
              "futures": 1000000.0
            },
            "require_complete_funding": true
          },
          "capital": {
            "model": "shared_pool",
            "initial_equity": 100000.0,
            "risk_per_trade": 0.01,
            "max_open_risk": 0.03
          },
          "costs": {
            "fee_bps": {
              "spot": 10.0,
              "futures": 5.0
            },
            "slippage_tiers": {
              "top": 2.0,
              "high": 8.0,
              "medium": 15.0,
              "low": 30.0
            },
            "funding_mode": "historical_series"
          },
          "baseline_name": "market-wide",
          "variant_name": "baseline-breakdown-short-only",
          "experiment_params": {
            "allowed_short_setup_types": ["BREAKDOWN_SHORT"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.dataset_root == dataset_root
    assert config.experiment_kind == "full_market_baseline"
    assert config.experiment_params is not None
    assert config.experiment_params.disabled_engines == ()
    assert config.experiment_params.allowed_short_setup_types == ("BREAKDOWN_SHORT",)


def test_load_backtest_config_parses_full_market_baseline_entry_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "full_market_intraday_multi_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "forward_return_windows": [],
          "universe": {
            "listing_age_days": 30,
            "min_quote_volume_usdt_24h": {
              "spot": 1000000.0,
              "futures": 1000000.0
            },
            "require_complete_funding": true
          },
          "capital": {
            "model": "shared_pool",
            "initial_equity": 100000.0,
            "risk_per_trade": 0.01,
            "max_open_risk": 0.03
          },
          "costs": {
            "fee_bps": {
              "spot": 10.0,
              "futures": 5.0
            },
            "slippage_tiers": {
              "top": 2.0,
              "high": 8.0,
              "medium": 15.0,
              "low": 30.0
            },
            "funding_mode": "historical_series"
          },
          "baseline_name": "market-wide",
          "variant_name": "intraday-multi",
          "experiment_params": {
            "entry_profile": "intraday_multi"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.experiment_params is not None
    assert config.experiment_params.entry_profile == "intraday_multi"


def test_load_backtest_config_parses_exit_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "exit_policy_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "costs": {
            "fee_bps": {
              "spot": 10.0,
              "futures": 5.0
            },
            "slippage_tiers": {
              "top": 2.0
            },
            "funding_mode": "historical_series"
          },
          "baseline_name": "market-wide",
          "variant_name": "exit-policy-config",
          "experiment_params": {
            "exit_policy": {
              "name": "after_cost_breakeven_stop",
              "after_cost_buffer_bps": 2.0,
              "activation_minute": 0
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.experiment_params is not None
    assert config.experiment_params.exit_policy is not None
    assert config.experiment_params.exit_policy.name == "after_cost_breakeven_stop"
    assert config.experiment_params.exit_policy.after_cost_buffer_bps == pytest.approx(2.0)
    assert config.experiment_params.exit_policy.activation_minute == 0
    assert config.experiment_params.exit_policy.giveback_fraction is None
    assert config.experiment_params.exit_policy.giveback_min_bps is None
    assert config.experiment_params.exit_policy.no_breakeven_time_stop_minute is None


def test_load_backtest_config_parses_setup_rewrite_rules(tmp_path: Path) -> None:
    config_path = tmp_path / "setup_rewrite_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {"name": "full_history", "start": "2026-03-10T00:00:00Z", "end": "2026-03-11T00:00:00Z"}
          ],
          "forward_return_windows": [],
          "costs": {
            "fee_bps": {"spot": 10.0, "futures": 5.0},
            "slippage_tiers": {"top": 2.0},
            "funding_mode": "historical_series"
          },
          "baseline_name": "current_system",
          "variant_name": "setup_rewrite_probe",
          "experiment_params": {
            "setup_rewrite": {
              "rules": [
                {"name": "require_min_score", "min_score": 0.72},
                {"name": "exclude_setup_types", "setup_types": ["RS_OVERHEAT", "late_breakout"]},
                {"name": "require_after_cost_breakeven_evidence"},
                {"name": "require_setup_min_score", "setup_types": ["rs_reacceleration"], "min_score": 0.74},
                {
                  "name": "require_setup_min_cost_coverage_ratio",
                  "setup_types": ["rs_pullback"],
                  "min_cost_coverage_ratio": 1.15
                },
                {"name": "require_setup_allowed_symbols", "setup_types": ["RS_PULLBACK"], "symbols": ["btcusdt", "ETHUSDT"]}
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.experiment_params is not None
    assert config.experiment_params.setup_rewrite is not None
    assert [rule.name for rule in config.experiment_params.setup_rewrite.rules] == [
        "require_min_score",
        "exclude_setup_types",
        "require_after_cost_breakeven_evidence",
        "require_setup_min_score",
        "require_setup_min_cost_coverage_ratio",
        "require_setup_allowed_symbols",
    ]
    assert config.experiment_params.setup_rewrite.rules[0].min_score == pytest.approx(0.72)
    assert config.experiment_params.setup_rewrite.rules[1].setup_types == ("RS_OVERHEAT", "LATE_BREAKOUT")
    assert config.experiment_params.setup_rewrite.rules[3].setup_types == ("RS_REACCELERATION",)
    assert config.experiment_params.setup_rewrite.rules[3].min_score == pytest.approx(0.74)
    assert config.experiment_params.setup_rewrite.rules[4].setup_types == ("RS_PULLBACK",)
    assert config.experiment_params.setup_rewrite.rules[4].min_cost_coverage_ratio == pytest.approx(1.15)
    assert config.experiment_params.setup_rewrite.rules[5].setup_types == ("RS_PULLBACK",)
    assert config.experiment_params.setup_rewrite.rules[5].symbols == ("BTCUSDT", "ETHUSDT")


@pytest.mark.parametrize(
    ("setup_rewrite", "message"),
    [
        ({"rules": [{"name": "invent_new_edge"}]}, "unknown setup rewrite rule"),
        ({"rules": [{"name": "require_min_score", "min_score": -0.1}]}, "min_score must be non-negative"),
        ({"rules": [{"name": "exclude_setup_types", "setup_types": "RS_OVERHEAT"}]}, "setup_types must be a list"),
        (
            {"rules": [{"name": "require_setup_min_score", "setup_types": [], "min_score": 0.7}]},
            "setup_types must not be empty",
        ),
        (
            {"rules": [{"name": "require_setup_min_score", "setup_types": ["RS_PULLBACK"], "min_score": -0.1}]},
            "min_score must be non-negative",
        ),
        (
            {
                "rules": [
                    {
                        "name": "require_setup_min_cost_coverage_ratio",
                        "setup_types": ["RS_PULLBACK"],
                        "min_cost_coverage_ratio": -0.1,
                    }
                ]
            },
            "min_cost_coverage_ratio must be non-negative",
        ),
        (
            {"rules": [{"name": "require_setup_allowed_symbols", "setup_types": ["RS_PULLBACK"], "symbols": []}]},
            "symbols must not be empty",
        ),
        ({"rules": [{"name": "require_after_cost_breakeven_evidence", "unexpected": True}]}, "unknown fields"),
    ],
)
def test_load_backtest_config_rejects_invalid_setup_rewrite(
    tmp_path: Path,
    setup_rewrite: dict[str, object],
    message: str,
) -> None:
    config_path = tmp_path / "invalid_setup_rewrite_config.json"
    config_path.write_text(
        f"""
        {{
          "dataset_root": "dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {{"name": "full_history", "start": "2026-03-10T00:00:00Z", "end": "2026-03-11T00:00:00Z"}}
          ],
          "forward_return_windows": [],
          "costs": {{
            "fee_bps": {{"spot": 10.0, "futures": 5.0}},
            "slippage_tiers": {{"top": 2.0}},
            "funding_mode": "historical_series"
          }},
          "baseline_name": "current_system",
          "variant_name": "setup_rewrite_probe",
          "experiment_params": {{
            "setup_rewrite": {json.dumps(setup_rewrite)}
          }}
        }}
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_backtest_config(config_path)


@pytest.mark.parametrize(
    ("exit_policy", "message"),
    [
        ({"name": "unknown_policy"}, "unknown exit policy"),
        ({"name": "after_cost_breakeven_stop", "after_cost_buffer_bps": -0.1}, "after_cost_buffer_bps"),
        ({"name": "after_cost_breakeven_stop", "activation_minute": -1}, "activation_minute"),
        ({"name": "mfe_giveback_cut", "giveback_fraction": -0.1}, "giveback_fraction"),
        ({"name": "mfe_giveback_cut", "giveback_fraction": 1.1}, "giveback_fraction"),
        ({"name": "mfe_giveback_cut", "giveback_min_bps": -1.0}, "giveback_min_bps"),
        (
            {"name": "no_breakeven_time_stop", "no_breakeven_time_stop_minute": -1},
            "no_breakeven_time_stop_minute",
        ),
    ],
)
def test_load_backtest_config_rejects_invalid_exit_policy(
    tmp_path: Path,
    exit_policy: dict[str, object],
    message: str,
) -> None:
    config_path = tmp_path / "invalid_exit_policy_config.json"
    config_path.write_text(
        f"""
        {{
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {{
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }}
          ],
          "costs": {{
            "fee_bps": {{
              "spot": 10.0,
              "futures": 5.0
            }},
            "slippage_tiers": {{
              "top": 2.0
            }},
            "funding_mode": "historical_series"
          }},
          "baseline_name": "market-wide",
          "variant_name": "invalid-exit-policy",
          "experiment_params": {{
            "exit_policy": {json.dumps(exit_policy)}
          }}
        }}
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_backtest_config(config_path)


def test_load_backtest_config_parses_rotation_suppression_experiment_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "rotation_suppression_config.json")

    assert config.experiment_kind == "rotation_suppression"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "3d"
    assert config.experiment_params.soft_score_floor == pytest.approx(0.72)
    assert config.experiment_params.walk_forward is None


def test_load_backtest_config_parses_allocator_friction_experiment_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "allocator_friction_config.json")

    assert config.experiment_kind == "allocator_friction"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "3d"
    assert config.experiment_params.soft_score_floor is None
    assert config.experiment_params.walk_forward is None


def test_load_backtest_config_parses_engine_filter_ablation_experiment_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "engine_filter_ablation_config.json")

    assert config.experiment_kind == "engine_filter_ablation"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "1d"
    assert config.experiment_params.soft_score_floor is None
    assert config.experiment_params.walk_forward is None



def test_load_backtest_config_parses_long_gate_telemetry_experiment_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "long_gate_telemetry_config.json")

    assert config.experiment_kind == "long_gate_telemetry"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "3d"
    assert config.experiment_params.soft_score_floor is None
    assert config.experiment_params.walk_forward is None



def test_load_backtest_config_parses_walk_forward_validation_experiment_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "walk_forward_validation_config.json")

    assert config.experiment_kind == "walk_forward_validation"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "3d"
    assert config.experiment_params.soft_score_floor is None
    assert config.experiment_params.walk_forward is not None
    assert config.experiment_params.walk_forward.in_sample_size == 90
    assert config.experiment_params.walk_forward.out_of_sample_size == 30
    assert config.experiment_params.walk_forward.step_size == 15


def test_load_backtest_config_parses_public_strategy_factor_families(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "public_strategy_factors_config.json")

    assert config.experiment_kind == "public_strategy_factors"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "3d"
    assert config.experiment_params.minimum_effectiveness_sample_count == 30
    assert config.experiment_params.public_strategy_families == (
        "trend_following",
        "momentum",
        "mean_reversion",
        "volatility_breakout",
        "liquidity_volume",
        "funding_basis",
        "onchain_flow",
    )


def test_load_backtest_config_parses_llm_trend_breakout_params(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "llm_trend_breakout_config.json")

    assert config.experiment_kind == "llm_trend_breakout"
    assert config.experiment_params is not None
    assert config.experiment_params.evaluation_window == "1d"
    assert config.experiment_params.entry_profile == "scout"
    assert config.experiment_params.llm_label_path == str(fixture_dir / "backtest" / "llm_labels" / "sample_labels.json")
    assert config.experiment_params.require_llm_label is True
    assert config.experiment_params.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert config.experiment_params.minimum_final_score == pytest.approx(0.75)
    assert config.experiment_params.minimum_label_confidence == pytest.approx(0.5)
    assert config.experiment_params.reject_high_fomo is False
    assert config.experiment_params.allowed_setup_types == ("BREAKOUT_CONTINUATION",)
    assert config.experiment_params.quarantined_setup_types == ()
    assert config.experiment_params.quarantined_short_setup_types == ()


def test_load_backtest_config_parses_quarantined_setup_types(tmp_path: Path) -> None:
    config_path = tmp_path / "general_quarantine_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "all",
              "start": "2026-03-01T00:00:00Z",
              "end": "2026-03-02T00:00:00Z"
            }
          ],
          "costs": {
            "fee_bps": {"spot": 4.0, "futures": 4.0},
            "slippage_tiers": {"top": 1.0},
            "funding_mode": "historical_series"
          },
          "baseline_name": "current",
          "variant_name": "quarantine",
          "experiment_params": {
            "quarantined_setup_types": ["rs_pullback", "RS_REACCELERATION", "rs_pullback"],
            "quarantined_short_setup_types": ["failed_bounce_short"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.experiment_params is not None
    assert config.experiment_params.quarantined_setup_types == ("RS_PULLBACK", "RS_REACCELERATION")
    assert config.experiment_params.quarantined_short_setup_types == ("FAILED_BOUNCE_SHORT",)


def test_load_backtest_config_rejects_non_list_quarantined_setup_types(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid_general_quarantine_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "all",
              "start": "2026-03-01T00:00:00Z",
              "end": "2026-03-02T00:00:00Z"
            }
          ],
          "costs": {
            "fee_bps": {"spot": 4.0, "futures": 4.0},
            "slippage_tiers": {"top": 1.0},
            "funding_mode": "historical_series"
          },
          "baseline_name": "current",
          "variant_name": "quarantine",
          "experiment_params": {
            "quarantined_setup_types": "RS_PULLBACK"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="experiment_params.quarantined_setup_types must be a list"):
        load_backtest_config(config_path)


def test_load_backtest_config_requires_llm_label_path_for_llm_trend_breakout(tmp_path: Path) -> None:
    config_path = tmp_path / "llm_trend_breakout_missing_labels.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "llm_trend_breakout",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "costs": {
            "fee_bps": 4.0,
            "slippage_bps": 6.0
          },
          "baseline_name": "trend-breakout",
          "variant_name": "llm-filtered",
          "experiment_params": {
            "evaluation_window": "1d"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="experiment_params.llm_label_path is required"):
        load_backtest_config(config_path)


def test_load_backtest_config_parses_promotion_metadata(tmp_path: Path) -> None:
    config_path = tmp_path / "promotion_metadata_config.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "walk_forward_validation",
          "sample_windows": [
            {
              "name": "train",
              "start": "2026-01-01T00:00:00Z",
              "end": "2026-02-01T00:00:00Z"
            }
          ],
          "costs": {
            "fee_bps": {"spot": 4.0, "futures": 4.0},
            "slippage_tiers": {"top": 1.0},
            "funding_mode": "historical_series"
          },
          "baseline_name": "current_policy",
          "variant_name": "candidate_policy",
          "experiment_params": {
            "evaluation_window": "3d",
            "walk_forward": {
              "in_sample_size": 90,
              "out_of_sample_size": 30,
              "step_size": 15
            }
          },
          "promotion_metadata": {
            "runtime_fields": ["regime", "allocator_decision_reason"],
            "rollback_target": "current_policy",
            "rollback_trigger": "oos_total_return_below_zero",
            "observation_window": "14d"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.promotion_metadata == PromotionMetadata(
        runtime_fields=("regime", "allocator_decision_reason"),
        rollback_target="current_policy",
        rollback_trigger="oos_total_return_below_zero",
        observation_window="14d",
    )


def test_load_backtest_config_parses_minimum_cost_coverage_ratio(tmp_path: Path) -> None:
    config_path = tmp_path / "full_market_baseline_cost_gate.json"
    config_path.write_text(
        """
        {
          "dataset_root": "sample_dataset",
          "experiment_kind": "full_market_baseline",
          "sample_windows": [
            {
              "name": "full_history",
              "start": "2026-03-10T00:00:00Z",
              "end": "2026-03-12T00:00:00Z"
            }
          ],
          "forward_return_windows": [],
          "universe": {
            "listing_age_days": 30,
            "min_quote_volume_usdt_24h": {"spot": 1000000.0, "futures": 1000000.0},
            "require_complete_funding": true
          },
          "capital": {
            "model": "shared_pool",
            "initial_equity": 100000.0,
            "risk_per_trade": 0.02,
            "max_open_risk": 0.03
          },
          "costs": {
            "fee_bps": {"spot": 10.0, "futures": 5.0},
            "slippage_tiers": {"top": 2.0, "high": 8.0},
            "funding_mode": "historical_series"
          },
          "baseline_name": "current_system",
          "variant_name": "cost_gate",
          "experiment_params": {
            "minimum_cost_coverage_ratio": 2.5
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    config = load_backtest_config(config_path)

    assert config.experiment_params is not None
    assert config.experiment_params.minimum_cost_coverage_ratio == pytest.approx(2.5)


def test_load_backtest_config_keeps_full_market_baseline_fixture_compatible(fixture_dir: Path) -> None:
    config = load_backtest_config(fixture_dir / "backtest" / "full_market_baseline.json")

    assert config.experiment_kind == "full_market_baseline"
    assert config.universe is not None
    assert config.capital is not None
    assert config.experiment_params is None


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
