from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


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

    def as_dict(self) -> dict[str, Any]:
        return {
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


@dataclass(frozen=True, slots=True)
class ArchivedRuntimeBundle:
    bundle_dir: Path
    metadata: RuntimeBundleMetadata
