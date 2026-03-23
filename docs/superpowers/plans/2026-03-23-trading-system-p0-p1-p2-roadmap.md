# Trading System P0 / P1 / P2 Roadmap Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current partial-v2 paper-trading system into a production-safe trading program by finishing the real execution, risk, state-recovery, short-execution, and reporting/audit gaps in a sequenced P0 / P1 / P2 roadmap.

**Architecture:** Keep the current modular `trading_system/app/*` structure and treat the recent `paper_verification` hardening work as support infrastructure rather than the main delivery thread. Finish the system in layers: first make the execution/risk/state bottom layer real and restart-safe (P0), then complete short/lifecycle/reporting loops (P1), then add strategy/risk sophistication and evaluation tooling (P2).

**Tech Stack:** Python, pytest, uv, Binance connector layer, JSON state/log artifacts, paper execution runtime, OpenClaw commit notifications.

---

## Current State Snapshot

### Already done

- [x] v2 main paper cycle exists: regime → universe → trend candidates → validation → allocation → paper execution → lifecycle/reporting.
- [x] `rotation engine` is partially integrated into runtime state and summaries.
- [x] `short engine` first version exists for defensive regimes, but execution coverage is still partial.
- [x] `runtime_state.json` persistence and runtime stdout summaries exist.
- [x] `paper_verification` has strong guardrails around malformed inputs, unreadable generated artifacts, path hazards, permission failures, and stderr/stdout contracts.
- [x] `execution_log.jsonl` has been classified as runtime output and ignored.
- [x] Short-management verification and generated artifact handling are now heavily hardened.

### Still not done

- [ ] Real order execution is not production-complete; current system is still fundamentally paper-first.
- [ ] Risk engine is not yet complete enough for real auto-execution (aggregate risk, correlation, kill-switch, hard reject rules, restart-safe exposure checks).
- [ ] State recovery / restart consistency is not yet finished to real-trading level.
- [ ] Short execution chain is not fully enabled end-to-end.
- [ ] Journal / audit / reporting is still weaker than the trading loop itself.
- [ ] Backtest / attribution / evaluation tooling is not yet in place.
- [ ] There is no clean documented “done” line yet for moving from safe simulation to small-scale real execution.

---

## File Map for Remaining Work

### Core execution/risk/state files
- `trading_system/app/main.py` — orchestrates the end-to-end cycle; must become stricter about execution and recovery boundaries.
- `trading_system/app/execution/executor.py` — current execution layer; likely starting point for real-vs-paper execution split hardening.
- `trading_system/app/execution/orders.py` — real order placement/cancel/replace behavior, currently not yet promoted to production-safe completeness.
- `trading_system/app/execution/idempotency.py` — must become a hard gate against duplicate live actions.
- `trading_system/app/risk/validator.py` — candidate-level validation exists; needs stricter account-level reject logic.
- `trading_system/app/risk/guardrails.py` — likely home for account/global risk, exposure, and kill-switch rules.
- `trading_system/app/risk/position_sizer.py` (or allocator-adjacent sizing logic) — needs production-safe size hard caps and loss budgeting.
- `trading_system/app/storage/state_store.py` — state persistence; must be restart-safe and adequate for recovery.
- `trading_system/app/storage/journal_store.py` — trade/audit log store; likely underbuilt relative to operational needs.

### Portfolio / short / reporting files
- `trading_system/app/portfolio/positions.py`
- `trading_system/app/portfolio/lifecycle.py`
- `trading_system/app/reporting/daily_report.py`
- `trading_system/devtools/paper_verification.py`
- `trading_system/tests/test_main_v2_cycle.py`
- `trading_system/tests/test_paper_verification.py`

### Docs that should stay aligned
- `trading_system/README.md`
- `trading_system/docs/MVP_ARCHITECTURE.md`
- `trading_system/runbook.md`

---

## P0 — Must finish before calling this a real automated trading program

**Definition:** Without these, the system may still be useful for simulation and verification, but it is not ready to control real money safely.

### P0.1 Real execution boundary and mode separation

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/execution/executor.py`
- Modify: `trading_system/app/execution/orders.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`
- Test: `trading_system/tests/test_executor.py` (create if missing)

- [ ] **Step 1: Write failing tests for real-vs-paper execution boundaries**
- [ ] **Step 2: Verify current behavior fails or is missing**
- [ ] **Step 3: Implement explicit execution mode boundary so live execution cannot happen implicitly**
- [ ] **Step 4: Verify focused execution tests pass**
- [ ] **Step 5: Commit**

**Why:** Right now the system is still paper-first. A production-safe system needs an explicit and testable boundary between simulation and live execution.

---

### P0.2 Hard risk gate before execution

**Files:**
- Modify: `trading_system/app/risk/validator.py`
- Modify: `trading_system/app/risk/guardrails.py`
- Modify: `trading_system/app/main.py`
- Test: `trading_system/tests/test_validator.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write failing tests for account-level reject rules**
- [ ] **Step 2: Add total-risk / exposure / correlation / no-stop reject rules**
- [ ] **Step 3: Run narrow risk tests**
- [ ] **Step 4: Run cycle tests proving rejected intents do not execute**
- [ ] **Step 5: Commit**

**Why:** Candidate-level validation is not enough. Real automation needs account-level refusal rules that override conviction.

---

### P0.3 Restart-safe state recovery and idempotent execution replay

