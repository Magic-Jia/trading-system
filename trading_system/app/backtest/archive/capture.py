from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...runtime_paths import build_runtime_paths
from .runtime_bundle import RuntimeBundleSourcePaths, archive_runtime_bundle


def _canonical_utc_timestamp(value: str, *, field_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field_name} must be canonical UTC") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise ValueError(f"{field_name} must be canonical UTC")
    return canonical


def _read_latest_summary(path: Path) -> dict[str, Any]:
    latest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(latest, Mapping):
        raise ValueError("latest summary must be a JSON object")
    return dict(latest)


def _latest_finished_at(latest: dict[str, Any]) -> str:
    value = latest.get("finished_at")
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("latest summary finished_at must be a non-empty timestamp string without whitespace")
    return _canonical_utc_timestamp(value, field_name="latest summary finished_at")


def _validate_latest_summary_identity(latest: dict[str, Any], *, mode: str, runtime_env: str) -> None:
    if latest.get("mode") != mode:
        raise ValueError("latest summary mode must match requested mode")
    if latest.get("runtime_env") != runtime_env:
        raise ValueError("latest summary runtime_env must match requested runtime_env")


def _validate_unique_runtime_envs(runtime_envs: Sequence[str]) -> None:
    if len(set(runtime_envs)) != len(runtime_envs):
        raise ValueError("runtime_env values must be unique")


@dataclass(frozen=True, slots=True)
class RuntimeCaptureResult:
    mode: str
    runtime_env: str
    archived_at: str
    status: str
    bundle_dir: Path

    def as_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload["bundle_dir"] = str(self.bundle_dir)
        return payload


def capture_runtime_env(
    *,
    runtime_root: str | Path,
    mode: str,
    runtime_env: str,
) -> RuntimeCaptureResult:
    paths = build_runtime_paths(mode, runtime_root=runtime_root, runtime_env=runtime_env)
    latest = _read_latest_summary(paths.latest_summary_file)
    _validate_latest_summary_identity(latest, mode=paths.mode, runtime_env=paths.runtime_env)
    archived_at = _latest_finished_at(latest)
    try:
        archived = archive_runtime_bundle(
            paths,
            RuntimeBundleSourcePaths(
                account_snapshot=paths.account_snapshot_file,
                market_context=paths.market_context_file,
                derivatives_snapshot=paths.derivatives_snapshot_file,
                runtime_state=paths.state_file,
            ),
            archived_at=archived_at,
        )
        status = "archived"
        bundle_dir = archived.bundle_dir
    except FileExistsError:
        archived_fragment = archived_at.replace(":", "-").replace("+", "-").replace(".", "-").lower()
        run_id = f"{paths.mode}-{paths.runtime_env}-{archived_fragment}"
        matches = sorted(paths.archive_runtime_bundles_dir.glob(f"*--{run_id}"))
        if len(matches) != 1:
            raise
        bundle_dir = matches[0]
        status = "already_archived"
    return RuntimeCaptureResult(
        mode=paths.mode,
        runtime_env=paths.runtime_env,
        archived_at=archived_at,
        status=status,
        bundle_dir=bundle_dir,
    )


def capture_runtime_envs(
    *,
    runtime_root: str | Path,
    mode: str,
    runtime_envs: Sequence[str],
) -> list[RuntimeCaptureResult]:
    _validate_unique_runtime_envs(runtime_envs)
    return [
        capture_runtime_env(runtime_root=runtime_root, mode=mode, runtime_env=runtime_env)
        for runtime_env in runtime_envs
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture runtime buckets into archive/runtime-bundles")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--runtime-env", dest="runtime_envs", action="append", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    captured = capture_runtime_envs(
        runtime_root=args.runtime_root,
        mode=args.mode,
        runtime_envs=tuple(args.runtime_envs),
    )
    print(json.dumps([item.as_dict() for item in captured], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RuntimeCaptureResult", "capture_runtime_env", "capture_runtime_envs", "main"]
