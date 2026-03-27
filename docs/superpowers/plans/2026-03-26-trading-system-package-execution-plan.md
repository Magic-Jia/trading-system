# Trading System Package Execution Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop micro-slicing and move the trading_system roadmap forward in larger packages, starting with a regime crash protection package now that the Phase 2 long-quality baseline is largely in place.

**Architecture:** Package the next work as coherent strategy outcomes instead of tiny isolated regressions. Treat the completed long-filter work as the baseline, then deliver the next package as a full regime-layer risk-compression capability: explicit crash/cascade/squeeze regime detection, compressed risk policy, and runtime/reporting visibility. Keep each task narrow internally, but only report completion at package level.

**Tech Stack:** Python, pytest, trading_system runtime pipeline, market regime classifier, derivatives summary, risk/reporting modules.

---

## Package roadmap

### Package A — Phase 2 long filter baseline (**completed / near-complete**)

Delivered baseline:
- long absolute strength floor on trend + rotation
- long price-extension overheat rejection on trend + rotation
- long funding / basis blow-off rejection
- explicit rotation-side regression symmetry for derivatives blow-off logic
- follow-up slice in flight/just landed for price + open-interest acceleration blow-off

Outcome:
- longs must now be strong enough and not obviously late / overheated before they survive into candidate selection

### Package B — Regime crash protection (**active next package**)

Target outcome:
- system can distinguish ordinary risk-off from explicit crash / cascade / squeeze conditions
- risk multiplier and execution policy compress automatically in those extreme states
- runtime output and regime report expose the crash-protection state clearly

### Package C — Edge-aware sizing + execution friction (**queued after Package B**)

Target outcome:
- candidate quality, crowding, and execution friction start influencing aggressiveness instead of only acceptance/rejection

---

## File structure for Package B

**Primary files to modify**
- `trading_system/app/market_regime/derivatives.py`
  - Add crash / cascade / squeeze-oriented late-stage heat summary fields from majors derivatives state.
- `trading_system/app/market_regime/classifier.py`
  - Map crash-oriented derivatives stress into explicit defensive regime labels / execution policy / risk multiplier compression.
- `trading_system/app/risk/regime_risk.py`
  - Keep risk-budget scaling aligned with the stronger crash-protection outputs if needed.
- `trading_system/app/reporting/regime_report.py`
  - Surface the new crash-protection regime summary fields in emitted reporting.
- `trading_system/app/main.py`
  - Ensure the runtime cycle carries the stronger crash-protection regime through allocation/reporting behavior.

**Primary tests to modify**
- `trading_system/tests/test_market_regime.py`
  - Add focused regime classification / derivatives summary regressions.
- `trading_system/tests/test_main_v2_cycle.py`
  - Add runtime proof that crash-protection state compresses behavior/output the way the package intends.

---

## Chunk 1: Package B — Regime crash protection

### Task 1: Add explicit crash / cascade / squeeze derivatives stress signals

**Files:**
- Modify: `trading_system/app/market_regime/derivatives.py`
- Test: `trading_system/tests/test_market_regime.py`

- [x] **Step 1: Write the failing test**

Add focused tests that describe at least one crash/cascade-like majors state and one squeeze-like majors state. Expected behavior:
- derivatives summary emits a stronger explicit stress signal than the current generic crowding bucket
- the signal is distinguishable from ordinary `crowded_long` / `crowded_short`

Example test shape:

```python
def test_derivatives_summary_flags_crash_cascade_stress():
    derivatives = _majors_derivatives_snapshot(
        funding_rate=-0.00005,
        open_interest_change_24h_pct=-0.12,
        taker_buy_sell_ratio=0.84,
        basis_bps=-18.0,
        mark_price_change_24h_pct=-0.08,
    )

    summary = summarize_derivatives_risk(derivatives)

    assert summary["late_stage_heat"] == "cascade"
    assert summary["execution_hazard"] == "compress_risk"
```

- [x] **Step 2: Run test to verify it fails**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py -k crash`

Expected:
- FAIL because the explicit crash/cascade/squeeze fields are not implemented yet or do not match the new expectation.

- [x] **Step 3: Write minimal implementation**

Implement the smallest possible extension in `trading_system/app/market_regime/derivatives.py` so the derivatives summary exposes:
- one explicit late-stage hazard bucket (for example: `cascade`, `squeeze`, `none`)
- one policy-oriented hint (for example: `compress_risk`, `suppress_rotation`, `none`)

Keep it narrow and driven by already-loaded majors derivatives fields.

- [x] **Step 4: Run test to verify it passes**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py -k 'crash or squeeze or cascade'`

Expected:
- PASS

- [x] **Step 5: Commit**

