from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_system.app.backtest import dataset as backtest_dataset
from trading_system.app.backtest.archive import importer as archive_importer
from trading_system.app.backtest.archive.importer import (
    _material_market_context_symbol_keys,
    build_phase1_dataset_bundle_materials,
    import_phase1_archive_dataset_root,
    inspect_phase1_imported_dataset_root,
    supplement_phase1_imported_dataset_root_instrument_snapshots,
    validate_phase1_imported_dataset_root,
    write_phase1_dataset_bundle,
    write_phase1_dataset_root_manifest,
)
from trading_system.app.backtest.archive.data_quality import build_raw_market_data_quality_report
from trading_system.app.backtest.archive.materialization import (
    _materialize_dataset_root,
    materialize_phase1_evidence_windows,
)
from trading_system.app.backtest.archive.raw_market import (
    ImportedRawMarketFile,
    archive_raw_market_payload,
    load_phase1_raw_market_imports,
)
from trading_system.app.backtest.dataset import load_historical_dataset



def test_dataset_root_manifest_payload_rejects_empty_list_material_source(tmp_path: Path) -> None:
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        run_id="phase1-20260101T000000Z",
        metadata={"source": []},
        market_context={"as_of": "2026-01-01T00:00:00Z", "symbols": {"BTCUSDT": {}}},
        derivatives_snapshot={},
        account_snapshot={},
    )

    with pytest.raises(ValueError, match="materialized dataset bundle metadata source must contain a JSON object"):
        archive_importer._phase1_dataset_root_manifest(
            archive_root=tmp_path / "archive",
            dataset_root=tmp_path / "dataset",
            symbols=["BTCUSDT"],
            materials=[material],
            bundle_dirs=[tmp_path / "bundle"],
        )



def test_materialized_dataset_row_source_rejects_empty_list_source() -> None:
    row = type("Row", (), {"meta": {"source": []}})()

    with pytest.raises(ValueError, match="materialized dataset bundle metadata source must contain a JSON object"):
        archive_importer._materialized_dataset_row_source([row])


class SourceTraceDictSubclass(dict[str, object]):
    pass


class SourceTraceMapping(Mapping[str, object]):
    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class SourceTraceListLike:
    def __iter__(self) -> Iterator[tuple[str, object]]:
        return iter((("scope", archive_importer.PHASE1_IMPORTER_SCOPE),))


class SourceTraceStringLike(str):
    pass


@pytest.mark.parametrize(
    "source",
    [
        SourceTraceDictSubclass(),
        SourceTraceMapping({}),
        SourceTraceListLike(),
        SourceTraceStringLike("scope"),
    ],
)
def test_materialized_dataset_row_source_rejects_non_plain_dict_source_trace_items(source: object) -> None:
    row = type("Row", (), {"meta": {"source": source}})()

    with pytest.raises(ValueError, match="materialized dataset bundle metadata source must contain a JSON object"):
        archive_importer._materialized_dataset_row_source([row])


@pytest.mark.parametrize("source", [{123: "x"}, {"": "x"}, {" bad ": "x"}])
def test_materialized_dataset_row_source_rejects_noncanonical_source_canonical_keys(source: object) -> None:
    row = type("Row", (), {"meta": {"source": source}})()

    with pytest.raises(
        ValueError,
        match="materialized dataset bundle metadata source keys must be canonical strings",
    ):
        archive_importer._materialized_dataset_row_source([row])


@pytest.mark.parametrize(
    "source",
    [
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "symbols": ("BTCUSDT",),
            "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
        },
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "symbols": [" BTCUSDT"],
            "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
        },
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": " binance",
            "market": "futures",
            "symbols": ["BTCUSDT"],
            "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
        },
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "symbols": ["BTCUSDT"],
            "series_keys": SourceTraceMapping({"0": "binance:futures:ohlcv:BTCUSDT:1h"}),
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
        },
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "symbols": ["BTCUSDT"],
            "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
            "ohlcv_timeframes": SourceTraceMapping({"available": ["1h"]}),
        },
        {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "symbols": ["BTCUSDT"],
            "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
            "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
            "unsafe": object(),
        },
    ],
)
def test_materialized_dataset_row_source_rejects_unsafe_source_trace_schema_values(source: object) -> None:
    row = type("Row", (), {"meta": {"source": source}})()

    with pytest.raises(ValueError, match="materialized dataset bundle metadata source"):
        archive_importer._materialized_dataset_row_source([row])


def test_materialized_dataset_row_source_rejects_string_subclass_series_key_item() -> None:
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": [SourceTraceStringLike("binance:futures:ohlcv:BTCUSDT:1h")],
        "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
    }
    row = type("Row", (), {"meta": {"source": source}})()

    with pytest.raises(
        ValueError,
        match=r"materialized dataset bundle metadata source\.series_keys\[0\] must be a string",
    ):
        archive_importer._materialized_dataset_row_source([row])


def test_materialized_dataset_row_source_preserves_valid_mapping_with_canonical_keys() -> None:
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
        "manifest_paths": ["/tmp/archive/raw-market/BTCUSDT/manifest.json"],
    }
    row = type("Row", (), {"meta": {"source": source}})()

    assert archive_importer._materialized_dataset_row_source([row]) == source



def test_row_market_symbol_keys_rejects_pair_list_symbols() -> None:
    row = type("Row", (), {"market": {"symbols": [("BTCUSDT", {})]}})()

    with pytest.raises(ValueError, match="materialized dataset row market symbols must be an object"):
        archive_importer._row_market_symbol_keys(row)


def test_row_market_symbol_keys_rejects_empty_symbol_list() -> None:
    row = type("Row", (), {"market": {"symbols": []}})()

    with pytest.raises(ValueError, match="materialized dataset row market symbols must be an object"):
        archive_importer._row_market_symbol_keys(row)



def test_phase1_dataset_root_summary_rejects_empty_list_source() -> None:
    with pytest.raises(ValueError, match="phase1 dataset root summary source must contain a JSON object"):
        archive_importer._phase1_dataset_root_summary_fields({"snapshot_count": 0, "source": []})



def test_phase1_dataset_root_summary_rejects_empty_string_bundle_dirs() -> None:
    with pytest.raises(ValueError, match="bundle_dirs must be a list"):
        archive_importer._phase1_dataset_root_summary_fields({"snapshot_count": 0, "bundle_dirs": ""})



def test_phase1_dataset_root_summary_rejects_empty_string_symbols() -> None:
    with pytest.raises(ValueError, match="symbols must be a list"):
        archive_importer._phase1_dataset_root_summary_fields({"snapshot_count": 0, "symbols": ""})



def test_phase1_dataset_root_summary_rejects_non_strict_snapshot_count() -> None:
    with pytest.raises(ValueError, match="snapshot_count must be a non-negative integer"):
        archive_importer._phase1_dataset_root_summary_fields({"snapshot_count": True})


def test_archive_root_from_manifest_paths_rejects_non_string_path_entries() -> None:
    with pytest.raises(ValueError, match="source manifest_paths entries\\[0\\] must be a string"):
        archive_importer._archive_root_from_manifest_paths([123])


def test_archive_root_from_manifest_paths_rejects_parent_traversal_path_entries() -> None:
    with pytest.raises(ValueError, match="source manifest_paths entries must not contain parent traversal"):
        archive_importer._archive_root_from_manifest_paths(
            ["/tmp/archive/raw-market/../raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json"]
        )


def test_validated_source_trace_rejects_tuple_manifest_paths_before_loading_manifests() -> None:
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
        "manifest_paths": ("/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json",),
    }

    with pytest.raises(ValueError, match="materialized dataset root source manifest_paths must be a list"):
        archive_importer._validated_source_trace_against_manifests(
            source,
            context="materialized dataset root source",
        )


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("symbols", ["BTCUSDT", "BTCUSDT"]),
        ("series_keys", ["binance:futures:ohlcv:BTCUSDT:1h", "binance:futures:ohlcv:BTCUSDT:1h"]),
        (
            "manifest_paths",
            [
                "/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json",
                "/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json",
            ],
        ),
    ],
)
def test_validated_source_trace_rejects_duplicate_canonical_source_identity_entries(
    field: str,
    values: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = "/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json"
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
        "manifest_paths": [manifest_path],
    }
    source[field] = values
    monkeypatch.setattr(
        archive_importer,
        "load_phase1_raw_market_manifest",
        lambda path: ImportedRawMarketFile(
            series_key="binance:futures:ohlcv:BTCUSDT:1h",
            manifest_path=Path(path),
            data_path=Path(path).with_suffix(".jsonl"),
            manifest={"symbol": "BTCUSDT"},
            symbol_metadata=None,
            coverage_start=datetime(2024, 1, 1, tzinfo=UTC),
            coverage_end=datetime(2024, 1, 1, tzinfo=UTC),
            fetched_at=datetime(2024, 1, 1, tzinfo=UTC),
            records=(),
        ),
    )

    with pytest.raises(ValueError, match=rf"materialized dataset root source {field} must not contain duplicate entries"):
        archive_importer._validated_source_trace_against_manifests(
            source,
            context="materialized dataset root source",
        )



def test_validate_bundle_payloads_rejects_non_string_metadata_identity(tmp_path: Path) -> None:
    expected_timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(expected_timestamp)}__{archive_importer._run_id(expected_timestamp)}"
    bundle_dir.mkdir()
    expected_as_of = expected_timestamp.isoformat().replace("+00:00", "Z")
    payloads = {
        "metadata.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_BUNDLE_SCHEMA,
            "run_id": archive_importer._run_id(expected_timestamp),
        },
        "market_context.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": expected_as_of,
            "instrument_rows": [],
        },
        "derivatives_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": expected_as_of,
        },
        "account_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": expected_as_of,
        },
        "instrument_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_INSTRUMENT_SNAPSHOT_SCHEMA,
            "as_of": expected_as_of,
            "rows": [],
        },
    }
    payloads["metadata.json"]["schema_version"] = 123
    for name, payload in payloads.items():
        (bundle_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata schema_version must be a string"):
        archive_importer._validate_bundle_payloads(bundle_dir, expected_timestamp=expected_timestamp)



def test_validate_bundle_payloads_rejects_non_string_payload_identity(tmp_path: Path) -> None:
    expected_timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(expected_timestamp)}__{archive_importer._run_id(expected_timestamp)}"
    bundle_dir.mkdir()
    expected_as_of = expected_timestamp.isoformat().replace("+00:00", "Z")
    payloads = {
        "metadata.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_BUNDLE_SCHEMA,
            "run_id": archive_importer._run_id(expected_timestamp),
        },
        "market_context.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": 123,
            "instrument_rows": [],
        },
        "derivatives_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": expected_as_of,
        },
        "account_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": expected_as_of,
        },
        "instrument_snapshot.json": {
            "schema_version": archive_importer.PHASE1_IMPORTER_INSTRUMENT_SNAPSHOT_SCHEMA,
            "as_of": expected_as_of,
            "rows": [],
        },
    }
    for name, payload in payloads.items():
        (bundle_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=r"market_context\.json as_of must be a string"):
        archive_importer._validate_bundle_payloads(bundle_dir, expected_timestamp=expected_timestamp)


