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


def _utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_fragment(value: str) -> str:
    return _utc_timestamp(value).replace(":", "-")


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


__all__ = [
    "ArchivedRawMarketPayload",
    "RAW_MARKET_MANIFEST_SCHEMA_VERSION",
    "RAW_MARKET_ROOT_DIRNAME",
    "archive_raw_market_payload",
    "raw_market_series_key",
    "raw_market_storage_dir",
]