```bash
git add trading_system/app/market_regime/derivatives.py trading_system/tests/test_market_regime.py
git commit -m "feat: summarize crash protection derivatives stress"
```

### Task 2: Map crash stress into explicit regime compression

**Files:**
- Modify: `trading_system/app/market_regime/classifier.py`
- Test: `trading_system/tests/test_market_regime.py`

- [x] **Step 1: Write the failing test**

Add a focused regime test proving that crash/cascade stress does more than change labels; it must compress risk/execution behavior.

Example test shape:

```python
def test_classify_regime_crash_stress_compresses_risk_and_execution():
    market = _high_vol_mixed_market_context()
    stressed = _majors_derivatives_snapshot(
        funding_rate=-0.00005,
        open_interest_change_24h_pct=-0.12,
        taker_buy_sell_ratio=0.84,
        basis_bps=-18.0,
        mark_price_change_24h_pct=-0.08,
    )

    regime = classify_regime(market, stressed)

    assert regime.label in {"CRASH_DEFENSIVE", "CASCADE_ALERT", "SQUEEZE_ALERT"}
    assert regime.risk_multiplier < 0.55
    assert regime.execution_policy == "suppress"
```

- [x] **Step 2: Run test to verify it fails**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py -k 'compresses_risk or crash_stress'`

Expected:
- FAIL because classifier does not yet map the new stress signal into an explicit crash-protection regime.

- [x] **Step 3: Write minimal implementation**

In `trading_system/app/market_regime/classifier.py`:
- add one explicit crash-protection regime label/profile
- connect the new derivatives summary signal to that label/profile
- compress risk multiplier and execution policy clearly
- keep the existing RISK_ON / MIXED / RISK_OFF ladder intact where not affected

- [x] **Step 4: Run test to verify it passes**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py -k 'compresses_risk or crash_stress or squeeze'`

Expected:
- PASS

- [x] **Step 5: Commit**

```bash
git add trading_system/app/market_regime/classifier.py trading_system/tests/test_market_regime.py
git commit -m "feat: classify crash protection regime"
```

### Task 3: Carry crash protection through runtime and reporting

**Files:**
- Modify: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/risk/regime_risk.py` (only if runtime/risk plumbing needs it)
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing runtime test**

Add a runtime test that proves a crash-protection regime actually shows up in the emitted summary and compresses behavior.

Example test shape:

```python
def test_main_cycle_surfaces_crash_protection_and_compresses_execution(tmp_path):
    # arrange crash/cascade-like derivatives snapshot
    # run main()
    # assert emitted regime summary shows crash-protection label/policy
    # assert allocations/execution are compressed or suppressed
```

- [x] **Step 2: Run test to verify it fails**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k 'crash_protection or cascade or squeeze'`

Expected:
- FAIL because runtime/reporting does not yet expose or respect the new crash-protection regime strongly enough.

- [x] **Step 3: Write minimal implementation**

Implement the minimum plumbing needed so:
- runtime summary/report includes the crash-protection signal/label
- risk/execution behavior is visibly compressed in the cycle output
- no unrelated reporting redesign is introduced

- [x] **Step 4: Run test to verify it passes**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_main_v2_cycle.py -k 'crash_protection or cascade or squeeze'`

Expected:
- PASS

- [x] **Step 5: Commit**

```bash
git add trading_system/app/reporting/regime_report.py trading_system/app/main.py trading_system/app/risk/regime_risk.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: surface crash protection regime"
```

### Task 4: Package verification and handoff

**Files:**
- Modify: `memory/dev-status.md`

- [x] **Step 1: Run the package verification bundle**

Run:
`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_market_regime.py trading_system/tests/test_main_v2_cycle.py`

Expected:
- PASS for the touched regime/runtime slice

- [x] **Step 2: Update plan tracking and dev status**

Update `memory/dev-status.md` with:
- last verified command/result
- latest commit(s)
- next package recommendation

- [x] **Step 3: Commit plan-status updates if needed**

```bash
git add memory/dev-status.md docs/superpowers/plans/2026-03-26-trading-system-package-execution-plan.md
git commit -m "docs: update package execution status"
```

## Chunk 2: Next package queue (not for this run)

### Package C — Edge-aware sizing + execution friction

Hold this package until Package B is merged/verified.

Expected focus later:
- translate candidate quality/crowding/liquidity into aggressiveness
- account for fee / spread / slippage / funding drag in strategy-layer selection
- reduce churn from unstable rotation signals

---

Plan complete and saved to `docs/superpowers/plans/2026-03-26-trading-system-package-execution-plan.md`. User already asked to execute according to the document, so proceed directly with Package B using Codex in the isolated worktree.
