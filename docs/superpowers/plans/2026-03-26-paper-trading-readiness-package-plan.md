# Paper Trading Readiness Package Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the trading_system from the current roadmap state to a level that is worth connecting to paper trading（模拟盘） for realistic observation.

**Architecture:** Stop micro-slicing. Deliver the remaining work as four larger packages that each produce a meaningful trading-system capability: complete Package C sizing/friction behavior, add stop taxonomy, add an MVP exit system, then wire the whole flow into paper-trading execution + observability. Each package should end with a package-level verification checkpoint and human-readable status summary.

**Tech Stack:** Python, pytest, trading_system runtime pipeline, allocator, reporting, market regime, portfolio/risk modules.

---

## Package roadmap overview

### Package 1 — Finish Package C（edge-aware sizing + execution friction，优势感知仓位 + 执行摩擦）

Target outcome:
- candidate quality, crowding, defensive regime, late-stage heat, and execution friction all influence aggressiveness（进攻性） / sizing（仓位）
- runtime/reporting explain why aggressiveness was compressed
- system is no longer near-binary accept/reject on candidates

### Package 2 — Stop taxonomy（止损分类） + protective behavior（保护性行为）

Target outcome:
- setup-specific invalidation（失效） / stop behavior exists instead of a vague single failure mode
- protective actions are attached to entries in a way that can later be simulated

### Package 3 — Exit system MVP（退出系统最小可用版）

Target outcome:
- there is a real path for partial reduction / take-profit / fail-fast exit behavior
- paper trading can evaluate more than entry logic

### Package 4 — Paper trading integration（模拟盘接线） + runbook（操作手册）

Target outcome:
- the current strategy can be connected to a paper-trading executor with logging, reporting, and replay/inspection hooks
- results are worth observing instead of being purely mechanical dry-runs

---

## File structure impact by package

### Package 1 — Package C completion

**Likely files**
- Modify: `trading_system/app/portfolio/allocator.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_allocator.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`

### Package 2 — Stop taxonomy

**Likely files**
- Modify/Create: `trading_system/app/risk/stop_policy.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/main.py`
- Modify/Create: `trading_system/tests/test_stop_policy.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

### Package 3 — Exit system MVP

**Likely files**
- Modify/Create: `trading_system/app/portfolio/exit_policy.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify/Create: `trading_system/tests/test_exit_policy.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

### Package 4 — Paper trading integration

**Likely files**
- Modify/Create: `trading_system/app/execution/paper_executor.py`
- Modify/Create: `trading_system/app/execution/paper_ledger.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/README.md`
- Modify/Create: `trading_system/docs/PAPER_TRADING_RUNBOOK.md`
- Modify/Create: `trading_system/tests/test_paper_executor.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

---

## Chunk 1: Package 1 — Finish Package C

### Task 1: Regime / late-heat compression completion

**Files:**
- Modify: `trading_system/app/portfolio/allocator.py`
- Modify: `trading_system/tests/test_allocator.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`
- Modify: `trading_system/tests/test_reporting.py`

- [ ] **Step 1: Write/fix the focused failing tests**

Add or refine tests that prove:
- strong candidates get more aggressive sizing than weak ones under similar base risk
- crowding / execution friction can compress aggressiveness without full rejection
- defensive regime and late-stage heat further compress aggressiveness
- compression reasons surface in runtime/reporting in human-readable form

- [ ] **Step 2: Run targeted tests to verify the gap**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_allocator.py trading_system/tests/test_reporting.py trading_system/tests/test_main_v2_cycle.py -k 'aggressiveness or compression or friction or heat'`

Expected:
- either FAIL on the remaining compression/reason gaps
- or PASS, proving Package 1 is already complete

- [ ] **Step 3: Implement the minimum remaining code**

Only if tests expose a real gap:
- finish allocator compression inputs
- keep runtime/reporting changes minimal and explanation-focused

- [ ] **Step 4: Run package verification**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_allocator.py trading_system/tests/test_reporting.py trading_system/tests/test_main_v2_cycle.py`

Expected:
- PASS for the touched Package 1 slice

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/allocator.py trading_system/app/main.py trading_system/app/reporting/regime_report.py trading_system/tests/test_allocator.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py
git commit -m "feat: complete package c sizing and friction foundation"
```

### Task 2: Package 1 handoff

**Files:**
- Modify: `memory/dev-status.md`

- [ ] **Step 1: Update package status**

Record:
- what Package 1 now covers
- latest verification command/result
- next package recommendation

- [ ] **Step 2: Commit status/doc updates if needed**

```bash
git add memory/dev-status.md docs/superpowers/plans/2026-03-26-paper-trading-readiness-package-plan.md
git commit -m "docs: update package 1 execution status"
```

---

## Chunk 2: Package 2 — Stop taxonomy + protective behavior

### Task 1: Define stop taxonomy

**Files:**
- Create/Modify: `trading_system/app/risk/stop_policy.py`
- Modify: `trading_system/tests/test_stop_policy.py`

- [ ] **Step 1: Write the failing stop-policy tests**

Cover at least:
- breakout continuation setup
- rotation setup
- crash-defensive environment setup

Each should produce a clearly different invalidation / stop style.

- [ ] **Step 2: Run tests to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_stop_policy.py`

