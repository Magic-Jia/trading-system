#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys


DISPLAY_COMMANDS = [
    "python3 scripts/verify.py --dry-run --strict-auto-changed",
    "python3 scripts/verify.py --suite workflow-meta",
    "python3 scripts/verify.py --suite evidence-chain",
]
PLANNED_SUITES = ["workflow-meta", "evidence-chain"]
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


def plan_fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            "command_argv": [
                ["python3", "scripts/verify.py", "--dry-run", "--strict-auto-changed"],
                ["python3", "scripts/verify.py", "--suite", "workflow-meta"],
                ["python3", "scripts/verify.py", "--suite", "evidence-chain"],
            ],
            "suites": PLANNED_SUITES,
            "strict_changed_verification": True,
        }
        payload["plan_fingerprint"] = plan_fingerprint(payload)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("plan_version: 1")
            print("plan_kind: ci_verification_plan")
            print("strict_changed_verification: true")
            print(f"suites: {','.join(PLANNED_SUITES)}")
            for command in DISPLAY_COMMANDS:
                print(command)
        return 0

    for command in COMMANDS:
        print("$ " + " ".join(command), flush=True)
        completed = subprocess.run(command, text=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
