from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RAW_MARKET_ROOT_DIRNAME = "raw-market"
RAW_MARKET_MANIFEST_SCHEMA_VERSION = "raw_market_manifest.v1"
PHASE1_RAW_MARKET_EXCHANGE = "binance"
PHASE1_RAW_MARKET_MARKET = "futures"
PHASE1_RAW_MARKET_DATASET_ALIASES = {
    "ohlcv": "ohlcv",
    "funding": "funding",
    "open-interest": "open-interest",
    "open_interest": "open-interest",
}


@dataclass(frozen=True, slots=True)
class ArchivedRawMarketPayload:
    storage_dir: Path
    data_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ImportedRawMarketRecord:
    observed_at: datetime
    payload: Any


@dataclass(frozen=True, slots=True)
class ImportedRawMarketFile:
    series_key: str
    manifest_path: Path
    data_path: Path
    manifest: dict[str, Any]
    coverage_start: datetime
    coverage_end: datetime
    fetched_at: datetime
    records: tuple[ImportedRawMarketRecord, ...]


@dataclass(frozen=True, slots=True)
class ImportedRawMarketSeries:
    series_key: str
    exchange: str
    market: str
    dataset: str
    symbol: str
    timeframe: str | None
    files: tuple[ImportedRawMarketFile, ...]
    records: tuple[ImportedRawMarketRecord, ...]


def _utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_fragment(value: str) -> str:
    return _utc_timestamp(value).replace(":", "-")


def _utc_datetime(value: str | int | float) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("timestamp value must not be empty")
    if normalized.isdigit():
        return datetime.fromtimestamp(int(normalized) / 1000.0, tz=timezone.utc)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_segment(value: str, *, lowercase: bool = True) -> str:
    normalized = value.strip()
    if lowercase:
        normalized = normalized.lower()
    if not normalized:
        raise ValueError("archive path segment must not be empty")
    return normalized


def _payload_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _canonical_dataset(value: str) -> str:
    normalized = _normalized_segment(value)
    canonical = PHASE1_RAW_MARKET_DATASET_ALIASES.get(normalized)
    if canonical is None:
        supported = ", ".join(sorted(set(PHASE1_RAW_MARKET_DATASET_ALIASES.values())))
        raise ValueError(f"dataset must be one of: {supported}")
    return canonical


def _validated_scope(
    *,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    timeframe: str | None,
) -> tuple[str, str, str, str, str | None]:
    normalized_exchange = _normalized_segment(exchange)
    normalized_market = _normalized_segment(market)
    canonical_dataset = _canonical_dataset(dataset)
    normalized_symbol = _normalized_segment(symbol, lowercase=False)
    normalized_timeframe = _normalized_segment(timeframe) if timeframe else None

    if (
        normalized_exchange != PHASE1_RAW_MARKET_EXCHANGE
        or normalized_market != PHASE1_RAW_MARKET_MARKET
    ):
        raise ValueError("only binance futures raw-market datasets are supported in phase 1")
    if canonical_dataset == "ohlcv":
        if normalized_timeframe is None:
            raise ValueError("ohlcv dataset requires timeframe")
    elif normalized_timeframe is not None:
        raise ValueError(f"{canonical_dataset} dataset does not allow timeframe")
    return (
        normalized_exchange,
        normalized_market,
        canonical_dataset,
        normalized_symbol,
        normalized_timeframe,
    )


def raw_market_series_key(
    *,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    timeframe: str | None = None,
) -> str:
    normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe = _validated_scope(
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    parts = [normalized_exchange, normalized_market, canonical_dataset, normalized_symbol]
    if normalized_timeframe:
        parts.append(normalized_timeframe)
    return ":".join(parts)


def _existing_manifest_for_coverage(
    storage_dir: Path,
    *,
    coverage_start: str,
    coverage_end: str,
) -> Path | None:
    if not storage_dir.exists():
        return None
    for manifest_path in storage_dir.glob("*.manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("coverage_start") == coverage_start
            and manifest.get("coverage_end") == coverage_end
        ):
            return manifest_path
    return None


def _required_manifest_value(manifest: dict[str, Any], key: str, *, manifest_path: Path) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"raw-market manifest missing required string field '{key}': {manifest_path}")
    return value


