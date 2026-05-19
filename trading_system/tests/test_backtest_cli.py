from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from trading_system.app.backtest import cli
from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import OrderIntent
from trading_system.run_cycle import _execution_sample_collection_health


FIXTURES = Path(__file__).parent / "fixtures" / "backtest"
GENERATED_AT = "2026-05-18T05:00:00Z"


def _run_cli(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


def _sample_order() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-btc-long",
        signal_id="signal-btc-long",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
    )


def _write_professional_config(path: Path, *, dataset_root: Path, experiment_kind: str) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset_root": str(dataset_root),
                "experiment_kind": experiment_kind,
                "sample_windows": [
                    {
                        "name": "smoke_window",
                        "start": "2026-03-10T00:00:00Z",
                        "end": "2026-03-12T00:00:00Z",
                    }
                ],
                "forward_return_windows": [],
                "universe": {
                    "listing_age_days": 30,
                    "min_quote_volume_usdt_24h": {"spot": 1000000.0, "futures": 1000000.0},
                    "require_complete_funding": True,
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100000.0,
                    "risk_per_trade": 0.02,
                    "max_open_risk": 0.03,
                },
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {"top": 2.0, "high": 8.0, "medium": 15.0, "low": 30.0},
                    "funding_mode": "historical_series",
                },
                "baseline_name": "current_system",
                "variant_name": f"diagnostic_{experiment_kind}",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


FORBIDDEN_ACCOUNT_FIELDS = {
    "margin_mode",
    "leverage",
    "maintenance_tier",
    "liquidation_price",
    "notional",
    "unrealized_pnl",
}


def _json_payloads(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*.json"))
    }


def _copy_schema_complete_metadata_dataset(tmp_path: Path) -> Path:
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    metadata_dataset = tmp_path / "schema-complete-metadata-dataset"
    shutil.copytree(source_dataset, metadata_dataset)
    for market_context_path in sorted(metadata_dataset.rglob("market_context.json")):
        payload = json.loads(market_context_path.read_text(encoding="utf-8"))
        symbols = payload["symbols"]
        for symbol_context in symbols.values():
            symbol_context["futures_context"] = {
                "funding_status": "available",
                "mark_price_status": "available",
                "open_interest_status": "available",
            }
        snapshot_id = market_context_path.parent.name.split("__", maxsplit=1)[0]
        snapshot_date, snapshot_time = snapshot_id.split("T", maxsplit=1)
        payload["as_of"] = f"{snapshot_date}T{snapshot_time.replace('-', ':').removesuffix(':')}"
        market_context_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata_dataset


def _copy_schema_complete_metadata_dataset_as_symlink_root(tmp_path: Path) -> Path:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    symlink_root = tmp_path / "schema-complete-metadata-symlink-root"
    symlink_root.mkdir()
    for snapshot_dir in sorted(path for path in metadata_dataset.iterdir() if path.is_dir()):
        (symlink_root / snapshot_dir.name).symlink_to(snapshot_dir, target_is_directory=True)
    return symlink_root