**Files:**
- Modify: `trading_system/app/storage/state_store.py`
- Modify: `trading_system/app/execution/idempotency.py`
- Modify: `trading_system/app/main.py`
- Test: `trading_system/tests/test_state_store.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write failing restart/replay tests**
- [ ] **Step 2: Persist enough execution identity and recovery state**
- [ ] **Step 3: Prove duplicate execution is blocked across restart/replay**
- [ ] **Step 4: Run focused restart/idempotency tests**
- [ ] **Step 5: Commit**

**Why:** “同一信号不能执行两次” and “程序重启后不能失忆” are core architecture rules, not nice-to-haves.

---

### P0.4 Journal / audit minimum viable truth trail

**Files:**
- Modify: `trading_system/app/storage/journal_store.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/README.md`
- Test: `trading_system/tests/test_journal_store.py` (create if missing)
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write failing tests for mandatory execution rationale and action logging**
- [ ] **Step 2: Persist rationale, invalidation, targets, execution outcome, and linkage to signal/execution ids**
- [ ] **Step 3: Verify audit records survive one complete cycle**
- [ ] **Step 4: Document audit expectations**
- [ ] **Step 5: Commit**

**Why:** Without an audit trail, the system is not safe to operate or improve.

---

## P1 — Complete the strategy/execution loop already implied by the current architecture

### P1.1 Short execution chain end-to-end

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/execution/executor.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`
- Test: `trading_system/tests/test_short_execution.py` (create if missing)

- [ ] **Step 1: Write failing tests proving accepted short intents actually execute in paper/live-safe mode**
- [ ] **Step 2: Remove the current “short_execution_not_enabled” dead-end where appropriate**
- [ ] **Step 3: Verify short entries/exits/management work end-to-end**
- [ ] **Step 4: Commit**

**Why:** Short analysis exists, but the system is still partial until short execution is real.

---

### P1.2 Lifecycle and protective order management completion

**Files:**
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/portfolio/positions.py`
- Modify: `trading_system/app/execution/orders.py`
- Test: `trading_system/tests/test_lifecycle.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write failing tests for stop movement / partial take-profit / invalidation-driven exits**
- [ ] **Step 2: Implement minimal production-safe lifecycle actions**
- [ ] **Step 3: Verify lifecycle action previews match executable behavior**
- [ ] **Step 4: Commit**

**Why:** Entry logic without disciplined exit mechanics is only half a trading system.

---

### P1.3 Operator reporting and daily summary

**Files:**
- Modify: `trading_system/app/reporting/daily_report.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/runbook.md`
- Test: `trading_system/tests/test_reporting.py` (create if missing)

- [ ] **Step 1: Write failing tests for daily/operator summary outputs**
- [ ] **Step 2: Implement concise operator report with actions, exposure, recent execution, and top risks**
- [ ] **Step 3: Verify human-facing report consistency with runtime state/journal**
- [ ] **Step 4: Commit**

**Why:** Once the core loop is safe, the next bottleneck is operator visibility.

---

## P2 — Expand system capability after the core is trustworthy

### P2.1 Backtest / replay / attribution foundation

**Files:**
- Create: `trading_system/app/reporting/attribution.py`
- Create: `trading_system/tests/test_attribution.py`
- Modify: `trading_system/docs/MVP_ARCHITECTURE.md`

- [ ] **Step 1: Define the minimal attribution schema**
- [ ] **Step 2: Add replayable performance summaries**
- [ ] **Step 3: Verify per-trade and per-module attribution outputs**
- [ ] **Step 4: Commit**

**Why:** Without structured attribution, the system can run but cannot learn well.

---

### P2.2 Broader strategy coverage

**Files:**
- Modify: `trading_system/app/signals/strategy_trend.py`
- Modify: `trading_system/app/main.py`
- Test: `trading_system/tests/test_strategy_trend.py`

- [ ] **Step 1: Add multi-timeframe filters or one carefully chosen strategy extension**
- [ ] **Step 2: Verify it improves selection quality without destabilizing execution**
- [ ] **Step 3: Commit**

**Why:** Strategy expansion should happen only after the execution/risk machine is trusted.

---

### P2.3 Production operations polish

**Files:**
- Modify: `trading_system/runbook.md`
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/MVP_ARCHITECTURE.md`

- [ ] **Step 1: Document deploy, restart, rollback, and incident-response paths**
- [ ] **Step 2: Add verification commands for operator checklists**
- [ ] **Step 3: Commit**

**Why:** A system that can trade but cannot be operated safely is still unfinished.

---

## Recommended sequencing

### Immediate next recommendation
1. **P0.1 Real execution boundary and mode separation**
2. **P0.2 Hard risk gate before execution**
3. **P0.3 Restart-safe state recovery and idempotent replay**
4. **P0.4 Journal / audit minimum viable truth trail**
5. Then move into **P1 short execution chain** and **lifecycle completion**

### Why this order
- Recent work has heavily hardened verification surfaces.
- The biggest remaining value gap is no longer “better error text”; it is whether the system can safely control real execution and recover from real-world failures.
- That makes execution/risk/state the true bottleneck.

---

## First slice to start from this roadmap

**Recommended first implementation slice:** P0.1 real execution boundary and mode separation.

**Reason:**
- The current repo is still clearly paper-first.
- Without an explicit, test-proven mode boundary, later work on risk and recovery can still sit on top of an ambiguous execution model.
- This is the cleanest way to shift from “partial v2 simulator with good guardrails” toward “production-safe automated trading program.”

---

## Verification standards for future work

For every task chosen from this roadmap:
- Start with a failing focused test or equivalent narrow repro.
- Make the smallest change that turns the target scenario green.
- Run one narrow adjacent verification set.
- Commit in small slices.
- Keep docs aligned when user-facing behavior changes.

---

## Chunk 1: Roadmap complete

This document is the working source of truth for unfinished trading_system work until superseded by a newer dated roadmap.