def test_write_phase1_dataset_bundle_rejects_malformed_derivatives_contract_type_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [
                {
                    "symbol": "BTCUSDT",
                    "instrument": "perpetual",
                    "category": "futures",
                    "exchange": "binance",
                    "contractType": "CURRENT_QUARTER",
                }
            ],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"derivatives_snapshot rows\[0\]\.contractType is unsupported: CURRENT_QUARTER"):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseAsset", "btc"),
        ("quoteAsset", " USDT"),
        ("marginAsset", True),
        ("base_asset", ""),
        ("quote_asset", "USD\nT"),
        ("margin_asset", "USDT-PERP"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_derivatives_asset_aliases_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    row = {
        "symbol": "BTCUSDT",
        "instrument": "perpetual",
        "category": "futures",
        "exchange": "binance",
        "contractType": "PERPETUAL",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "margin_asset": "USDT",
    }
    row[field] = value
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [row],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"derivatives_snapshot rows\[0\]\.{field} must be an uppercase asset code",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_malformed_open_position_identity_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "btcusdt",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"account\.open_positions\[0\]\.symbol must be an uppercase canonical string"):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("qty", "expected_message"),
    [
        (0.0, r"account\.open_positions\[0\]\.qty must be a positive finite number"),
        (-0.25, r"account\.open_positions\[0\]\.qty must be a positive finite number"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_non_positive_open_position_qty_without_artifact(
    tmp_path: Path, qty: float, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": qty,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize("status", ["CLOSED", "SKIPPED", "FAILED", "CANCELLED", "CANCELED"])
def test_write_phase1_dataset_bundle_rejects_terminal_open_position_statuses_without_artifact(
    tmp_path: Path, status: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "status": status,
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.status must not be a terminal open position state",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_order_status_in_open_positions_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "status": "OPEN",
                    "orderStatus": "FILLED",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.orderStatus is not an open position status field",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize("field", ["positionStatus", "position_status"])
def test_write_phase1_dataset_bundle_rejects_terminal_open_position_status_aliases_without_artifact(
    tmp_path: Path, field: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    field: "FILLED",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must not be a terminal open position state",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_execution_before_order_time_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "order_time": "2024-01-01T00:00:02Z",
                    "execution_time": "2024-01-01T00:00:01Z",
                    "fill_time": "2024-01-01T00:00:03Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.execution_time must be at or after order_time",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_updated_before_opened_at_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:02Z",
                    "updated_at": "2024-01-01T00:00:01Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.updated_at must be at or after opened_at",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("close_time", "2023-12-31T23:59:59Z", r"account\.open_positions\[0\]\.close_time must be at or after opened_at"),
        ("settlement_time", "2024-01-01T00:00:02Z", r"account\.open_positions\[0\]\.settlement_time must be at or after close_time"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_impossible_close_lifecycle_without_artifact(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "opened_at": "2024-01-01T00:00:00Z",
        "close_time": "2024-01-01T00:00:03Z",
        "settlement_time": "2024-01-01T00:00:04Z",
        "qty": 0.5,
        "entry_price": 60000.0,
        "mark_price": 61000.0,
    }
    position[field] = value
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [position],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=match):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "match"),
    [
        ("closed_at", r"account\.open_positions\[0\]\.closed_at must be at or after opened_at"),
        ("closedAt", r"account\.open_positions\[0\]\.closedAt must be at or after opened_at"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_closed_position_alias_before_opened_at_without_artifact(
    tmp_path: Path, field: str, match: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "opened_at": "2024-01-01T00:00:00Z",
        field: "2023-12-31T23:59:59Z",
        "qty": 0.5,
        "entry_price": 60000.0,
        "mark_price": 61000.0,
    }
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [position],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=match):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_expiry_before_opened_at_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "expiry_time": "2023-12-31T23:59:59Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.expiry_time must be at or after opened_at",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_settlement_before_opened_at_without_close_time(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "settlement_time": "2023-12-31T23:59:59Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.settlement_time must be at or after opened_at",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_settlement_alias_before_expiry_time_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "expiry_time": "2024-01-01T00:00:03Z",
                    "settlementTime": "2024-01-01T00:00:02Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.settlementTime must be at or after expiry_time",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("event_time", "2023-12-31T23:59:59Z", r"account\.open_positions\[0\]\.event_time must be at or after opened_at"),
        ("trade_time", "2023-12-31T23:59:59Z", r"account\.open_positions\[0\]\.trade_time must be at or after opened_at"),
        ("orderTime", "2023-12-31T23:59:57Z", r"account\.open_positions\[0\]\.orderTime must be at or after opened_at"),
        ("executionTime", "2023-12-31T23:59:58Z", r"account\.open_positions\[0\]\.executionTime must be at or after opened_at"),
        ("fillTime", "2023-12-31T23:59:59Z", r"account\.open_positions\[0\]\.fillTime must be at or after opened_at"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_open_position_lifecycle_times_before_opened_at_without_artifact(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "event_time": "2024-01-01T00:00:00Z",
                    "trade_time": "2024-01-01T00:00:00Z",
                    "orderTime": "2024-01-01T00:00:00Z",
                    "executionTime": "2024-01-01T00:00:01Z",
                    "fillTime": "2024-01-01T00:00:02Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"
    material.account_snapshot["open_positions"][0][field] = value

    with pytest.raises(ValueError, match=match):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("source", "paper_snapshot", r"account\.open_positions\[0\]\.source must be one of"),
        ("venue", " BINANCE", r"account\.open_positions\[0\]\.venue must be a canonical string"),
        ("exchange", "coinbase", r"account\.open_positions\[0\]\.exchange must be an uppercase canonical string"),
        ("accountSource", True, r"account\.open_positions\[0\]\.accountSource must be a canonical string"),
        ("positionSource", "live_exchange", r"account\.open_positions\[0\]\.positionSource must be one of"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_origin_aliases_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "source": "archive_fixture",
                    "venue": "BINANCE",
                    "exchange": "BINANCE",
                    "accountSource": "account_snapshot",
                    "positionSource": "paper_execution",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("quote_asset", True, r"account\.open_positions\[0\]\.quote_asset must be an uppercase asset code"),
        ("base_asset", "", r"account\.open_positions\[0\]\.base_asset must be an uppercase asset code"),
        ("margin_asset", " USDT", r"account\.open_positions\[0\]\.margin_asset must be an uppercase asset code"),
        ("collateral_asset", "usdt", r"account\.open_positions\[0\]\.collateral_asset must be an uppercase asset code"),
        ("settlement_asset", "USD\nT", r"account\.open_positions\[0\]\.settlement_asset must be an uppercase asset code"),
        ("fee_asset", "USDT-PERP", r"account\.open_positions\[0\]\.fee_asset must be an uppercase asset code"),
        ("commission_asset", "bnb", r"account\.open_positions\[0\]\.commission_asset must be an uppercase asset code"),
        ("funding_asset", "USDT/USDC", r"account\.open_positions\[0\]\.funding_asset must be an uppercase asset code"),
        ("pnl_asset", "USDT ", r"account\.open_positions\[0\]\.pnl_asset must be an uppercase asset code"),
        ("pnl_currency", [], r"account\.open_positions\[0\]\.pnl_currency must be an uppercase asset code"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_asset_currency_aliases_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "margin_asset": "USDT",
                    "collateral_asset": "USDT",
                    "settlement_asset": "USDT",
                    "fee_asset": "BNB",
                    "funding_asset": "USDT",
                    "pnl_asset": "USDT",
                    "pnl_currency": "USDT",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fundingFee", "0.1"),
        ("borrowFee", float("nan")),
        ("realizedFee", True),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_camelcase_fee_numerics_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "fee": 1.0,
                    "commission": 1.0,
                    "funding_fee": 0.0,
                    "borrow_fee": 0.0,
                    "realized_fee": 1.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a non-negative finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_preserves_canonical_open_position_asset_currency_aliases(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "margin_asset": "USDT",
                    "collateral_asset": "FDUSD",
                    "settlement_asset": "USDC",
                    "fee_asset": "BNB",
                    "funding_asset": "USDT",
                    "pnl_asset": "USDT",
                    "pnl_currency": "USDT",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )

    bundle_dir = write_phase1_dataset_bundle(material, tmp_path)
    loaded_account = json.loads((bundle_dir / "account_snapshot.json").read_text(encoding="utf-8"))

    assert loaded_account["open_positions"][0]["base_asset"] == "BTC"
    assert loaded_account["open_positions"][0]["quote_asset"] == "USDT"
    assert loaded_account["open_positions"][0]["collateral_asset"] == "FDUSD"
    assert loaded_account["open_positions"][0]["settlement_asset"] == "USDC"
    assert loaded_account["open_positions"][0]["fee_asset"] == "BNB"


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("collateralAsset", "collateral_asset"),
        ("collateralCurrency", "collateral_currency"),
        ("feeAsset", "fee_asset"),
        ("commissionAsset", "commission_asset"),
        ("fundingCurrency", "funding_currency"),
        ("pnlCurrency", "pnl_currency"),
        ("costCurrency", "cost_currency"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_fee_commission_asset_aliases_without_artifact(
    tmp_path: Path, canonical: str, alias: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    canonical: "BNB",
                    alias: "USDT",
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{alias} must equal {canonical}",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_malformed_account_balance_asset_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "usdt",
                    "free": 75000.0,
                    "locked": 25000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.asset must be an uppercase asset code",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_asset_string_subclass_without_artifact(
    tmp_path: Path,
) -> None:
    class AssetCode(str):
        pass

    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": AssetCode("USDT"),
                    "free": 75000.0,
                    "locked": 25000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.asset must be an uppercase asset code",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("free", True),
        ("locked", "25000.0"),
        ("walletBalance", float("nan")),
        ("crossWalletBalance", float("inf")),
        ("availableBalance", -1.0),
        ("maxWithdrawAmount", float("-inf")),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_account_balance_numeric_aliases_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "walletBalance": 100000.0,
                    "crossWalletBalance": 90000.0,
                    "availableBalance": 75000.0,
                    "maxWithdrawAmount": 70000.0,
                }
            ],
        },
    )
    material.account_snapshot["balances"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.balances\[0\]\.{field} must be a non-negative finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_cross_wallet_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "crossWalletBalance": 90000.0,
                    "cross_wallet_balance": 90001.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.cross_wallet_balance must equal crossWalletBalance",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_available_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "availableBalance": 75000.0,
                    "available_balance": 75001.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.available_balance must equal availableBalance",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_max_withdraw_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "maxWithdrawAmount": 70000.0,
                    "max_withdraw_amount": 70001.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.max_withdraw_amount must equal maxWithdrawAmount",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_margin_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "marginBalance": 100000.0,
                    "margin_balance": 100001.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.margin_balance must equal marginBalance",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_maintenance_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "maintenanceMargin": 1250.0,
                    "maintenance_margin": 1251.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.maintenance_margin must equal maintenanceMargin",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_initial_margin_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "initialMargin": 1250.0,
                    "initial_margin": 1251.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.initial_margin must equal initialMargin",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_total_wallet_balance_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "totalWalletBalance": 100000.0,
            "total_wallet_balance": 100001.0,
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.total_wallet_balance must equal totalWalletBalance",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_futures_wallet_balance_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "futuresWalletBalance": 100000.0,
            "futures_wallet_balance": 100001.0,
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.futures_wallet_balance must equal futuresWalletBalance",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("canonical", "alias", "expected_message"),
    [
        (
            "totalInitialMargin",
            "total_initial_margin",
            r"account\.total_initial_margin must equal totalInitialMargin",
        ),
        (
            "totalMaintMargin",
            "total_maint_margin",
            r"account\.total_maint_margin must equal totalMaintMargin",
        ),
        (
            "totalMarginBalance",
            "total_margin_balance",
            r"account\.total_margin_balance must equal totalMarginBalance",
        ),
    ],
)
def test_write_phase1_dataset_bundle_rejects_total_margin_alias_mismatch_without_artifact(
    tmp_path: Path,
    canonical: str,
    alias: str,
    expected_message: str,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            canonical: 1250.0,
            alias: 1251.0,
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("totalWalletBalance", True),
        ("total_wallet_balance", "100000.0"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_invalid_total_wallet_balance_alias_values_without_artifact(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "totalWalletBalance": 100000.0,
            "total_wallet_balance": 100000.0,
        },
    )
    material.account_snapshot[field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.{field} must be a non-negative finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_unsafe_account_id_without_artifact(tmp_path: Path) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "account_id": "paper account 001",
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"account\.account_id must be a canonical identifier string"):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initialMargin", True),
        ("initialMargin", "1250.0"),
        ("initial_margin", float("nan")),
        ("initial_margin", float("inf")),
    ],
)
def test_write_phase1_dataset_bundle_rejects_invalid_account_balance_initial_margin_alias_values_without_artifact(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    field: value,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.balances\[0\]\.{field} must be a non-negative finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("canonical", "alias", "expected_message"),
    [
        (
            "totalUnrealizedProfit",
            "total_unrealized_profit",
            r"account\.balances\[0\]\.total_unrealized_profit must equal totalUnrealizedProfit",
        ),
        (
            "unrealizedPnl",
            "unrealized_pnl",
            r"account\.balances\[0\]\.unrealized_pnl must equal unrealizedPnl",
        ),
        (
            "unRealizedProfit",
            "unrealizedProfit",
            r"account\.balances\[0\]\.unrealizedProfit must equal unRealizedProfit",
        ),
        (
            "realizedPnl",
            "realized_pnl",
            r"account\.balances\[0\]\.realized_pnl must equal realizedPnl",
        ),
        (
            "realizedPnl",
            "realizedProfit",
            r"account\.balances\[0\]\.realizedProfit must equal realizedPnl",
        ),
    ],
)
def test_write_phase1_dataset_bundle_rejects_account_balance_signed_pnl_alias_mismatch_without_artifact(
    tmp_path: Path, canonical: str, alias: str, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    canonical: -12.5,
                    alias: -12.4,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_allows_negative_account_balance_signed_pnl_aliases(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "totalUnrealizedProfit": -12.5,
                    "total_unrealized_profit": -12.5,
                    "unRealizedProfit": -12.5,
                    "unrealizedProfit": -12.5,
                    "unrealizedPnl": -12.5,
                    "unrealized_pnl": -12.5,
                    "pnl": -12.5,
                    "upl": -12.5,
                    "realizedPnl": -7.25,
                    "realized_pnl": -7.25,
                    "realizedProfit": -7.25,
                }
            ],
        },
    )

    bundle_dir = write_phase1_dataset_bundle(material, tmp_path)

    assert bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("realizedPnl", True),
        ("realizedProfit", "1.0"),
        ("realized_pnl", float("nan")),
    ],
)
def test_write_phase1_dataset_bundle_rejects_invalid_account_balance_realized_pnl_alias_values_without_artifact(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    field: value,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.balances\[0\]\.{field} must be a finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_account_balance_wallet_parity_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "balances": [
                {
                    "asset": "USDT",
                    "free": 75000.0,
                    "locked": 25000.0,
                    "walletBalance": 100001.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.balances\[0\]\.walletBalance must equal free \+ locked",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_tag", True),
        ("strategyTag", "strategy v2"),
        ("intent_id", "intent-btc "),
        ("intentId", "intent-btc\n"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_strategy_intent_aliases_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "strategy_tag": "trend_v2",
                    "strategyTag": "trend_v2",
                    "intent_id": "intent-btc",
                    "intentId": "intent-btc",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a canonical identifier string",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_strategy_tag_alias_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "strategy_tag": "trend_v2",
                    "strategyTag": "mean_reversion_v1",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.strategy_tag must equal strategyTag",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position_id", 123),
        ("positionId", ""),
        ("order_id", " order-123"),
        ("orderId", "order-123\n"),
        ("client_order_id", "client/order"),
        ("clientOrderId", "client order"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_order_position_ids_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "position_id": "pos-123",
                    "positionId": "pos:123",
                    "order_id": "order_123",
                    "orderId": "order-123",
                    "client_order_id": "client_123",
                    "clientOrderId": "client-123",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a canonical identifier string",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("canonical", "alias", "canonical_value", "alias_value"),
    [
        ("tradeId", "trade_id", "trade-001", "trade-002"),
        ("orderId", "order_id", "order-001", "order-002"),
        ("clientOrderId", "client_order_id", "client-001", "client-002"),
        ("strategyId", "strategy_id", "strategy-001", "strategy-002"),
        ("sourceId", "source_id", "source-001", "source-002"),
        ("parentOrderId", "parent_order_id", "parent-001", "parent-002"),
        ("exchangeOrderId", "exchange_order_id", "exchange-001", "exchange-002"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_identifier_aliases_without_artifact(
    tmp_path: Path,
    canonical: str,
    alias: str,
    canonical_value: str,
    alias_value: str,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    canonical: canonical_value,
                    alias: alias_value,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{alias} must equal {canonical}",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maker", "false"),
        ("taker", 1),
        ("buyer", "true"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_execution_boolean_aliases_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "maker": False,
                    "taker": True,
                    "buyer": True,
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=rf"account\.open_positions\[0\]\.{field} must be a strict boolean"):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("opened_at", 123),
        ("openedAt", ""),
        ("updated_at", " 2024-01-01T00:00:00Z"),
        ("updatedAt", "2024-01-01T00:00:00Z\n"),
        ("closed_at", "2024-01-01 00:00:00Z"),
        ("closedAt", "2024-01-01T00:00:00"),
        ("as_of", "2024-01-01T00:00:00+00:00"),
        ("timestamp", "not-a-timestamp"),
        ("last_update_time", "2024-01-01T00:00:00.1Z"),
        ("createdAt", "2024-01-01 00:00:00Z"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_time_metadata_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "as_of": "2024-01-01T00:00:00Z",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "last_update_time": "2024-01-01T00:00:00Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a canonical UTC ISO timestamp",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_time", 123),
        ("trade_time", ""),
        ("execution_time", " 2024-01-01T00:00:00Z"),
        ("fill_time", "2024-01-01T00:00:00Z\n"),
        ("order_time", "2024-01-01T00:00:00\x00Z"),
        ("close_time", "2024-01-01T00:00:00+01:00"),
        ("expiry_time", "2024-01-01T00:00:00.1Z"),
        ("settlement_time", "2024-01-01"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_provenance_timestamps_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "event_time": "2024-01-01T00:00:00Z",
                    "trade_time": "2024-01-01T00:00:00Z",
                    "execution_time": "2024-01-01T00:00:00Z",
                    "fill_time": "2024-01-01T00:00:00Z",
                    "order_time": "2024-01-01T00:00:00Z",
                    "close_time": "2024-01-01T00:00:00Z",
                    "expiry_time": "2024-01-01T00:00:00Z",
                    "settlement_time": "2024-01-01T00:00:00Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a canonical UTC ISO timestamp",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_preserves_canonical_open_position_provenance_timestamps(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "event_time": "2024-01-01T00:00:00Z",
                    "trade_time": "2024-01-01T00:00:00Z",
                    "execution_time": "2024-01-01T00:00:00Z",
                    "fill_time": "2024-01-01T00:00:00Z",
                    "order_time": "2024-01-01T00:00:00Z",
                    "close_time": "2024-01-01T00:00:00Z",
                    "expiry_time": "2024-01-01T00:00:00Z",
                    "settlement_time": "2024-01-01T00:00:00Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )

    bundle_dir = write_phase1_dataset_bundle(material, tmp_path)
    loaded_account = json.loads((bundle_dir / "account_snapshot.json").read_text(encoding="utf-8"))

    assert loaded_account["open_positions"][0]["event_time"] == "2024-01-01T00:00:00Z"
    assert loaded_account["open_positions"][0]["settlement_time"] == "2024-01-01T00:00:00Z"


def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_event_time_aliases_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "opened_at": "2024-01-01T00:00:00Z",
                    "eventTime": "2024-01-01T00:00:02Z",
                    "event_time": "2024-01-01T00:00:01Z",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.event_time must equal eventTime",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("margin_type", "expected_message"),
    [
        (" cross ", r"account\.open_positions\[0\]\.marginType must be a canonical string"),
        ("cross", r"account\.open_positions\[0\]\.marginType must be one of CROSS, ISOLATED"),
        ("PORTFOLIO", r"account\.open_positions\[0\]\.marginType must be one of CROSS, ISOLATED"),
        (123, r"account\.open_positions\[0\]\.marginType must be a canonical string"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_margin_type_without_artifact(
    tmp_path: Path, margin_type: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "marginType": margin_type,
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("position_source", 123, r"account\.open_positions\[0\]\.position_source must be a canonical string"),
        ("position_source", "account_snapshot\n", r"account\.open_positions\[0\]\.position_source must be a canonical string"),
        ("position_source", "exchange_import", r"account\.open_positions\[0\]\.position_source must be one of"),
        ("signal_source", " ", r"account\.open_positions\[0\]\.signal_source must be a canonical string"),
        ("signal_source", "trend/engine", r"account\.open_positions\[0\]\.signal_source must be a canonical identifier string"),
        ("strategy_source", True, r"account\.open_positions\[0\]\.strategy_source must be a canonical string"),
        ("strategy_source", "trend engine", r"account\.open_positions\[0\]\.strategy_source must be a canonical identifier string"),
        ("data_source", "binance futures", r"account\.open_positions\[0\]\.data_source must be a canonical identifier string"),
        ("margin_type", "ISOLATED ", r"account\.open_positions\[0\]\.margin_type must be a canonical string"),
        ("margin_type", "PORTFOLIO", r"account\.open_positions\[0\]\.margin_type must be one of CROSS, ISOLATED"),
        ("product_type", [], r"account\.open_positions\[0\]\.product_type must be a canonical string"),
        ("product_type", "PERPETUAL", r"account\.open_positions\[0\]\.product_type must be one of FUTURES, MARGIN, SPOT"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_provenance_taxonomy_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "position_source": "account_snapshot",
                    "signal_source": "trend_engine",
                    "strategy_source": "trend_v2",
                    "data_source": "binance_futures",
                    "margin_type": "CROSS",
                    "product_type": "FUTURES",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("canonical", "alias", "canonical_value", "alias_value"),
    [
        ("positionSource", "position_source", "archive_fixture", "paper_execution"),
        ("signalSource", "signal_source", "trend_engine", "mean_reversion"),
        ("strategySource", "strategy_source", "trend_v2", "carry_v1"),
        ("dataSource", "data_source", "binance_futures", "archive_backfill"),
        ("marginType", "margin_type", "CROSS", "ISOLATED"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_source_aliases_without_artifact(
    tmp_path: Path, canonical: str, alias: str, canonical_value: str, alias_value: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    canonical: canonical_value,
                    alias: alias_value,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{alias} must equal {canonical}",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_conflicting_open_position_venue_exchange_aliases_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(backtest_dataset._ACCOUNT_OPEN_POSITION_UPPERCASE_ENUM_FIELDS, "venue", {"BINANCE", "COINBASE"})
    monkeypatch.setitem(
        backtest_dataset._ACCOUNT_OPEN_POSITION_UPPERCASE_ENUM_FIELDS,
        "exchange",
        {"BINANCE", "COINBASE"},
    )
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "venue": "BINANCE",
                    "exchange": "COINBASE",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.exchange must equal venue",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("leverage", True, r"account\.open_positions\[0\]\.leverage must be a positive finite number"),
        ("leverage", "2", r"account\.open_positions\[0\]\.leverage must be a positive finite number"),
        ("leverage", float("nan"), r"account\.open_positions\[0\]\.leverage must be a positive finite number"),
        ("leverage", float("inf"), r"account\.open_positions\[0\]\.leverage must be a positive finite number"),
        ("leverage", 0.0, r"account\.open_positions\[0\]\.leverage must be a positive finite number"),
        (
            "isolated_margin",
            -1.0,
            r"account\.open_positions\[0\]\.isolated_margin must be a non-negative finite number",
        ),
        ("margin", "10", r"account\.open_positions\[0\]\.margin must be a non-negative finite number"),
        (
            "initial_margin",
            True,
            r"account\.open_positions\[0\]\.initial_margin must be a non-negative finite number",
        ),
        (
            "maintenance_margin",
            float("-inf"),
            r"account\.open_positions\[0\]\.maintenance_margin must be a non-negative finite number",
        ),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_leverage_margin_numbers_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "leverage": 2.0,
                    "isolated_margin": 1000.0,
                    "margin": 1000.0,
                    "initial_margin": 1000.0,
                    "maintenance_margin": 75.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("notional", "30000.0", r"account\.open_positions\[0\]\.notional must be a positive finite number"),
        ("notional", -1.0, r"account\.open_positions\[0\]\.notional must be a positive finite number"),
        ("unrealized_pnl", True, r"account\.open_positions\[0\]\.unrealized_pnl must be a finite number"),
        ("unrealizedPnl", "12.5", r"account\.open_positions\[0\]\.unrealizedPnl must be a finite number"),
        ("realized_pnl", float("nan"), r"account\.open_positions\[0\]\.realized_pnl must be a finite number"),
        ("realized_cost", True, r"account\.open_positions\[0\]\.realized_cost must be a non-negative finite number"),
        (
            "realizedCost",
            "12.5",
            r"account\.open_positions\[0\]\.realizedCost must be a non-negative finite number",
        ),
        ("pnl", float("inf"), r"account\.open_positions\[0\]\.pnl must be a finite number"),
        ("margin_ratio", -0.01, r"account\.open_positions\[0\]\.margin_ratio must be a ratio in \(0, 1\]"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_notional_pnl_numbers_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "notional": 30500.0,
                    "unrealized_pnl": 500.0,
                    "unrealizedPnl": 500.0,
                    "realized_pnl": -25.0,
                    "pnl": 475.0,
                    "margin_ratio": 0.08,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("position_value", True, r"account\.open_positions\[0\]\.position_value must be a positive finite number"),
        ("market_value", "30500.0", r"account\.open_positions\[0\]\.market_value must be a positive finite number"),
        ("exposure_value", float("nan"), r"account\.open_positions\[0\]\.exposure_value must be a positive finite number"),
        ("positionValue", 0.0, r"account\.open_positions\[0\]\.positionValue must be a positive finite number"),
        ("marketValue", float("inf"), r"account\.open_positions\[0\]\.marketValue must be a positive finite number"),
        (
            "exposureValue",
            float("-inf"),
            r"account\.open_positions\[0\]\.exposureValue must be a positive finite number",
        ),
        ("margin_used", "1000.0", r"account\.open_positions\[0\]\.margin_used must be a non-negative finite number"),
        ("marginUsed", float("nan"), r"account\.open_positions\[0\]\.marginUsed must be a non-negative finite number"),
        (
            "collateral_value",
            -1.0,
            r"account\.open_positions\[0\]\.collateral_value must be a non-negative finite number",
        ),
        (
            "collateralValue",
            True,
            r"account\.open_positions\[0\]\.collateralValue must be a non-negative finite number",
        ),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_derived_value_numbers_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "position_value": 30500.0,
                    "positionValue": 30500.0,
                    "market_value": 30500.0,
                    "marketValue": 30500.0,
                    "exposure_value": 30500.0,
                    "exposureValue": 30500.0,
                    "margin_used": 1000.0,
                    "marginUsed": 1000.0,
                    "collateral_value": 1000.0,
                    "collateralValue": 1000.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_open_position_collateral_value_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "collateralValue": 1000.0,
                    "collateral_value": 999.99,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.collateral_value must equal collateralValue",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_open_position_unrealized_cost_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "unrealizedCost": 12.5,
                    "unrealized_cost": 12.51,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.unrealized_cost must equal unrealizedCost",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_open_position_realized_cost_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "realizedCost": 12.5,
                    "realized_cost": 12.51,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.realized_cost must equal realizedCost",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("liquidationPrice", "liquidation_price"),
        ("breakEvenPrice", "break_even_price"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_open_position_price_alias_mismatch_without_artifact(
    tmp_path: Path, canonical: str, alias: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    canonical: 59000.0,
                    alias: 59000.01,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{alias} must equal {canonical}",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_open_position_margin_used_alias_mismatch_without_artifact(
    tmp_path: Path,
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "marginUsed": 1000.0,
                    "margin_used": 999.99,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.margin_used must equal marginUsed",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("margin_ratio", 0.0, r"account\.open_positions\[0\]\.margin_ratio must be a ratio in \(0, 1\]"),
        ("marginRatio", 1.01, r"account\.open_positions\[0\]\.marginRatio must be a ratio in \(0, 1\]"),
        (
            "maintenance_margin_ratio",
            "0.004",
            r"account\.open_positions\[0\]\.maintenance_margin_ratio must be a ratio in \(0, 1\]",
        ),
        (
            "initial_margin_ratio",
            True,
            r"account\.open_positions\[0\]\.initial_margin_ratio must be a ratio in \(0, 1\]",
        ),
        ("risk_ratio", float("nan"), r"account\.open_positions\[0\]\.risk_ratio must be a ratio in \(0, 1\]"),
        ("riskRatio", float("inf"), r"account\.open_positions\[0\]\.riskRatio must be a ratio in \(0, 1\]"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_ratio_fields_without_artifact(
    tmp_path: Path, field: str, value: object, expected_message: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "margin_ratio": 0.08,
                    "marginRatio": 0.08,
                    "maintenance_margin_ratio": 0.004,
                    "initial_margin_ratio": 0.05,
                    "risk_ratio": 0.01,
                    "riskRatio": 0.01,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=expected_message):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("liquidation_price", "59000.0"),
        ("liquidationPrice", True),
        ("break_even_price", float("inf")),
        ("breakEvenPrice", "60050.0"),
        ("risk_price", -1.0),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_open_position_risk_prices_without_artifact(
    tmp_path: Path, field: str, value: object
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "liquidation_price": 59000.0,
                    "liquidationPrice": 59000.0,
                    "break_even_price": 60050.0,
                    "breakEvenPrice": 60050.0,
                    "risk_price": 59500.0,
                }
            ],
        },
    )
    material.account_snapshot["open_positions"][0][field] = value
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    expected_qualifier = (
        "positive finite"
        if field in {"liquidation_price", "liquidationPrice", "break_even_price", "breakEvenPrice", "risk_price"}
        else "non-negative finite"
    )
    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a {expected_qualifier} number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_zero_open_position_notional_without_artifact(tmp_path: Path) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "notional": 0.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.notional must be a positive finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    "field",
    [
        "entry_price",
        "entryPrice",
        "mark_price",
        "markPrice",
        "liquidation_price",
        "break_even_price",
        "stop_price",
    ],
)
def test_write_phase1_dataset_bundle_rejects_zero_open_position_core_prices_without_artifact(
    tmp_path: Path, field: str
) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    position = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.5,
        "entry_price": 60000.0,
        "mark_price": 61000.0,
    }
    position[field] = 0.0
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [position],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"account\.open_positions\[0\]\.{field} must be a positive finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_zero_open_position_risk_price_without_artifact(tmp_path: Path) -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=timestamp,
        run_id=archive_importer._run_id(timestamp),
        metadata={"timestamp": "2024-01-01T00:00:00Z", "run_id": archive_importer._run_id(timestamp)},
        market_context={
            "schema_version": archive_importer.PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "symbols": {"BTCUSDT": {}},
            "instrument_rows": [],
        },
        derivatives_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "rows": [],
        },
        account_snapshot={
            "schema_version": archive_importer.PHASE1_IMPORTER_ACCOUNT_SCHEMA,
            "as_of": "2024-01-01T00:00:00Z",
            "equity": 100000.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "qty": 0.5,
                    "entry_price": 60000.0,
                    "mark_price": 61000.0,
                    "risk_price": 0.0,
                }
            ],
        },
    )
    expected_bundle_dir = tmp_path / f"{archive_importer._bundle_fragment(timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=r"account\.open_positions\[0\]\.risk_price must be a positive finite number",
    ):
        write_phase1_dataset_bundle(material, tmp_path)

    assert not expected_bundle_dir.exists()


def test_merged_execution_evidence_coverage_rejects_non_object_buckets() -> None:
    with pytest.raises(ValueError, match="execution_evidence.materialized must be a JSON object"):
        archive_importer._merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": True,
                        "max_staleness_seconds": 300,
                        "materialized": ["order_book"],
                    }
                }
            ]
        )


def test_merged_execution_evidence_coverage_rejects_unknown_bucket_keys() -> None:
    with pytest.raises(ValueError, match="execution_evidence.materialized.liquidations is unsupported"):
        archive_importer._merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": True,
                        "max_staleness_seconds": 300,
                        "materialized": {"order_book": 1, "liquidations": 1},
                    }
                }
            ]
        )


def test_merged_execution_evidence_coverage_preserves_identical_non_default_max_staleness() -> None:
    merged = archive_importer._merged_execution_evidence_coverage(
        [
            {
                "execution_evidence": {
                    "available": True,
                    "max_staleness_seconds": 120,
                    "materialized": {"order_book": 1},
                }
            },
            {
                "execution_evidence": {
                    "available": False,
                    "max_staleness_seconds": 120,
                    "materialized": {"trades": 1},
                }
            },
        ]
    )

    assert merged["max_staleness_seconds"] == 120
    assert merged["materialized"] == {"order_book": 1, "trades": 1}


def test_merged_execution_evidence_coverage_rejects_conflicting_non_default_max_staleness() -> None:
    with pytest.raises(
        ValueError,
        match="execution_evidence.max_staleness_seconds has conflicting producer values",
    ):
        archive_importer._merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": True,
                        "max_staleness_seconds": 120,
                        "materialized": {"order_book": 1},
                    }
                },
                {
                    "execution_evidence": {
                        "available": True,
                        "max_staleness_seconds": 300,
                        "materialized": {"trades": 1},
                    }
                },
            ]
        )