def _write_phase1_snapshot(
    snapshot_dir: Path,
    *,
    symbol: str = "BTCUSDTPERP",
    include_derivatives_row: bool = True,
    include_open_interest: bool = True,
) -> None:
    snapshot_dir.mkdir(parents=True)
    observed_at = "2026-01-01T00:00:00Z"
    (snapshot_dir / "instrument_snapshot.json").write_text(
        json.dumps(
            {
                "as_of": observed_at,
                "schema_version": "imported_instrument_snapshot.v1",
                "rows": [
                    {
                        "symbol": symbol,
                        "market_type": "futures",
                        "base_asset": "BTC",
                        "listing_timestamp": "2020-01-01T00:00:00Z",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (snapshot_dir / "market_context.json").write_text(
        json.dumps(
            {
                "as_of": observed_at,
                "symbols": {
                    symbol: {
                        "daily": {"close": 100.0, "volume_usdt_24h": 50000000.0},
                        "liquidity_tier": "top",
                        "sector": "majors",
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    derivatives_rows = []
    if include_derivatives_row:
        derivatives_row = {
            "symbol": symbol,
            "funding_rate": 0.0001,
            "mark_price_change_24h_pct": 0.02,
        }
        if include_open_interest:
            derivatives_row["open_interest_usdt"] = 1234567.0
        derivatives_rows.append(derivatives_row)
    (snapshot_dir / "derivatives_snapshot.json").write_text(
        json.dumps(
            {
                "as_of": observed_at,
                "schema_version": "imported_derivatives_snapshot.v1",
                "rows": derivatives_rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_phase1_dataset(tmp_path: Path, *, include_open_interest: bool = True) -> Path:
    dataset_root = tmp_path / "phase1-imported-dataset"
    _write_phase1_snapshot(
        dataset_root / "2026-01-01T00-00-00Z__row-001",
        include_open_interest=include_open_interest,
    )
    return dataset_root


def _write_phase1_dataset_as_symlink_root(tmp_path: Path) -> Path:
    dataset_root = _write_phase1_dataset(tmp_path)
    symlink_root = tmp_path / "phase1-symlink-root"
    symlink_root.mkdir()
    for snapshot_dir in sorted(path for path in dataset_root.iterdir() if path.is_dir()):
        (symlink_root / snapshot_dir.name).symlink_to(snapshot_dir, target_is_directory=True)
    return symlink_root


def _remove_enrichment_fields(source_dataset: Path, legacy_dataset: Path) -> None:
    shutil.copytree(source_dataset, legacy_dataset)
    for instrument_path in sorted(legacy_dataset.rglob("instrument_snapshot.json")):
        payload = json.loads(instrument_path.read_text(encoding="utf-8"))
        for row in payload["rows"]:
            row.pop("lifecycle_status", None)
        instrument_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for market_context_path in sorted(legacy_dataset.rglob("market_context.json")):
        payload = json.loads(market_context_path.read_text(encoding="utf-8"))
        for symbol_context in payload["symbols"].values():
            symbol_context.pop("futures_context", None)
        market_context_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_build_historical_dataset_enrichment_metadata_emits_complete_read_only_source(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    before_payloads = _json_payloads(metadata_dataset)
    output_path = tmp_path / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    assert _json_payloads(metadata_dataset) == before_payloads
    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "historical_dataset_enrichment_metadata.v1"
    assert metadata["generated_at"] == GENERATED_AT
    assert metadata["source"] == "fixture_public_read_only_metadata"
    assert metadata["provenance"]["metadata_dataset_root"] == str(metadata_dataset)
    assert metadata["provenance"]["source_kind"] == "local_offline_public_read_only_metadata"
    assert metadata["provenance"]["field_families_sourced"] == ["lifecycle_status", "futures_context"]
    assert metadata["coverage"] == {
        "symbol_count": 4,
        "lifecycle_status_symbol_count": 4,
        "futures_context_symbol_count": 4,
        "missing_lifecycle_status_symbols": [],
        "missing_futures_context_symbols": [],
    }
    assert metadata["side_effect_boundary"]["real_orders"] == "forbidden"
    assert metadata["side_effect_boundary"]["testnet_orders"] == "forbidden"
    assert metadata["side_effect_boundary"]["credential_use"] == "forbidden"
    assert metadata["side_effect_boundary"]["source_root_mutation"] == "forbidden"
    assert set(metadata["symbols"]) == {"BTCUSDT", "BTCUSDTPERP", "ETHUSDT", "SOLUSDTPERP"}

    serialized = json.dumps(metadata, sort_keys=True)
    for field in FORBIDDEN_ACCOUNT_FIELDS:
        assert field not in serialized
    for symbol_payload in metadata["symbols"].values():
        assert symbol_payload["lifecycle_status"] == "listed"
        assert set(symbol_payload["futures_context"]) == {
            "funding_status",
            "mark_price_status",
            "open_interest_status",
        }
        assert symbol_payload["lifecycle_status_provenance"]["source"] == "instrument_snapshot"
        assert symbol_payload["lifecycle_status_provenance"]["source_file"].endswith("instrument_snapshot.json")
        assert symbol_payload["lifecycle_status_provenance"]["source_kind"] == "local_offline_public_read_only_metadata"
        assert symbol_payload["futures_context_provenance"]["source"] == "market_context"
        assert symbol_payload["futures_context_provenance"]["source_file"].endswith("market_context.json")
        assert symbol_payload["futures_context_provenance"]["source_kind"] == "local_offline_public_read_only_metadata"


def test_build_historical_dataset_enrichment_metadata_reads_symlink_snapshot_dirs(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset_as_symlink_root(tmp_path)
    output_path = tmp_path / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    assert metadata["coverage"]["symbol_count"] == 4
    assert metadata["coverage"]["missing_lifecycle_status_symbols"] == []
    assert metadata["coverage"]["missing_futures_context_symbols"] == []
    assert set(metadata["symbols"]) == {"BTCUSDT", "BTCUSDTPERP", "ETHUSDT", "SOLUSDTPERP"}


def test_build_phase1_historical_dataset_enrichment_metadata_derives_local_provenance(
    tmp_path: Path,
) -> None:
    source_dataset = _write_phase1_dataset(tmp_path)
    output_path = tmp_path / "phase1_metadata.json"

    exit_code = _run_cli(
        [
            "build-phase1-historical-dataset-enrichment-metadata",
            "--source-dataset-root",
            str(source_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "phase1_fixture",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "historical_dataset_enrichment_metadata.v1"
    assert metadata["source"] == "phase1_fixture"
    assert metadata["provenance"]["source_dataset_root"] == str(source_dataset)
    assert metadata["provenance"]["source_kind"] == "local_offline_phase1_imported_dataset_provenance"
    assert metadata["provenance"]["network_access"] == "forbidden"
    assert metadata["side_effect_boundary"]["real_orders"] == "forbidden"
    assert metadata["side_effect_boundary"]["testnet_orders"] == "forbidden"
    assert metadata["side_effect_boundary"]["credential_use"] == "forbidden"
    assert metadata["side_effect_boundary"]["source_root_mutation"] == "forbidden"
    assert metadata["coverage"] == {
        "symbol_count": 1,
        "lifecycle_status_symbol_count": 1,
        "futures_context_symbol_count": 1,
        "missing_lifecycle_status_symbols": [],
        "missing_futures_context_symbols": [],
    }
    symbol_payload = metadata["symbols"]["BTCUSDTPERP"]
    assert symbol_payload["lifecycle_status"] == "listed"
    assert symbol_payload["futures_context"] == {
        "funding_status": "available",
        "mark_price_status": "available",
        "open_interest_status": "available",
    }
    assert symbol_payload["lifecycle_status_provenance"]["source"] == "instrument_snapshot"
    assert symbol_payload["lifecycle_status_provenance"]["source_kind"] == (
        "local_offline_phase1_imported_dataset_provenance"
    )
    assert symbol_payload["lifecycle_status_provenance"]["derivation"] == (
        "observed_listed_in_imported_instrument_snapshot"
    )
    assert symbol_payload["futures_context_provenance"]["source"] == "derivatives_snapshot"
    assert symbol_payload["futures_context_provenance"]["source_kind"] == (
        "local_offline_phase1_imported_dataset_provenance"
    )
    assert symbol_payload["futures_context_provenance"]["derivation"] == (
        "observed_available_in_imported_derivatives_snapshot"
    )
    serialized = json.dumps(metadata, sort_keys=True)
    for field in FORBIDDEN_ACCOUNT_FIELDS:
        assert field not in serialized


def test_build_phase1_historical_dataset_enrichment_metadata_reads_symlink_snapshot_dirs(
    tmp_path: Path,
) -> None:
    source_dataset = _write_phase1_dataset_as_symlink_root(tmp_path)
    output_path = tmp_path / "phase1_metadata.json"

    exit_code = _run_cli(
        [
            "build-phase1-historical-dataset-enrichment-metadata",
            "--source-dataset-root",
            str(source_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "phase1_fixture",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    metadata = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(metadata["symbols"]) == {"BTCUSDTPERP"}


def test_build_phase1_historical_dataset_enrichment_metadata_fails_closed_on_incomplete_derivatives(
    tmp_path: Path,
) -> None:
    source_dataset = _write_phase1_dataset(tmp_path, include_open_interest=False)
    output_path = tmp_path / "phase1_metadata.json"

    exit_code = _run_cli(
        [
            "build-phase1-historical-dataset-enrichment-metadata",
            "--source-dataset-root",
            str(source_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "phase1_fixture",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_build_phase1_historical_dataset_enrichment_metadata_rejects_output_inside_source_root(
    tmp_path: Path,
) -> None:
    source_dataset = _write_phase1_dataset(tmp_path)
    output_path = source_dataset / "phase1_metadata.json"

    exit_code = _run_cli(
        [
            "build-phase1-historical-dataset-enrichment-metadata",
            "--source-dataset-root",
            str(source_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "phase1_fixture",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_build_phase1_historical_dataset_enrichment_metadata_feeds_migration_sample(
    tmp_path: Path,
) -> None:
    source_dataset = _write_phase1_dataset(tmp_path)
    metadata_source = tmp_path / "phase1_metadata.json"

    metadata_exit_code = _run_cli(
        [
            "build-phase1-historical-dataset-enrichment-metadata",
            "--source-dataset-root",
            str(source_dataset),
            "--output-path",
            str(metadata_source),
            "--source-name",
            "phase1_fixture",
            "--generated-at",
            "2026-05-19T00:00:00Z",
        ]
    )
    target_dataset = tmp_path / "target-dataset"
    sample_output_path = tmp_path / "migration_sample.json"
    sample_exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(source_dataset),
            "--target-dataset-root",
            str(target_dataset),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(sample_output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert metadata_exit_code == 0
    assert sample_exit_code == 0
    report = json.loads(sample_output_path.read_text(encoding="utf-8"))
    assert "dataset_missing_lifecycle_status" not in report["post_migration_preflight"]["reason_codes"]
    assert "dataset_missing_futures_context" not in report["post_migration_preflight"]["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in report["post_migration_preflight"]["reason_codes"]


def test_build_historical_dataset_enrichment_metadata_feeds_migration_sample(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    legacy_dataset = tmp_path / "legacy-dataset"
    _remove_enrichment_fields(metadata_dataset, legacy_dataset)
    metadata_source = tmp_path / "metadata_source.json"

    metadata_exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(metadata_source),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            "2026-05-19T00:00:00Z",
        ]
    )
    target_dataset = tmp_path / "target-dataset"
    sample_output_path = tmp_path / "migration_sample.json"
    sample_exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(legacy_dataset),
            "--target-dataset-root",
            str(target_dataset),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(sample_output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert metadata_exit_code == 0
    assert sample_exit_code == 0
    report = json.loads(sample_output_path.read_text(encoding="utf-8"))
    assert "dataset_missing_lifecycle_status" not in report["post_migration_preflight"]["reason_codes"]
    assert "dataset_missing_futures_context" not in report["post_migration_preflight"]["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in report["reason_codes"]


def test_build_historical_dataset_enrichment_metadata_rejects_output_inside_source_root(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    before_payloads = _json_payloads(metadata_dataset)
    output_path = metadata_dataset / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert _json_payloads(metadata_dataset) == before_payloads
    assert not output_path.exists()


def test_build_historical_dataset_enrichment_metadata_fails_closed_on_partial_coverage(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    instrument_path = next(iter(sorted(metadata_dataset.rglob("instrument_snapshot.json"))))
    payload = json.loads(instrument_path.read_text(encoding="utf-8"))
    payload["rows"][0].pop("lifecycle_status", None)
    instrument_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_path = tmp_path / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_build_historical_dataset_enrichment_metadata_rejects_forbidden_account_fields(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    market_context_path = next(iter(sorted(metadata_dataset.rglob("market_context.json"))))
    payload = json.loads(market_context_path.read_text(encoding="utf-8"))
    first_symbol = next(iter(payload["symbols"]))
    payload["symbols"][first_symbol]["futures_context"]["leverage"] = 10
    market_context_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_path = tmp_path / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_build_historical_dataset_enrichment_metadata_rejects_nested_forbidden_account_fields(
    tmp_path: Path,
) -> None:
    metadata_dataset = _copy_schema_complete_metadata_dataset(tmp_path)
    market_context_path = next(iter(sorted(metadata_dataset.rglob("market_context.json"))))
    payload = json.loads(market_context_path.read_text(encoding="utf-8"))
    first_symbol = next(iter(payload["symbols"]))
    payload["symbols"][first_symbol]["futures_context"]["risk_tiers"] = [{"maintenance_tier": "tier-1"}]
    market_context_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_path = tmp_path / "metadata.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-enrichment-metadata",
            "--metadata-dataset-root",
            str(metadata_dataset),
            "--output-path",
            str(output_path),
            "--source-name",
            "fixture_public_read_only_metadata",
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code != 0
    assert not output_path.exists()


def test_build_historical_dataset_migration_sample_copies_and_enriches_read_only_source(
    tmp_path: Path,
) -> None:
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    legacy_dataset = tmp_path / "legacy-dataset"
    shutil.copytree(source_dataset, legacy_dataset)
    original_source_payloads = {
        path.relative_to(legacy_dataset): path.read_bytes()
        for path in sorted(legacy_dataset.rglob("*.json"))
    }
    metadata_symbols: dict[str, dict[str, object]] = {}

    for source_path in sorted(source_dataset.rglob("instrument_snapshot.json")):
        target_path = legacy_dataset / source_path.relative_to(source_dataset)
        original_payload = json.loads(source_path.read_text(encoding="utf-8"))
        legacy_payload = json.loads(target_path.read_text(encoding="utf-8"))
        for original_row, legacy_row in zip(original_payload["rows"], legacy_payload["rows"], strict=True):
            symbol = original_row["symbol"]
            metadata_symbols.setdefault(
                symbol,
                {
                    "lifecycle_status": original_row["lifecycle_status"],
                    "lifecycle_status_provenance": {
                        "source": "exchange_info_status",
                        "observed_at": original_payload["as_of"],
                    },
                    "futures_context": {
                        "mark_price_status": "missing",
                        "funding_status": "missing",
                        "open_interest_status": "missing",
                    },
                    "futures_context_provenance": {
                        "source": "futures_exchange_info",
                        "observed_at": original_payload["as_of"],
                    },
                },
            )
            legacy_row.pop("lifecycle_status", None)
        target_path.write_text(json.dumps(legacy_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for target_path in sorted(legacy_dataset.rglob("market_context.json")):
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        for symbol_context in payload["symbols"].values():
            symbol_context.pop("futures_context", None)
        target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    mutated_legacy_payloads = {
        path.relative_to(legacy_dataset): path.read_bytes()
        for path in sorted(legacy_dataset.rglob("*.json"))
    }
    assert mutated_legacy_payloads != original_source_payloads

    metadata_source = tmp_path / "metadata_source.json"
    metadata_source.write_text(
        json.dumps(
            {
                "schema_version": "historical_dataset_enrichment_metadata.v1",
                "generated_at": "2026-05-19T00:00:00Z",
                "source": "fixture_public_read_only_metadata",
                "provenance": {
                    "capture_method": "fixture",
                    "source_kind": "public_read_only_exchange_metadata",
                },
                "symbols": metadata_symbols,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    target_dataset = tmp_path / "target-dataset"
    output_path = tmp_path / "migration_sample.json"

    exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(legacy_dataset),
            "--target-dataset-root",
            str(target_dataset),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    assert {
        path.relative_to(legacy_dataset): path.read_bytes()
        for path in sorted(legacy_dataset.rglob("*.json"))
    } == mutated_legacy_payloads
    assert target_dataset.exists()
    assert target_dataset != legacy_dataset
    assert output_path.exists()

    for instrument_path in sorted(target_dataset.rglob("instrument_snapshot.json")):
        payload = json.loads(instrument_path.read_text(encoding="utf-8"))
        for row in payload["rows"]:
            assert row["lifecycle_status"] == metadata_symbols[row["symbol"]]["lifecycle_status"]

    forbidden_account_fields = {
        "margin_mode",
        "leverage",
        "maintenance_tier",
        "liquidation_price",
        "notional",
        "unrealized_pnl",
    }
    for market_context_path in sorted(target_dataset.rglob("market_context.json")):
        payload = json.loads(market_context_path.read_text(encoding="utf-8"))
        for symbol, symbol_context in payload["symbols"].items():
            assert symbol_context["futures_context"] == metadata_symbols[symbol]["futures_context"]
            assert forbidden_account_fields.isdisjoint(symbol_context["futures_context"])

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "historical_dataset_migration_sample.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["decision"] == "hold"
    assert report["source_dataset_root"] == str(legacy_dataset)
    assert report["target_dataset_root"] == str(target_dataset)
    assert report["metadata_source"] == str(metadata_source)
    assert report["counts"]["copied_files"] == len([path for path in source_dataset.rglob("*") if path.is_file()])
    assert report["counts"]["enriched_lifecycle_rows"] == 5
    assert report["counts"]["enriched_futures_contexts"] == 7
    assert report["counts"]["skipped_existing_lifecycle_rows"] == 0
    assert report["counts"]["skipped_existing_futures_contexts"] == 0
    assert report["counts"]["missing_lifecycle_metadata_rows"] == 0
    assert report["counts"]["missing_futures_context_metadata_symbols"] == 0
    assert report["provenance_summary"] == {
        "schema_version": "historical_dataset_enrichment_metadata.v1",
        "generated_at": "2026-05-19T00:00:00Z",
        "source": "fixture_public_read_only_metadata",
        "provenance": {
            "capture_method": "fixture",
            "source_kind": "public_read_only_exchange_metadata",
        },
        "symbol_count": len(metadata_symbols),
    }
    assert report["side_effect_boundary"]["source_dataset_mutation"] == "forbidden"
    assert report["side_effect_boundary"]["account_policy_fields"] == "not_modified"
    assert report["safety_reasons"] == ["account_policy_fields_not_modified"]
    assert "dataset_missing_lifecycle_status" not in report["post_migration_preflight"]["reason_codes"]
    assert "dataset_missing_futures_context" not in report["post_migration_preflight"]["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in report["reason_codes"]

    serialized_target_market_context = json.dumps(
        [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(target_dataset.rglob("market_context.json"))
        ],
        sort_keys=True,
    )
    serialized_report = json.dumps(report, sort_keys=True)
    for field in forbidden_account_fields:
        assert field not in serialized_target_market_context
        assert field not in serialized_report

    first_target_payloads = {
        path.relative_to(target_dataset): path.read_bytes()
        for path in sorted(target_dataset.rglob("*"))
        if path.is_file()
    }
    second_output_path = tmp_path / "migration_sample_second.json"
    rerun_exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(legacy_dataset),
            "--target-dataset-root",
            str(target_dataset),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(second_output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert rerun_exit_code != 0
    assert {
        path.relative_to(target_dataset): path.read_bytes()
        for path in sorted(target_dataset.rglob("*"))
        if path.is_file()
    } == first_target_payloads
    assert not second_output_path.exists()


def test_build_historical_dataset_migration_sample_rejects_writes_inside_source_root(
    tmp_path: Path,
) -> None:
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    legacy_dataset = tmp_path / "legacy-dataset"
    shutil.copytree(source_dataset, legacy_dataset)
    original_source_payloads = {
        path.relative_to(legacy_dataset): path.read_bytes()
        for path in sorted(legacy_dataset.rglob("*"))
        if path.is_file()
    }
    metadata_source = tmp_path / "metadata_source.json"
    metadata_source.write_text(
        json.dumps(
            {
                "schema_version": "historical_dataset_enrichment_metadata.v1",
                "generated_at": "2026-05-19T00:00:00Z",
                "source": "fixture_public_read_only_metadata",
                "provenance": {"source_kind": "public_read_only_exchange_metadata"},
                "symbols": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    target_inside_source_exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(legacy_dataset),
            "--target-dataset-root",
            str(legacy_dataset / "migration-target"),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(tmp_path / "target_inside_source_report.json"),
            "--generated-at",
            GENERATED_AT,
        ]
    )
    output_inside_source_exit_code = _run_cli(
        [
            "build-historical-dataset-migration-sample",
            "--source-dataset-root",
            str(legacy_dataset),
            "--target-dataset-root",
            str(tmp_path / "target-dataset"),
            "--metadata-source",
            str(metadata_source),
            "--output-path",
            str(legacy_dataset / "migration-report.json"),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert target_inside_source_exit_code != 0
    assert output_inside_source_exit_code != 0
    assert {
        path.relative_to(legacy_dataset): path.read_bytes()
        for path in sorted(legacy_dataset.rglob("*"))
        if path.is_file()
    } == original_source_payloads
    assert not (legacy_dataset / "migration-target").exists()
    assert not (legacy_dataset / "migration-report.json").exists()


def test_diagnostic_reason_codes_keep_margin_policy_failure_separate_from_futures_context_gap() -> None:
    reasons = cli._diagnostic_reason_codes(
        "trades[0].margin_mode must be isolated or cross",
        preflight_reasons=["margin_liquidation_path_not_evaluable"],
    )

    assert "pipeline_generation_failed" in reasons
    assert "margin_liquidation_path_not_evaluable" in reasons
    assert "dataset_missing_futures_context" not in reasons


def test_run_professional_evidence_pipeline_writes_account_margin_policy_hold_evidence(
    tmp_path: Path,
) -> None:
    dataset_root = _copy_schema_complete_metadata_dataset(tmp_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    backtest_config = configs_dir / "backtest.json"
    walk_forward_config = configs_dir / "walk_forward.json"
    allocator_config = configs_dir / "allocator.json"
    _write_professional_config(backtest_config, dataset_root=dataset_root, experiment_kind="full_market_baseline")
    _write_professional_config(walk_forward_config, dataset_root=dataset_root, experiment_kind="walk_forward_validation")
    _write_professional_config(allocator_config, dataset_root=dataset_root, experiment_kind="allocator_friction")
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(backtest_config),
            "--walk-forward-config",
            str(walk_forward_config),
            "--allocator-friction-config",
            str(allocator_config),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    evidence_chain = json.loads(
        (output_dir / "professional_evidence" / "backtest_evidence_chain.json").read_text(encoding="utf-8")
    )
    assert evidence_chain["summary"]["decision"] == "hold"
    assert evidence_chain["summary"]["reason_codes"] == [
        "pipeline_generation_failed",
        "margin_liquidation_path_not_evaluable",
    ]
    policy = evidence_chain["account_margin_policy_evidence"]
    assert policy["schema_version"] == "account_margin_policy_evidence.v1"
    assert policy["decision"] == "hold"
    assert policy["status"] == "unavailable"
    assert policy["reason_codes"] == ["margin_liquidation_path_not_evaluable"]
    assert policy["fabricated_fields"] == []
    assert policy["non_fabrication_policy"]["account_fields_may_be_defaulted"] is False
    assert set(policy["required_fields"]) == FORBIDDEN_ACCOUNT_FIELDS
    for field, field_status in policy["required_fields"].items():
        assert field_status["status"] == "unavailable"
        assert field_status["provenance"] == "unavailable"
        assert field_status["fabricated"] is False


def test_run_professional_evidence_pipeline_writes_hold_diagnostic_when_dataset_generation_fails(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    backtest_config = configs_dir / "backtest.json"
    walk_forward_config = configs_dir / "walk_forward.json"
    allocator_config = configs_dir / "allocator.json"
    _write_professional_config(backtest_config, dataset_root=dataset_root, experiment_kind="full_market_baseline")
    _write_professional_config(walk_forward_config, dataset_root=dataset_root, experiment_kind="walk_forward_validation")
    _write_professional_config(allocator_config, dataset_root=dataset_root, experiment_kind="allocator_friction")
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(backtest_config),
            "--walk-forward-config",
            str(walk_forward_config),
            "--allocator-friction-config",
            str(allocator_config),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    evidence_chain_path = output_dir / "professional_evidence" / "backtest_evidence_chain.json"
    assert evidence_chain_path.exists()
    evidence_chain = json.loads(evidence_chain_path.read_text(encoding="utf-8"))
    assert evidence_chain["schema_version"] == "backtest_evidence_chain.v1"
    assert evidence_chain["summary"]["decision"] == "hold"
    assert evidence_chain["historical_backtest"]["status"] == "hold"
    assert "dataset_missing_lifecycle_status" in evidence_chain["historical_backtest"]["reason_codes"]
    assert "pipeline_generation_failed" in evidence_chain["summary"]["reason_codes"]
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "hold"
    assert manifest["professional_evidence"]["evidence_chain_path"] == str(evidence_chain_path)
    assert manifest["professional_evidence"]["generation_failed"] is True


def test_run_professional_evidence_pipeline_preflights_multiple_legacy_dataset_gaps(tmp_path: Path) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif source_path.name == "market_context.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for symbol_context in payload["symbols"].values():
                symbol_context.pop("futures_context", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    backtest_config = configs_dir / "backtest.json"
    walk_forward_config = configs_dir / "walk_forward.json"
    allocator_config = configs_dir / "allocator.json"
    _write_professional_config(backtest_config, dataset_root=dataset_root, experiment_kind="full_market_baseline")
    _write_professional_config(walk_forward_config, dataset_root=dataset_root, experiment_kind="walk_forward_validation")
    _write_professional_config(allocator_config, dataset_root=dataset_root, experiment_kind="allocator_friction")
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(backtest_config),
            "--walk-forward-config",
            str(walk_forward_config),
            "--allocator-friction-config",
            str(allocator_config),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    evidence_chain = json.loads(
        (output_dir / "professional_evidence" / "backtest_evidence_chain.json").read_text(encoding="utf-8")
    )
    assert evidence_chain["summary"]["decision"] == "hold"
    assert "dataset_missing_lifecycle_status" in evidence_chain["summary"]["reason_codes"]
    assert "dataset_missing_futures_context" in evidence_chain["summary"]["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in evidence_chain["summary"]["reason_codes"]
    preflight = evidence_chain["generation_failure"]["preflight"]
    assert preflight["dataset_root"] == str(dataset_root)
    assert preflight["snapshot_count"] > 0
    assert preflight["missing_lifecycle_status"]["row_count"] > 0
    assert preflight["missing_lifecycle_status"]["snapshot_count"] > 0
    assert preflight["missing_lifecycle_status"]["examples"][0]["path"].endswith("instrument_snapshot.json")
    assert preflight["missing_futures_context"]["symbol_count"] > 0
    assert preflight["missing_futures_context"]["snapshot_count"] > 0
    assert preflight["missing_futures_context"]["examples"][0]["path"].endswith("market_context.json")


def test_preflight_historical_dataset_command_writes_migration_report(tmp_path: Path) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif source_path.name == "market_context.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for symbol_context in payload["symbols"].values():
                symbol_context.pop("futures_context", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    output_path = tmp_path / "preflight.json"

    exit_code = cli.main(
        [
            "preflight-historical-dataset",
            "--dataset-root",
            str(dataset_root),
            "--output-path",
            str(output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "historical_dataset_preflight.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["decision"] == "hold"
    assert report["dataset_root"] == str(dataset_root)
    assert "dataset_missing_lifecycle_status" in report["reason_codes"]
    assert "dataset_missing_futures_context" in report["reason_codes"]
    assert report["missing_lifecycle_status"]["row_count"] > 0
    assert report["missing_futures_context"]["symbol_count"] > 0


def test_plan_historical_dataset_migration_writes_read_only_hold_plan_for_legacy_gaps(tmp_path: Path) -> None:
    dataset_root = tmp_path / "legacy-dataset"
    source_dataset = FIXTURES / "full_market_baseline_dataset"
    for source_path in source_dataset.rglob("*"):
        target_path = dataset_root / source_path.relative_to(source_dataset)
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.name == "instrument_snapshot.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for row in payload["rows"]:
                row.pop("lifecycle_status", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif source_path.name == "market_context.json":
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            for symbol_context in payload["symbols"].values():
                symbol_context.pop("futures_context", None)
            target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    before_payloads = {path.relative_to(dataset_root): path.read_text(encoding="utf-8") for path in dataset_root.rglob("*.json")}
    output_path = tmp_path / "migration_plan.json"

    exit_code = cli.main(
        [
            "plan-historical-dataset-migration",
            "--dataset-root",
            str(dataset_root),
            "--output-path",
            str(output_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    after_payloads = {path.relative_to(dataset_root): path.read_text(encoding="utf-8") for path in dataset_root.rglob("*.json")}
    assert after_payloads == before_payloads
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "historical_dataset_migration_plan.v1"
    assert report["generated_at"] == GENERATED_AT
    assert report["decision"] == "hold"
    assert report["source_dataset_root"] == str(dataset_root)
    assert "dataset_missing_lifecycle_status" in report["reason_codes"]
    assert "dataset_missing_futures_context" in report["reason_codes"]
    assert "margin_liquidation_path_not_evaluable" in report["reason_codes"]
    assert report["preflight_summary"]["snapshot_count"] > 0
    assert report["preflight_summary"]["missing_lifecycle_status"]["row_count"] > 0
    assert report["preflight_summary"]["missing_futures_context"]["symbol_count"] > 0

    required_fields = {entry["field"]: entry for entry in report["target_required_fields"]}
    assert set(required_fields) >= {"lifecycle_status", "futures_context", "margin_liquidation_path"}
    assert required_fields["lifecycle_status"]["requires_provenance"] is True
    assert required_fields["lifecycle_status"]["preflight_counts"]["missing_row_count"] > 0
    assert "public_exchange_instrument_metadata" in required_fields["lifecycle_status"]["candidate_sources"]
    assert required_fields["futures_context"]["requires_provenance"] is True
    assert required_fields["futures_context"]["preflight_counts"]["missing_symbol_count"] > 0
    assert "public_exchange_futures_market_metadata" in required_fields["futures_context"]["candidate_sources"]
    assert required_fields["margin_liquidation_path"]["requires_provenance"] is True
    assert "margin_mode" in required_fields["margin_liquidation_path"]["not_derivable_fields"]
    assert "leverage" in required_fields["margin_liquidation_path"]["not_derivable_fields"]
    assert "maintenance_tier" in required_fields["margin_liquidation_path"]["not_derivable_fields"]
    assert "liquidation_price" in required_fields["margin_liquidation_path"]["not_derivable_fields"]
    assert required_fields["margin_liquidation_path"]["recommended_action"] == "attach_account_policy_or_hold"
    assert report["account_execution_policy_classification"]["public_market_data_derivable"] is False
    assert "preflight-historical-dataset" in report["operations"]["validation_commands"]
    assert "run-professional-evidence-pipeline" in report["operations"]["validation_commands"]
    serialized = json.dumps(report, sort_keys=True)
    assert "default_to_listed" not in serialized
    assert "fabricated" not in serialized
    assert "placeholder_liquidation_price" not in serialized


def test_run_professional_evidence_pipeline_writes_bundles_reports_and_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    pipeline_manifest_path = output_dir / "professional_evidence_pipeline_manifest.json"
    assert pipeline_manifest_path.exists()
    manifest = json.loads(pipeline_manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "professional_evidence_pipeline.v1"
    assert manifest["generated_at"] == GENERATED_AT
    assert manifest["decision"] in {"pass", "hold"}
    assert manifest["bundles"]["backtest"].endswith("full_market_baseline__current_system__auditable_baseline")
    assert manifest["bundles"]["walk_forward"].endswith("walk_forward_validation__current_policy__rolling_walk_forward")
    assert manifest["bundles"]["allocator_friction"].endswith("allocator_friction__current_policy__allocator_fee_drag")

    evidence_outputs = manifest["professional_evidence"]
    evidence_chain_path = Path(evidence_outputs["evidence_chain_path"])
    assert evidence_chain_path == output_dir / "professional_evidence" / "backtest_evidence_chain.json"
    assert evidence_chain_path.exists()
    assert Path(evidence_outputs["walk_forward_report_path"]).exists()
    assert Path(evidence_outputs["cost_sensitivity_report_path"]).exists()

    evidence_chain = json.loads(evidence_chain_path.read_text(encoding="utf-8"))
    assert evidence_chain["schema_version"] == "backtest_evidence_chain.v1"
    assert evidence_chain["generated_at"] == GENERATED_AT
    assert evidence_chain["summary"]["decision"] == manifest["decision"]


def test_run_professional_evidence_pipeline_writes_promotion_gate_report_and_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"
    gate_inputs = tmp_path / "gate-inputs"
    window_path = gate_inputs / "simulated_live_evidence_window.json"
    trend_path = gate_inputs / "promotion_readiness_scorecard_trend.json"
    calibration_path = gate_inputs / "calibration_feedback.json"
    gate_inputs.mkdir(parents=True)
    window_path.write_text(
        json.dumps(
            {
                "schema_version": "simulated_live_evidence_window.v1",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reason_codes": [],
                "checks": {
                    "minimum_distinct_sessions_met": True,
                    "session_identities_unique": True,
                    "generated_at_monotonic": True,
                    "as_of_monotonic": True,
                    "all_bundles_pass": True,
                    "all_required_bundle_components_present": True,
                },
                "bundles": [
                    {"session_id": "s1", "day": "2026-05-15", "generated_at": "2026-05-15T00:00:00Z"},
                    {"session_id": "s2", "day": "2026-05-16", "generated_at": "2026-05-16T00:00:00Z"},
                    {"session_id": "s3", "day": "2026-05-17", "generated_at": "2026-05-17T00:00:00Z"},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    trend_path.write_text(
        json.dumps(
            {
                "schema_version": "promotion_readiness_scorecard_trend.v1",
                "mode": "simulated_live",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reasons": [],
                "checks": {
                    "sample_window_sufficient": True,
                    "scorecards_well_formed": True,
                    "generated_at_monotonic": True,
                    "scorecard_identities_unique": True,
                    "score_deterioration_within_threshold": True,
                    "repeated_blockers_absent": True,
                },
                "scorecards": [
                    {"identity": "scorecard-1", "generated_at": "2026-05-16T00:00:00Z", "decision": "pass", "score": 90.0},
                    {"identity": "scorecard-2", "generated_at": "2026-05-17T00:00:00Z", "decision": "pass", "score": 91.0},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    calibration_path.write_text(
        json.dumps(
            {
                "schema_version": "calibration_feedback_artifact.v1",
                "generated_at": GENERATED_AT,
                "decision": "ready",
                "checks": {"sample_count_met": True, "evidence_fresh": True},
                "reasons": [],
                "components": [
                    {"component": "tca_report", "identity": "tca-20260518", "schema_version": "tca_calibration_report.v1"}
                ],
                "side_effect_boundary": "offline_local_only",
                "strategy_config_mutation": "forbidden",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--simulated-live-evidence-window",
            str(window_path),
            "--promotion-readiness-scorecard-trend",
            str(trend_path),
            "--calibration-artifact",
            str(calibration_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 0
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    gate_path = Path(manifest["promotion_gate"]["decision_report_path"])
    assert gate_path == output_dir / "promotion_gate_decision.json"
    assert gate_path.exists()
    gate_report = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate_report["schema_version"] == "promotion_gate_decision.v1"
    assert gate_report["checks"]["professional_evidence_chain"]["status"] in {"pass", "hold"}
    assert gate_report["checks"]["professional_evidence_chain"]["execution_realism"]["status"] in {"pass", "hold"}
    assert manifest["promotion_gate"]["decision"] == gate_report["decision"]
    assert manifest["promotion_gate"]["professional_evidence_chain_path"] == manifest["professional_evidence"]["evidence_chain_path"]


def test_run_professional_evidence_pipeline_passes_execution_realism_from_non_empty_paper_samples(tmp_path: Path) -> None:
    runtime_paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        state_file=runtime_paths.state_file,
        execution=replace(DEFAULT_CONFIG.execution, mode="paper", environment="research"),
    )
    executor = OrderExecutor(config, mode="paper")
    result = executor.execute(_sample_order(), RuntimeStateV2.empty())
    health = _execution_sample_collection_health(runtime_paths, {"candidate_count": 1, "allocation_count": 1})
    health_path = runtime_paths.bucket_dir / "execution_sample_collection_health.json"
    health_path.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime_paths.latest_summary_file.write_text(
        json.dumps(
            {
                "status": "ok",
                "mode": "paper",
                "runtime_env": "research",
                "candidate_count": 1,
                "allocation_count": 1,
                "execution_sample_collection_health_file": str(health_path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--runtime-summary-path",
            str(runtime_paths.latest_summary_file),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert result["result"] == "FILLED"
    assert health["status"] == "available"
    assert exit_code == 0
    manifest = json.loads((output_dir / "professional_evidence_pipeline_manifest.json").read_text(encoding="utf-8"))
    evidence_chain = json.loads(Path(manifest["professional_evidence"]["evidence_chain_path"]).read_text(encoding="utf-8"))
    assert manifest["professional_evidence"]["runtime_summary_path"] == str(runtime_paths.latest_summary_file)
    assert manifest["professional_evidence"]["execution_sample_collection_health_path"] == str(health_path)
    assert evidence_chain["execution_realism"]["status"] == "pass"
    assert evidence_chain["execution_realism"]["sample_count"] == 1
    assert evidence_chain["execution_realism"]["reason_codes"] == []
    assert evidence_chain["summary"]["component_statuses"]["execution_realism"] == "pass"


def test_run_professional_evidence_pipeline_rejects_partial_promotion_gate_inputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"
    window_path = tmp_path / "simulated_live_evidence_window.json"
    window_path.write_text(
        json.dumps(
            {
                "schema_version": "simulated_live_evidence_window.v1",
                "generated_at": GENERATED_AT,
                "decision": "pass",
                "reason_codes": [],
                "checks": {},
                "bundles": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "full_market_baseline.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--simulated-live-evidence-window",
            str(window_path),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 1
    assert not (output_dir / "professional_evidence_pipeline_manifest.json").exists()
    assert not (output_dir / "promotion_gate_decision.json").exists()


def test_run_professional_evidence_pipeline_rejects_mismatched_config_kind(tmp_path: Path) -> None:
    output_dir = tmp_path / "professional-pipeline"

    exit_code = cli.main(
        [
            "run-professional-evidence-pipeline",
            "--backtest-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--walk-forward-config",
            str(FIXTURES / "walk_forward_validation_config.json"),
            "--allocator-friction-config",
            str(FIXTURES / "allocator_friction_config.json"),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ]
    )

    assert exit_code == 1
    assert not (output_dir / "professional_evidence_pipeline_manifest.json").exists()
