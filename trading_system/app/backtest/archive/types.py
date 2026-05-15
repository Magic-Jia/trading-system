from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Mapping

_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_RAW_MARKET_PROVENANCE_IDENTITY_FIELDS = (
    "source",
    "archive_root",
    "coverage_start",
    "coverage_end",
    "fetched_at",
)
_RAW_MARKET_PROVENANCE_TIMESTAMP_FIELDS = frozenset(
    {
        "coverage_start",
        "coverage_end",
        "fetched_at",
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeBundleSourcePaths:
    account_snapshot: Path
    market_context: Path
    derivatives_snapshot: Path
    runtime_state: Path


@dataclass(frozen=True, slots=True)
class RuntimeBundleMetadata:
    timestamp: str
    run_id: str
    archived_at: str
    schema_version: str
    bundle_kind: str
    mode: str
    runtime_env: str
    source: dict[str, str]
    input_timestamps: dict[str, str]
    raw_market: dict[str, str] | None = None

    @staticmethod
    def _canonical_string_mapping(value: Any, *, field_name: str) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError(f"runtime bundle metadata {field_name} must be a mapping")
        copied: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"runtime bundle metadata {field_name} keys must be strings")
            if not isinstance(item, str):
                raise ValueError(f"runtime bundle metadata {field_name}.{key} must be a string")
            copied[key] = item
        return copied

    @staticmethod
    def _canonical_raw_market_provenance(value: Any) -> dict[str, str]:
        provenance = RuntimeBundleMetadata._canonical_string_mapping(value, field_name="raw_market")
        for field in _RAW_MARKET_PROVENANCE_IDENTITY_FIELDS:
            if field not in provenance:
                raise ValueError(f"runtime bundle metadata raw_market.{field} must be present")
            if not provenance[field].strip() or provenance[field] != provenance[field].strip():
                raise ValueError(f"runtime bundle metadata raw_market.{field} must be canonical")
        for field in _RAW_MARKET_PROVENANCE_TIMESTAMP_FIELDS:
            value = provenance[field]
            if not _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value):
                raise ValueError(f"runtime bundle metadata raw_market.{field} must be a canonical UTC Z timestamp")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"runtime bundle metadata raw_market.{field} must be a canonical UTC Z timestamp") from exc
            if parsed.isoformat().replace("+00:00", "Z") != value:
                raise ValueError(f"runtime bundle metadata raw_market.{field} must be a canonical UTC Z timestamp")
        return {field: provenance[field] for field in _RAW_MARKET_PROVENANCE_IDENTITY_FIELDS}

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "archived_at": self.archived_at,
            "schema_version": self.schema_version,
            "bundle_kind": self.bundle_kind,
            "mode": self.mode,
            "runtime_env": self.runtime_env,
            "source": self._canonical_string_mapping(self.source, field_name="source"),
            "input_timestamps": self._canonical_string_mapping(
                self.input_timestamps,
                field_name="input_timestamps",
            ),
        }
        if self.raw_market is not None:
            payload["raw_market"] = self._canonical_raw_market_provenance(self.raw_market)
        return payload


@dataclass(frozen=True, slots=True)
class ArchivedRuntimeBundle:
    bundle_dir: Path
    metadata: RuntimeBundleMetadata