@pytest.mark.parametrize("contaminated_available", [1, "true"])
def test_merged_execution_evidence_coverage_rejects_contaminated_existing_available(
    monkeypatch: pytest.MonkeyPatch,
    contaminated_available: object,
) -> None:
    original_template = archive_importer._execution_coverage_template

    def contaminated_template(**kwargs: object) -> dict[str, object]:
        template = original_template(**kwargs)
        template["available"] = contaminated_available
        return template

    monkeypatch.setattr(archive_importer, "_execution_coverage_template", contaminated_template)

    with pytest.raises(ValueError, match="execution_evidence.available must be boolean"):
        archive_importer._merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": False,
                        "max_staleness_seconds": 300,
                    }
                }
            ]
        )


@pytest.mark.parametrize("contaminated_counter", [True, "1"])
def test_merged_execution_evidence_coverage_rejects_contaminated_existing_counter(
    monkeypatch: pytest.MonkeyPatch,
    contaminated_counter: object,
) -> None:
    original_template = archive_importer._execution_coverage_template

    def contaminated_template(**kwargs: object) -> dict[str, object]:
        template = original_template(**kwargs)
        template["materialized"]["order_book"] = contaminated_counter
        return template

    monkeypatch.setattr(archive_importer, "_execution_coverage_template", contaminated_template)

    with pytest.raises(ValueError, match="execution_evidence.materialized.order_book must be a non-negative integer"):
        archive_importer._merged_execution_evidence_coverage(
            [
                {
                    "execution_evidence": {
                        "available": True,
                        "max_staleness_seconds": 300,
                        "materialized": {"order_book": 1},
                    }
                }
            ]
        )


def test_merged_futures_context_coverage_rejects_non_object_buckets() -> None:
    with pytest.raises(ValueError, match="futures_context.materialized must be a JSON object"):
        archive_importer._merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": True,
                        "max_age_seconds": {"mark_price": 3660, "funding": 28860, "open_interest": 3660},
                        "materialized": ["mark_price"],
                    }
                }
            ]
        )


def test_merged_futures_context_coverage_rejects_unknown_bucket_keys() -> None:
    with pytest.raises(ValueError, match="futures_context.stale.index_price is unsupported"):
        archive_importer._merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": True,
                        "max_age_seconds": {"mark_price": 3660, "funding": 28860, "open_interest": 3660},
                        "stale": {"mark_price": 1, "index_price": 1},
                    }
                }
            ]
        )


@pytest.mark.parametrize(
    ("invalid_key", "expected_error"),
    [
        (123, "futures_context.max_age_seconds key must be a string"),
        (" mark_price", "futures_context.max_age_seconds key must be canonical"),
        ("index_price", "futures_context.max_age_seconds.index_price is unsupported"),
    ],
)
def test_merged_futures_context_coverage_rejects_invalid_max_age_keys(
    invalid_key: object, expected_error: str
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        archive_importer._merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": True,
                        "max_age_seconds": {
                            "mark_price": 3660,
                            "funding": 28860,
                            "open_interest": 3660,
                            invalid_key: 1,
                        },
                        "materialized": {"mark_price": 1},
                    }
                }
            ]
        )


@pytest.mark.parametrize(
    ("field", "first_value", "second_value"),
    [
        ("mark_price", 300, 301),
        ("funding", 25200, 25201),
        ("open_interest", 3600, 3601),
    ],
)
def test_merged_futures_context_coverage_rejects_conflicting_max_age_values(
    field: str,
    first_value: int,
    second_value: int,
) -> None:
    first_max_age = {"mark_price": 300, "funding": 25200, "open_interest": 3600}
    second_max_age = dict(first_max_age)
    first_max_age[field] = first_value
    second_max_age[field] = second_value

    with pytest.raises(
        ValueError,
        match=rf"futures_context\.max_age_seconds\.{field} has conflicting values",
    ):
        archive_importer._merged_futures_context_coverage(
            [
                {"futures_context": {"available": True, "max_age_seconds": first_max_age}},
                {"futures_context": {"available": True, "max_age_seconds": second_max_age}},
            ]
        )


def test_merged_futures_context_coverage_preserves_identical_max_age_values() -> None:
    coverage = archive_importer._merged_futures_context_coverage(
        [
            {
                "futures_context": {
                    "available": True,
                    "max_age_seconds": {"mark_price": 301, "funding": 25201, "open_interest": 3601},
                    "materialized": {"mark_price": 1},
                }
            },
            {
                "futures_context": {
                    "available": True,
                    "max_age_seconds": {"mark_price": 301, "funding": 25201, "open_interest": 3601},
                    "materialized": {"funding": 1, "open_interest": 1},
                }
            },
        ]
    )

    assert coverage["max_age_seconds"] == {"mark_price": 301, "funding": 25201, "open_interest": 3601}
    assert coverage["materialized"] == {"mark_price": 1, "funding": 1, "open_interest": 1}


@pytest.mark.parametrize("contaminated_available", [1, "true"])
def test_merged_futures_context_coverage_rejects_contaminated_existing_available(
    monkeypatch: pytest.MonkeyPatch,
    contaminated_available: object,
) -> None:
    original_template = archive_importer._context_coverage_template

    def contaminated_template(**kwargs: object) -> dict[str, object]:
        template = original_template(**kwargs)
        template["available"] = contaminated_available
        return template

    monkeypatch.setattr(archive_importer, "_context_coverage_template", contaminated_template)

    with pytest.raises(ValueError, match="futures_context.available must be boolean"):
        archive_importer._merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": False,
                        "max_age_seconds": {"mark_price": 3660, "funding": 28860, "open_interest": 3660},
                    }
                }
            ]
        )


@pytest.mark.parametrize("contaminated_counter", [True, "1"])
def test_merged_futures_context_coverage_rejects_contaminated_existing_counter(
    monkeypatch: pytest.MonkeyPatch,
    contaminated_counter: object,
) -> None:
    original_template = archive_importer._context_coverage_template

    def contaminated_template(**kwargs: object) -> dict[str, object]:
        template = original_template(**kwargs)
        template["materialized"]["mark_price"] = contaminated_counter
        return template

    monkeypatch.setattr(archive_importer, "_context_coverage_template", contaminated_template)

    with pytest.raises(ValueError, match="futures_context.materialized.mark_price must be a non-negative integer"):
        archive_importer._merged_futures_context_coverage(
            [
                {
                    "futures_context": {
                        "available": True,
                        "max_age_seconds": {"mark_price": 3660, "funding": 28860, "open_interest": 3660},
                        "materialized": {"mark_price": 1},
                    }
                }
            ]
        )


def test_materialize_dataset_root_rejects_negative_futures_context_age_before_write(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=60 * 24)
    start = datetime(2024, 2, 29, 23, tzinfo=UTC)
    end = start + timedelta(hours=1)
    _archive_mark_price_history(
        archive_root,
        symbol="BTCUSDT",
        start=start,
        end=end,
        rows=[{"timestamp": _timestamp_ms(start), "markPrice": "64395.5"}],
    )
    latest = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))[-1]
    market_context = json.loads(json.dumps(latest.market_context))
    market_context["symbols"]["BTCUSDT"]["futures_context"]["mark_price_age_seconds"] = -1
    material = replace(latest, market_context=market_context)
    dataset_root = tmp_path / "dataset"

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.mark_price_age_seconds must be a non-negative integer",
    ):
        _materialize_dataset_root(archive_root=archive_root, dataset_root=dataset_root, materials=[material])

    assert not dataset_root.exists()


def test_materialize_dataset_root_rejects_fractional_futures_context_age_before_write(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    latest = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))[-1]
    market_context = json.loads(json.dumps(latest.market_context))
    market_context["symbols"]["BTCUSDT"]["futures_context"]["funding_age_seconds"] = 25200.5
    material = replace(latest, market_context=market_context)
    dataset_root = tmp_path / "dataset"

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.funding_age_seconds must be a non-negative integer",
    ):
        _materialize_dataset_root(archive_root=archive_root, dataset_root=dataset_root, materials=[material])

    assert not dataset_root.exists()


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("execution_latency_ms", True),
        ("execution_latency_ms", "1"),
        ("execution_latency_ms", 1.5),
        ("execution_latency_ms", float("nan")),
        ("execution_latency_ms", float("inf")),
        ("execution_latency_ms", -1),
        ("sample_count", True),
        ("sample_count", "1"),
        ("sample_count", 1.5),
        ("sample_count", float("nan")),
        ("sample_count", float("inf")),
        ("sample_count", -1),
    ],
)
def test_write_phase1_dataset_bundle_rejects_derivatives_snapshot_latency_and_count_fields_before_write(
    tmp_path: Path,
    field_name: str,
    malformed_value: object,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    latest = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))[-1]
    derivatives_snapshot = json.loads(json.dumps(latest.derivatives_snapshot))
    derivatives_snapshot["rows"][0][field_name] = malformed_value
    material = replace(latest, derivatives_snapshot=derivatives_snapshot)
    dataset_root = tmp_path / "dataset"

    with pytest.raises(
        ValueError,
        match=rf"derivatives_snapshot rows\[0\]\.{field_name} must be a non-negative integer",
    ):
        write_phase1_dataset_bundle(material, dataset_root)

    assert not dataset_root.exists()


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("markPriceAgeSeconds", True),
        ("fundingAgeSeconds", "1"),
        ("openInterestAgeSeconds", 1.5),
        ("orderBookLatencyMs", float("nan")),
        ("tradeCount", float("inf")),
        ("openInterestCount", -1),
    ],
)
def test_write_phase1_dataset_bundle_rejects_derivatives_snapshot_camelcase_numeric_metadata_before_write(
    tmp_path: Path,
    field_name: str,
    malformed_value: object,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    latest = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))[-1]
    derivatives_snapshot = json.loads(json.dumps(latest.derivatives_snapshot))
    derivatives_snapshot["rows"][0][field_name] = malformed_value
    material = replace(latest, derivatives_snapshot=derivatives_snapshot)
    dataset_root = tmp_path / "dataset"

    with pytest.raises(
        ValueError,
        match=rf"derivatives_snapshot rows\[0\]\.{field_name} must be a non-negative integer",
    ):
        write_phase1_dataset_bundle(material, dataset_root)

    assert not dataset_root.exists()



def test_resolved_phase1_imported_dataset_root_path_rejects_non_string_relative_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="phase1 imported dataset root path value must be a string"):
        archive_importer._resolved_phase1_imported_dataset_root_path(tmp_path, 123)


def test_resolved_phase1_imported_dataset_root_path_rejects_padded_relative_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="phase1 imported dataset root path value must be canonical"):
        archive_importer._resolved_phase1_imported_dataset_root_path(tmp_path, " bundles/a ")



def test_resolved_source_manifest_paths_rejects_non_string_entries(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"source manifest_paths\[0\] must be a string"):
        archive_importer._resolved_source_manifest_paths(tmp_path, [123])


def test_resolved_source_manifest_paths_rejects_padded_entries(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"source manifest_paths\[0\] must be canonical"):
        archive_importer._resolved_source_manifest_paths(tmp_path, [" raw-market/BTC/manifest.json "])



def test_validated_source_trace_against_manifests_rejects_noncanonical_manifest_paths(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"materialized dataset bundle metadata source manifest_paths\[0\] must be canonical",
    ):
        archive_importer._validated_source_trace_against_manifests(
            {
                "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                "exchange": "binance",
                "market": "futures",
                "symbols": [],
                "series_keys": [],
                "manifest_paths": [" raw-market/BTC/manifest.json "],
            },
            context="materialized dataset bundle metadata source",
        )


def test_phase1_imported_dataset_root_manifest_paths_rejects_missing_manifest_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        archive_importer,
        "_materialized_dataset_row_source",
        lambda rows: {"scope": archive_importer.PHASE1_IMPORTER_SCOPE},
    )

    with pytest.raises(ValueError, match="phase1 imported dataset root does not declare source manifest_paths"):
        archive_importer._phase1_imported_dataset_root_manifest_paths(tmp_path, [object()])


