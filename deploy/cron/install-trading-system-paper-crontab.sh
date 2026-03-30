#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
WRAPPER="${REPO_ROOT}/deploy/cron/trading-system-paper-cron.sh"
CRON_EXPR="${TRADING_PAPER_CRON_EXPR:-*/15 * * * *}"
BLOCK_BEGIN="# >>> trading-system-paper cron >>>"
BLOCK_END="# <<< trading-system-paper cron <<<"

if [[ ! -x "${WRAPPER}" ]]; then
  echo "wrapper is missing or not executable: ${WRAPPER}" >&2
  exit 1
fi

read_output_file="$(mktemp)"
tmp_crontab=""
cleanup() {
  rm -f "${read_output_file}"
  if [[ -n "${tmp_crontab}" ]]; then
    rm -f "${tmp_crontab}"
  fi
}
trap cleanup EXIT

current_crontab=""
if crontab -l >"${read_output_file}" 2>&1; then
  current_crontab="$(cat "${read_output_file}")"
else
  read_error="$(cat "${read_output_file}")"
  if [[ "${read_error}" != *"no crontab for"* ]]; then
    echo "unable to read current crontab safely: ${read_error}" >&2
    exit 1
  fi
fi

sanitized_crontab="$(
  printf '%s\n' "${current_crontab}" | awk -v begin="${BLOCK_BEGIN}" -v end="${BLOCK_END}" '
    $0 == begin {skip = 1; next}
    $0 == end {skip = 0; next}
    skip {next}
    {print}
  '
)"

tmp_crontab="$(mktemp)"
{
  if [[ -n "${sanitized_crontab}" ]]; then
    printf '%s\n' "${sanitized_crontab}"
  fi
  printf '%s\n' "${BLOCK_BEGIN}"
  printf '%s\n' 'SHELL=/bin/bash'
  printf '%s\n' 'PATH=/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin'
  printf '%s %s\n' "${CRON_EXPR}" "${WRAPPER}"
  printf '%s\n' "${BLOCK_END}"
} >"${tmp_crontab}"

crontab "${tmp_crontab}"
echo "installed trading-system paper cron entry:"
sed -n "/^${BLOCK_BEGIN//\//\\/}$/,/^${BLOCK_END//\//\\/}$/p" "${tmp_crontab}"
