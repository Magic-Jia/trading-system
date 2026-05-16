# Professional Trading/Backtest Gap Closure Ledger

Date: 2026-05-16
Baseline branch: `feat/live-readiness-gates`
Baseline HEAD at ledger creation: `a9931694 test: enforce setup rewrite symbol summary contracts`
Side-effect boundary: offline/local only. No real orders, no testnet orders, no exchange API calls, no service/cron/runtime activation unless CN explicitly expands scope in a later turn.

This ledger turns the gap assessment into executable hardening tracks. It is intentionally stricter than a normal roadmap: every item must become RED→GREEN tests, fail-closed producer/consumer contracts, controller-side verification, and checkpoint evidence before it can be marked closed.

## Current strongest areas

- Test discipline and controller verification workflow.
- Fail-closed serialized payload contracts.
- Walk-forward/reporting/evaluation timestamp and shape contracts.
- Execution evidence timestamp interval checks across several producer-consumer boundaries.
- Setup-rewrite summary/source/by-symbol contract hardening.

## Remaining institutional-grade closure tracks

### Track A — Market microstructure realism

Goal: replace optimistic or underspecified fill assumptions with conservative exchange-realistic evidence.

Closure requirements:

1. Depth-aware taker fill model consumes order-book levels and emits price/quantity/notional/impact provenance.
2. Maker/passive fill model requires queue/touch/fill eligibility evidence; missing queue evidence degrades conservatively or blocks promotion.
3. Partial fill and missed fill are first-class outcomes, not silent full-fill assumptions.
4. Latency model records signal, decision, submit, exchange ack, first fill, last fill, cancel ack timestamps.
5. Same-bar/intrabar TP/SL ambiguity remains conservative and becomes path-evidence driven where tick/L2 data exists.
6. Fee/funding/rebate/tier evidence is time-varying and tied to account/venue/symbol state.

Initial frontier candidates:

- `microstructure-depth-fill-contracts`
- `maker-queue-evidence-required`
- `partial-fill-consumer-boundaries`
- `latency-timestamp-lifecycle-contracts`
- `fee-funding-timevarying-provenance`

### Track B — Audit-grade data lineage and as-of correctness

Goal: prove every feature, universe, label, and report value only uses information available as of the decision timestamp.

Closure requirements:

1. Dataset manifests expose raw hash, importer version, config hash, generated artifact hash, exchange/venue/symbol/timeframe/coverage identity.
2. Coverage matrix identifies missing/duplicate/overlapping/maintenance/outage intervals by series.
3. Historical universe membership is as-of and includes listed/delisted/renamed/contract-migrated instruments.
4. Feature timestamp, funding/OI/mark/index timestamp, regime label timestamp, LLM label timestamp, and decision timestamp are explicitly ordered.
5. Report aggregation cannot write OOS/post-hoc information back into IS decisions.
6. Producer-consumer boundaries reject noncanonical timestamps, bool numeric values, NaN/inf, duplicate observed_at, and coercive string numerics.

Initial frontier candidates:

- `dataset-asof-decision-timestamp-contracts`
- `universe-survivorship-asof-contracts`
- `feature-label-lookahead-boundary-contracts`
- `manifest-hash-lineage-contracts`
- `coverage-matrix-gap-identity-contracts`

### Track C — Statistical robustness / anti-overfit

Goal: prove candidate results are not parameter-mined or sample-fragile.

Closure requirements:

1. Purged/embargoed walk-forward split metadata.
2. Parameter perturbation/stability surface reporting; promotion rejects isolated spike optima.
3. Regime-stratified OOS results by volatility/liquidity/funding/crash/squeeze buckets.
4. Multiple-testing correction metadata for multi-setup and parameter sweeps.
5. Deflated Sharpe / conservative false-discovery or equivalent guardrail where applicable.
6. Paper/live-shadow drift reports before any promotion claim.

Initial frontier candidates:

- `walkforward-purged-embargo-contracts`
- `parameter-stability-surface-schema`
- `multiple-testing-correction-required`
- `regime-stratified-oos-contracts`

### Track D — Portfolio, margin, liquidation, and risk realism

Goal: make account path and risk controls realistic enough to reject unsafe strategies before promotion.

Closure requirements:

1. Isolated/cross margin state, maintenance tier, unrealized PnL, liquidation price, and funding accrual are path-dependent.
2. Portfolio-level net/gross exposure, correlation/concentration, and crowded-risk limits are auditable.
3. Dynamic sizing responds to liquidity, volatility, drawdown, and observed execution degradation.
4. Flash crash, exchange outage, WebSocket lag, REST limit, cancel failure, and stuck partial-order replay scenarios are represented as tests or fixtures.
5. Hard kill-switch contracts reject stale data, stale account, clock skew, max daily loss, max order count, max notional, and ambiguous exchange state.

Initial frontier candidates:

- `margin-liquidation-path-contracts`
- `portfolio-correlation-exposure-contracts`
- `dynamic-sizing-liquidity-volatility-contracts`
- `kill-switch-stale-clock-account-contracts`

### Track E — Production execution state machine and reconciliation

Goal: make runtime order/account state auditable and recoverable before any live/testnet expansion.

Closure requirements:

1. Event chain: signal → order intent → risk check → submit → ack → fill → position reconcile.
2. Order state machine covers new/accepted/partially_filled/filled/canceled/cancel_pending/rejected/expired/unknown.
3. Unknown exchange state fails closed.
4. Local ledger reconciles against order/trade/position/account snapshots.
5. Structured logs, metrics, traces, alerting, and incident replay bundle exist for each critical path.
6. Research/paper/testnet/prod configs and API permissions remain isolated with explicit production gates.

Initial frontier candidates:

