#!/usr/bin/env python3
from __future__ import annotations

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

# Equivalent shell command after unsetting runtime env:
# python3 scripts/verify.py --suite full
COMMAND = [sys.executable, "scripts/verify.py", "--suite", "full"]


def main() -> int:
    env = os.environ.copy()
    for key in UNSET_ENV:
        env.pop(key, None)
    print("unset " + " ".join(UNSET_ENV), flush=True)
    print("$ " + " ".join(COMMAND), flush=True)
    completed = subprocess.run(COMMAND, text=True, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
