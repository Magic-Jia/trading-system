# B1 Crypto Derivatives + Crowding Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push derivatives and crowding features from coarse regime context down into candidate-level filtering for trend, rotation, and short candidates.

**Architecture:** Reuse the existing derivatives summary concepts, but add a candidate-level feature adapter so each engine can read per-symbol funding / OI / basis / taker-flow inputs without duplicating parsing logic. Land this in narrow slices: first introduce typed derivatives-feature access and trend gating, then extend rotation and short, then expose rejection reasons in runtime summaries/tests.

**Tech Stack:** Python, pytest, uv, existing `trading_system.app.signals.*`, `market_regime.derivatives`, runtime state JSON, paper-first execution flow.

---

## File map

### Existing files to modify
- `trading_system/app/types.py`
  - Extend candidate/runtime-facing structures only if needed for derivatives/crowding metadata to travel cleanly.
- `trading_system/app/market_regime/derivatives.py`
  - Keep current majors summary behavior intact, but add reusable helpers or a symbol-level derivatives feature view.
- `trading_system/app/signals/trend_engine.py`
  - Add candidate-level derivatives/crowding filters for trend candidates.
- `trading_system/app/signals/rotation_engine.py`
  - Add candidate-level derivatives/crowding filters for rotation candidates.
- `trading_system/app/signals/short_engine.py`
  - Add candidate-level derivatives/crowding filters for short candidates, especially squeeze-risk / crowded-short handling.
- `trading_system/app/main.py`
  - If needed, preserve any new candidate metadata into runtime summaries/state.
- `trading_system/tests/test_trend_engine.py`
  - Add focused red/green tests for trend derivatives-aware candidate filtering.
- `trading_system/tests/test_rotation_engine.py`
  - Add focused red/green tests for rotation crowding / overheat filtering.
- `trading_system/tests/test_short_engine.py`
  - Add focused red/green tests for short squeeze-risk / crowding filtering.
- `trading_system/tests/test_main_v2_cycle.py`
  - Add one narrow propagation/runtime-summary proof only if new metadata/rejection reasons must survive the cycle.

### New files to consider
- `trading_system/app/signals/derivatives_features.py`
  - Optional focused helper for symbol-level derivatives feature extraction and crowding classification.
- `trading_system/tests/fixtures/derivatives_snapshot_v2.json`
  - Reuse if possible; only add or extend fixtures if current ones cannot express the new candidate-level states cleanly.

### Notes on boundaries
- Do **not** mix this B1 slice with stop-taxonomy or exit-system changes.
- Do **not** move live-execution or P0.3 recovery work into this plan.
- Keep regime-level summary behavior working; this plan adds candidate-level edge, not a wholesale regime rewrite.

## Chunk 1: Trend candidate derivatives-aware filtering

### Task 1: Add reusable symbol-level derivatives feature access

**Files:**
- Modify: `trading_system/app/market_regime/derivatives.py`
- Create (if needed): `trading_system/app/signals/derivatives_features.py`
- Test: `trading_system/tests/test_trend_engine.py`

- [ ] **Step 1: Write the failing trend-engine test for candidate-level derivatives features**

```python
def test_generate_trend_candidates_filters_crowded_longs_from_symbol_level_derivatives(...):
    ...
    assert {c.symbol for c in candidates} == {"BTCUSDT"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_trend_engine.py -k crowded_long`
Expected: FAIL because trend candidates currently ignore candidate-level derivatives state.

- [ ] **Step 3: Implement minimal derivatives feature adapter**

```python
def symbol_derivatives_features(derivatives, symbol):
    return {
        "funding_rate": ...,
        "open_interest_change_24h_pct": ...,
        "basis_bps": ...,
        "taker_buy_sell_ratio": ...,
        "crowding_bias": ...,
    }
```

- [ ] **Step 4: Wire trend engine to block unhealthy crowded-long states**

```python
if crowding_bias == "crowded_long" and basis_bps > ...:
    continue
```

- [ ] **Step 5: Re-run focused test and verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_trend_engine.py -k crowded_long`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trading_system/app/market_regime/derivatives.py trading_system/app/signals/derivatives_features.py trading_system/app/signals/trend_engine.py trading_system/tests/test_trend_engine.py
git commit -m "feat: filter trend candidates by derivatives crowding"
```

### Task 2: Preserve minimal trend derivatives metadata when useful

**Files:**
- Modify: `trading_system/app/signals/trend_engine.py`
- Modify: `trading_system/app/types.py` (only if extra metadata needs explicit support)
- Test: `trading_system/tests/test_trend_engine.py`

- [ ] **Step 1: Write the failing test for trend candidate metadata passthrough**

