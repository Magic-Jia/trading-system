#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PATH="/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "${UV_BIN}" ]]; then
  echo "uv not found; PATH=${PATH}" >&2
  exit 127
fi

MODE="${TRADING_EXECUTION_MODE:-paper}"
RUNTIME_ENV="${TRADING_RUNTIME_ENV:-paper}"
BASE_DIR="${TRADING_BASE_DIR:-${REPO_ROOT}/trading_system}"
RUNTIME_BUCKET_DIR="${BASE_DIR}/data/runtime/${MODE}/${RUNTIME_ENV}"
LOG_DIR="${TRADING_CRON_LOG_DIR:-${RUNTIME_BUCKET_DIR}/logs}"
LOG_FILE="${TRADING_CRON_LOG_FILE:-${LOG_DIR}/run-cycle.log}"
LOCK_FILE="${TRADING_CRON_LOCK_FILE:-${RUNTIME_BUCKET_DIR}/run-cycle.lock}"
LATEST_JSON="${RUNTIME_BUCKET_DIR}/latest.json"

export TRADING_EXECUTION_MODE="${MODE}"
export TRADING_RUNTIME_ENV="${RUNTIME_ENV}"
export TRADING_BASE_DIR="${BASE_DIR}"
export TRADING_ACCOUNT_SNAPSHOT_FILE="${TRADING_ACCOUNT_SNAPSHOT_FILE:-${RUNTIME_BUCKET_DIR}/account_snapshot.json}"
export TRADING_MARKET_CONTEXT_FILE="${TRADING_MARKET_CONTEXT_FILE:-${RUNTIME_BUCKET_DIR}/market_context.json}"
export TRADING_DERIVATIVES_SNAPSHOT_FILE="${TRADING_DERIVATIVES_SNAPSHOT_FILE:-${RUNTIME_BUCKET_DIR}/derivatives_snapshot.json}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${BASE_DIR}/data/runtime/.uv-cache}"

mkdir -p "${LOG_DIR}" "$(dirname -- "${LOCK_FILE}")" "${UV_CACHE_DIR}"
touch "${LOG_FILE}"
exec 9>"${LOCK_FILE}"

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

{
  echo "[$(timestamp)] starting trading-system paper cron cycle"
  echo "[$(timestamp)] repo_root=${REPO_ROOT} base_dir=${BASE_DIR} mode=${MODE} runtime_env=${RUNTIME_ENV}"
  echo "[$(timestamp)] log_file=${LOG_FILE} lock_file=${LOCK_FILE} uv_cache_dir=${UV_CACHE_DIR}"

  if ! flock -n 9; then
    echo "[$(timestamp)] skip: previous run still holds lock ${LOCK_FILE}"
    exit 0
  fi

  cd "${REPO_ROOT}"

  run_rc=0
  if ! "${UV_BIN}" run python -m trading_system.run_cycle --mode "${MODE}" --runtime-env "${RUNTIME_ENV}"; then
    run_rc=$?
  fi

  echo "[$(timestamp)] finished exit_code=${run_rc} latest_json=${LATEST_JSON}"
  exit "${run_rc}"
} >>"${LOG_FILE}" 2>&1
