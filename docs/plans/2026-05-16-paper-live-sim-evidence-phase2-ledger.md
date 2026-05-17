# Paper/Live-Sim Evidence Phase 2 Ledger

Status: phase10_paper_calibration_logging_full_green
Base: bbd3c0ab
Scope: simulated live / paper-live only. In this project context `live` means simulated live unless explicitly stated otherwise.

## Goal

Move from offline fail-closed contracts to operational evidence loops for simulated live/paper-live trading. The system should produce and consume daily/periodic evidence that validates execution assumptions, drift, reconciliation, and promotion readiness.

## Initial frontiers

- `paper-live-sim-evidence-bundle`: collect canonical simulated-live evidence bundle with signal/order/risk/ack/fill/reconcile, paper/shadow snapshots, freshness, lineage, and fail-closed schema checks.
- `tca-calibration-report`: compare backtest assumptions against simulated-live observations: slippage, fill probability, maker/taker, latency, partial fills, adverse selection, fees/funding, reject reasons.
- `daily-quality-gate-report`: produce machine-readable daily decision (`pass_for_continued_paper`, `hold_for_review`, `reject_live_promotion`) from evidence bundle + TCA + drift/reconcile/latency checks.

## Acceptance

- RED→GREEN tests for each frontier.
- Production code and consumer wiring, not fixture-only tests.
- No real-money or real-exchange side effects.
- Simulated live/paper-live operations are allowed.
- Worker exact audit and mainline exact audit for every integrated commit.
- Bounded full-suite checkpoint after each batch.


## Completed batch 1

- `tca-calibration-report` — `9588d590`, simulated-live TCA calibration report; main exact 4047 passed + diff-check.
- `daily-quality-gate-report` — `3bd39d1f`, machine-readable daily quality gate and testnet daily report surfacing; focused exact 8 passed + diff-check.
- `paper-live-sim-evidence-bundle` — `f6497387`, canonical paper/live-sim evidence bundle wired into live-readiness and promotion bundle; focused exact 966 passed + diff-check.

## Next frontiers

- Wire daily scheduled generation for simulated-live evidence bundle, TCA report, and daily quality gate from current runtime artifacts.
- Add longitudinal trend report over multiple simulated-live days.
- Add alert/hold workflow for quality-gate regressions.


## Completed batch 2

- `scheduled-live-sim-generation` — `754df312`, periodic generation for paper/live-sim evidence bundle, TCA calibration, and daily quality gate; main exact 1800 passed + diff-check.
- `longitudinal-live-sim-trend-report` — `e71f09ce`, multi-day trend report over quality gate, TCA, drift, reconciliation, latency, slippage, fill quality, and freshness; focused exact 4 passed + diff-check.
- `quality-gate-alert-hold-workflow` — `32fdf5d3`, quality gate regression hold/alert workflow surfaced in testnet daily report; focused exact 16 passed + diff-check.

## Next frontiers after batch 2

- Run the scheduled simulated-live generation against real local simulated runtime artifacts and inspect produced evidence.
- Add external data-source cross-checks if multiple simulated/live feeds are available.
- Add operator runbook/acknowledgement persistence if hold workflows need human release tracking beyond JSON artifacts.


## Real local simulated-runtime verification

- `ff9a3d5c` added bootstrap from legacy local simulated artifacts into Phase 2 runtime bucket inputs.
- `58543ed2` accepts explicit canonical decimal-string exchange fields such as liquidation price without silent coercion.
- `670af564` handles missing legacy `account_snapshot.as_of` by emitting freshness/quality reasons rather than fabricating source timestamps.
- `2a4427bc` derives missing top-level account equity from futures total margin balance with provenance.
- `9fcea0f9` handles recommendation-only legacy `paper_trades.jsonl` without fabricating TCA records; scheduled generation writes `daily_quality_gate_report.json` as `hold_for_review` with `data_freshness_violation`, `calibration_records_unavailable`, and `insufficient_sample_size`.
- Real local command verification: bootstrap + scheduled generation completed with `status=ok`; produced evidence bundle and daily quality gate; no bootstrap/scheduled error artifact remained; TCA report intentionally absent because real calibration records are unavailable.