@pytest.mark.parametrize(
    ("manifest_paths", "match"),
    [
        ("raw-market/BTCUSDT/manifest.json", "phase1 imported dataset root source manifest_paths must be a list"),
        ([], "phase1 imported dataset root source manifest_paths must not be empty"),
        ({}, "phase1 imported dataset root source manifest_paths must be a list"),
        (False, "phase1 imported dataset root source manifest_paths must be a list"),
        ([123], "phase1 imported dataset root source manifest_paths entries must be canonical strings"),
        ([" raw-market/BTCUSDT/manifest.json "], "phase1 imported dataset root source manifest_paths entries must be canonical strings"),
        ([""], "phase1 imported dataset root source manifest_paths entries must be canonical strings"),
    ],
)
def test_phase1_imported_dataset_root_manifest_paths_rejects_present_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_paths: object,
    match: str,
) -> None:
    monkeypatch.setattr(
        archive_importer,
        "_materialized_dataset_row_source",
        lambda rows: {
            "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
            "exchange": "binance",
            "market": "futures",
            "manifest_paths": manifest_paths,
        },
    )

    with pytest.raises(ValueError, match=match):
        archive_importer._phase1_imported_dataset_root_manifest_paths(tmp_path, [object()])


def test_ohlcv_timeframe_coverage_rejects_present_invalid_not_materialized() -> None:
    with pytest.raises(ValueError, match="ohlcv_timeframes.not_materialized must contain a JSON object"):
        archive_importer._ohlcv_timeframe_coverage(
            [],
            materialized_timeframes=["1h"],
            not_materialized=[],
        )


@pytest.mark.parametrize(
    ("bucket", "value", "match"),
    [
        ("available", "2h", r"ohlcv_timeframes.available\[0\] must be a known importer timeframe"),
        ("available", " 1h", r"ohlcv_timeframes.available\[0\] must be canonical"),
        ("materialized", "1H", r"ohlcv_timeframes.materialized\[0\] must be a known importer timeframe"),
        (
            "not_materialized",
            "../raw-market/binance/futures/ohlcv/BTCUSDT/1h",
            "ohlcv_timeframes.not_materialized key must be a known importer timeframe",
        ),
    ],
)
def test_merged_ohlcv_timeframe_coverage_rejects_unknown_timeframe_values(
    bucket: str,
    value: str,
    match: str,
) -> None:
    coverage: dict[str, object] = {
        "available": ["1h"],
        "materialized": ["1h"],
        "not_materialized": {},
    }
    if bucket == "not_materialized":
        coverage[bucket] = {value: "missing_contiguous_bars"}
    else:
        coverage[bucket] = [value]

    with pytest.raises(ValueError, match=match):
        archive_importer._merged_ohlcv_timeframe_coverage([{"ohlcv_timeframes": coverage}])


@pytest.mark.parametrize(
    ("reason", "match"),
    [
        (123, r"ohlcv_timeframes.not_materialized.5m must be a string"),
        ("", r"ohlcv_timeframes.not_materialized.5m must be a string"),
        (" missing_contiguous_bars", r"ohlcv_timeframes.not_materialized.5m must be canonical"),
        ("missing_contiguous_bars ", r"ohlcv_timeframes.not_materialized.5m must be canonical"),
        ("Missing Contiguous Bars", r"ohlcv_timeframes.not_materialized.5m must be a canonical reason code"),
        ("missing contiguous bars", r"ohlcv_timeframes.not_materialized.5m must be a canonical reason code"),
        ("MISSING_CONTIGUOUS_BARS", r"ohlcv_timeframes.not_materialized.5m must be a canonical reason code"),
    ],
)
def test_merged_ohlcv_timeframe_coverage_rejects_malformed_not_materialized_reasons(
    reason: object,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        archive_importer._merged_ohlcv_timeframe_coverage(
            [
                {
                    "ohlcv_timeframes": {
                        "available": ["1h", "5m"],
                        "materialized": ["1h"],
                        "not_materialized": {"5m": reason},
                    }
                }
            ]
        )


def test_materialized_source_trace_rejects_bool_as_number_in_execution_evidence() -> None:
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
        "manifest_paths": ["/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json"],
        "execution_evidence": {
            "available": True,
            "materialized": {"order_book": True},
        },
    }

    with pytest.raises(ValueError, match="execution_evidence.materialized.order_book must be a non-negative integer"):
        archive_importer._merged_execution_evidence_coverage([source])


def test_materialized_source_trace_rejects_bool_as_number_in_futures_context() -> None:
    source = {
        "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": ["BTCUSDT"],
        "series_keys": ["binance:futures:ohlcv:BTCUSDT:1h"],
        "manifest_paths": ["/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/a.manifest.json"],
        "futures_context": {
            "available": True,
            "max_age_seconds": {"mark_price": True},
        },
    }

    with pytest.raises(ValueError, match="futures_context.max_age_seconds.mark_price must be a non-negative integer"):
        archive_importer._merged_futures_context_coverage([source])


def test_merged_ohlcv_timeframe_coverage_preserves_known_importer_timeframes() -> None:
    assert archive_importer._merged_ohlcv_timeframe_coverage(
        [
            {
                "ohlcv_timeframes": {
                    "available": ["1h", "1m", "5m", "15m", "30m"],
                    "materialized": ["1h", "5m"],
                    "not_materialized": {"15m": "missing_contiguous_bars"},
                }
            }
        ]
    ) == {
        "available": ["1h", "1m", "5m", "15m", "30m"],
        "materialized": ["1h", "5m"],
        "missing_optional": [],
        "not_materialized": {"15m": "missing_contiguous_bars"},
    }


def test_merged_import_trace_rejects_non_string_keys_before_merge() -> None:
    with pytest.raises(ValueError, match="import_trace entry keys must be strings"):
        archive_importer._merged_import_trace(
            [
                {
                    123: "x",
                    "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                    "exchange": "binance",
                    "market": "futures",
                }
            ]
        )


def test_merged_import_trace_rejects_padded_keys_before_merge() -> None:
    with pytest.raises(ValueError, match="import_trace entry keys must be canonical"):
        archive_importer._merged_import_trace(
            [
                {
                    " scope ": archive_importer.PHASE1_IMPORTER_SCOPE,
                    "exchange": "binance",
                    "market": "futures",
                }
            ]
        )


@pytest.mark.parametrize(
    "series_key",
    [
        "../raw-market/binance/futures/ohlcv/BTCUSDT/1h",
        "/raw-market/binance/futures/ohlcv/BTCUSDT/1h",
        "raw-market/binance/futures//BTCUSDT/1h",
        "raw-market\\binance\\futures\\ohlcv\\BTCUSDT\\1h",
        "raw-market/binance/futures/ohlcv/BTCUSDT/\n1h",
    ],
)
def test_merged_import_trace_rejects_malformed_series_key_path_shapes(series_key: str) -> None:
    with pytest.raises(ValueError, match=r"import_trace\.series_keys\[0\] must be a valid archive series key"):
        archive_importer._merged_import_trace(
            [
                {
                    "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                    "exchange": "binance",
                    "market": "futures",
                    "series_keys": [series_key],
                }
            ]
        )


@pytest.mark.parametrize(
    "manifest_path",
    [
        "raw-market/binance/futures/ohlcv/BTCUSDT/1h//2026/01.manifest.json",
        "raw-market\\binance\\futures\\ohlcv\\BTCUSDT\\1h\\2026\\01.manifest.json",
        "raw-market/binance/futures/ohlcv/BTCUSDT/1h/2026/\n01.manifest.json",
    ],
)
def test_merged_import_trace_rejects_malformed_manifest_path_shapes(manifest_path: str) -> None:
    with pytest.raises(ValueError, match=r"import_trace\.manifest_paths\[0\] must be a valid manifest path"):
        archive_importer._merged_import_trace(
            [
                {
                    "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                    "exchange": "binance",
                    "market": "futures",
                    "manifest_paths": [manifest_path],
                }
            ]
        )


@pytest.mark.parametrize(
    "manifest_path",
    [
        "raw-market/binance/futures/ohlcv/BTCUSDT/1h/2026/01.manifest.json",
        "/tmp/archive/raw-market/binance/futures/ohlcv/BTCUSDT/1h/2026/01.manifest.json",
    ],
)
def test_merged_import_trace_preserves_valid_manifest_paths(manifest_path: str) -> None:
    assert archive_importer._merged_import_trace(
        [
            {
                "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                "exchange": "binance",
                "market": "futures",
                "manifest_paths": [manifest_path],
            }
        ]
    )["manifest_paths"] == [manifest_path]


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _archive_phase1_symbol_history(
    archive_root: Path,
    *,
    symbol: str,
    start: datetime | None = None,
    total_hours: int = 60 * 24,
    ohlcv_row_format: str = "dict",
    open_interest_field: str = "sumOpenInterest",
    open_interest_base: float = 10_000.0,
    open_interest_step: float = 10.0,
    extra_ohlcv_timeframes: tuple[str, ...] = (),
    symbol_metadata: dict[str, object] | None = None,
    ohlcv_symbol_metadata: dict[str, object] | None = None,
    funding_symbol_metadata: dict[str, object] | None = None,
    open_interest_symbol_metadata: dict[str, object] | None = None,
) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC) if start is None else start
    hourly_rows: list[dict[str, str | int] | list[str | int]] = []
    funding_rows: list[dict[str, str | int]] = []
    open_interest_rows: list[dict[str, str | int]] = []
    ohlcv_metadata = symbol_metadata if ohlcv_symbol_metadata is None else ohlcv_symbol_metadata
    funding_metadata = symbol_metadata if funding_symbol_metadata is None else funding_symbol_metadata
    open_interest_metadata = symbol_metadata if open_interest_symbol_metadata is None else open_interest_symbol_metadata

    for index in range(total_hours):
        observed_at = start + timedelta(hours=index)
        close = 50_000.0 + (index * 10.0)
        volume = 1_000.0 + index
        open_time_ms = _timestamp_ms(observed_at)
        open_value = f"{close - 5.0:.6f}"
        high_value = f"{close + 20.0:.6f}"
        low_value = f"{close - 20.0:.6f}"
        close_value = f"{close:.6f}"
        volume_value = f"{volume:.6f}"
        quote_volume_value = f"{close * volume:.6f}"
        if ohlcv_row_format == "binance-array":
            hourly_rows.append(
                [
                    open_time_ms,
                    open_value,
                    high_value,
                    low_value,
                    close_value,
                    volume_value,
                    open_time_ms + int(timedelta(hours=1).total_seconds() * 1000) - 1,
                    quote_volume_value,
                    1234,
                    f"{volume * 0.55:.6f}",
                    f"{close * volume * 0.55:.6f}",
                    "0",
                ]
            )
        else:
            hourly_rows.append(
                {
                    "open_time": open_time_ms,
                    "open": open_value,
                    "high": high_value,
                    "low": low_value,
                    "close": close_value,
                    "volume": volume_value,
                    "quote_asset_volume": quote_volume_value,
                }
            )
        open_interest_rows.append(
            {
                "timestamp": _timestamp_ms(observed_at),
                open_interest_field: f"{open_interest_base + (index * open_interest_step):.6f}",
            }
        )
        if index % 8 == 0:
            funding_rows.append(
                {
                    "fundingTime": _timestamp_ms(observed_at),
                    "fundingRate": f"{0.0001 + ((index // 8) * 0.000001):.8f}",
                }
            )

    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol=symbol,
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": symbol, "interval": "1h", "rows": hourly_rows},
        symbol_metadata=ohlcv_metadata,
    )
    intervals_by_timeframe = {"30m": 2, "15m": 4, "5m": 12, "1m": 60}
    for timeframe in extra_ohlcv_timeframes:
        intervals_per_hour = intervals_by_timeframe[timeframe]
        interval = timedelta(hours=1) / intervals_per_hour
        intraday_rows: list[dict[str, str | int]] = []
        for index in range(total_hours * intervals_per_hour):
            observed_at = start + (interval * index)
            close = 50_000.0 + (index * (10.0 / intervals_per_hour))
            volume = 500.0 + index
            open_time_ms = _timestamp_ms(observed_at)
            intraday_rows.append(
                {
                    "open_time": open_time_ms,
                    "open": f"{close - 2.5:.6f}",
                    "high": f"{close + 10.0:.6f}",
                    "low": f"{close - 10.0:.6f}",
                    "close": f"{close:.6f}",
                    "volume": f"{volume:.6f}",
                    "quote_asset_volume": f"{close * volume:.6f}",
                }
            )
        archive_raw_market_payload(
            archive_root=archive_root,
            exchange="binance",
            market="futures",
            dataset="ohlcv",
            symbol=symbol,
            timeframe=timeframe,
            coverage_start=start.isoformat().replace("+00:00", "Z"),
            coverage_end=(start + timedelta(hours=total_hours)).isoformat().replace("+00:00", "Z"),
            fetched_at="2026-04-01T07:30:30Z",
            endpoint="/fapi/v1/klines",
            payload={"symbol": symbol, "interval": timeframe, "rows": intraday_rows},
            symbol_metadata=ohlcv_metadata,
        )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:31:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=funding_rows,
        symbol_metadata=funding_metadata,
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="open_interest",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(hourly_rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:32:00Z",
        endpoint="/futures/data/openInterestHist",
        payload=open_interest_rows,
        symbol_metadata=open_interest_metadata,
    )


def _rewrite_raw_market_manifest_fields(manifest_path: Path, **updates: object) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(updates)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_raw_market_payload(manifest_path: Path, rows: list[dict[str, object]]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_path = Path(manifest["file"]["path"])
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        payload["rows"] = rows
    else:
        payload = rows
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_bytes = data_path.read_bytes()
    manifest["file"]["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    manifest["file"]["size_bytes"] = len(raw_bytes)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _archive_mark_price_history(
    archive_root: Path,
    *,
    symbol: str,
    rows: list[dict[str, object]],
    start: datetime,
    end: datetime,
) -> None:
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="mark_price",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=end.isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:32:30Z",
        endpoint="/fapi/v1/premiumIndex",
        payload=rows,
    )


def test_raw_market_data_quality_report_locates_gaps_and_l2_coverage(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for index in (0, 1, 3):
        observed_at = start + timedelta(hours=index)
        rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": "100.0",
                "high": "110.0",
                "low": "90.0",
                "close": "105.0",
                "volume": "1000.0",
                "quote_asset_volume": "105000.0",
            }
        )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": "BTCUSDT", "interval": "1h", "rows": rows},
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="order_book",
        symbol="BTCUSDT",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:31:00Z",
        endpoint="/fapi/v1/depth",
        payload=[{"timestamp": _timestamp_ms(start), "bids": [["100", "1"]], "asks": [["101", "1"]]}],
    )

    report = build_raw_market_data_quality_report(
        archive_root,
        expected_intervals={"ohlcv:1h": timedelta(hours=1), "order-book": timedelta(hours=1)},
        required_l2_coverage=0.99,
    )

    assert report["schema_version"] == "raw_market_data_quality_report.v1"
    assert report["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert "raw_market_missing_intervals" in report["promotion_gate"]["reasons"]
    assert "l2_coverage_below_threshold" in report["promotion_gate"]["reasons"]
    assert report["summary"]["series_count"] == 2
    assert report["summary"]["series_with_missing_intervals"] == 1
    assert report["summary"]["l2_coverage_met"] is False
    ohlcv = report["series"]["binance:futures:ohlcv:BTCUSDT:1h"]
    assert ohlcv["coverage_ratio"] == pytest.approx(0.75)
    assert ohlcv["missing_intervals"] == [
        {
            "start": "2024-01-01T02:00:00Z",
            "end": "2024-01-01T03:00:00Z",
            "missing_records": 1,
        }
    ]
    l2 = report["l2_tick_coverage"]
    assert l2["required_coverage"] == pytest.approx(0.99)
    assert l2["coverage_ratio"] == pytest.approx(0.25)
    assert l2["missing_by_symbol_timeframe"][0]["symbol"] == "BTCUSDT"


def test_phase1_dataset_root_manifest_embeds_data_quality_report(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=60 * 24)
    materials = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))
    bundle_dirs = [write_phase1_dataset_bundle(materials[-1], dataset_root)]

    manifest_path = write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=["BTCUSDT"],
        materials=[materials[-1]],
        bundle_dirs=bundle_dirs,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_quality = manifest["data_quality_report"]
    assert data_quality["schema_version"] == "raw_market_data_quality_report.v1"
    assert data_quality["summary"]["series_count"] >= 3
    assert data_quality["promotion_gate"]["decision"] == "reject_for_live_promotion"
    assert data_quality["promotion_gate"]["reasons"] == ["l2_coverage_below_threshold"]