```python
def test_generate_trend_candidates_attach_derivatives_meta(...):
    assert candidate.timeframe_meta["derivatives"]["crowding_bias"] == "balanced"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_trend_engine.py -k derivatives_meta`
Expected: FAIL because no derivatives metadata is attached yet.

- [ ] **Step 3: Implement the minimal metadata passthrough**
- [ ] **Step 4: Re-run focused test and verify it passes**
- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/trend_engine.py trading_system/app/types.py trading_system/tests/test_trend_engine.py
git commit -m "feat: expose trend derivatives metadata"
```

## Chunk 2: Rotation and short candidate crowding filters

### Task 3: Add rotation overheat / crowding filters

**Files:**
- Modify: `trading_system/app/signals/rotation_engine.py`
- Test: `trading_system/tests/test_rotation_engine.py`

- [ ] **Step 1: Write the failing rotation test for overheat / crowding rejection**

```python
def test_generate_rotation_candidates_rejects_overheated_crowded_leader(...):
    assert {c.symbol for c in candidates} == {"LINKUSDT"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_rotation_engine.py -k overheated`
Expected: FAIL because rotation currently ignores candidate-level crowding.

- [ ] **Step 3: Implement minimal rotation crowding filter**
- [ ] **Step 4: Re-run focused test and verify it passes**
- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/rotation_engine.py trading_system/tests/test_rotation_engine.py
git commit -m "feat: filter rotation candidates by derivatives overheat"
```

### Task 4: Add short squeeze-risk / crowded-short filter

**Files:**
- Modify: `trading_system/app/signals/short_engine.py`
- Test: `trading_system/tests/test_short_engine.py`

- [ ] **Step 1: Write the failing short-engine test for squeeze-risk rejection**

```python
def test_generate_short_candidates_rejects_crowded_short_squeeze_risk(...):
    assert {c.symbol for c in candidates} == {"ETHUSDT"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_short_engine.py -k squeeze_risk`
Expected: FAIL because short currently ignores candidate-level crowding.

- [ ] **Step 3: Implement minimal short squeeze-risk filter**
- [ ] **Step 4: Re-run focused test and verify it passes**
- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/short_engine.py trading_system/tests/test_short_engine.py
git commit -m "feat: filter short candidates by squeeze risk"
```

## Chunk 3: Runtime proof and broader verification

### Task 5: Prove the new filters survive the main cycle where needed

**Files:**
- Modify: `trading_system/app/main.py` (only if runtime summaries need extra reasons/meta)
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write one failing cycle test only if runtime reporting needs to surface derivatives-driven rejection or metadata**

```python
def test_main_v2_reports_derivatives_filtered_candidates(...):
    assert runtime_state["rotation_summary"]["rejected_reason_codes"] == [...]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_main_v2_cycle.py -k derivatives_filtered`
Expected: FAIL for missing runtime propagation.

- [ ] **Step 3: Implement the smallest runtime summary change**
- [ ] **Step 4: Re-run focused test and verify it passes**
- [ ] **Step 5: Commit**

```bash
git add trading_system/app/main.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: report derivatives-filtered candidates in runtime state"
```

### Task 6: Run broader relevant verification

**Files:**
- Test: `trading_system/tests/test_trend_engine.py`
- Test: `trading_system/tests/test_rotation_engine.py`
- Test: `trading_system/tests/test_short_engine.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Run engine-level verification**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_trend_engine.py trading_system/tests/test_rotation_engine.py trading_system/tests/test_short_engine.py`
Expected: PASS

- [ ] **Step 2: Run adjacent cycle verification**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests/test_main_v2_cycle.py -k 'trend or rotation or short or regime'`
Expected: PASS

- [ ] **Step 3: Run full trading-system suite if earlier steps are green**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -q trading_system/tests`
Expected: PASS

- [ ] **Step 4: Commit any final cleanup if needed**

```bash
git add <files>
git commit -m "test: verify derivatives-aware candidate filters"
```

## Execution notes

- Start narrow: trend first, then rotation, then short.
- Keep derivatives parsing logic DRY; do not fork three separate crowding calculators.
- Do not bundle absolute-strength filters into this B1 slice; that belongs to B2.
- If current fixtures cannot express crowding states, extend them surgically instead of creating a giant new fixture set.
- If runtime reporting changes are not necessary to prove B1 works, skip Task 5 entirely.

## Recommended first execution slice

Start with **Task 1** only: add the focused trend crowded-long rejection test, make it fail, implement the smallest symbol-level derivatives feature adapter, and turn that test green. That gives a safe base to reuse in rotation and short.
