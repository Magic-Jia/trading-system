# Testnet Phase 1 Runbook

Approved minimal Phase 1 scope = **one-shot only**.

- Default behavior stays preview-only: `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0`.
- No real order submission is performed by default.
- Continuous-running entry/path is **deliberately deferred to Phase 2 by this approved minimal-delivery scope**.
- This runbook covers the one-shot Phase 1 operator flow only.

## 1. Credentials and required environment

Primary env file path:

- `/home/cn/.local/secrets/binance-testnet.env`

Recommended file contents:

```bash
export BINANCE_TESTNET_API_KEY=your_testnet_key
export BINANCE_TESTNET_API_SECRET=your_testnet_secret
```

Required env vars for Phase 1 one-shot runs:

- `TRADING_EXECUTION_MODE=testnet`
- `BINANCE_USE_TESTNET=1`
- `BINANCE_FAPI_URL=https://testnet.binancefuture.com`
- `BINANCE_TESTNET_API_KEY`
- `BINANCE_TESTNET_API_SECRET`
- `TRADING_TESTNET_ALLOWED_SYMBOLS=BTCUSDT` (example allowlist)
- `TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT=500` (example cap)
- `TRADING_TESTNET_MAX_OPEN_POSITIONS=1` (example cap)
- `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0` (Phase 1 default)
- `TRADING_STATE_FILE=trading_system/data/runtime_state.testnet.json` (example output path)

Important startup behavior:

- The config layer requires the testnet credentials to be present in the current shell environment before startup.
- In practice, source `binance-testnet.env` first so both config guardrails and signed requests see the same credentials.
- Startup rejects missing or half-configured credentials.
- Startup rejects production Futures URLs; if the process starts in testnet mode, the `production-endpoint assertion` has passed.

## 2. One-shot command

Example one-shot invocation:

```bash
set -a
. /home/cn/.local/secrets/binance-testnet.env
set +a

TRADING_EXECUTION_MODE=testnet \
BINANCE_USE_TESTNET=1 \
BINANCE_FAPI_URL=https://testnet.binancefuture.com \
TRADING_TESTNET_ALLOWED_SYMBOLS=BTCUSDT \
TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT=500 \
TRADING_TESTNET_MAX_OPEN_POSITIONS=1 \
TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0 \
TRADING_STATE_FILE=trading_system/data/runtime_state.testnet.json \
python -m trading_system.app.main
```

Operator note:

- This is a one-shot run, not a daemon, scheduler, or continuous loop.
- Keep `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0` unless you are intentionally testing submission-readiness on testnet.

## 3. What the run writes and where to inspect it

The one-shot run writes the key Phase 1 outputs into `TRADING_STATE_FILE`.

Primary locations:

- `testnet_preflight`: signed connectivity and account-mode checks
- `validated_order_preview`: the preview-only order package and local validation result
- `precheck_summary`: top-level operator summary for the run outcome

The one-shot stdout summary includes:

- `execution_mode: testnet`
- `precheck_summary.status`
- a compact `validated_order_preview` subset (`symbol`, `side`, `qty`, `preview_source`, `submission_prerequisites_passed`, `would_submit`)

## 4. How to read `testnet_preflight`

`testnet_preflight` is the operator truth source for the signed interface and account preflight.

Expected Phase 1 checks:

- `server-time` check: inspect `server_time_ms`
- local clock capture: inspect `local_time_ms`
- timestamp skew / `recvWindow` result: inspect `timestamp_skew_ms`, `recv_window_ms`, and `timestamp_skew_ok`
- signed connectivity: inspect `signed_connectivity_ok`
- position mode: expect `position_mode=one-way` and `position_mode_ok=true`
- margin mode: expect `single_asset_mode=single-asset` and `single_asset_mode_ok=true`
- cross-margin expectation: expect `cross_margin_mode=cross` and `cross_margin_mode_ok=true`
- aggregate account-mode result: inspect `mode_checks_passed`

Account identifier / permission reporting:

- If `testnet_preflight.account_identifier` is present, report it verbatim in the operator note for the run.
- When submission-readiness is enabled, `testnet_preflight.permission_check` is also reported.
- `permission_check.can_trade=true` means the signed account response says trading is allowed.
- `permission_check.can_trade=false` means the response was not submission-ready; inspect `permission_check.reason`.
- In the default preview-only path with `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0`, `permission_check` may be absent.

## 5. How to read `validated_order_preview`

`validated_order_preview` is the operator truth source for the preview package.

What it contains:

- selected `symbol`, `side`, and `qty`
- `order_types`
- generated payload mapping in `payloads`
- `preview_source`
- `local_validation_passed`
- `submission_enabled`
- `submission_prerequisites_passed`
- `would_submit`
- `reasons`

Phase 1 guardrails actually implemented in this slice:

- allowlist enforcement via `TRADING_TESTNET_ALLOWED_SYMBOLS`
- max entry notional enforcement via `TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT`
- required testnet config cap via `TRADING_TESTNET_MAX_OPEN_POSITIONS`
- exchange metadata checks for supported order types, quantity step size, price tick size, and minimum notional
- fixed payload validation for entry / stop / take-profit mapping

Leverage note:

- No separate leverage submission guardrail is implemented in this Phase 1 slice.
- Existing account snapshots may still carry leverage fields for reporting, but leverage is not blocked by a dedicated Phase 1 preview validator here.

Interpretation:

- `local_validation_passed=true` means the preview payload passed the implemented local guardrails.
- `would_submit=true` requires both `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=1` and a clean preview.
- With the approved default `TRADING_TESTNET_ORDER_SUBMISSION_ENABLED=0`, `would_submit` should stay `false` even when preview validation passes.

## 6. Manual troubleshooting order

Use this order exactly:

1. endpoint
   - Confirm `BINANCE_USE_TESTNET=1`.
   - Confirm `BINANCE_FAPI_URL=https://testnet.binancefuture.com`.
   - If startup passed, the `production-endpoint assertion` already passed.
2. credentials
   - Re-source `/home/cn/.local/secrets/binance-testnet.env`.
   - Confirm both `BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_API_SECRET` are exported.
3. position mode
   - Confirm one-way mode and single-asset mode.
   - Confirm cross margin is verifiable and acceptable for the current account state.
4. metadata
   - Confirm the symbol is in `TRADING_TESTNET_ALLOWED_SYMBOLS`.
   - Confirm exchange metadata exists and supports the required order types.
5. preview validation
   - Read `validated_order_preview.reasons` in full.
   - Fix the first blocking reason before rerunning.

## 7. Manual handling for unsupported or ambiguous account failures

Phase 1 does **not** auto-resolve unsupported or ambiguous account preflight states.

Manual handling rules:

- If signed connectivity fails, stop and fix endpoint or credentials first.
- If position mode is hedge, switch to one-way before rerunning.
- If multi-assets mode is enabled, switch back to single-asset before rerunning.
- If cross-margin verification is unsupported or ambiguous, stop and inspect the account manually before proceeding.
- If the run surfaces an interface failure with optional `account_identifier` or `permission_check`, record both in the operator note before retrying.

## 8. Phase boundary

This runbook is intentionally narrow:

- Phase 1 = one-shot preflight plus `validated_order_preview`
- default path = preview-only, no real order submission
- continuous-running orchestration = deferred to Phase 2
