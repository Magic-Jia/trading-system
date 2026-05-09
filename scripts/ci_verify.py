#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys


DISPLAY_COMMANDS = [
    "python3 scripts/verify.py --dry-run --strict-auto-changed",
    "python3 scripts/verify.py --suite workflow-meta",
    "python3 scripts/verify.py --suite evidence-chain",
]
COMMANDS = [
    [sys.executable, "scripts/verify.py", "--dry-run", "--strict-auto-changed"],
    [sys.executable, "scripts/verify.py", "--suite", "workflow-meta"],
    [sys.executable, "scripts/verify.py", "--suite", "evidence-chain"],
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local CI-equivalent verification")
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
            "plan_kind": "ci_verification_plan",
            "entrypoint": "ci_verify",
            "commands": DISPLAY_COMMANDS,
            "strict_changed_verification": True,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("plan_version: 1")
            print("plan_kind: ci_verification_plan")
            print("strict_changed_verification: true")
            print("\n".join(DISPLAY_COMMANDS))
        return 0
    for command in COMMANDS:
        print("$ " + " ".join(command), flush=True)
        completed = subprocess.run(command, text=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