## Remaining operational frontier

- Collect or generate genuine execution calibration records from simulated live order/fill logs so TCA can move from `calibration_records_unavailable` hold to measured pass/fail.
- If needed, persist operator acknowledgement/release workflow beyond JSON artifacts.
- Add external feed cross-checks only when independent simulated/live feeds are available.


## Phase 3 execution calibration records

- `bdd663e5` wires valid `passive_order_calibration_records.jsonl` into scheduled generation so measured TCA replaces `calibration_records_unavailable`; stale unavailable markers no longer pollute measured paths; empty records without unavailable marker fail closed. Main exact: 1725 passed + diff-check.
- `fa76863e` adds `generate_execution_calibration_records` to convert canonical simulated execution event chains into loader-compatible passive order calibration JSONL. It rejects malformed lifecycle stages, timestamp/identity mismatch, duplicate trade identity, bool/non-finite/coerced numerics, impossible fills, and ambiguous maker/taker. Focused main exact: 128 passed + diff-check.
- End-to-end temp runtime verification: execution_log + paper_ledger -> 1 calibration record -> scheduled generation produced calibration summary, TCA report, and daily gate with no `calibration_records_unavailable` marker. Gate moved from unavailable-hold to measured decision; sample was rejected for measured slippage threshold (`tca_slippage_exceeds_threshold`), which is correct measured-path behavior rather than missing-evidence behavior.


## Phase 4 rolling TCA durability gate

- `e07df9c3` adds `rolling_tca_durability_report.v1` and CLI `trading_system.generate_rolling_tca_durability_report` for rolling/bucketed TCA durability over calibration records. It supports deterministic windows/buckets and fail-closed malformed record/date handling. Focused main exact: 91 passed + diff-check.
- `6f592ae5` wires rolling durability inputs into scheduled generation, daily quality gate, and longitudinal trend. Focused worker exact: 34 passed + diff-check.
- Integration fixes `5a13a0ba` and `40d1bf75` align producer/consumer contracts: scheduled generation reads explicit rolling artifacts, and daily gate accepts producer decisions (`durable`, `insufficient`, `rejected`) mapped to pass/hold/reject semantics. Combined exact: 125 passed + diff-check.
- End-to-end temp runtime verification: calibration records -> rolling report CLI -> scheduled generation -> daily gate. Durable rolling report is included in generated artifacts, parsed as passed rolling durability, and no scheduled error artifact is emitted.


## Phase 6 exchange-realism evidence frontier

- `23cb0352` adds versioned venue rulebook constraints and provenance-aware constraint reports. Main exact: 450 passed + diff-check.
- `fb399050` adds deterministic L2 event replay diagnostics for order-book reconstruction, gap/crossed-book detection, and replay reason codes. Main exact: 4188 passed + diff-check.
- `cfbcfc66` adds latency stress calibration summary and conservative latency/slippage stress evidence. Main exact: 2010 passed + diff-check.
- Final Phase 6 full-suite checkpoint: `scripts/verify.py --suite full` passed 6356 tests; `git --no-pager diff --check HEAD` clean.

## Phase 7 critical-gap closure frontier

Status: `full_green` at `a7c69fab` on `feat/live-readiness-gates`.

Side-effect boundary: offline/local simulated-live evidence only. No real orders, no testnet orders, no exchange API calls, and no live credential use.

Closed critical gaps:

- `5c28e52f` adds venue rulebook catalog coverage over venue/symbol/product type with fail-closed malformed, stale, duplicate, and provenance checks. Main exact: 461 passed + diff-check.
- `4ab9bfeb` adds derivatives risk evidence for margin mode, position mode, leverage, funding, liquidation estimate, and ADL bucket with fail-closed invalid/stale/duplicate funding evidence. Main exact: 4213 passed + diff-check.
- `690a2148` adds longitudinal L2 replay calibration evidence across samples/sessions, including gap rate, crossed-book rate, stale rate, depth medians/maxima, and review/hold reason codes. Main exact: 4129 passed + diff-check.
- `58e64380` adds execution race-condition evidence for cancel/fill/replace ordering, late fills after cancel request/ack, terminal-status conflicts, and hold-for-review race decisions. Main exact: 2043 passed + diff-check.
- `4ba74e54` maps longitudinal trend report verification impact after worker audit exposed unmapped workflow coverage. Workflow fix was landed separately before accepting the cross-source product commit.
- `a7c69fab` adds cross-source parity drift evidence across bid/ask/last/volume/latency, missing-source intervals, insufficient overlap, duplicate source identity, stale samples, and crossed quotes. Main exact: 4156 passed + diff-check.

Final Phase 7 full-suite checkpoint:

- `python3 scripts/verify.py --suite full` passed 6418 tests in 234.92s.
- `git --no-pager diff --check HEAD` clean.
- Branch status at checkpoint: `feat/live-readiness-gates...origin/feat/live-readiness-gates [ahead 284]`.

Next operational frontier: Phase 8 should move from one-shot offline correctness closure to rolling simulated-live evidence: canonical rolling evidence bundles, promotion readiness scorecards, and calibration feedback loops that turn live-sim observations into backtest/execution-model calibration inputs.

## Phase 8 rolling simulated-live evidence loop

Status: `batch1_full_green` at `f22dc469` on `feat/live-readiness-gates`.

Side-effect boundary: offline/local simulated-live evidence only. No real orders, no testnet orders, no exchange API calls, and no live credential use.

Closed Batch 1 frontiers:

- `fac49a20` adds rolling simulated-live evidence bundle generation and reporting. Main exact: 2173 passed + diff-check.
- `201f8e08` adds promotion readiness scorecard with reporting and scheduled-generation impact wiring. Main exact: 2198 passed + diff-check.
- `f22dc469` adds simulated-live calibration feedback artifact so observed live-sim evidence can feed back into execution/backtest calibration. Main exact: 2097 passed + diff-check.

Final Phase 8 Batch 1 full-suite checkpoint:

- `python3 scripts/verify.py --suite full` passed 6446 tests in 251.28s.
- `git --no-pager diff --check HEAD` clean.
- Branch status at checkpoint: `feat/live-readiness-gates...origin/feat/live-readiness-gates [ahead 289]`.

Remaining operational frontier: keep running/accumulating rolling simulated-live evidence over multiple days/sessions, then use the scorecard and calibration feedback to decide whether the backtest/execution assumptions are stable enough for a future promotion gate. External independent-feed checks and operator acknowledgement persistence remain conditional on available feeds and workflow need.

## Phase 8 Batch 2 rolling evidence hardening

Status: `batch2_full_green` at `b434dc22` on `feat/live-readiness-gates`.

Side-effect boundary: offline/local simulated-live evidence only. No real orders, no testnet orders, no exchange API calls, and no live credential use.

Closed Batch 2 frontiers:

- `b72625ec` adds simulated-live evidence window continuity gate across rolling bundles, including distinct-session sufficiency, duplicate identity rejection, timestamp monotonicity, and machine-readable hold/reject reason propagation. Worker exact: 2194 passed + diff-check. Main exact: 2194 passed + diff-check.
- `406bdac4` adds promotion readiness scorecard trend reporting over multiple scorecards, including deterioration detection, repeated blocker surfacing, malformed scorecard rejection, duplicate identity rejection, and insufficient-window hold behavior. Worker exact: 513 passed + diff-check. Main exact: 513 passed + diff-check.
- `b434dc22` adds calibration assumption update recommendations from simulated-live calibration feedback without mutating canonical TCA assumptions. It produces auditable human-review recommendations and preserves fail-closed malformed/stale/input checks. Worker exact: 2190 passed + diff-check. Main exact: 2192 passed + diff-check.

Final Phase 8 Batch 2 full-suite checkpoint:

- `python3 scripts/verify.py --suite full` passed 6473 tests in 215.23s.
- `git --no-pager diff --check HEAD` clean.
- Branch status before ledger commit: `feat/live-readiness-gates...origin/feat/live-readiness-gates [ahead 3]`.

Remaining operational frontier: continue accumulating real local simulated-live evidence across multiple independent sessions/days, then use the rolling evidence window, scorecard trend, and calibration recommendation artifacts as promotion gate inputs. Independent external-feed cross-checks and operator acknowledgement persistence remain conditional on available feeds and workflow need.

## Phase 9 Batch 1 continuous simulated-live evidence closure

Status: `batch1_full_green` at `c3f3edb9` on `feat/live-readiness-gates` before ledger commit.

Side-effect boundary: offline/local simulated-live evidence only. No real orders, no testnet orders, no exchange API calls, and no live credential use.

Closed Batch 1 frontiers:

- `7c72e167` adds a real-local simulated-live evidence chain checkpoint that consumes local evidence-window and scorecard-trend artifacts, records explicit lineage/source paths, requires `source_mode=simulated_live_local`, and fails closed for malformed/missing/replay inputs. Worker exact: 2211 passed + diff-check. Main exact: 2211 passed + diff-check.
- `e217f576` adds the promotion gate decision report that aggregates simulated-live evidence window, scorecard trend, and calibration artifacts into a single `reject` / `hold` / `candidate_for_paper_promotion` decision with blocking reasons, lineage, provenance summary, and human-review enforcement for calibration recommendations. Worker exact: 2218 passed + diff-check. Main exact: 2219 passed + diff-check.
- `c3f3edb9` adds replay provenance for simulated-live evidence windows, including explicit `source_mode=replay`, replay lineage, replay-only window mode, and fail-closed handling for mixed/missing/invalid source modes so replay artifacts cannot masquerade as real local simulated-live evidence. Worker exact: 2216 passed + diff-check. Main exact: 2220 passed + diff-check.

Final Phase 9 Batch 1 full-suite checkpoint:

- `python3 scripts/verify.py --suite full` passed 6501 tests in 204.38s.
- `git --no-pager diff --check HEAD` clean.
- Branch status before ledger commit: `feat/live-readiness-gates...origin/feat/live-readiness-gates [ahead 3]`.

Remaining operational frontier: run the new real-local chain and promotion gate against accumulated multi-session simulated-live artifact directories on a cadence, store those promotion decisions as longitudinal evidence, and only then evaluate whether external independent-feed checks or operator acknowledgement persistence are needed for the next promotion threshold.


## Phase 9 Batch 2 cadence promotion evidence loop

Status: `batch2_full_green` at `d1363e70` on `feat/live-readiness-gates` before ledger commit.

Side-effect boundary: offline/local simulated-live evidence only. No real orders, no testnet orders, no exchange API calls, and no live credential use.

Closed Batch 2 frontiers:

- `0d96434a` adds a fail-closed simulated-live artifact inventory for local optimization runtime directories. It records present/missing/malformed Phase 9 artifacts, expected schema versions, hashes, source paths, and returns `hold` until all cadence/promotion inputs are present. Worker exact: 2238 passed + diff-check. Main exact: 2238 passed + diff-check.
- `a6f3b9a9` adds the offline simulated-live cadence runner that chains rolling bundle -> evidence window -> promotion scorecard -> scorecard trend -> real-local chain checkpoint -> promotion gate decision, while failing closed and persisting a cadence result when required local artifacts are missing or replay provenance is detected. Worker exact: 2000 passed + diff-check. Main exact: 2000 passed + diff-check.
- `4b23cf50` adds a longitudinal promotion decision archive that stores multiple promotion decisions across sessions/days, rejects duplicate identities and malformed inputs, and summarizes decision history for promotion review. Worker exact: 2239 passed + diff-check. Main exact: 2240 passed + diff-check.
- `d1363e70` fixes the cadence runner artifact filename contract so it reuses the inventory/runtime canonical Phase 9 filenames such as `daily_quality_gate_report.json`, avoiding false missing-artifact reports for real local scheduled-generation output. Focused exact: 1916 passed + diff-check.

