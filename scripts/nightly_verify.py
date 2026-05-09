#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


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
COMMAND = [sys.executable, "scripts/verify.py", "--suite", "full"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run clean-env nightly full verification")
    parser.add_argument("--dry-run", action="store_true", help="print planned commands without executing them")
    parser.add_argument("--json", action="store_true", help="emit dry-run plan as JSON; requires --dry-run")
    return parser


def main() -> int:
    args = build_parser().parse_args()
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
            "unset_env": UNSET_ENV,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("plan_version: 1")
            print("plan_kind: nightly_verification_plan")
            print("clean_env: true")
            print("unset " + " ".join(UNSET_ENV))
            print("\n".join(DISPLAY_COMMANDS))
        return 0

    env = os.environ.copy()
    for key in UNSET_ENV:
        env.pop(key, None)
    print("unset " + " ".join(UNSET_ENV), flush=True)
    print("$ " + " ".join(COMMAND), flush=True)
    completed = subprocess.run(COMMAND, text=True, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
