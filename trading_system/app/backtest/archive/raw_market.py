from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RAW_MARKET_ROOT_DIRNAME = "raw-market"
RAW_MARKET_MANIFEST_SCHEMA_VERSION = "raw_market_manifest.v1"


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


def raw_market_storage_dir(
    archive_root: Path,
    *,
    exchange: str,
    market: str,
    dataset: str,
    symbol: str,
    timeframe: str | None = None,
) -> Path:
    path = (
        Path(archive_root)
        / RAW_MARKET_ROOT_DIRNAME
        / _normalized_segment(exchange)
        / _normalized_segment(market)
        / _normalized_segment(dataset)
        / _normalized_segment(symbol, lowercase=False)
    )
    if timeframe:
        path = path / _normalized_segment(timeframe)
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
    storage_dir = raw_market_storage_dir(
        archive_root,
        exchange=exchange,
        market=market,
        dataset=dataset,
        symbol=symbol,
        timeframe=timeframe,
    )
    storage_dir.mkdir(parents=True, exist_ok=True)
    stem = "__".join(
        (
            _timestamp_fragment(coverage_start),
            _timestamp_fragment(coverage_end),
            _timestamp_fragment(fetched_at),
        )
    )
    data_path = storage_dir / f"{stem}.json"
    manifest_path = storage_dir / f"{stem}.manifest.json"
    if data_path.exists() or manifest_path.exists():
        raise FileExistsError(f"immutable raw-market archive already exists: {data_path}")

    raw_bytes = _payload_bytes(payload)
    data_path.write_bytes(raw_bytes)
    manifest: dict[str, Any] = {
        "schema_version": RAW_MARKET_MANIFEST_SCHEMA_VERSION,
        "source": _normalized_segment(exchange),
        "exchange": _normalized_segment(exchange),
        "endpoint": endpoint,
        "market": _normalized_segment(market),
        "dataset": _normalized_segment(dataset),
        "symbol": _normalized_segment(symbol, lowercase=False),
        "coverage_start": _utc_timestamp(coverage_start),
        "coverage_end": _utc_timestamp(coverage_end),
        "fetched_at": _utc_timestamp(fetched_at),
        "file": {
            "path": str(data_path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
        },
    }
    if timeframe:
        manifest["timeframe"] = _normalized_segment(timeframe)
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
    "raw_market_storage_dir",
]