Final Phase 9 Batch 2 full-suite checkpoint:

- `python3 scripts/verify.py --suite full` passed 6518 tests in 202.31s.
- `git --no-pager diff --check HEAD` clean.
- Real-local smoke on `trading_system/data/runtime/paper/paper/optimization` correctly recognized `daily_quality_gate_report.json` and failed closed with `hold` only for the remaining absent Phase 9 inputs: rolling TCA durability, L2 longitudinal replay calibration, cross-source parity, venue rulebook freshness, execution race evidence, and promotion readiness evidence.

Remaining operational frontier: produce the missing real local Phase 9 input artifacts from actual simulated-live runtime streams, then let the cadence runner generate real-local promotion gate decisions and accumulate them in the longitudinal archive over multiple sessions/days.


## Phase 9 Batch 3 local producer closure

- `0bc5d126` adds local execution-stream producers for `execution_race_evidence.json` and `l2_longitudinal_replay_calibration.json`; main exact: 2257 passed + diff-check.
- `e173fafd` adds local market-coverage producers for `cross_source_parity_report.json` and `venue_rulebook_catalog_freshness.json`; main exact: 2256 passed + diff-check.
- `80a15a02` adds local `promotion_readiness_evidence.json` producer; main exact: 112 passed + diff-check.
- `ed68c698` allows explicit missing/null metrics and boolean indicator fields in simulated-live bundle inputs while preserving bool rejection for true numeric metrics; main exact: 2167 passed + diff-check.
- Real local runtime smoke generated all missing Phase9 input artifacts from `trading_system/data/runtime/paper/paper/optimization/` without exchange/testnet/real-order side effects. Cadence result completed with `decision=hold` and auditable blockers: missing calibration artifact, bundle component hold, data freshness violation, independent source unavailable, insufficient sample sizes, missing dates, L2 depth evidence unavailable, and venue rulebook catalog unavailable.
- Batch checkpoint: `python3 scripts/verify.py --suite full && git --no-pager diff --check HEAD` -> 6538 passed in 205.16s, diff-check clean.

## Next frontiers after Phase 9 Batch 3

- Produce genuine calibration records and multi-day simulated-live dates so cadence blockers can move from explicit hold reasons to measured pass/fail.
- Attach independent market/feed source and venue rulebook catalog sources when available; keep current local producers fail-closed when unavailable.
- Persist human/operator release acknowledgement only after hold reasons are reviewable from multi-day evidence.


## Phase 10 paper execution calibration logging closure — 2026-05-17

Objective: close the P0 gap where paper/simulated-live executions did not emit canonical runtime execution evidence, leaving `passive_order_calibration_records.jsonl` empty even though the calibration generator was implemented.

Integrated commit:
- `8133daea` — `Emit paper execution calibration lifecycle logs`

What changed:
- Paper-mode `OrderExecutor.execute` now writes canonical lifecycle rows to the runtime bucket `execution_log.jsonl` when running under `data/runtime/paper/<runtime_env>/`.
- `PaperExecutor`/`PaperLedger` now support the runtime `paper_ledger.jsonl` path used by the existing calibration generator.
- Legacy execution log behavior is preserved.
- Paper execution path remains offline-only and does not call testnet submission functions.

Evidence:
- Worker commit audit: `scripts/audit_worker_commit.py --commit 1908cc26` returned `status: ok`.
- Worker/mainline exact plan: `463 passed` for executor/run_cycle/main/reporting/validator/testnet_preview/runtime_paths tests plus `git diff --check`.
- Standalone smoke from a local paper execution produced one loader-compatible calibration record: `generated_count=1`, symbol `BTCUSDT`, filled qty `0.01`.
- Full suite after integration: `6540 passed in 208.14s` plus `git --no-pager diff --check HEAD` clean.

Remaining frontier:
- Run actual scheduled paper/simulated-live cycles over time so runtime calibration records accumulate naturally.
- Then rerun cadence/promotion gate and track which hold reasons disappear versus remaining multi-day/independent-source/rulebook/L2 gaps.
