# Live-Grade Trading Evidence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the six remaining “not yet industry-grade” gaps into concrete implementation work that produces real evidence artifacts, not just gates.

**Architecture:** Keep the current live-readiness hard gates as promotion blockers, then add producers for the artifacts those gates require. Start with offline, deterministic artifact producers and tests; do not place real/testnet/live orders without explicit per-action approval.

**Tech Stack:** Python, pytest, JSON/JSONL artifacts, existing `trading_system.app.backtest.live_readiness` CLI/reporting path, existing backtest/raw-market/runtime modules.

---

## Six implementation tracks

1. **Historical L2/orderbook/tick path ingestion and audit**
   - Implement an import/audit path for historical L2 snapshots, incremental updates, and trade ticks.
   - Produce `market_microstructure_gate.json` with `l2_tick_coverage_met` and coverage metrics.

2. **Depth-driven taker simulator replay**
   - Implement deterministic orderbook-depth fill simulation for taker orders.
   - Replace fixed-bps-only execution evidence with order-size-aware depth impact evidence where L2 is available.
   - Feed `depth_driven_taker_met` into `market_microstructure_gate.json`.

3. **Testnet/live dust maker/taker calibration ingestion**
   - Build JSONL parser for dust/testnet/live order/fill records.
   - Produce `passive_order_calibration_summary.json` and taker slippage/latency summaries.
   - This is ingestion/analysis only; no orders may be submitted without explicit user approval.

4. **Walk-forward / OOS / regime / cost-stress validation artifact producer**
   - Implement validation runner/reporter that summarizes frozen-parameter OOS, multi-regime results, double-cost/stress results, and forward-contamination checks.
   - Produce `validation_gate.json`.

5. **Runtime safety evidence producer**
   - Add offline/runtime-report tooling for kill-switch dry-run, order/position reconciliation, fail-closed behavior, dust-before-scale state, complete ledger, explainability, and drift guard.
   - Produce `runtime_safety_gate.json` from real runtime/paper/testnet logs when available.

6. **End-to-end promotion evidence bundle**
   - Add a manifest/collector that assembles all gate artifacts for a candidate run and fails closed if any required producer is missing.
   - Output a single promotion evidence bundle directory with provenance and checksums.

---

## Implementation order

### Task 1: Document the next-stage implementation plan

**Files:**
- Create: `trading_system/docs/LIVE_GRADE_EVIDENCE_IMPLEMENTATION_PLAN.md`
- Modify: `trading_system/docs/INSTITUTIONAL_BEST_PRACTICES_ROADMAP.md`

**Steps:**
1. Write this plan into repo docs.
2. Link it from the roadmap completion audit section.
3. Run doc read-back and grep for the six tracks.
4. Commit: `docs(backtest): add live-grade evidence implementation plan`

### Task 2: Historical microstructure artifact producer skeleton

**Files:**
- Create: `trading_system/app/backtest/microstructure_evidence.py`
- Test: `trading_system/tests/test_backtest_microstructure_evidence.py`

**Goal:** Produce `market_microstructure_gate.json` from local historical L2/tick manifest inputs.

**TDD behavior:**
- Given a fixture manifest with L2 snapshot/update coverage and tick coverage ≥ 99%, writer outputs:
  - `schema_version = market_microstructure_gate_input.v1`
  - `checks.l2_tick_coverage_met = true`
  - `checks.depth_driven_taker_met = false` until depth fills are attached
  - summary coverage metrics
- Given missing/low coverage, output false and reasons.

**Verification:**
```bash
python -m pytest -q trading_system/tests/test_backtest_microstructure_evidence.py
```

### Task 3: Depth-driven taker fill simulator

**Files:**
- Modify: `trading_system/app/backtest/microstructure_evidence.py`
- Test: `trading_system/tests/test_backtest_microstructure_evidence.py`

**Goal:** Simulate marketable taker orders against orderbook levels and include depth impact in microstructure gate evidence.

**TDD behavior:**
- Buy order consumes ask levels by quantity and computes VWAP, consumed levels, residual quantity, and slippage bps.
- Sell order consumes bid levels similarly.
- Insufficient depth marks fill incomplete and `depth_driven_taker_met=false`.

### Task 4: Calibration evidence producer

**Files:**
- Extend existing calibration module if suitable: `trading_system/app/execution/calibration.py`
- Test: existing/new calibration tests

**Goal:** Produce passive/taker calibration summary artifacts from JSONL real/testnet/live order records.

**Safety:** no order submission, ingestion only.

### Task 5: Validation artifact producer

**Files:**
- Create: `trading_system/app/backtest/validation_evidence.py`
- Test: `trading_system/tests/test_backtest_validation_evidence.py`

**Goal:** Convert OOS/regime/cost-stress run summaries into `validation_gate.json`.

### Task 6: Runtime safety artifact producer

**Files:**
- Create: `trading_system/app/runtime/runtime_safety_evidence.py` or nearest existing runtime module.
- Test: `trading_system/tests/test_runtime_safety_evidence.py`

**Goal:** Convert runtime/paper/testnet logs into `runtime_safety_gate.json`.

### Task 7: Promotion evidence bundle collector

**Files:**
- Create: `trading_system/app/backtest/promotion_evidence_bundle.py`
- Test: `trading_system/tests/test_backtest_promotion_evidence_bundle.py`

**Goal:** Collect `trades.json`, `exit_path_replay.json`, and all required evidence artifacts, compute checksums, write a manifest that live-readiness can consume directly, and provide a fail-closed verifier that detects missing or tampered artifacts before promotion review. The verifier must also reject invalid manifest schema versions, missing `candidate_id`, and unsafe artifact paths such as absolute paths or `..` traversal.

### Task 8: Live-readiness smoke consumption of producer artifacts

**Files:**
- Modify: `trading_system/app/backtest/live_readiness.py`
- Test: `trading_system/tests/test_backtest_live_readiness.py`

**Goal:** Ensure the live-readiness smoke normalizer preserves every producer artifact required by hard gates, so a candidate run with real producer outputs is evaluated from evidence rather than incorrectly rejected as missing evidence. When requested, live-readiness must also verify the source promotion bundle integrity and fail closed on missing or tampered artifacts before promotion review.

**TDD behavior:**
- Given a source chunk with `exit_path_replay.json`, `passive_order_calibration_summary.json`, `market_microstructure_gate.json`, `validation_gate.json`, and `runtime_safety_gate.json`, `write_live_readiness_smoke_report(...)` copies them into normalized chunks and the gate consumes them.
- Given required producer evidence with invalid artifact schema versions or missing non-synthetic `evidence_source`, live-readiness emits machine-readable schema/provenance failure reasons and rejects promotion instead of trusting `checks=true` alone. This includes `passive_order_calibration_summary.json` and `exit_path_replay.json`; legacy `provenance` may be displayed for compatibility but must not substitute for live-grade `evidence_source` in required promotion gates.
- Given `--require-promotion-bundle-integrity` and a source bundle with a tampered artifact, live-readiness emits `promotion_bundle_integrity_failed`, records the verifier report, and rejects promotion.
- If artifacts are absent, existing fail-closed missing-evidence reasons remain unchanged.

---

## Non-negotiables

- No real/testnet/live order placement unless explicitly approved in the current turn.
- Do not mark real evidence present from synthetic fixtures.
- Synthetic fixtures are allowed only to test producer logic and must be labeled synthetic.
- Every producer must write machine-readable schema versions and conservative failure reasons.
- Every task uses TDD and a focused commit.
