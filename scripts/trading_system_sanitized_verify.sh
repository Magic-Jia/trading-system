#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY_PYTHON="${TRADING_SYSTEM_VERIFY_PYTHON:-/home/cn/.hermes/hermes-agent/venv/bin/python}"

UNSET_ENV=(
  TRADING_RUNTIME_ENV
  TRADING_ENTRY_PROFILE
  TRADING_EXECUTION_MODE
  TRADING_BASE_DIR
  TRADING_STATE_FILE
  TRADING_ACCOUNT_SNAPSHOT_FILE
  TRADING_MARKET_CONTEXT_FILE
  TRADING_DERIVATIVES_SNAPSHOT_FILE
)

if [[ ! -x "${VERIFY_PYTHON}" ]]; then
  printf 'verification python is not executable: %s\n' "${VERIFY_PYTHON}" >&2
  exit 2
fi

for key in "${UNSET_ENV[@]}"; do
  unset "${key}"
done

cd "${ROOT}"

if [[ "${1:-}" == "--print-env" ]]; then
  exec "${VERIFY_PYTHON}" - <<'PY'
from __future__ import annotations

import json
import os

keys = [
    "SANITIZED_VERIFY_SENTINEL",
    "TRADING_BASE_DIR",
    "TRADING_ENTRY_PROFILE",
    "TRADING_RUNTIME_ENV",
    "TRADING_STATE_FILE",
]
print(json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True))
PY
fi

if [[ "${1:-}" == "--dry-run" && "${2:-}" == "--json" && "$#" -eq 2 ]]; then
  exec "${VERIFY_PYTHON}" - "${VERIFY_PYTHON}" <<'PY'
from __future__ import annotations

import json
import sys

verify_python = sys.argv[1]
payload = {
    "contract_version": 1,
    "contract_kind": "sanitized_verification_environment",
    "entrypoint": "trading_system_sanitized_verify",
    "python": verify_python,
    "command_argv": [verify_python, "scripts/verify.py"],
    "unset_env": [
        "TRADING_RUNTIME_ENV",
        "TRADING_ENTRY_PROFILE",
        "TRADING_EXECUTION_MODE",
        "TRADING_BASE_DIR",
        "TRADING_STATE_FILE",
        "TRADING_ACCOUNT_SNAPSHOT_FILE",
        "TRADING_MARKET_CONTEXT_FILE",
        "TRADING_DERIVATIVES_SNAPSHOT_FILE",
    ],
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY
fi

exec "${VERIFY_PYTHON}" scripts/verify.py "$@"
