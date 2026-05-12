# Industry Best Correctness Closure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining tail risk between the trading/backtest system and an industry-best correctness standard with executable, fail-closed, auditable implementation slices.

**Architecture:** Treat the remaining gap as evidence, environment, producer-consumer, execution-microstructure, statistical, lifecycle, audit, and rollout-proof risk surfaces. Each phase adds RED→GREEN tests, minimal production code, exact controller verification, broad/full verification, and checkpoint references. Offline-only by default: no real orders, no testnet/live APIs, no cron/service/runtime config changes unless explicitly approved.

**Tech Stack:** Python 3.11 via `/home/cn/.hermes/hermes-agent/venv/bin/python`, pytest, git worktrees, Codex CLI (`sgmini2026.shop` / `gpt-5.5` / medium), trading_system backtest/runtime modules.

---

## Non-Negotiable Execution Rules

1. Use the sanitized verifier environment for controller/broad/full tests:
   - interpreter: `/home/cn/.hermes/hermes-agent/venv/bin/python`
   - clear inherited real `TRADING_*` variables for verification touching config/defaults/full suites.
2. Do not trigger live/testnet/real orders.
3. Start from clean checkpoints only.
4. Split independent work into isolated Codex worktrees; every Codex launch must use `notify_on_complete=true`, `pty=true`, and explicit provider/model/reasoning.
5. Controller must audit every worker commit with `scripts/audit_worker_commit.py --commit HEAD` before integration.
6. Execute the exact audit verification plan, then re-run the same owning verification after cherry-pick on mainline.
7. Run broad regression and full `scripts/nightly_verify.py` after each accepted batch.
8. Push `feat/live-readiness-gates` and `master` only after full-clean.
9. Update `trading-system-operations` checkpoint reference and compact memory after each full-clean batch.

---

## Phase 0: Verification Environment Closure

**Purpose:** Make the Python/PATH/TRADING_* isolation rule executable and regression-tested so verification cannot accidentally run under Homebrew Python or real testnet/prod env.

### Task 0.1: Add sanitized verification wrapper tests

**Files:**
- Modify: `scripts/trading_system_sanitized_verify.sh`
- Test: `trading_system/tests/test_development_workflow.py`
- Test: `trading_system/tests/test_development_workflow_worker_audit.py`

**Steps:**
1. Write tests that prove the wrapper clears representative `TRADING_*` variables while preserving non-trading env.
2. Write tests that prove the wrapper invokes `/home/cn/.hermes/hermes-agent/venv/bin/python` or a configured project venv path rather than ambient Homebrew `python3`.
3. Run targeted workflow tests and expect RED if wrapper lacks assertions.
4. Implement minimal wrapper metadata / dry-run mode if needed.
5. Run targeted workflow tests GREEN.
6. Commit: `Harden sanitized verification environment contract`.

### Task 0.2: Enforce sanitized full verification in scripts/verify.py or nightly path

**Files:**
- Modify: `scripts/verify.py` or `scripts/nightly_verify.py`
- Test: `trading_system/tests/test_development_workflow.py`

**Steps:**
1. Add RED test that full verification plan includes sanitized environment for config-sensitive suites.
2. Implement minimal plan/environment propagation.
3. Ensure `git --no-pager diff --check HEAD` remains unchanged.
4. Run workflow tests.
5. Commit.

---

## Phase 1: Producer Evidence Closure

**Purpose:** Move from strict verifiers to equally strict evidence producers.

### Task 1.1: L2/depth evidence producer manifest strictness

**Files:**
- Modify/create producer under `trading_system/app/backtest/` or existing evidence producer module.
- Test: `trading_system/tests/test_backtest_microstructure_evidence.py`

**Required behavior:**
- Producer emits source, symbol, venue, interval, generated_at, coverage counters, and path-safe artifact references.
- Missing/zero coverage for required intervals is explicit and fail-closed.
- No coercion from strings to numerics for evidence counters.

### Task 1.2: Passive fill calibration artifact producer

**Files:**
- Modify evidence producer/calibration module.
- Test: `trading_system/tests/test_execution_calibration_evidence.py`

**Required behavior:**
- Maker/taker observed fill records must be schema-strict.
- Calibration summary records sample size, latency buckets, spread buckets, and confidence bounds.
- Promotion rejects stale/low-sample calibration.