def _manifest_timeframe(manifest: dict[str, Any]) -> str | None:
    value = manifest.get("timeframe")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("raw-market manifest timeframe must be a non-empty string when present")
    return value


def _manifest_data_path(manifest: dict[str, Any], *, manifest_path: Path) -> Path:
    file_payload = manifest.get("file")
    if not isinstance(file_payload, dict):
        raise ValueError(f"raw-market manifest missing file metadata: {manifest_path}")
    raw_path = file_payload.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"raw-market manifest missing file.path: {manifest_path}")
    path = Path(raw_path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path


def _validate_manifest_file_metadata(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    data_path: Path,
    raw_bytes: bytes,
) -> None:
    file_payload = manifest.get("file")
    if not isinstance(file_payload, dict):
        raise ValueError(f"raw-market manifest missing file metadata: {manifest_path}")
    expected_sha = file_payload.get("sha256")
    expected_size = file_payload.get("size_bytes")
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    actual_size = len(raw_bytes)
    if expected_sha != actual_sha:
        raise ValueError(f"raw-market file sha256 mismatch: {data_path}")
    if expected_size != actual_size:
        raise ValueError(f"raw-market file size mismatch: {data_path}")


def _rows_payload(payload: Any, *, dataset: str, data_path: Path) -> list[Any]:
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"raw-market payload rows must be an array for {dataset}: {data_path}")
    return rows


def _timestamp_value_for_row(*, dataset: str, row: Any, data_path: Path, index: int) -> str | int | float:
    if dataset == "ohlcv":
        if isinstance(row, dict):
            value = row.get("open_time", row.get("openTime"))
        elif isinstance(row, (list, tuple)) and row:
            value = row[0]
        else:
            value = None
        if value is None:
            raise ValueError(f"ohlcv row missing open_time/openTime: {data_path} rows[{index}]")
        return value
    if not isinstance(row, dict):
        raise ValueError(f"{dataset} rows must be JSON objects: {data_path} rows[{index}]")
    field_name = "fundingTime" if dataset == "funding" else "timestamp"
    value = row.get(field_name)
    if value is None:
        raise ValueError(f"{dataset} row missing {field_name}: {data_path} rows[{index}]")
    return value


def _validated_import_scope(manifest: dict[str, Any], *, manifest_path: Path) -> tuple[str, str, str, str, str | None, str]:
    schema_version = _required_manifest_value(manifest, "schema_version", manifest_path=manifest_path)
    if schema_version != RAW_MARKET_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported raw-market manifest schema: {manifest_path}")
    normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe = _validated_scope(
        exchange=_required_manifest_value(manifest, "exchange", manifest_path=manifest_path),
        market=_required_manifest_value(manifest, "market", manifest_path=manifest_path),
        dataset=_required_manifest_value(manifest, "dataset", manifest_path=manifest_path),
        symbol=_required_manifest_value(manifest, "symbol", manifest_path=manifest_path),
        timeframe=_manifest_timeframe(manifest),
    )
    series_key = raw_market_series_key(
        exchange=normalized_exchange,
        market=normalized_market,
        dataset=canonical_dataset,
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
    )
    sync_payload = manifest.get("sync")
    if not isinstance(sync_payload, dict):
        raise ValueError(f"raw-market manifest missing sync metadata: {manifest_path}")
    if sync_payload.get("series_key") != series_key:
        raise ValueError(f"raw-market manifest sync.series_key mismatch: {manifest_path}")
    return normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe, series_key