def test_build_phase1_dataset_bundle_materials_returns_dataset_ready_bundle_and_writes_dataset_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    total_hours = 60 * 24
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    latest = materials[-1]
    assert latest.run_id.startswith("phase1-import-")
    assert latest.metadata["timestamp"] == latest.timestamp.isoformat().replace("+00:00", "Z")
    assert latest.metadata["source"]["series_keys"] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
    ]

    market_symbol = latest.market_context["symbols"]["BTCUSDT"]
    assert latest.market_context["as_of"] == latest.metadata["timestamp"]
    assert latest.market_context["schema_version"] == "imported_market_context.v1"
    assert market_symbol["sector"] == "majors"
    assert market_symbol["liquidity_tier"] == "top"
    assert market_symbol["1h"]["close"] == pytest.approx(64_390.0)
    assert market_symbol["1h"]["volume_usdt_24h"] > 0.0
    assert market_symbol["4h"]["return_pct_3d"] > 0.0
    assert market_symbol["daily"]["return_pct_7d"] > 0.0

    assert latest.derivatives_snapshot["as_of"] == latest.metadata["timestamp"]
    assert latest.derivatives_snapshot["schema_version"] == "imported_derivatives_snapshot.v1"
    latest_close = 50_000.0 + ((total_hours - 1) * 10.0)
    close_24h_ago = 50_000.0 + ((total_hours - 25) * 10.0)
    latest_open_interest = 10_000.0 + ((total_hours - 1) * 10.0)
    open_interest_24h_ago = 10_000.0 + ((total_hours - 25) * 10.0)
    latest_funding = 0.0001 + (((total_hours - 1) // 8) * 0.000001)
    derivative = latest.derivatives_snapshot["rows"][0]
    assert derivative["symbol"] == "BTCUSDT"
    assert derivative["funding_rate"] == pytest.approx(latest_funding)
    assert derivative["funding_timestamp"] == "2024-02-29T16:00:00Z"
    assert derivative["funding_age_seconds"] == 7 * 60 * 60
    assert derivative["open_interest_usdt"] == pytest.approx(latest_open_interest * latest_close)
    assert derivative["open_interest_timestamp"] == "2024-02-29T23:00:00Z"
    assert derivative["open_interest_age_seconds"] == 0
    assert derivative["open_interest_change_24h_pct"] == pytest.approx(
        (latest_open_interest / open_interest_24h_ago) - 1.0,
        rel=1e-6,
    )
    assert derivative["mark_price_change_24h_pct"] == pytest.approx((latest_close / close_24h_ago) - 1.0, rel=1e-6)
    assert derivative["taker_buy_sell_ratio"] == 1.0
    assert derivative["basis_bps"] == 0.0

    bundle_dir = write_phase1_dataset_bundle(latest, dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert bundle_dir == dataset_root / "2024-02-29T23-00-00Z__phase1-import-2024-02-29T23-00-00Z"
    assert len(rows) == 1
    assert rows[0].timestamp == latest.timestamp
    assert rows[0].market["symbols"]["BTCUSDT"]["1h"]["close"] == pytest.approx(64_390.0)
    assert rows[0].derivatives[0]["open_interest_usdt"] == pytest.approx(latest_open_interest * latest_close)


def test_build_phase1_dataset_bundle_materials_aligns_futures_context_without_lookahead(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    total_hours = 60 * 24
    latest_timestamp = start + timedelta(hours=total_hours - 1)
    future_timestamp = latest_timestamp + timedelta(hours=1)
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", start=start, total_hours=total_hours)
    _archive_mark_price_history(
        archive_root,
        symbol="BTCUSDT",
        start=latest_timestamp,
        end=future_timestamp,
        rows=[
            {"timestamp": _timestamp_ms(latest_timestamp), "markPrice": "64395.5"},
            {"timestamp": _timestamp_ms(future_timestamp), "markPrice": "99999.9"},
        ],
    )
    funding_manifest = next((archive_root / "raw-market").glob("**/funding/BTCUSDT/*.manifest.json"))
    funding_payload = json.loads(Path(json.loads(funding_manifest.read_text(encoding="utf-8"))["file"]["path"]).read_text())
    funding_payload.append({"fundingTime": _timestamp_ms(future_timestamp), "fundingRate": "0.99999999"})
    _rewrite_raw_market_payload(funding_manifest, funding_payload)
    oi_manifest = next((archive_root / "raw-market").glob("**/open-interest/BTCUSDT/*.manifest.json"))
    oi_payload = json.loads(Path(json.loads(oi_manifest.read_text(encoding="utf-8"))["file"]["path"]).read_text())
    oi_payload.append({"timestamp": _timestamp_ms(future_timestamp), "sumOpenInterest": "999999999.0"})
    _rewrite_raw_market_payload(oi_manifest, oi_payload)

    materials = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))
    latest = materials[-1]

    futures_context = latest.market_context["symbols"]["BTCUSDT"]["futures_context"]
    derivative = latest.derivatives_snapshot["rows"][0]
    assert futures_context["mark_price"] == pytest.approx(64395.5)
    assert futures_context["mark_price_timestamp"] == latest_timestamp.isoformat().replace("+00:00", "Z")
    assert futures_context["mark_price_age_seconds"] == 0
    assert derivative["mark_price"] == pytest.approx(64395.5)
    assert derivative["funding_rate"] != pytest.approx(0.99999999)
    assert derivative["open_interest_usdt"] != pytest.approx(999999999.0 * derivative["mark_price"])
    assert latest.metadata["source"]["futures_context"]["materialized"]["mark_price"] >= 1


def test_build_phase1_dataset_bundle_materials_reports_stale_and_missing_futures_context(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    materials = build_phase1_dataset_bundle_materials(
        load_phase1_raw_market_imports(archive_root),
        funding_max_age=timedelta(hours=1),
    )
    latest = materials[-1]

    futures_context = latest.market_context["symbols"]["BTCUSDT"]["futures_context"]
    coverage = latest.metadata["source"]["futures_context"]
    assert futures_context["funding_status"] == "stale"
    assert "funding_rate" not in futures_context
    assert futures_context["mark_price_status"] == "missing"
    assert coverage["stale"]["funding"] >= 1
    assert coverage["missing"]["mark_price"] >= 1


def test_build_phase1_dataset_bundle_materials_defaults_missing_open_interest_change_for_regime_compatibility(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(60 * 24):
        observed_at = start + timedelta(hours=index)
        close = 50_000.0 + (index * 10.0)
        rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": f"{close - 5.0:.6f}",
                "high": f"{close + 20.0:.6f}",
                "low": f"{close - 20.0:.6f}",
                "close": f"{close:.6f}",
                "volume": "1000.0",
                "quote_asset_volume": f"{close * 1000.0:.6f}",
            }
        )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": "BTCUSDT", "interval": "1h", "rows": rows},
    )

    materials = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))
    latest = materials[-1]

    futures_context = latest.market_context["symbols"]["BTCUSDT"]["futures_context"]
    derivative = latest.derivatives_snapshot["rows"][0]
    assert futures_context["open_interest_status"] == "missing"
    assert "open_interest_usdt" not in derivative
    assert derivative["open_interest_change_24h_pct"] == pytest.approx(0.0)
    assert derivative["mark_price_change_24h_pct"] > 0.0
    assert derivative["taker_buy_sell_ratio"] == pytest.approx(1.0)
    assert derivative["basis_bps"] == pytest.approx(0.0)


def test_build_phase1_dataset_bundle_materials_allows_ohlcv_only_archive_with_context_missing(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(60 * 24):
        observed_at = start + timedelta(hours=index)
        close = 50_000.0 + (index * 10.0)
        rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": f"{close - 5.0:.6f}",
                "high": f"{close + 20.0:.6f}",
                "low": f"{close - 20.0:.6f}",
                "close": f"{close:.6f}",
                "volume": "1000.0",
                "quote_asset_volume": f"{close * 1000.0:.6f}",
            }
        )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=len(rows))).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": "BTCUSDT", "interval": "1h", "rows": rows},
    )

    materials = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))

    assert materials
    futures_context = materials[-1].market_context["symbols"]["BTCUSDT"]["futures_context"]
    assert futures_context["mark_price_status"] == "missing"
    assert futures_context["funding_status"] == "missing"
    assert futures_context["open_interest_status"] == "missing"
    assert materials[-1].metadata["source"]["futures_context"]["available"] is False


def test_build_phase1_dataset_bundle_materials_includes_available_intraday_trigger_timeframes(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        extra_ohlcv_timeframes=("30m", "15m"),
    )

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    market_symbol = materials[-1].market_context["symbols"]["BTCUSDT"]
    assert market_symbol["30m"]["close"] == pytest.approx(market_symbol["1h"]["close"])
    assert market_symbol["30m"]["high"] >= market_symbol["30m"]["close"]
    assert market_symbol["30m"]["low"] <= market_symbol["30m"]["close"]
    assert market_symbol["30m"]["return_pct_8h"] > 0.0
    assert market_symbol["15m"]["close"] == pytest.approx(market_symbol["1h"]["close"])
    assert market_symbol["15m"]["high"] >= market_symbol["15m"]["close"]
    assert market_symbol["15m"]["low"] <= market_symbol["15m"]["close"]
    assert market_symbol["15m"]["return_pct_4h"] > 0.0
    assert "binance:futures:ohlcv:BTCUSDT:30m" in materials[-1].metadata["source"]["series_keys"]
    assert "binance:futures:ohlcv:BTCUSDT:15m" in materials[-1].metadata["source"]["series_keys"]


def test_build_phase1_dataset_bundle_materials_materializes_contiguous_5m_ohlcv_payload(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        extra_ohlcv_timeframes=("5m",),
    )

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    market_symbol = materials[-1].market_context["symbols"]["BTCUSDT"]
    assert market_symbol["5m"]["close"] == pytest.approx(market_symbol["1h"]["close"])
    assert market_symbol["5m"]["high"] >= market_symbol["5m"]["close"]
    assert market_symbol["5m"]["low"] <= market_symbol["5m"]["close"]
    assert market_symbol["5m"]["return_pct_1h"] > 0.0
    assert market_symbol["5m"]["volume_usdt_24h"] > 0.0
    assert "binance:futures:ohlcv:BTCUSDT:5m" in materials[-1].metadata["source"]["series_keys"]
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["available"] == ["1h", "5m"]
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["materialized"] == ["1h", "5m"]
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["missing_optional"] == ["1m", "15m", "30m"]


def test_build_phase1_dataset_bundle_materials_skips_non_contiguous_5m_payload_and_reports_coverage(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        extra_ohlcv_timeframes=("5m",),
    )
    manifest_path = next((archive_root / "raw-market").glob("**/ohlcv/BTCUSDT/5m/*.manifest.json"))
    data_path = Path(json.loads(manifest_path.read_text(encoding="utf-8"))["file"]["path"])
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["rows"] = [
        row
        for row in payload["rows"]
        if row["open_time"] != _timestamp_ms(datetime(2024, 2, 29, 22, 55, tzinfo=UTC))
    ]
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_bytes = data_path.read_bytes()

    manifest["file"]["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    manifest["file"]["size_bytes"] = len(raw_bytes)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    market_symbol = materials[-1].market_context["symbols"]["BTCUSDT"]
    assert "5m" not in market_symbol
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["available"] == ["1h", "5m"]
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["materialized"] == ["1h"]
    assert materials[-1].metadata["source"]["ohlcv_timeframes"]["not_materialized"] == {
        "5m": "missing_contiguous_bars"
    }


def test_import_phase1_archive_dataset_root_manifest_exposes_finer_ohlcv_coverage(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        extra_ohlcv_timeframes=("5m",),
    )

    import_phase1_archive_dataset_root(archive_root, dataset_root)

    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    assert manifest["coverage"]["ohlcv_timeframes"] == {
        "available": ["1h", "5m"],
        "materialized": ["1h", "5m"],
        "missing_optional": ["1m", "15m", "30m"],
        "not_materialized": {},
    }



def test_materialize_phase1_evidence_windows_prefilters_manifests_outside_requested_windows(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        start=datetime(2022, 1, 1, tzinfo=UTC),
        total_hours=70,
        extra_ohlcv_timeframes=("1m",),
    )
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        total_hours=50 * 24,
        extra_ohlcv_timeframes=("1m",),
    )

    report = materialize_phase1_evidence_windows(
        archive_root / "raw-market" / "binance" / "futures",
        output_root,
        symbols=("BTCUSDT",),
        windows_days=(30,),
    )

    assert report["windows"]["30d"]["status"] == "materialized"
    selected_paths = report["selected_manifest_paths"]
    assert selected_paths
    assert all("2022-" not in path for path in selected_paths)
    assert any("2024-" in path for path in selected_paths)

def test_materialize_phase1_evidence_windows_selects_intraday_layers_and_reports_missing_execution_evidence(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        total_hours=50 * 24,
        extra_ohlcv_timeframes=("1m", "5m", "15m"),
    )

    report = materialize_phase1_evidence_windows(
        archive_root / "raw-market" / "binance" / "futures",
        output_root,
        symbols=("BTCUSDT",),
        windows_days=(30, 90, 180),
    )

    assert report["windows"]["30d"]["status"] == "materialized"
    assert report["windows"]["90d"]["status"] == "materialized"
    assert report["windows"]["180d"]["status"] == "materialized"
    for window_name in ("30d", "90d", "180d"):
        window = report["windows"][window_name]
        assert Path(window["dataset_root"]).is_dir()
        assert window["coverage"]["ohlcv_timeframes"]["available"] == ["1h", "1m", "5m", "15m"]
        assert window["coverage"]["ohlcv_timeframes"]["materialized"] == ["1h", "1m", "5m", "15m"]
        assert window["coverage"]["ohlcv_timeframes"]["missing_optional"] == ["30m"]
        assert window["coverage"]["execution_evidence"]["available"] is False
        assert window["coverage"]["execution_evidence"]["materialized"] == {"order_book": 0, "trades": 0}
        assert window["evidence_gap"]["missing_execution_evidence"] == ["order_book", "trades"]