### Task 1.3: Promotion gate consumes producer identity exactly

**Files:**
- Modify: `trading_system/app/backtest/promotion_evidence_bundle.py`
- Test: `trading_system/tests/test_backtest_promotion_evidence_bundle.py`

**Required behavior:**
- Producer artifact identity must match manifest identity exactly.
- Schema-fatal producer drift creates no verification report artifact in verify-only mode.

---

## Phase 2: Execution Microstructure Closure

**Purpose:** Make backtest fills conservative under incomplete path/microstructure evidence.

### Task 2.1: Same-bar TP/SL conservative ordering expansion

**Files:**
- Modify: `trading_system/app/backtest/execution_sim.py`
- Test: `trading_system/tests/test_backtest_execution_sim.py`
- Test: `trading_system/tests/test_backtest_engine.py`

**Required behavior:**
- When same-bar TP/SL order cannot be proven from intrabar path evidence, choose stop/worse outcome.
- Emit audit reason for conservative ordering.

### Task 2.2: Queue/fill probability lower-bound gate

**Files:**
- Modify execution or microstructure module.
- Test: `trading_system/tests/test_backtest_microstructure_evidence.py`

**Required behavior:**
- Maker fills require evidence of touch/queue/fill eligibility.
- Missing queue evidence degrades to taker/worse fill or rejects promotion depending on mode.

### Task 2.3: Slippage stress matrix evidence

**Files:**
- Modify reporting/evidence module.
- Test: `trading_system/tests/test_backtest_validation_evidence.py`

**Required behavior:**
- Backtest report includes spread/volatility/liquidity stress slices.
- Promotion requires passing stress envelope.

---

## Phase 3: Statistical Robustness Closure

**Purpose:** Prove outputs are not fragile to sampling or parameter perturbations.

### Task 3.1: Walk-forward/OOS coverage manifest

**Files:**
- Modify experiment/evaluation module.
- Test: `trading_system/tests/test_backtest_evaluation.py`
- Test: `trading_system/tests/test_backtest_regime_experiments.py`

**Required behavior:**
- Evaluation records train/test split identity, embargo period, regime buckets, and symbol coverage.
- Noncanonical split metadata fails closed.

### Task 3.2: Parameter perturbation / bootstrap report schema

**Files:**
- Modify evaluation/reporting module.
- Test: `trading_system/tests/test_backtest_ablation_experiments.py`

**Required behavior:**
- Report median, drawdown, tail loss, and confidence intervals.
- Promotion rejects high fragility or missing bootstrap evidence.

### Task 3.3: False-discovery guard for multi-setup experiments

**Files:**
- Modify setup rewrite / promotion logic.
- Test: `trading_system/tests/test_backtest_setup_rewrite_experiment.py`

**Required behavior:**
- Multiple setup comparisons require correction metadata.
- Missing correction keeps setup quarantined.

---

## Phase 4: Runtime ↔ Backtest Semantic Parity Closure

**Purpose:** Prove runtime signal/risk/execution semantics are replayable and equivalent in backtest.

### Task 4.1: Signal schema parity

**Files:**
- Modify signal/runtime/backtest loader modules.
- Test: `trading_system/tests/test_main_v2_cycle.py`
- Test: `trading_system/tests/test_backtest_dataset.py`

**Required behavior:**
- Runtime signals serialize to backtest input without coercion or field loss.
- Alias conflicts fail before load.

### Task 4.2: Risk config parity manifest

**Files:**
- Modify config/reporting modules.
- Test: `trading_system/tests/test_run_cycle.py`
- Test: `trading_system/tests/test_backtest_validation_evidence.py`

**Required behavior:**
- Backtest report includes exact risk config hash/identity.
- Runtime and backtest risk limits mismatch fails promotion.

### Task 4.3: Execution preview replayability

**Files:**
- Modify executor/reporting/backtest replay modules.
- Test: `trading_system/tests/test_executor.py`
- Test: `trading_system/tests/test_backtest_promotion.py`

**Required behavior:**
- Runtime preview payloads can be replayed by backtest validator.
- Unsupported order semantics are explicit fail-closed reasons.

---

## Phase 5: Portfolio Lifecycle Closure

**Purpose:** Close path-dependent portfolio/order/position correctness gaps.

### Task 5.1: Protective stop lifecycle parity