def _load_import_file(manifest_path: Path) -> ImportedRawMarketFile:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"raw-market manifest must be a JSON object: {manifest_path}")
    normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe, series_key = (
        _validated_import_scope(manifest, manifest_path=manifest_path)
    )
    data_path = _manifest_data_path(manifest, manifest_path=manifest_path)
    if not data_path.exists():
        raise FileNotFoundError(f"raw-market data file missing: {data_path}")
    raw_bytes = data_path.read_bytes()
    _validate_manifest_file_metadata(manifest, manifest_path=manifest_path, data_path=data_path, raw_bytes=raw_bytes)
    payload = json.loads(raw_bytes.decode("utf-8"))
    rows = _rows_payload(payload, dataset=canonical_dataset, data_path=data_path)
    records = tuple(
        sorted(
            (
                ImportedRawMarketRecord(
                    observed_at=_utc_datetime(
                        _timestamp_value_for_row(dataset=canonical_dataset, row=row, data_path=data_path, index=index)
                    ),
                    payload=row,
                )
                for index, row in enumerate(rows)
            ),
            key=lambda record: record.observed_at,
        )
    )
    return ImportedRawMarketFile(
        series_key=series_key,
        manifest_path=manifest_path,
        data_path=data_path,
        manifest=manifest,
        coverage_start=_utc_datetime(_required_manifest_value(manifest, "coverage_start", manifest_path=manifest_path)),
        coverage_end=_utc_datetime(_required_manifest_value(manifest, "coverage_end", manifest_path=manifest_path)),
        fetched_at=_utc_datetime(_required_manifest_value(manifest, "fetched_at", manifest_path=manifest_path)),
        records=records,
    )


def _build_import_series(files: list[ImportedRawMarketFile]) -> ImportedRawMarketSeries:
    ordered_files = tuple(
        sorted(
            files,
            key=lambda item: (item.coverage_start, item.coverage_end, item.fetched_at, str(item.manifest_path)),
        )
    )
    first = ordered_files[0]
    manifest = first.manifest
    flattened_records = tuple(
        record
        for imported_file in ordered_files
        for record in imported_file.records
    )
    return ImportedRawMarketSeries(
        series_key=first.series_key,
        exchange=str(manifest["exchange"]),
        market=str(manifest["market"]),
        dataset=str(manifest["dataset"]),
        symbol=str(manifest["symbol"]),
        timeframe=manifest.get("timeframe"),
        files=ordered_files,
        records=flattened_records,
    )