def test_materialize_phase1_evidence_windows_rejects_non_string_manifest_symbol(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=50 * 24)
    manifest_path = next((archive_root / "raw-market").rglob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbol"] = 123
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest field 'symbol' must be a string"):
        materialize_phase1_evidence_windows(
            archive_root / "raw-market" / "binance" / "futures",
            output_root,
            symbols=("BTCUSDT",),
            windows_days=(30,),
        )


@pytest.mark.parametrize("invalid_window_day", [True, "30", 30.5, 0, -1])
def test_materialize_phase1_evidence_windows_rejects_invalid_window_days_before_output_side_effects(
    tmp_path: Path,
    invalid_window_day: object,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=50 * 24)

    with pytest.raises(ValueError, match="windows_days must contain positive integer window days"):
        materialize_phase1_evidence_windows(
            archive_root / "raw-market" / "binance" / "futures",
            output_root,
            symbols=("BTCUSDT",),
            windows_days=(invalid_window_day,),
        )

    assert not output_root.exists()
    assert not (output_root / "coverage_report.json").exists()


def test_materialize_phase1_evidence_windows_rejects_duplicate_window_days_before_output_side_effects(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=50 * 24)

    with pytest.raises(ValueError, match="windows_days must not contain duplicate window days"):
        materialize_phase1_evidence_windows(
            archive_root / "raw-market" / "binance" / "futures",
            output_root,
            symbols=("BTCUSDT",),
            windows_days=(30, 30),
        )

    assert not output_root.exists()
    assert not (output_root / "coverage_report.json").exists()


@pytest.mark.parametrize("invalid_symbol", [123, " BTCUSDT", "btcusdt", "BTC/USDT", "BTCUSDT "])
def test_materialize_phase1_evidence_windows_rejects_invalid_filter_symbols_before_output_side_effects(
    tmp_path: Path,
    invalid_symbol: object,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=50 * 24)

    with pytest.raises(ValueError, match="symbols must contain canonical uppercase exchange symbols"):
        materialize_phase1_evidence_windows(
            archive_root / "raw-market" / "binance" / "futures",
            output_root,
            symbols=(invalid_symbol,),  # type: ignore[arg-type]
            windows_days=(30,),
        )

    assert not output_root.exists()
    assert not (output_root / "coverage_report.json").exists()


def test_materialize_phase1_evidence_windows_rejects_duplicate_filter_symbols_before_output_side_effects(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=50 * 24)

    with pytest.raises(ValueError, match="symbols must not contain duplicate symbols"):
        materialize_phase1_evidence_windows(
            archive_root / "raw-market" / "binance" / "futures",
            output_root,
            symbols=("BTCUSDT", "BTCUSDT"),
            windows_days=(30,),
        )

    assert not output_root.exists()
    assert not (output_root / "coverage_report.json").exists()


def test_materialize_phase1_evidence_windows_streams_windows_and_reports_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive_root = tmp_path / "archive"
    output_root = tmp_path / "materialized"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        total_hours=100 * 24,
    )
    real_manifest = next((archive_root / "raw-market").rglob("*.manifest.json"))
    events: list[tuple[str, str]] = []

    class FakeSeries:
        symbol = "BTCUSDT"

    class FakeMaterial:
        def __init__(self, timestamp: datetime) -> None:
            self.timestamp = timestamp
            self.market_context = {"symbols": {"BTCUSDT": {}}}

    def fake_selected_manifest_paths(
        _archive_root: Path,
        *,
        symbols: tuple[str, ...] | None = None,
        start_timestamp: datetime | None = None,
        end_timestamp: datetime | None = None,
    ) -> tuple[Path, ...]:
        if start_timestamp is None or end_timestamp is None:
            events.append(("select", "initial"))
            return (real_manifest,)
        days = int((end_timestamp - start_timestamp).total_seconds() // 86400) - 50
        events.append(("select", f"{days}d"))
        return (Path(f"{days}d.manifest.json"),)

    def fake_load_phase1_raw_market_imports_from_manifest_paths(
        manifest_paths: tuple[Path, ...],
        *,
        start_timestamp: datetime | None = None,
        end_timestamp: datetime | None = None,
    ) -> tuple[FakeSeries, ...]:
        assert start_timestamp is not None
        assert end_timestamp is not None
        events.append(("load", Path(manifest_paths[0]).name.removesuffix(".manifest.json")))
        return (FakeSeries(),)

    def fake_build_phase1_dataset_bundle_materials(
        imported_series: tuple[FakeSeries, ...],
        *,
        start_timestamp: datetime | None = None,
        end_timestamp: datetime | None = None,
    ) -> tuple[FakeMaterial, ...]:
        assert start_timestamp is not None
        assert end_timestamp is not None
        days = int((end_timestamp - start_timestamp).total_seconds() // 86400)
        events.append(("build", f"{days}d"))
        return (FakeMaterial(end_timestamp - timedelta(hours=1)),)

    def fake_materialize_dataset_root(
        *,
        archive_root: Path,
        dataset_root: Path,
        materials: tuple[FakeMaterial, ...],
    ) -> dict[str, object]:
        if dataset_root.name == "30d":
            assert not any(event == ("select", "90d") for event in events)
        dataset_root.mkdir(parents=True)
        (dataset_root / "manifest.json").write_text("{}", encoding="utf-8")
        events.append(("write", dataset_root.name))
        return {
            "dataset_root": str(dataset_root),
            "snapshot_count": len(materials),
            "start_timestamp": materials[0].timestamp.isoformat().replace("+00:00", "Z"),
            "end_timestamp": materials[-1].timestamp.isoformat().replace("+00:00", "Z"),
            "coverage": {"execution_evidence": {"materialized": {"order_book": 0, "trades": 0}}},
        }

    monkeypatch.setattr(
        "trading_system.app.backtest.archive.materialization._selected_manifest_paths",
        fake_selected_manifest_paths,
    )
    monkeypatch.setattr(
        "trading_system.app.backtest.archive.materialization.load_phase1_raw_market_imports_from_manifest_paths",
        fake_load_phase1_raw_market_imports_from_manifest_paths,
    )
    monkeypatch.setattr(
        "trading_system.app.backtest.archive.materialization.build_phase1_dataset_bundle_materials",
        fake_build_phase1_dataset_bundle_materials,
    )
    monkeypatch.setattr(
        "trading_system.app.backtest.archive.materialization._materialize_dataset_root",
        fake_materialize_dataset_root,
    )

    report = materialize_phase1_evidence_windows(
        archive_root,
        output_root,
        symbols=("BTCUSDT",),
        windows_days=(30, 90),
    )

    assert events == [
        ("select", "initial"),
        ("select", "30d"),
        ("load", "30d"),
        ("build", "30d"),
        ("write", "30d"),
        ("select", "90d"),
        ("load", "90d"),
        ("build", "90d"),
        ("write", "90d"),
    ]
    assert report["windows"]["30d"]["status"] == "materialized"
    assert json.loads((output_root / "coverage_report.json").read_text(encoding="utf-8"))["windows"]["30d"]["status"] == "materialized"
    progress = capsys.readouterr().err
    assert "selected manifests" in progress
    assert "window 30d start" in progress
    assert "window 30d end" in progress
    assert "imported series count=1" in progress
    assert "snapshot count=1" in progress
    assert "elapsed seconds=" in progress


def test_materialized_intraday_imported_rows_include_next_bar_open_for_evidence_backed_fill(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        extra_ohlcv_timeframes=("1m", "5m", "15m"),
    )

    import_phase1_archive_dataset_root(archive_root, dataset_root)

    row = load_historical_dataset(dataset_root)[-2]
    symbol_payload = row.market["symbols"]["BTCUSDT"]
    assert symbol_payload["1m"]["next_bar"]["open"] != pytest.approx(symbol_payload["15m"]["close"])
    assert symbol_payload["1m"]["next_bar"]["timestamp"] > row.timestamp.isoformat().replace("+00:00", "Z")


def test_build_phase1_dataset_bundle_materials_materializes_fresh_order_book_execution_evidence(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    signal_time = datetime(2024, 2, 29, 23, tzinfo=UTC)
    evidence_time = signal_time + timedelta(seconds=5)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="order_book",
        symbol="BTCUSDT",
        coverage_start=signal_time.isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="local://execution/order_book",
        payload={
            "rows": [
                {
                    "timestamp": _timestamp_ms(evidence_time),
                    "symbol": "BTCUSDT",
                    "bid": 64389.50,
                    "ask": 64390.50,
                    "bid_size": 3.25,
                    "ask_size": 2.75,
                }
            ]
        },
    )

    imported = load_phase1_raw_market_imports(archive_root)

    latest = build_phase1_dataset_bundle_materials(imported)[-1]

    execution = latest.market_context["symbols"]["BTCUSDT"]["execution"]
    assert execution["order_book"] == {
        "timestamp": "2024-02-29T23:00:05Z",
        "symbol": "BTCUSDT",
        "bid": pytest.approx(64389.5),
        "ask": pytest.approx(64390.5),
        "bid_size": pytest.approx(3.25),
        "ask_size": pytest.approx(2.75),
    }
    assert latest.metadata["source"]["execution_evidence"]["materialized"]["order_book"] == 1
    assert latest.metadata["source"]["execution_evidence"]["missing"]["order_book"] == 0


def test_build_phase1_dataset_bundle_materials_materializes_numeric_execution_evidence(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    signal_time = datetime(2024, 2, 29, 23, tzinfo=UTC)
    evidence_time = signal_time + timedelta(seconds=5)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="order_book",
        symbol="BTCUSDT",
        coverage_start=signal_time.isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="/fapi/v1/depth",
        payload={
            "rows": [
                {
                    "timestamp": _timestamp_ms(evidence_time),
                    "symbol": "BTCUSDT",
                    "bid": 64389.50,
                    "ask": 64390.50,
                    "bid_size": 3.25,
                    "ask_size": 2.75,
                }
            ]
        },
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="trades",
        symbol="BTCUSDT",
        coverage_start=signal_time.isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:01Z",
        endpoint="/fapi/v1/aggTrades",
        payload={
            "rows": [
                {
                    "timestamp": _timestamp_ms(signal_time + timedelta(seconds=2)),
                    "symbol": "BTCUSDT",
                    "price": 64391.00,
                    "quantity": 0.20,
                    "side": "buy",
                }
            ]
        },
    )

    latest = build_phase1_dataset_bundle_materials(
        load_phase1_raw_market_imports(archive_root),
        execution_evidence_max_staleness=timedelta(minutes=5),
    )[-1]

    execution = latest.market_context["symbols"]["BTCUSDT"]["execution"]
    assert execution["order_book"]["bid"] == pytest.approx(64389.5)
    assert execution["order_book"]["ask"] == pytest.approx(64390.5)
    assert execution["order_book"]["bid_size"] == pytest.approx(3.25)
    assert execution["order_book"]["ask_size"] == pytest.approx(2.75)
    assert execution["trades"] == [
        {
            "timestamp": "2024-02-29T23:00:02Z",
            "symbol": "BTCUSDT",
            "price": pytest.approx(64391.0),
            "quantity": pytest.approx(0.2),
            "side": "buy",
        }
    ]


def test_build_phase1_dataset_bundle_materials_materializes_only_fresh_trades_at_or_after_signal_time(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    signal_time = datetime(2024, 2, 29, 23, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="trades",
        symbol="BTCUSDT",
        coverage_start=(signal_time - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="local://execution/trades",
        payload={
            "rows": [
                {
                    "timestamp": _timestamp_ms(signal_time - timedelta(seconds=1)),
                    "symbol": "BTCUSDT",
                    "price": 64380.00,
                    "quantity": 0.10,
                    "side": "sell",
                },
                {
                    "timestamp": _timestamp_ms(signal_time + timedelta(seconds=2)),
                    "symbol": "BTCUSDT",
                    "price": 64391.00,
                    "quantity": 0.20,
                    "side": "buy",
                },
                {
                    "timestamp": _timestamp_ms(signal_time + timedelta(minutes=4)),
                    "symbol": "BTCUSDT",
                    "price": 64392.00,
                    "quantity": 0.30,
                    "side": "buy",
                },
                {
                    "timestamp": _timestamp_ms(signal_time + timedelta(minutes=6)),
                    "symbol": "BTCUSDT",
                    "price": 64395.00,
                    "quantity": 0.40,
                    "side": "buy",
                },
            ]
        },
    )

    imported = load_phase1_raw_market_imports(archive_root)

    latest = build_phase1_dataset_bundle_materials(
        imported,
        execution_evidence_max_staleness=timedelta(minutes=5),
    )[-1]

    assert latest.market_context["symbols"]["BTCUSDT"]["execution"]["trades"] == [
        {
            "timestamp": "2024-02-29T23:00:02Z",
            "symbol": "BTCUSDT",
            "price": pytest.approx(64391.0),
            "quantity": pytest.approx(0.2),
            "side": "buy",
        },
        {
            "timestamp": "2024-02-29T23:04:00Z",
            "symbol": "BTCUSDT",
            "price": pytest.approx(64392.0),
            "quantity": pytest.approx(0.3),
            "side": "buy",
        },
    ]
    assert latest.metadata["source"]["execution_evidence"]["materialized"]["trades"] == 1


def test_build_phase1_dataset_bundle_materials_materializes_binance_agg_trades_rows(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    signal_time = datetime(2024, 2, 29, 23, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="aggTrades",
        symbol="BTCUSDT",
        coverage_start=signal_time.isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="/fapi/v1/aggTrades",
        payload={
            "rows": [
                {"a": 10, "p": 64391.00, "q": 0.20, "T": _timestamp_ms(signal_time + timedelta(seconds=2)), "m": False},
                {"a": 11, "p": 64392.00, "q": 0.30, "T": _timestamp_ms(signal_time + timedelta(minutes=4)), "m": True},
            ]
        },
    )

    latest = build_phase1_dataset_bundle_materials(
        load_phase1_raw_market_imports(archive_root),
        execution_evidence_max_staleness=timedelta(minutes=5),
    )[-1]

    assert latest.market_context["symbols"]["BTCUSDT"]["execution"]["trades"] == [
        {
            "timestamp": "2024-02-29T23:00:02Z",
            "symbol": "BTCUSDT",
            "price": pytest.approx(64391.0),
            "quantity": pytest.approx(0.2),
            "side": "buy",
        },
        {
            "timestamp": "2024-02-29T23:04:00Z",
            "symbol": "BTCUSDT",
            "price": pytest.approx(64392.0),
            "quantity": pytest.approx(0.3),
            "side": "sell",
        },
    ]
    assert latest.metadata["source"]["execution_evidence"]["materialized"]["trades"] == 1


def test_import_phase1_archive_dataset_root_reports_stale_execution_evidence_without_materializing_it(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    signal_time = datetime(2024, 2, 29, 23, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="order_book",
        symbol="BTCUSDT",
        coverage_start=signal_time.isoformat().replace("+00:00", "Z"),
        coverage_end=(signal_time + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="local://execution/order_book",
        payload={
            "rows": [
                {
                    "timestamp": _timestamp_ms(signal_time + timedelta(minutes=10)),
                    "symbol": "BTCUSDT",
                    "bid": 64389.50,
                    "ask": 64390.50,
                    "bid_size": 3.25,
                    "ask_size": 2.75,
                }
            ]
        },
    )

    import_phase1_archive_dataset_root(archive_root, dataset_root)

    rows = load_historical_dataset(dataset_root)
    latest = rows[-1]
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    assert "execution" not in latest.market["symbols"]["BTCUSDT"]
    assert latest.meta["source"]["execution_evidence"]["stale"]["order_book"] == 1
    assert manifest["coverage"]["execution_evidence"]["stale"]["order_book"] >= 1
    assert manifest["coverage"]["execution_evidence"]["missing"]["order_book"] >= 1


def test_import_phase1_archive_dataset_root_keeps_ohlcv_only_archives_backward_compatible(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)

    rows = load_historical_dataset(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    assert rows
    assert all("execution" not in row.market["symbols"]["BTCUSDT"] for row in rows)
    assert manifest["coverage"]["execution_evidence"]["available"] is False


def test_build_phase1_dataset_bundle_materials_uses_quote_denominated_open_interest_rows_directly(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    total_hours = 60 * 24
    open_interest_base = 114_000_000.0
    open_interest_step = 250_000.0
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        total_hours=total_hours,
        open_interest_field="sumOpenInterestValue",
        open_interest_base=open_interest_base,
        open_interest_step=open_interest_step,
    )

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    latest = materials[-1]
    latest_open_interest_value = open_interest_base + ((total_hours - 1) * open_interest_step)
    open_interest_value_24h_ago = open_interest_base + ((total_hours - 25) * open_interest_step)

    derivative = latest.derivatives_snapshot["rows"][0]
    assert derivative["symbol"] == "BTCUSDT"
    assert derivative["funding_rate"] == pytest.approx(0.000279)
    assert derivative["open_interest_usdt"] == pytest.approx(latest_open_interest_value)
    assert derivative["open_interest_timestamp"] == "2024-02-29T23:00:00Z"
    assert derivative["open_interest_age_seconds"] == 0
    assert derivative["open_interest_change_24h_pct"] == pytest.approx(
        (latest_open_interest_value / open_interest_value_24h_ago) - 1.0,
        rel=1e-6,
    )
    assert derivative["mark_price_change_24h_pct"] == pytest.approx((64_390.0 / 64_150.0) - 1.0, rel=1e-6)
    assert derivative["taker_buy_sell_ratio"] == 1.0
    assert derivative["basis_bps"] == 0.0


def test_build_phase1_dataset_bundle_materials_accepts_binance_array_ohlcv_rows(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", ohlcv_row_format="binance-array")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    latest = materials[-1]
    market_symbol = latest.market_context["symbols"]["BTCUSDT"]
    assert market_symbol["1h"]["close"] == pytest.approx(64_390.0)
    assert market_symbol["1h"]["volume_usdt_24h"] > 0.0
    assert latest.derivatives_snapshot["rows"][0]["symbol"] == "BTCUSDT"


def test_build_phase1_dataset_bundle_materials_can_limit_output_window(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(
        imported,
        start_timestamp=datetime(2024, 2, 29, 20, tzinfo=UTC),
        end_timestamp=datetime(2024, 2, 29, 22, tzinfo=UTC),
    )

    assert [material.timestamp for material in materials] == [
        datetime(2024, 2, 29, 20, tzinfo=UTC),
        datetime(2024, 2, 29, 21, tzinfo=UTC),
    ]


def test_build_phase1_dataset_bundle_materials_reuses_derived_timeframe_bars_across_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT", total_hours=90 * 24)
    imported = load_phase1_raw_market_imports(archive_root)
    original_resample_bars = archive_importer._resample_bars
    resample_calls: list[int] = []

    def counting_resample_bars(hourly_bars, *, hours: int):
        resample_calls.append(hours)
        return original_resample_bars(hourly_bars, hours=hours)

    monkeypatch.setattr(archive_importer, "_resample_bars", counting_resample_bars)

    materials = build_phase1_dataset_bundle_materials(
        imported,
        start_timestamp=datetime(2024, 3, 25, tzinfo=UTC),
        end_timestamp=datetime(2024, 3, 25, 6, tzinfo=UTC),
    )

    assert len(materials) == 6
    assert set(resample_calls).issubset({4, 24})
    assert len(resample_calls) <= 4


def test_build_phase1_dataset_bundle_materials_skips_sparse_ohlcv_rows_mislabeled_as_hourly(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    symbol = "BTCUSDT"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    total_days = 60
    symbol_metadata = {
        "listing_timestamp": "2020-01-01T00:00:00Z",
        "quantity_step": 0.001,
        "price_tick": 0.1,
    }

    sparse_ohlcv_rows: list[dict[str, str | int]] = []
    for index in range(total_days):
        observed_at = start + timedelta(days=index)
        close = 50_000.0 + (index * 100.0)
        base_volume = 1_000.0 + index
        sparse_ohlcv_rows.append(
            {
                "open_time": _timestamp_ms(observed_at),
                "open": f"{close - 5.0:.6f}",
                "high": f"{close + 20.0:.6f}",
                "low": f"{close - 20.0:.6f}",
                "close": f"{close:.6f}",
                "volume": f"{base_volume:.6f}",
                "quote_asset_volume": f"{close * base_volume:.6f}",
            }
        )

    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol=symbol,
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(days=total_days)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:30:00Z",
        endpoint="/fapi/v1/klines",
        payload={"symbol": symbol, "interval": "1h", "rows": sparse_ohlcv_rows},
        symbol_metadata=symbol_metadata,
    )

    open_interest_rows: list[dict[str, str | int]] = []
    funding_rows: list[dict[str, str | int]] = []
    for hour_index in range(total_days * 24):
        observed_at = start + timedelta(hours=hour_index)
        open_interest_rows.append(
            {
                "timestamp": _timestamp_ms(observed_at),
                "sumOpenInterest": f"{10_000.0 + (hour_index * 10.0):.6f}",
            }
        )
        if hour_index % 8 == 0:
            funding_rows.append(
                {
                    "fundingTime": _timestamp_ms(observed_at),
                    "fundingRate": f"{0.0001 + ((hour_index // 8) * 0.000001):.8f}",
                }
            )

    coverage_end = (start + timedelta(days=total_days)).isoformat().replace("+00:00", "Z")
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=coverage_end,
        fetched_at="2026-04-01T07:31:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=funding_rows,
        symbol_metadata=symbol_metadata,
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="open_interest",
        symbol=symbol,
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=coverage_end,
        fetched_at="2026-04-01T07:32:00Z",
        endpoint="/futures/data/openInterestHist",
        payload=open_interest_rows,
        symbol_metadata=symbol_metadata,
    )

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials == ()


def test_imported_dataset_bundle_exposes_tradeability_metadata(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)
    latest = materials[-1]
    write_phase1_dataset_bundle(latest, dataset_root)

    rows = load_historical_dataset(dataset_root)

    symbol_row = rows[0].instrument_rows[0]
    assert symbol_row.symbol == "BTCUSDT"
    assert symbol_row.market_type == "futures"
    assert symbol_row.base_asset == "BTC"
    assert symbol_row.listing_timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert symbol_row.quote_volume_usdt_24h == pytest.approx(3_744_673_000.0)
    assert symbol_row.liquidity_tier == "top"
    assert symbol_row.quantity_step == pytest.approx(0.001)
    assert symbol_row.price_tick == pytest.approx(0.1)
    assert symbol_row.has_complete_funding is True


def test_imported_dataset_bundle_prefers_explicit_symbol_metadata_over_coverage_start_and_defaults(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        symbol_metadata={
            "listing_timestamp": "2020-05-01T00:00:00Z",
            "quantity_step": 0.005,
            "price_tick": 0.25,
        },
    )

    imported = load_phase1_raw_market_imports(archive_root)

    latest = build_phase1_dataset_bundle_materials(imported)[-1]
    write_phase1_dataset_bundle(latest, dataset_root)
    rows = load_historical_dataset(dataset_root)

    symbol_row = rows[0].instrument_rows[0]
    assert symbol_row.listing_timestamp == datetime(2020, 5, 1, tzinfo=UTC)
    assert symbol_row.quantity_step == pytest.approx(0.005)
    assert symbol_row.price_tick == pytest.approx(0.25)


def test_imported_dataset_bundle_resolves_symbol_metadata_at_symbol_scope_when_ohlcv_manifest_omits_it(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    shared_symbol_metadata = {
        "listing_timestamp": "2020-05-01T00:00:00Z",
        "quantity_step": 0.005,
        "price_tick": 0.25,
    }
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        ohlcv_symbol_metadata=None,
        funding_symbol_metadata=shared_symbol_metadata,
        open_interest_symbol_metadata=shared_symbol_metadata,
    )

    imported = load_phase1_raw_market_imports(archive_root)

    latest = build_phase1_dataset_bundle_materials(imported)[-1]
    write_phase1_dataset_bundle(latest, dataset_root)
    rows = load_historical_dataset(dataset_root)

    symbol_row = rows[0].instrument_rows[0]
    assert symbol_row.listing_timestamp == datetime(2020, 5, 1, tzinfo=UTC)
    assert symbol_row.quantity_step == pytest.approx(0.005)
    assert symbol_row.price_tick == pytest.approx(0.25)


def test_build_phase1_dataset_bundle_materials_rejects_cross_dataset_symbol_metadata_mismatch(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        ohlcv_symbol_metadata={
            "listing_timestamp": "2020-05-01T00:00:00Z",
            "quantity_step": 0.005,
            "price_tick": 0.25,
        },
        funding_symbol_metadata={
            "listing_timestamp": "2020-05-01T00:00:00Z",
            "quantity_step": 0.01,
            "price_tick": 0.25,
        },
        open_interest_symbol_metadata={
            "listing_timestamp": "2020-05-01T00:00:00Z",
            "quantity_step": 0.005,
            "price_tick": 0.25,
        },
    )

    imported = load_phase1_raw_market_imports(archive_root)

    with pytest.raises(ValueError, match="raw-market symbol metadata mismatch across phase1 datasets for symbol BTCUSDT"):
        build_phase1_dataset_bundle_materials(imported)


def test_build_phase1_dataset_bundle_materials_rejects_non_string_generated_row_symbol(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = tuple(replace(series, symbol=123) for series in load_phase1_raw_market_imports(archive_root))

    with pytest.raises(ValueError, match="market_context instrument_rows row symbol must be a string"):
        build_phase1_dataset_bundle_materials(imported)


def test_build_phase1_dataset_bundle_materials_keeps_eligible_symbol_subsets_per_timestamp(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        total_hours=110 * 24,
    )
    _archive_phase1_symbol_history(
        archive_root,
        symbol="ETHUSDT",
        start=datetime(2024, 2, 15, tzinfo=UTC),
        total_hours=60 * 24,
    )

    imported = load_phase1_raw_market_imports(archive_root)

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    first = materials[0]
    assert first.timestamp == datetime(2024, 2, 19, tzinfo=UTC)
    assert first.metadata["source"]["symbols"] == ["BTCUSDT"]
    assert first.metadata["source"]["series_keys"] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
    ]
    assert len(first.metadata["source"]["manifest_paths"]) == 3
    assert set(first.market_context["symbols"]) == {"BTCUSDT"}
    assert [row["symbol"] for row in first.market_context["instrument_rows"]] == ["BTCUSDT"]
    assert [row["symbol"] for row in first.derivatives_snapshot["rows"]] == ["BTCUSDT"]

    overlap_before_eth_is_ready = next(
        material for material in materials if material.timestamp == datetime(2024, 3, 20, tzinfo=UTC)
    )
    assert overlap_before_eth_is_ready.metadata["source"]["symbols"] == ["BTCUSDT"]
    assert set(overlap_before_eth_is_ready.market_context["symbols"]) == {"BTCUSDT"}
    assert [row["symbol"] for row in overlap_before_eth_is_ready.derivatives_snapshot["rows"]] == ["BTCUSDT"]

    first_dual_symbol = next(
        material for material in materials if material.timestamp == datetime(2024, 4, 4, tzinfo=UTC)
    )
    assert first_dual_symbol.metadata["source"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert first_dual_symbol.metadata["source"]["series_keys"] == [
        "binance:futures:funding:BTCUSDT",
        "binance:futures:funding:ETHUSDT",
        "binance:futures:ohlcv:BTCUSDT:1h",
        "binance:futures:ohlcv:ETHUSDT:1h",
        "binance:futures:open-interest:BTCUSDT",
        "binance:futures:open-interest:ETHUSDT",
    ]
    assert len(first_dual_symbol.metadata["source"]["manifest_paths"]) == 6
    assert set(first_dual_symbol.market_context["symbols"]) == {"BTCUSDT", "ETHUSDT"}
    assert [row["symbol"] for row in first_dual_symbol.market_context["instrument_rows"]] == ["BTCUSDT", "ETHUSDT"]
    assert [row["symbol"] for row in first_dual_symbol.derivatives_snapshot["rows"]] == ["BTCUSDT", "ETHUSDT"]

    imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    rows = validate_phase1_imported_dataset_root(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))

    assert imported_root.start_timestamp == datetime(2024, 2, 19, tzinfo=UTC)
    assert imported_root.symbols == ("BTCUSDT", "ETHUSDT")
    assert rows[0].timestamp == datetime(2024, 2, 19, tzinfo=UTC)
    assert rows[0].meta["source"]["symbols"] == ["BTCUSDT"]
    assert next(row for row in rows if row.timestamp == datetime(2024, 3, 20, tzinfo=UTC)).meta["source"]["symbols"] == [
        "BTCUSDT"
    ]
    assert next(row for row in rows if row.timestamp == datetime(2024, 4, 4, tzinfo=UTC)).meta["source"]["symbols"] == [
        "BTCUSDT",
        "ETHUSDT",
    ]
    assert manifest["source"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert len(manifest["source"]["manifest_paths"]) == 6


def test_import_phase1_archive_dataset_root_excludes_never_eligible_symbols_from_materialized_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(
        archive_root,
        symbol="BTCUSDT",
        start=datetime(2024, 1, 1, tzinfo=UTC),
        total_hours=110 * 24,
    )
    _archive_phase1_symbol_history(
        archive_root,
        symbol="ETHUSDT",
        start=datetime(2024, 2, 15, tzinfo=UTC),
        total_hours=20 * 24,
    )

    imported = load_phase1_raw_market_imports(archive_root)
    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials
    assert {
        symbol
        for material in materials
        for symbol in material.market_context["symbols"].keys()
    } == {"BTCUSDT"}

    try:
        imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    except ValueError as exc:
        remaining_paths = sorted(path.name for path in dataset_root.iterdir()) if dataset_root.exists() else []
        assert remaining_paths == []
        pytest.fail(f"import should succeed without leaking partial dataset output: {exc}")

    rows = validate_phase1_imported_dataset_root(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))

    assert imported_root.symbols == ("BTCUSDT",)
    assert manifest["symbols"] == ["BTCUSDT"]
    assert manifest["source"]["symbols"] == ["BTCUSDT"]
    assert len(manifest["source"]["manifest_paths"]) == 3
    assert {symbol for row in rows for symbol in row.market["symbols"].keys()} == {"BTCUSDT"}
    assert [row.symbol for dataset_row in rows for row in dataset_row.instrument_rows] == ["BTCUSDT"] * len(rows)


def test_build_phase1_dataset_bundle_materials_treats_funding_and_open_interest_as_optional_context(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    start = datetime(2024, 1, 1, tzinfo=UTC)
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="ohlcv",
        symbol="BTCUSDT",
        timeframe="1h",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:33:00Z",
        endpoint="/fapi/v1/klines",
        payload={
            "rows": [
                {
                    "open_time": _timestamp_ms(start),
                    "open": "49950.0",
                    "high": "50100.0",
                    "low": "49900.0",
                    "close": "50000.0",
                    "volume": "1000.0",
                    "quote_asset_volume": "50000000.0",
                }
            ]
        },
    )
    archive_raw_market_payload(
        archive_root=archive_root,
        exchange="binance",
        market="futures",
        dataset="funding",
        symbol="BTCUSDT",
        coverage_start=start.isoformat().replace("+00:00", "Z"),
        coverage_end=(start + timedelta(hours=80)).isoformat().replace("+00:00", "Z"),
        fetched_at="2026-04-01T07:34:00Z",
        endpoint="/fapi/v1/fundingRate",
        payload=[{"fundingTime": _timestamp_ms(start), "fundingRate": "0.0001"}],
    )

    imported = load_phase1_raw_market_imports(archive_root)

    assert build_phase1_dataset_bundle_materials(imported) == ()


@pytest.mark.parametrize(
    ("manifest_glob", "manifest_updates", "expected_dataset", "expected_timeframe"),
    [
        ("**/open-interest/BTCUSDT/*.manifest.json", {"dataset": "open_interest"}, "open-interest", None),
        ("**/ohlcv/BTCUSDT/1h/*.manifest.json", {"timeframe": "1H"}, "ohlcv", "1h"),
    ],
)
def test_build_phase1_dataset_bundle_materials_uses_canonicalized_import_scope_from_valid_manifest_aliases(
    tmp_path: Path,
    manifest_glob: str,
    manifest_updates: dict[str, str],
    expected_dataset: str,
    expected_timeframe: str | None,
) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    manifest_path = next((archive_root / "raw-market").glob(manifest_glob))
    _rewrite_raw_market_manifest_fields(manifest_path, **manifest_updates)

    imported = load_phase1_raw_market_imports(archive_root)

    canonical_series = next(
        series
        for series in imported
        if series.symbol == "BTCUSDT"
        and series.dataset == expected_dataset
        and series.timeframe == expected_timeframe
    )

    assert canonical_series.exchange == "binance"
    assert canonical_series.market == "futures"
    assert canonical_series.dataset == expected_dataset
    assert canonical_series.symbol == "BTCUSDT"
    assert canonical_series.timeframe == expected_timeframe

    materials = build_phase1_dataset_bundle_materials(imported)

    assert materials[-1].metadata["source"]["symbols"] == ["BTCUSDT"]


def test_import_phase1_archive_dataset_root_materializes_loadable_dataset_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    rows = load_historical_dataset(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))

    assert imported_root.archive_root == archive_root
    assert imported_root.dataset_root == dataset_root
    assert imported_root.snapshot_count == len(rows)
    assert imported_root.snapshot_count > 0
    assert imported_root.symbols == ("BTCUSDT",)
    assert imported_root.bundle_dirs[0] == rows[0].source_path
    assert imported_root.bundle_dirs[-1] == rows[-1].source_path
    assert imported_root.start_timestamp == rows[0].timestamp
    assert imported_root.end_timestamp == rows[-1].timestamp
    assert manifest["schema_version"] == "phase1_imported_dataset_root.v1"
    assert manifest["archive_root"] == str(archive_root)
    assert manifest["dataset_root"] == str(dataset_root)
    assert manifest["snapshot_count"] == imported_root.snapshot_count
    assert manifest["symbols"] == ["BTCUSDT"]
    assert manifest["start_timestamp"] == rows[0].timestamp.isoformat().replace("+00:00", "Z")
    assert manifest["end_timestamp"] == rows[-1].timestamp.isoformat().replace("+00:00", "Z")
    assert manifest["bundle_dirs"][0] == str(rows[0].source_path)
    assert manifest["bundle_dirs"][-1] == str(rows[-1].source_path)
    assert manifest["source"]["scope"] == "phase1_binance_futures"
    assert len(manifest["source"]["manifest_paths"]) == 3
    validated_rows = validate_phase1_imported_dataset_root(dataset_root)
    assert [row.timestamp for row in validated_rows] == [row.timestamp for row in rows]


def test_validate_phase1_imported_dataset_root_rejects_manifest_schema_version_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "phase1_imported_dataset_root.v2"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported phase1 dataset root manifest schema"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_noncanonical_manifest_dataset_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = f" {manifest['dataset_root']} "
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest dataset_root must be a canonical string"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_dataset_root_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = str(tmp_path / "other-dataset")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset root manifest dataset_root mismatch"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_boolean_manifest_snapshot_count(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_count"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest snapshot_count must be a non-negative integer"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_snapshot_count_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_count"] += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest snapshot_count did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_scope_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scope"] = "phase1_okx_futures"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest scope is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_parent_traversal_manifest_archive_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = "../archive"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest archive_root must not contain parent traversal"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_non_string_manifest_symbols(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbols"] = [123]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest symbols entries must be canonical strings"):
        validate_phase1_imported_dataset_root(dataset_root)


@pytest.mark.parametrize("symbol", ["btcusdt", "BTC-USDT"])
def test_validate_phase1_imported_dataset_root_rejects_non_uppercase_exchange_manifest_symbols(
    tmp_path: Path,
    symbol: str,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbols"] = [symbol]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="root manifest symbols entries must be uppercase exchange symbols",
    ):
        validate_phase1_imported_dataset_root(dataset_root)


def test_write_phase1_dataset_root_manifest_rejects_non_string_symbols(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    materials = build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root))
    bundle_dirs = [write_phase1_dataset_bundle(materials[-1], dataset_root)]

    with pytest.raises(ValueError, match="materialized dataset root manifest symbols entries must be canonical strings"):
        write_phase1_dataset_root_manifest(
            archive_root,
            dataset_root,
            symbols=[123],
            materials=[materials[-1]],
            bundle_dirs=bundle_dirs,
        )


def test_material_market_context_symbol_keys_rejects_empty_list_symbols(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    materials = list(build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root)))
    bad_material = replace(materials[-1], market_context={**materials[-1].market_context, "symbols": []})

    with pytest.raises(ValueError, match="market_context symbols must be an object"):
        _material_market_context_symbol_keys(bad_material)


def test_materialize_dataset_root_rejects_non_string_material_symbol_key_and_cleans_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    materials = list(build_phase1_dataset_bundle_materials(load_phase1_raw_market_imports(archive_root)))
    bad_material = replace(materials[-1], market_context={**materials[-1].market_context, "symbols": {123: {}}})

    with pytest.raises(ValueError, match="market_context symbols keys must be canonical strings"):
        _materialize_dataset_root(
            archive_root=archive_root,
            dataset_root=dataset_root,
            materials=[bad_material],
        )

    assert not dataset_root.exists()


def test_validate_phase1_imported_dataset_root_rejects_manifest_symbols_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["symbols"] = ["ETHUSDT"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest symbols did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_non_uppercase_source_trace_symbols(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["symbols"] = ["btcusdt"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="materialized dataset root manifest source symbols entries must be uppercase exchange symbols",
    ):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_non_string_manifest_bundle_timestamps(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_timestamps"] = [123]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest bundle_timestamps entries must be canonical strings"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_noncanonical_manifest_bundle_timestamp_shape(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_timestamps"] = [
        timestamp.replace("Z", "+00:00")
        for timestamp in manifest["bundle_timestamps"]
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest bundle_timestamps entries must be canonical UTC timestamps"):
        validate_phase1_imported_dataset_root(dataset_root)


@pytest.mark.parametrize("field", ["start_timestamp", "end_timestamp"])
@pytest.mark.parametrize("value", ["2024-02-19T00:00:00+00:00", "not-a-timestamp", 123])
def test_validate_phase1_imported_dataset_root_rejects_noncanonical_manifest_root_timestamp_shape(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=rf"root manifest {field} must be a canonical UTC timestamp",
    ):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_noncanonical_manifest_archive_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = f" {manifest['archive_root']} "
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest archive_root must be a canonical string"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_archive_root_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(tmp_path / "other-archive")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest archive_root did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_noncanonical_manifest_bundle_dirs(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_dirs"] = [f" {manifest['bundle_dirs'][0]} "]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest bundle_dirs entries must be canonical strings"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_parent_traversal_manifest_bundle_dirs(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_dirs"] = ["../dataset/bundle"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest bundle_dirs must not contain parent traversal"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_non_string_root_source_manifest_paths(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["manifest_paths"] = [123]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="materialized dataset root manifest source manifest_paths entries must be canonical strings"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_invalid_referenced_source_manifest_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    original_load_manifest = archive_importer.load_phase1_raw_market_manifest

    def load_manifest_with_invalid_symbol(manifest_path: str | Path) -> object:
        imported_file = original_load_manifest(manifest_path)
        return replace(imported_file, manifest={**imported_file.manifest, "symbol": None})

    monkeypatch.setattr(archive_importer, "load_phase1_raw_market_manifest", load_manifest_with_invalid_symbol)

    with pytest.raises(ValueError, match="referenced raw-market manifest symbol must be a string"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_source_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["series_keys"] = ["binance:futures:ohlcv:BTCUSDT:1h"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest source did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_missing_source_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    source_manifest_path = Path(manifest["source"]["manifest_paths"][0])
    source_manifest_path.unlink()

    with pytest.raises(FileNotFoundError, match="raw-market manifest missing"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_out_of_scope_source_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))
    source_manifest_path = Path(manifest["source"]["manifest_paths"][0])
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["market"] = "spot"
    source_manifest_path.write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="raw-market manifest market must match canonical archive identity"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_write_phase1_dataset_bundle_materializes_instrument_snapshot_file(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    instrument_snapshot = json.loads((bundle_dir / "instrument_snapshot.json").read_text(encoding="utf-8"))

    assert instrument_snapshot == {
        "as_of": material.market_context["as_of"],
        "schema_version": "imported_instrument_snapshot.v1",
        "rows": material.market_context["instrument_rows"],
    }


def test_instrument_snapshot_payload_rejects_non_object_rows() -> None:
    with pytest.raises(ValueError, match=r"instrument_snapshot rows\[1\] must be an object"):
        archive_importer._instrument_snapshot_payload(
            as_of="2024-01-01T00:00:00Z",
            instrument_rows=[
                {"symbol": "BTCUSDT", "base_asset": "BTC"},
                [("symbol", "ETHUSDT")],
            ],
        )


def test_symbol_metadata_float_rejects_numeric_strings() -> None:
    with pytest.raises(ValueError, match="symbol_metadata quantity_step must be numeric"):
        archive_importer._symbol_metadata_float(
            symbol_metadata={"quantity_step": "0.001"},
            field="quantity_step",
            default=1.0,
        )


def test_symbol_metadata_float_rejects_bool() -> None:
    with pytest.raises(ValueError, match="symbol_metadata price_tick must be numeric"):
        archive_importer._symbol_metadata_float(
            symbol_metadata={"price_tick": True},
            field="price_tick",
            default=1.0,
        )


def test_symbol_metadata_timestamp_rejects_non_string_listing_timestamp(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    series = load_phase1_raw_market_imports(archive_root)[0]

    with pytest.raises(ValueError, match="symbol_metadata listing_timestamp must be a canonical string"):
        archive_importer._symbol_metadata_timestamp(
            symbol_metadata={"listing_timestamp": 123},
            fallback_series=series,
        )


def test_inspect_phase1_imported_dataset_root_rejects_non_string_row_symbol_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()

    class Row:
        timestamp = datetime(2024, 3, 1, tzinfo=UTC)
        source_path = tmp_path / "dataset" / "bundle"
        meta = {
            "source": {
                "scope": archive_importer.PHASE1_IMPORTER_SCOPE,
                "exchange": "binance",
                "market": "futures",
                "symbols": ["BTCUSDT"],
                "series_keys": [],
                "manifest_paths": [],
            }
        }
        market = {"symbols": {123: {}}}

    monkeypatch.setattr(archive_importer, "load_historical_dataset", lambda path: [Row()])

    with pytest.raises(ValueError, match="materialized dataset row market symbols keys must be canonical strings"):
        inspect_phase1_imported_dataset_root(dataset_root)


def test_import_phase1_archive_dataset_root_rejects_non_string_material_symbol_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    market_context["symbols"] = {123: {}}
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    monkeypatch.setattr(
        archive_importer,
        "build_phase1_dataset_bundle_materials",
        lambda imported_series: (bad_material,),
    )

    with pytest.raises(ValueError, match="market_context symbols keys must be canonical strings"):
        import_phase1_archive_dataset_root(archive_root, dataset_root)


def test_write_phase1_dataset_bundle_rejects_non_object_instrument_rows(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    market_context["instrument_rows"] = [[("symbol", "BTCUSDT")]]
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(ValueError, match=r"market_context instrument_rows\[1\] must be an object"):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_mapping_instrument_rows_without_artifact(tmp_path: Path) -> None:
    class InstrumentRowMapping(Mapping[str, object]):
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def __getitem__(self, key: str) -> object:
            return self._payload[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._payload)

        def __len__(self) -> int:
            return len(self._payload)

    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    market_context["instrument_rows"] = [
        InstrumentRowMapping(dict(market_context["instrument_rows"][0])),
    ]
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    expected_bundle_dir = dataset_root / f"{archive_importer._bundle_fragment(material.timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"market_context instrument_rows\[1\] must be a JSON object"):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_non_list_instrument_filters_without_artifact(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["filters"] = {"filterType": "LOT_SIZE"}
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    expected_bundle_dir = dataset_root / f"{archive_importer._bundle_fragment(material.timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"instrument_snapshot rows\[0\]\.filters must be a list"):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_non_object_instrument_filter_entries_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["filters"] = [{"filterType": "LOT_SIZE"}, ["filterType", "PRICE_FILTER"]]
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    expected_bundle_dir = dataset_root / f"{archive_importer._bundle_fragment(material.timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"instrument_snapshot rows\[0\]\.filters\[1\] must be an object"):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_mapping_instrument_filter_entries_without_artifact(
    tmp_path: Path,
) -> None:
    class InstrumentFilterMapping(Mapping[str, object]):
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def __getitem__(self, key: str) -> object:
            return self._payload[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._payload)

        def __len__(self) -> int:
            return len(self._payload)

    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["filters"] = [InstrumentFilterMapping({"filterType": "LOT_SIZE"})]
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    expected_bundle_dir = dataset_root / f"{archive_importer._bundle_fragment(material.timestamp)}__{material.run_id}"

    with pytest.raises(ValueError, match=r"instrument_snapshot rows\[0\]\.filters\[0\] must be a JSON object"):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not expected_bundle_dir.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minQty", True),
        ("maxQty", "100.0"),
        ("stepSize", 0.0),
        ("tickSize", -0.1),
        ("minPrice", float("nan")),
        ("maxPrice", float("inf")),
        ("minNotional", "5.0"),
        ("notional", 0.0),
    ],
)
def test_write_phase1_dataset_bundle_rejects_malformed_instrument_filter_numerics_without_artifact(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["filters"] = [{"filterType": "LOT_SIZE", field: value}]
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    expected_bundle_dir = dataset_root / f"{archive_importer._bundle_fragment(material.timestamp)}__{material.run_id}"

    with pytest.raises(
        ValueError,
        match=rf"instrument_snapshot rows\[0\]\.filters\[0\]\.{field} must be a positive finite number",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not expected_bundle_dir.exists()


def test_write_phase1_dataset_bundle_rejects_non_string_market_context_as_of(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    market_context["as_of"] = True
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(ValueError, match="market_context as_of must be a string"):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_funding_rate(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["funding_rate"] = "0.000279"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.funding_rate must be numeric",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_funding_age_seconds(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["funding_age_seconds"] = "25200"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.funding_age_seconds must be a non-negative integer",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_derivatives_age_seconds(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    derivatives_snapshot = dict(material.derivatives_snapshot)
    rows = [dict(row) for row in derivatives_snapshot["rows"]]
    rows[0]["open_interest_age_seconds"] = "0"
    derivatives_snapshot["rows"] = rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=material.market_context,
        derivatives_snapshot=derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"derivatives_snapshot rows\[0\]\.open_interest_age_seconds must be a non-negative integer",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    [
        ("symbol", 123),
        ("symbol", ""),
        ("symbol", " BTCUSDT"),
        ("instrument", "BTCUSDT"),
        ("category", "spot"),
        ("exchange", "coinbase"),
    ],
)
def test_write_phase1_dataset_bundle_rejects_present_invalid_derivatives_identity_before_write(
    tmp_path: Path,
    field_name: str,
    malformed_value: object,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    derivatives_snapshot = dict(material.derivatives_snapshot)
    rows = [dict(row) for row in derivatives_snapshot["rows"]]
    rows[0][field_name] = malformed_value
    derivatives_snapshot["rows"] = rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=material.market_context,
        derivatives_snapshot=derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(ValueError, match=rf"derivatives_snapshot rows\[0\]\.{field_name}"):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_present_malformed_open_interest_usdt(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["open_interest_usdt"] = "1450000000.0"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.open_interest_usdt must be numeric",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_mark_price(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["mark_price"] = "64395.5"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.mark_price must be numeric",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_index_price(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["index_price"] = "64390.0"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.index_price must be numeric",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_malformed_basis_bps(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    symbols = dict(market_context["symbols"])
    symbol_context = dict(symbols["BTCUSDT"])
    futures_context = dict(symbol_context["futures_context"])
    futures_context["basis_bps"] = "12.5"
    symbol_context["futures_context"] = futures_context
    symbols["BTCUSDT"] = symbol_context
    market_context["symbols"] = symbols
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"market_context symbols\.BTCUSDT\.futures_context\.basis_bps must be numeric",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_present_invalid_instrument_rows(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    market_context["instrument_rows"] = ""
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(ValueError, match="market_context instrument_rows must be a list"):
        write_phase1_dataset_bundle(bad_material, dataset_root)


def test_write_phase1_dataset_bundle_rejects_non_boolean_instrument_funding_flag_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["has_complete_funding"] = "false"
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.has_complete_funding must be a boolean",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_numeric_string_instrument_quantity_step_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["quantity_step"] = "0.001"
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.quantity_step must be a positive finite number",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_numeric_string_instrument_min_notional_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["min_notional"] = "5.0"
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.min_notional must be a positive finite number",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_numeric_string_instrument_lot_size_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["lot_size"] = "0.001"
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.lot_size must be a positive finite number",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_boolean_instrument_max_leverage_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["maxLeverage"] = True
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.maxLeverage must be a positive finite integer",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_write_phase1_dataset_bundle_rejects_boolean_instrument_price_precision_without_artifact(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["pricePrecision"] = True
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.pricePrecision must be a positive finite integer",
    ):
        write_phase1_dataset_bundle(bad_material, dataset_root)

    assert not dataset_root.exists()


def test_supplement_phase1_imported_dataset_root_rejects_non_object_archive_derived_instrument_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    (bundle_dir / "instrument_snapshot.json").unlink()
    market_context = dict(material.market_context)
    market_context["instrument_rows"] = [[("symbol", "BTCUSDT")]]
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    monkeypatch.setattr(
        archive_importer,
        "build_phase1_dataset_bundle_materials",
        lambda imported_series: (bad_material,),
    )

    with pytest.raises(ValueError, match=r"market_context instrument_rows\[1\] must be an object"):
        supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)


def test_supplement_phase1_imported_dataset_root_rejects_non_string_archive_derived_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    (bundle_dir / "instrument_snapshot.json").unlink()
    market_context = dict(material.market_context)
    market_context["as_of"] = True
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    monkeypatch.setattr(
        archive_importer,
        "build_phase1_dataset_bundle_materials",
        lambda imported_series: (bad_material,),
    )

    with pytest.raises(ValueError, match="market_context as_of must be a string"):
        supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)


def test_supplement_phase1_imported_dataset_root_rejects_numeric_string_archive_derived_price_tick_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    instrument_snapshot_path = bundle_dir / "instrument_snapshot.json"
    instrument_snapshot_path.unlink()
    market_context = dict(material.market_context)
    instrument_rows = [dict(row) for row in market_context["instrument_rows"]]
    instrument_rows[0]["price_tick"] = "0.1"
    market_context["instrument_rows"] = instrument_rows
    bad_material = archive_importer.Phase1DatasetBundleMaterial(
        timestamp=material.timestamp,
        run_id=material.run_id,
        metadata=material.metadata,
        market_context=market_context,
        derivatives_snapshot=material.derivatives_snapshot,
        account_snapshot=material.account_snapshot,
    )
    monkeypatch.setattr(
        archive_importer,
        "build_phase1_dataset_bundle_materials",
        lambda imported_series: (bad_material,),
    )

    with pytest.raises(
        ValueError,
        match=r"instrument_snapshot rows\[0\]\.price_tick must be a positive finite number",
    ):
        supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)

    assert not instrument_snapshot_path.exists()


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_backfills_legacy_root(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = dataset_root / f"{material.timestamp.isoformat().replace('+00:00', 'Z').replace(':', '-')}__{material.run_id}"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "metadata.json").write_text(json.dumps(material.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle_dir / "market_context.json").write_text(
        json.dumps({key: value for key, value in material.market_context.items() if key != "instrument_rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps(material.derivatives_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps(material.account_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )

    written_paths = supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert written_paths == (bundle_dir / "instrument_snapshot.json",)
    assert rows[0].instrument_rows[0].symbol == "BTCUSDT"
    assert rows[0].instrument_rows[0].listing_timestamp == datetime(2024, 1, 1, tzinfo=UTC)


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_uses_recorded_manifest_paths_not_full_archive_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = dataset_root / f"{material.timestamp.isoformat().replace('+00:00', 'Z').replace(':', '-')}__{material.run_id}"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "metadata.json").write_text(json.dumps(material.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle_dir / "market_context.json").write_text(
        json.dumps({key: value for key, value in material.market_context.items() if key != "instrument_rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "derivatives_snapshot.json").write_text(
        json.dumps(material.derivatives_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "account_snapshot.json").write_text(
        json.dumps(material.account_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )

    _archive_phase1_symbol_history(archive_root, symbol="ETHUSDT")

    written_paths = supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert written_paths == (bundle_dir / "instrument_snapshot.json",)
    assert [item.symbol for item in rows[0].instrument_rows] == ["BTCUSDT"]


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_rejects_padded_relative_dataset_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    archive_root = repo_root / "archive"
    dataset_root = repo_root / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    imported = import_phase1_archive_dataset_root(archive_root, dataset_root)
    bundle_dir = imported.bundle_dirs[0]
    (bundle_dir / "instrument_snapshot.json").unlink()

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = f" {Path(manifest['dataset_root']).relative_to(repo_root)} "
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="root manifest dataset_root must be a canonical string"):
        supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_resolves_relative_manifest_paths(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    archive_root = repo_root / "trading_system" / "data" / "archive"
    dataset_root = repo_root / "trading_system" / "data" / "imported-datasets" / "sample_dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    (bundle_dir / "instrument_snapshot.json").unlink()

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in metadata["source"]["manifest_paths"]]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(Path(manifest["archive_root"]).relative_to(repo_root))
    manifest["dataset_root"] = str(Path(manifest["dataset_root"]).relative_to(repo_root))
    manifest["bundle_dirs"] = [str(Path(path).relative_to(repo_root)) for path in manifest["bundle_dirs"]]
    manifest["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in manifest["source"]["manifest_paths"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    written_paths = supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert written_paths == (bundle_dir / "instrument_snapshot.json",)
    assert [item.symbol for item in rows[0].instrument_rows] == ["BTCUSDT"]


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_dedupes_manifest_paths_after_relative_resolution(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    archive_root = repo_root / "trading_system" / "data" / "archive"
    dataset_root = repo_root / "trading_system" / "data" / "imported-datasets" / "sample_dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    (bundle_dir / "instrument_snapshot.json").unlink()

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    absolute_paths = list(metadata["source"]["manifest_paths"])
    relative_paths = [str(Path(path).relative_to(repo_root)) for path in absolute_paths]
    metadata["source"]["manifest_paths"] = absolute_paths + relative_paths
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(Path(manifest["archive_root"]).relative_to(repo_root))
    manifest["dataset_root"] = str(Path(manifest["dataset_root"]).relative_to(repo_root))
    manifest["bundle_dirs"] = [str(Path(path).relative_to(repo_root)) for path in manifest["bundle_dirs"]]
    manifest["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in manifest["source"]["manifest_paths"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    written_paths = supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)
    rows = load_historical_dataset(dataset_root)

    assert written_paths == (bundle_dir / "instrument_snapshot.json",)
    assert [item.symbol for item in rows[0].instrument_rows] == ["BTCUSDT"]


def test_supplement_phase1_imported_dataset_root_instrument_snapshots_rejects_relative_manifest_paths_when_dataset_root_base_dir_cannot_be_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    archive_root = repo_root / "trading_system" / "data" / "archive"
    dataset_root = repo_root / "trading_system" / "data" / "imported-datasets" / "sample_dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )
    (bundle_dir / "instrument_snapshot.json").unlink()

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in metadata["source"]["manifest_paths"]]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(Path(manifest["archive_root"]).relative_to(repo_root))
    manifest["dataset_root"] = "broken/place/for/dataset_root"
    manifest["bundle_dirs"] = [str(Path(path).relative_to(repo_root)) for path in manifest["bundle_dirs"]]
    manifest["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in manifest["source"]["manifest_paths"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.chdir(repo_root)

    with pytest.raises(ValueError, match="relative source manifest_paths require a resolvable dataset_root base dir"):
        supplement_phase1_imported_dataset_root_instrument_snapshots(dataset_root)


def test_validate_phase1_imported_dataset_root_accepts_relative_manifest_paths_and_root_fields(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    archive_root = repo_root / "trading_system" / "data" / "archive"
    dataset_root = repo_root / "trading_system" / "data" / "imported-datasets" / "sample_dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    write_phase1_dataset_root_manifest(
        archive_root,
        dataset_root,
        symbols=("BTCUSDT",),
        materials=(material,),
        bundle_dirs=(bundle_dir,),
    )

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in metadata["source"]["manifest_paths"]]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_root"] = str(Path(manifest["archive_root"]).relative_to(repo_root))
    manifest["dataset_root"] = str(Path(manifest["dataset_root"]).relative_to(repo_root))
    manifest["bundle_dirs"] = [str(Path(path).relative_to(repo_root)) for path in manifest["bundle_dirs"]]
    manifest["source"]["manifest_paths"] = [str(Path(path).relative_to(repo_root)) for path in manifest["source"]["manifest_paths"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = validate_phase1_imported_dataset_root(dataset_root)

    assert len(rows) == 1
    assert rows[0].source_path == bundle_dir
    assert [item.symbol for item in rows[0].instrument_rows] == ["BTCUSDT"]


def test_validate_phase1_imported_dataset_root_rejects_parent_traversal_dataset_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    import_phase1_archive_dataset_root(archive_root, dataset_root)

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_root"] = "../dataset"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest dataset_root must not contain parent traversal"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_parent_traversal_source_manifest_paths(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    import_phase1_archive_dataset_root(archive_root, dataset_root)

    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["manifest_paths"] = ["../archive/raw-market/binance/futures/BTCUSDT/ohlcv/1h/2026/01.manifest.json"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest source\\.manifest_paths must not contain parent traversal"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_parent_traversal_bundle_metadata_source_manifest_paths(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")
    import_phase1_archive_dataset_root(archive_root, dataset_root)

    bundle_dir = next(path for path in dataset_root.iterdir() if path.is_dir())
    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source"]["manifest_paths"] = [
        "../archive/raw-market/binance/futures/BTCUSDT/ohlcv/1h/2026/01.manifest.json"
    ]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="bundle metadata source\\.manifest_paths must not contain parent traversal",
    ):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_missing_instrument_snapshot(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    (bundle_dir / "instrument_snapshot.json").unlink()

    with pytest.raises(FileNotFoundError, match="instrument_snapshot.json"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_instrument_snapshot_drift_from_market_context(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    instrument_snapshot_path = bundle_dir / "instrument_snapshot.json"
    instrument_snapshot = json.loads(instrument_snapshot_path.read_text(encoding="utf-8"))
    instrument_snapshot["rows"][0]["liquidity_tier"] = "drifted-tier"
    instrument_snapshot_path.write_text(json.dumps(instrument_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="instrument rows drifted between market_context.json and instrument_snapshot.json"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )



def test_validate_phase1_imported_dataset_root_allows_legacy_market_context_without_instrument_rows(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    market_context_path = bundle_dir / "market_context.json"
    market_context = json.loads(market_context_path.read_text(encoding="utf-8"))
    market_context.pop("instrument_rows", None)
    market_context_path.write_text(json.dumps(market_context, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = validate_phase1_imported_dataset_root(
        dataset_root,
        expected_bundle_dirs=(bundle_dir,),
        expected_timestamps=(material.timestamp,),
    )

    assert [item.symbol for item in rows[0].instrument_rows] == ["BTCUSDT"]


def test_validate_phase1_imported_dataset_root_rejects_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = metadata_path.read_text(encoding="utf-8")
    metadata_path.write_text(
        metadata.replace(
            '"timestamp": "2024-02-29T23:00:00Z"',
            '"timestamp": "2024-03-01T00:00:00Z"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="timestamps did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_metadata_run_id_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["run_id"] = "phase1-import-2024-03-01T00-00-00Z"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle metadata run_id did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_metadata_schema_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = "phase1_import_bundle.v2"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle metadata schema_version is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_payload_schema_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    market_context_path = bundle_dir / "market_context.json"
    market_context = json.loads(market_context_path.read_text(encoding="utf-8"))
    market_context["schema_version"] = "imported_market_context.v2"
    market_context_path.write_text(json.dumps(market_context, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle payload schema_version is out of phase1 importer scope"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_payload_as_of_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)

    market_context_path = bundle_dir / "market_context.json"
    market_context = json.loads(market_context_path.read_text(encoding="utf-8"))
    market_context["as_of"] = "2024-03-01T00:00:00Z"
    market_context_path.write_text(json.dumps(market_context, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle payload as_of did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_dir_mismatch(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported = load_phase1_raw_market_imports(archive_root)
    material = build_phase1_dataset_bundle_materials(imported)[-1]
    bundle_dir = write_phase1_dataset_bundle(material, dataset_root)
    renamed_bundle_dir = bundle_dir.with_name(f"{bundle_dir.name}__renamed")
    bundle_dir.rename(renamed_bundle_dir)

    with pytest.raises(ValueError, match="bundle directories did not round-trip"):
        validate_phase1_imported_dataset_root(
            dataset_root,
            expected_bundle_dirs=(bundle_dir,),
            expected_timestamps=(material.timestamp,),
        )


def test_validate_phase1_imported_dataset_root_rejects_bundle_dir_name_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_dir = Path(manifest["bundle_dirs"][-1])
    renamed_bundle_dir = bundle_dir.with_name("renamed-bundle")
    bundle_dir.rename(renamed_bundle_dir)
    manifest["bundle_dirs"][-1] = str(renamed_bundle_dir)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle directory name did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_bundle_dirs_outside_dataset_root(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_dirs"][-1] = str(tmp_path / "outside" / "bundle")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest bundle_dirs must stay under dataset_root"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_bundle_timestamps_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_timestamps"][-1] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest bundle_timestamps did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_start_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["start_timestamp"] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest start_timestamp did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_validate_phase1_imported_dataset_root_rejects_manifest_end_timestamp_drift(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    import_phase1_archive_dataset_root(archive_root, dataset_root)
    manifest_path = dataset_root / "import_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["end_timestamp"] = "2024-03-01T00:00:00Z"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest end_timestamp did not round-trip"):
        validate_phase1_imported_dataset_root(dataset_root)


def test_inspect_phase1_imported_dataset_root_summarizes_manifest_and_rows(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    rows = load_historical_dataset(dataset_root)
    manifest = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))

    summary = inspect_phase1_imported_dataset_root(dataset_root)

    expected = {
        "snapshot_count": imported_root.snapshot_count,
        "symbols": list(imported_root.symbols),
        "archive_root": str(imported_root.archive_root),
        "bundle_dirs": [str(bundle_dir) for bundle_dir in imported_root.bundle_dirs],
        "bundle_timestamps": [row.timestamp.isoformat().replace("+00:00", "Z") for row in rows],
        "start_timestamp": imported_root.start_timestamp.isoformat().replace("+00:00", "Z"),
        "end_timestamp": imported_root.end_timestamp.isoformat().replace("+00:00", "Z"),
        "source": manifest["source"],
    }

    assert summary["manifest"] == summary["rows"]
    assert summary["manifest"] == expected


def test_inspect_phase1_imported_dataset_root_keeps_row_summary_without_manifest(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    dataset_root = tmp_path / "dataset"
    _archive_phase1_symbol_history(archive_root, symbol="BTCUSDT")

    imported_root = import_phase1_archive_dataset_root(archive_root, dataset_root)
    rows = load_historical_dataset(dataset_root)
    manifest_source = json.loads((dataset_root / "import_manifest.json").read_text(encoding="utf-8"))["source"]
    (dataset_root / "import_manifest.json").unlink()

    summary = inspect_phase1_imported_dataset_root(dataset_root)

    assert summary["manifest"] is None
    assert summary["rows"] == {
        "snapshot_count": imported_root.snapshot_count,
        "symbols": list(imported_root.symbols),
        "archive_root": str(imported_root.archive_root),
        "bundle_dirs": [str(bundle_dir) for bundle_dir in imported_root.bundle_dirs],
        "bundle_timestamps": [row.timestamp.isoformat().replace("+00:00", "Z") for row in rows],
        "start_timestamp": imported_root.start_timestamp.isoformat().replace("+00:00", "Z"),
        "end_timestamp": imported_root.end_timestamp.isoformat().replace("+00:00", "Z"),
        "source": manifest_source,
    }