**Files:**
- Modify portfolio lifecycle / reporting modules.
- Test: `trading_system/tests/test_main_v2_cycle.py`
- Test: `trading_system/tests/test_backtest_portfolio.py`

**Required behavior:**
- Protective stop add/update/cancel lifecycle is ordered and auditable.
- Missing/duplicate stop state fails closed.

### Task 5.2: Open order ↔ position reconciliation

**Files:**
- Modify dataset/runtime account validation.
- Test: `trading_system/tests/test_backtest_dataset.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

**Required behavior:**
- Open orders referencing nonexistent positions or conflicting sides fail before load.
- Reduce-only/close-position semantics must be strict booleans and side-compatible.

### Task 5.3: Margin/funding/liquidation lifecycle stress

**Files:**
- Modify costs/portfolio/risk modules.
- Test: `trading_system/tests/test_backtest_costs.py`
- Test: `trading_system/tests/test_backtest_portfolio.py`

**Required behavior:**
- Funding/liquidation/margin updates have timestamp ordering and positive domain checks.
- Missing liquidation-risk evidence prevents promotion.

---

## Phase 6: Auditability and Third-Party Reproducibility Closure

**Purpose:** Make every promotion decision independently reproducible.

### Task 6.1: Verification run manifest

**Files:**
- Modify: `scripts/nightly_verify.py` / verification reporting.
- Test: `trading_system/tests/test_development_workflow.py`

**Required behavior:**
- Full verification records git sha, Python path/version, env sanitization, test count, diff-check command, and timestamp.

### Task 6.2: Promotion decision reason taxonomy completeness

**Files:**
- Modify promotion/report modules.
- Test: `trading_system/tests/test_backtest_promotion_evidence_bundle.py`

**Required behavior:**
- Every reject/quarantine/pass has stable machine-readable reason codes.
- Unknown reason code fails workflow tests.

### Task 6.3: Artifact reproducibility hash chain

**Files:**
- Modify evidence bundle verifier.
- Test: `trading_system/tests/test_backtest_promotion_evidence_bundle.py`

**Required behavior:**
- Bundle manifest includes deterministic hash chain for artifacts, verification config, and source identities.
- Any mismatch is schema/integrity fatal as appropriate.

---

## Phase 7: Offline Rollout Readiness Closure

**Purpose:** Prepare for future paper/shadow/canary without actually placing orders.

### Task 7.1: Paper/shadow readiness checklist artifact

**Files:**
- Modify live-readiness/reporting modules.
- Test: `trading_system/tests/test_backtest_live_readiness.py`

**Required behavior:**
- Readiness report lists remaining paper/shadow/canary evidence requirements.
- Missing checklist blocks production promotion but not offline evaluation.

### Task 7.2: Kill-switch and stale-data simulation evidence

**Files:**
- Modify runtime safety evidence module.
- Test: `trading_system/tests/test_runtime_safety_evidence.py`

**Required behavior:**
- Offline simulation proves stale data, duplicate submission, API degradation, and kill-switch paths are rejected.

### Task 7.3: Canary guard manifest schema

**Files:**
- Modify promotion/live-readiness modules.
- Test: `trading_system/tests/test_backtest_live_readiness.py`

**Required behavior:**
- Future canary requires explicit max notional, symbol allowlist, timeout, rollback, and alerting evidence.
- Missing guard fields fail closed.

---

## Batch Execution Plan

- Batch84: Phase 0 + one small Phase 6 auditability slice.
- Batch85: Producer evidence closure slices.
- Batch86: Execution microstructure closure slices.
- Batch87: Statistical robustness closure slices.
- Batch88: Runtime/backtest semantic parity slices.
- Batch89: Portfolio lifecycle closure slices.
- Batch90: Auditability/reproducibility closure slices.
- Batch91: Offline rollout readiness closure slices.

Each batch follows:
1. Start from clean full checkpoint.
2. Create 2-3 isolated worktrees from current HEAD.
3. Write prompt files and read back for truncation.
4. Launch Codex workers with notify guard.
5. Audit completed workers.
6. Exact verify worker commit.
7. Cherry-pick accepted commits one by one.
8. Mainline exact after each cherry-pick.
9. Broad regression.
10. Full verification.
11. Push feature/master.
12. Checkpoint skill + memory.
13. Continue next batch.
