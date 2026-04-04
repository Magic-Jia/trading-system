from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ...runtime_paths import build_runtime_paths
from .runtime_bundle import RuntimeBundleSourcePaths, archive_runtime_bundle


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
    latest = json.loads(paths.latest_summary_file.read_text(encoding="utf-8"))
    archived_at = str(latest["finished_at"])
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
        bundle_timestamp = json.loads(paths.account_snapshot_file.read_text(encoding="utf-8")).get("as_of") or archived_at
        bundle_dir = paths.archive_runtime_bundles_dir / f"{str(bundle_timestamp).replace(':', '-')}--{run_id}"
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
