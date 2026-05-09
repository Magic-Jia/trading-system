#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys


# Equivalent shell commands:
# python3 scripts/verify.py --dry-run --strict-auto-changed
# python3 scripts/verify.py --suite workflow-meta
# python3 scripts/verify.py --suite evidence-chain
COMMANDS = [
    [sys.executable, "scripts/verify.py", "--dry-run", "--strict-auto-changed"],
    [sys.executable, "scripts/verify.py", "--suite", "workflow-meta"],
    [sys.executable, "scripts/verify.py", "--suite", "evidence-chain"],
]


def main() -> int:
    for command in COMMANDS:
        print("$ " + " ".join(command), flush=True)
        completed = subprocess.run(command, text=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