Expected:
- FAIL because the taxonomy is incomplete or missing

- [ ] **Step 3: Implement the minimum stop taxonomy**

Add the smallest useful structure that:
- maps setup type → stop style / invalidation basis
- is usable later by lifecycle/execution code

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/risk/stop_policy.py trading_system/tests/test_stop_policy.py
git commit -m "feat: add stop taxonomy foundation"
```

### Task 2: Attach protective behavior into runtime

**Files:**
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing runtime test**

Prove that qualifying entries now emit or preserve the right protective stop intent for the setup type.

- [ ] **Step 2: Run test to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_main_v2_cycle.py -k 'protective or stop'`

Expected:
- FAIL

- [ ] **Step 3: Implement the minimum runtime/lifecycle plumbing**

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/lifecycle.py trading_system/app/main.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: attach protective stop behavior"
```

---

## Chunk 3: Package 3 — Exit system MVP

### Task 1: Add exit policy primitives

**Files:**
- Create/Modify: `trading_system/app/portfolio/exit_policy.py`
- Modify: `trading_system/tests/test_exit_policy.py`

- [ ] **Step 1: Write the failing tests**

Cover at least:
- partial take-profit
- fail-fast exit on invalidation
- de-risking in defensive regime

- [ ] **Step 2: Run tests to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_exit_policy.py`

Expected:
- FAIL

- [ ] **Step 3: Implement the minimum exit policy**

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/exit_policy.py trading_system/tests/test_exit_policy.py
git commit -m "feat: add exit policy mvp"
```

### Task 2: Thread exit behavior into runtime/reporting

**Files:**
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing runtime test**

Prove that runtime output reflects at least one real exit/de-risk action path.

- [ ] **Step 2: Run tests to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_main_v2_cycle.py -k 'exit or take_profit or de_risk'`

Expected:
- FAIL

- [ ] **Step 3: Implement the minimum plumbing**

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/lifecycle.py trading_system/app/main.py trading_system/app/reporting/regime_report.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: surface exit system mvp"
```

---

## Chunk 4: Package 4 — Paper trading integration + runbook

### Task 1: Add paper executor / ledger foundation

**Files:**
- Create: `trading_system/app/execution/paper_executor.py`
- Create: `trading_system/app/execution/paper_ledger.py`
- Create/Modify: `trading_system/tests/test_paper_executor.py`

- [ ] **Step 1: Write the failing tests**

Cover:
- order intent → paper fill/ledger event
- position update recording
- replayable result storage

- [ ] **Step 2: Run tests to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_paper_executor.py`

Expected:
- FAIL

- [ ] **Step 3: Implement minimal paper executor / ledger**

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/execution/paper_executor.py trading_system/app/execution/paper_ledger.py trading_system/tests/test_paper_executor.py
git commit -m "feat: add paper executor foundation"
```

### Task 2: Wire main runtime into paper trading mode

**Files:**
- Modify: `trading_system/app/main.py`
- Modify/Create: `trading_system/docs/PAPER_TRADING_RUNBOOK.md`
- Modify: `trading_system/README.md`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing integration test**

Prove that a strategy cycle can emit executable paper-trading intents and record them through the paper executor path.

- [ ] **Step 2: Run tests to verify failure**

Run:
`uv run --with pytest python -m pytest -q trading_system/tests/test_main_v2_cycle.py -k 'paper_trading or paper_executor'`

Expected:
- FAIL

- [ ] **Step 3: Implement minimal integration plumbing**

- [ ] **Step 4: Re-run tests**

Expected:
- PASS

- [ ] **Step 5: Update docs and commit**

```bash
git add trading_system/app/main.py trading_system/docs/PAPER_TRADING_RUNBOOK.md trading_system/README.md trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: connect strategy cycle to paper trading"
```

---

## Readiness rule

Do **not** claim “ready for paper trading” until all four packages are complete and the final integration path has at least one end-to-end paper-executor verification run.

## Practical estimate

If packages are executed as packages rather than micro-slices:
- optimistic: 3 package turns
- realistic: 4 package turns
- conservative: 5 package turns

That means: after current progress, the system is still roughly **4 package turns** away from being worth connecting to paper trading for observation.

---

Plan complete and saved to `docs/superpowers/plans/2026-03-26-paper-trading-readiness-package-plan.md`. Ready to execute?