- `order-state-machine-unknown-failclosed`
- `ledger-exchange-reconciliation-contracts`
- `runtime-incident-bundle-schema`
- `environment-permission-isolation-contracts`

### Track F — Investment-committee-grade reporting

Goal: make reports explain why the system wins or fails, not just whether aggregate metrics look good.

Closure requirements:

1. PnL attribution by entry alpha, exit alpha, sizing, fees, funding, slippage, regime, symbol selection.
2. Capacity analysis estimates capital limits and impact deterioration by symbol/liquidity regime.
3. Drawdown anatomy identifies regime/symbol/trade clusters and whether the failure is edge, execution, or risk control.
4. Tail risk report includes CVaR, worst-N days/trades, stress loss, liquidation proximity, correlated loss clusters.
5. Per-trade decision audit records entry/exit reason and all as-of evidence used.

Initial frontier candidates:

- `pnl-attribution-schema-contracts`
- `capacity-impact-report-contracts`
- `drawdown-anatomy-contracts`
- `tail-risk-report-contracts`
- `decision-audit-asof-evidence-contracts`

## Execution policy

1. Start with P0 tracks A and B because they determine whether PnL and evidence are trustworthy.
2. Use isolated Codex worktrees for independent slices; no nested agents inside workers.
3. Every slice must start with a failing test and land the smallest fail-closed implementation.
4. Worker commits require `scripts/audit_worker_commit.py --commit <hash>` and exact emitted verification plan before cherry-pick.
5. Mainline must rerun exact verification after cherry-pick, then `git diff --check HEAD`.
6. Run full suite at bounded checkpoints, not after every tiny discovery if a bounded orchestrator is already managing the batch.
7. Do not mark a track closed based on docs alone; docs only create the ledger. Closure requires tests and production behavior.

## First batch selected

P0 first batch begins with two independent offline slices:

1. `dataset-asof-decision-timestamp-contracts`: find a genuine dataset/report/evaluation boundary where timestamped evidence can bypass as-of ordering and add fail-closed tests.
2. `microstructure-depth-fill-contracts`: find a genuine execution/microstructure boundary where depth/fill evidence can be missing, malformed, non-finite, or overoptimistic and add fail-closed tests.

Both slices must avoid live/testnet calls and exchange APIs.


## Closure progress log

Controller policy: entries below are marked only after isolated worker verification, mainline cherry-pick, exact mainline verification, and `git diff --check HEAD` pass. Full-suite checkpoints remain bounded batch evidence.

### Closed slices

- `microstructure-depth-fill-contracts` — `886246b5`, fail-closed depth fill provenance contracts.
- `dataset-asof-decision-timestamp-contracts` — `c0267d2c`, fail-closed dataset as-of decision timestamp contracts.
- `partial-fill-consumer-boundaries` — `04c4e263`, partial-fill producer/consumer contracts.
- `feature-label-lookahead-boundary-contracts` — `90ffa8d7`, feature/label lookahead contracts.
- `manifest-hash-lineage-contracts` — `73bbf31a`, manifest hash lineage contracts.
- `maker-queue-evidence-required` — `0b93efc1`, maker queue evidence required for passive fills.
- `coverage-matrix-gap-identity-contracts` — `a9a8ae43`, raw market coverage gap identity.
- `universe-survivorship-asof-contracts` — `afb0aa93`, universe as-of survivorship contracts.
- `parameter-stability-surface-schema` — `a2031b18`, parameter stability surface metadata gate.
- `multiple-testing-correction-required` — `cc8aeeb4`, multiple testing correction gate.
- `walkforward-purged-embargo-contracts` — `d3b86271`, purged/embargo walk-forward split metadata gate.
- `latency-timestamp-lifecycle-contracts` — `41db3c0a`, execution lifecycle timestamp evidence.
- `fee-funding-timevarying-provenance` — `9a5c861a`, time-varying fee/funding provenance gate.
- `regime-stratified-oos-contracts` — `4367194d`, regime-stratified OOS promotion gate.
- `margin-liquidation-path-contracts` — `ff59f93c`, margin/liquidation path evidence gate.
- `order-state-machine-unknown-failclosed` — `c7d574c0`, unknown order lifecycle state fail-closed gate.
- `pnl-attribution-schema-contracts` — `9ae6df83`, PnL attribution evidence gate.
- `dynamic-sizing-liquidity-volatility-contracts` — `ca784b45`, dynamic sizing evidence contracts.
- `portfolio-correlation-exposure-contracts` — `5daab745`, portfolio correlation/exposure promotion frontier.
- `kill-switch-stale-clock-account-contracts` — `5ac887eb`, stale clock/account kill-switch evidence contracts.
- `ledger-exchange-reconciliation-contracts` — `fec2e099`, local ledger/exchange reconciliation contracts.
- `runtime-incident-bundle-schema` — `dd73f6aa`, runtime incident bundle schema contract.
- `environment-permission-isolation-contracts` — `ae0600a7`, environment permission isolation contracts.
- `capacity-impact-report-contracts` — `f7bcd81a` + `af6ebc00`, live-readiness capacity impact and promotion capacity gates.
- `drawdown-anatomy-contracts` — `02406954`, drawdown anatomy experiment/reporting/promotion contracts.
- `tail-risk-report-contracts` — `34430f5a` + `2cbdd1ae`, tail-risk promotion and report evidence contracts.

### Remaining frontier

- Track C: `deflated-sharpe-false-discovery-contracts`, `paper-live-shadow-drift-contracts`.
- Track D: exchange stress/replay fixtures for flash crash, outage, websocket lag, REST limit, cancel failure, and stuck partial order.
- Track F: `decision-audit-asof-evidence-contracts`.
