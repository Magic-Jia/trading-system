#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


SANITIZED_ENV_REMOVED_PREFIXES = ["TRADING_"]
UNSET_ENV = [
    "TRADING_RUNTIME_ENV",
    "TRADING_ENTRY_PROFILE",
    "TRADING_EXECUTION_MODE",
    "TRADING_BASE_DIR",
    "TRADING_STATE_FILE",
    "TRADING_ACCOUNT_SNAPSHOT_FILE",
    "TRADING_MARKET_CONTEXT_FILE",
    "TRADING_DERIVATIVES_SNAPSHOT_FILE",
]

DISPLAY_COMMANDS = ["python3 scripts/verify.py --suite full"]
PLANNED_SUITES = ["full"]
PLAN_COMMAND_ARGV = [["python3", "scripts/verify.py", "--suite", "full"]]
DIFF_CHECK_COMMAND = "git --no-pager diff --check HEAD"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run clean-env nightly full verification")
    parser.add_argument("--dry-run", action="store_true", help="print planned commands without executing them")
    parser.add_argument("--json", action="store_true", help="emit dry-run plan as JSON; requires --dry-run")
    parser.add_argument("--manifest-path", help="write a JSON verification run manifest to this path")
    return parser


def plan_fingerprint(payload: dict[str, object]) -> str:
    canonical_payload = dict(payload)
    canonical_payload.pop("plan_fingerprint", None)
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sanitized_env(env: dict[str, str] | None = None) -> dict[str, str]:
    cleaned = dict(os.environ if env is None else env)
    for key in list(cleaned):
        if any(key.startswith(prefix) for prefix in SANITIZED_ENV_REMOVED_PREFIXES):
            cleaned.pop(key, None)
    return cleaned


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def git_sha() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def sanitized_trading_env(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, bool]]:
    return {
        key: {
            "present_before": key in before,
            "present_after": key in after,
        }
        for key in UNSET_ENV
    }


def write_manifest(
    path: str,
    *,
    started_at: str,
    finished_at: str,
    returncode: int,
    env_before: dict[str, str],
    env_after: dict[str, str],
) -> None:
    payload = {
        "manifest_version": 1,
        "manifest_kind": "nightly_verification_run",
        "entrypoint": "nightly_verify",
        "started_at": started_at,
        "finished_at": finished_at,
        "git_sha": git_sha(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "clean_env": True,
        "sanitized_trading_env": sanitized_trading_env(env_before, env_after),
        "sanitized_env_removed_prefixes": SANITIZED_ENV_REMOVED_PREFIXES,
        "suites": PLANNED_SUITES,
        "test_command": DISPLAY_COMMANDS[0],
        "test_command_argv": PLAN_COMMAND_ARGV[0],
        "test_result_count": None,
        "diff_check_command": DIFF_CHECK_COMMAND,
        "returncode": returncode,
    }
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.json and not args.dry_run:
        print("--json requires --dry-run", file=sys.stderr)
        return 2
    if args.dry_run:
        payload = {
            "plan_version": 1,
            "plan_kind": "nightly_verification_plan",
            "entrypoint": "nightly_verify",
            "clean_env": True,
            "commands": DISPLAY_COMMANDS,
            "command_argv": PLAN_COMMAND_ARGV,
            "suites": PLANNED_SUITES,
            "unset_env": UNSET_ENV,
            "sanitized_env_removed_prefixes": SANITIZED_ENV_REMOVED_PREFIXES,
        }
        payload["plan_fingerprint"] = plan_fingerprint(payload)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("plan_version: 1")
            print("plan_kind: nightly_verification_plan")
            print("clean_env: true")
            print(f"suites: {','.join(PLANNED_SUITES)}")
            print("unset " + " ".join(UNSET_ENV))
            print("\n".join(DISPLAY_COMMANDS))
        return 0

    env_before = os.environ.copy()
    env = sanitized_env(env_before)
    started_at = utc_now()
    print("unset " + " ".join(UNSET_ENV), flush=True)
    print("$ " + " ".join(PLAN_COMMAND_ARGV[0]), flush=True)
    completed = subprocess.run(PLAN_COMMAND_ARGV[0], text=True, env=env, shell=False)
    finished_at = utc_now()
    if args.manifest_path:
        write_manifest(
            args.manifest_path,
            started_at=started_at,
            finished_at=finished_at,
            returncode=completed.returncode,
            env_before=env_before,
            env_after=env,
        )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
