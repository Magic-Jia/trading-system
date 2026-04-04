from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "archived_at": self.archived_at,
            "schema_version": self.schema_version,
            "bundle_kind": self.bundle_kind,
            "mode": self.mode,
            "runtime_env": self.runtime_env,
            "source": dict(self.source),
            "input_timestamps": dict(self.input_timestamps),
        }


@dataclass(frozen=True, slots=True)
class ArchivedRuntimeBundle:
    bundle_dir: Path
    metadata: RuntimeBundleMetadata