def raw_market_storage_dir(
    archive_root: Path,
    *,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    timeframe: str | None = None,
) -> Path:
    normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe = _validated_scope(
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    path = (
        Path(archive_root)
        / RAW_MARKET_ROOT_DIRNAME
        / normalized_exchange
        / normalized_market
        / canonical_dataset
        / normalized_symbol
    )
    if normalized_timeframe:
        path = path / normalized_timeframe
    return path


def archive_raw_market_payload(
    *,
    archive_root: Path,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    coverage_start: str,
    coverage_end: str,
    fetched_at: str,
    endpoint: str,
    payload: Any,
    timeframe: str | None = None,
) -> ArchivedRawMarketPayload:
    normalized_exchange, normalized_market, canonical_dataset, normalized_symbol, normalized_timeframe = _validated_scope(
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    normalized_coverage_start = _utc_timestamp(coverage_start)
    normalized_coverage_end = _utc_timestamp(coverage_end)
    normalized_fetched_at = _utc_timestamp(fetched_at)
    storage_dir = raw_market_storage_dir(
        archive_root,
        exchange=normalized_exchange,
        market=normalized_market,
        dataset=canonical_dataset,
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
    )
    storage_dir.mkdir(parents=True, exist_ok=True)
    duplicate_manifest = _existing_manifest_for_coverage(
        storage_dir,
        coverage_start=normalized_coverage_start,
        coverage_end=normalized_coverage_end,
    )
    if duplicate_manifest is not None:
        raise FileExistsError(f"coverage window already archived: {duplicate_manifest}")
    stem = "__".join(
        (
            _timestamp_fragment(normalized_coverage_start),
            _timestamp_fragment(normalized_coverage_end),
            _timestamp_fragment(normalized_fetched_at),
        )
    )
    data_path = storage_dir / f"{stem}.json"
    manifest_path = storage_dir / f"{stem}.manifest.json"
    if data_path.exists() or manifest_path.exists():
        raise FileExistsError(f"immutable raw-market archive already exists: {data_path}")

    raw_bytes = _payload_bytes(payload)
    data_path.write_bytes(raw_bytes)
    series_key = raw_market_series_key(
        exchange=normalized_exchange,
        market=normalized_market,
        dataset=canonical_dataset,
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
    )
    manifest: dict[str, Any] = {
        "schema_version": RAW_MARKET_MANIFEST_SCHEMA_VERSION,
        "source": normalized_exchange,
        "exchange": normalized_exchange,
        "endpoint": endpoint,
        "market": normalized_market,
        "dataset": canonical_dataset,
        "symbol": normalized_symbol,
        "coverage_start": normalized_coverage_start,
        "coverage_end": normalized_coverage_end,
        "fetched_at": normalized_fetched_at,
        "sync": {
            "mode": "coverage",
            "series_key": series_key,
            "cursor_field": "coverage_end",
            "cursor": normalized_coverage_end,
            "next_start": normalized_coverage_end,
        },
        "file": {
            "path": str(data_path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
        },
    }
    if normalized_timeframe:
        manifest["timeframe"] = normalized_timeframe
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return ArchivedRawMarketPayload(
        storage_dir=storage_dir,
        data_path=data_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def load_phase1_raw_market_series(
    archive_root: str | Path,
    *,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    timeframe: str | None = None,
) -> ImportedRawMarketSeries:
    series_dir = raw_market_storage_dir(
        Path(archive_root),
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    if not series_dir.exists():
        raise FileNotFoundError(f"raw-market storage dir not found: {series_dir}")
    manifest_paths = sorted(series_dir.glob("*.manifest.json"))
    if not manifest_paths:
        raise FileNotFoundError(f"raw-market manifests not found: {series_dir}")
    imported_files = [_load_import_file(manifest_path) for manifest_path in manifest_paths]
    expected_series_key = raw_market_series_key(
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    for imported_file in imported_files:
        if imported_file.series_key != expected_series_key:
            raise ValueError(f"raw-market manifest scope mismatch for requested series: {imported_file.manifest_path}")
    return _build_import_series(imported_files)


def load_phase1_raw_market_manifest(manifest_path: str | Path) -> ImportedRawMarketFile:
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"raw-market manifest missing: {path}")
    return _load_import_file(path)


def load_phase1_raw_market_imports(archive_root: str | Path) -> tuple[ImportedRawMarketSeries, ...]:
    raw_market_root = Path(archive_root) / RAW_MARKET_ROOT_DIRNAME
    if not raw_market_root.exists():
        return ()
    grouped_files: dict[str, list[ImportedRawMarketFile]] = {}
    for manifest_path in sorted(raw_market_root.rglob("*.manifest.json")):
        imported_file = _load_import_file(manifest_path)
        grouped_files.setdefault(imported_file.series_key, []).append(imported_file)
    return tuple(_build_import_series(grouped_files[series_key]) for series_key in sorted(grouped_files))


__all__ = [
    "ArchivedRawMarketPayload",
    "ImportedRawMarketFile",
    "ImportedRawMarketRecord",
    "ImportedRawMarketSeries",
    "RAW_MARKET_MANIFEST_SCHEMA_VERSION",
    "RAW_MARKET_ROOT_DIRNAME",
    "archive_raw_market_payload",
    "load_phase1_raw_market_imports",
    "load_phase1_raw_market_manifest",
    "load_phase1_raw_market_series",
    "raw_market_series_key",
    "raw_market_storage_dir",
]
