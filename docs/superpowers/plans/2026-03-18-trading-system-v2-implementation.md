# Trading System v2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Trading System v2 P0 as a regime-aware, portfolio-managed trading system that adds a market regime layer, dynamic universe building, trend-engine v2, portfolio allocation, and lifecycle v2 on top of the existing trading system.

**Architecture:** Extend the current `trading_system/app/` skeleton instead of rewriting it. Implement v2 as a pipeline of regime classification → universe construction → trend signal generation → portfolio allocation → execution/lifecycle updates, while preserving the existing execution, state, and paper-trading flow.

**Tech Stack:** Python 3.12, existing `trading_system/app` modules, JSON state files under `trading_system/data`, pytest for tests, git for incremental commits.

---

## File Structure Map

### Existing files to modify
- `trading_system/app/main.py` — replace v1 single-path loop with v2 orchestration while keeping paper execution path intact
- `trading_system/app/config.py` — add regime, universe, allocator, and lifecycle config sections
- `trading_system/app/types.py` — add types for regimes, engine candidates, allocation decisions, lifecycle states, and richer metadata
- `trading_system/app/risk/validator.py` — add regime-aware and engine-aware validation inputs
- `trading_system/app/risk/guardrails.py` — add portfolio concentration and bucket checks
- `trading_system/app/risk/position_sizer.py` — add engine-tier sizing and regime scaling
- `trading_system/app/portfolio/lifecycle.py` — either keep as v1 compatibility layer or delegate to v2 lifecycle module
- `trading_system/app/storage/state_store.py` — persist regime, universes, candidate queues, allocation results, and lifecycle state
- `trading_system/app/reporting/daily_report.py` — include v2 fields where useful
- `trading_system/README.md` — document new v2 pipeline and runtime expectations
- `trading_system/docs/MVP_ARCHITECTURE.md` — align architecture notes with implemented v2 P0 scope

### New files to create
- `trading_system/app/data_sources/__init__.py`
- `trading_system/app/data_sources/market_loader.py`
- `trading_system/app/data_sources/derivatives_loader.py`
- `trading_system/app/market_regime/__init__.py`
- `trading_system/app/market_regime/breadth.py`
- `trading_system/app/market_regime/derivatives.py`
- `trading_system/app/market_regime/classifier.py`
- `trading_system/app/universe/__init__.py`
- `trading_system/app/universe/liquidity_filter.py`
- `trading_system/app/universe/sector_map.py`
- `trading_system/app/universe/builder.py`
- `trading_system/app/signals/trend_engine.py`
- `trading_system/app/signals/scoring.py`
- `trading_system/app/portfolio/exposure.py`
- `trading_system/app/portfolio/allocator.py`
- `trading_system/app/portfolio/lifecycle_v2.py`
- `trading_system/app/risk/regime_risk.py`
- `trading_system/app/reporting/regime_report.py`
- `trading_system/tests/conftest.py`
- `trading_system/tests/test_market_regime.py`
- `trading_system/tests/test_universe_builder.py`
- `trading_system/tests/test_trend_engine.py`
- `trading_system/tests/test_allocator.py`
- `trading_system/tests/test_lifecycle_v2.py`
- `trading_system/tests/test_main_v2_cycle.py`
- `trading_system/tests/fixtures/account_snapshot_v2.json`
- `trading_system/tests/fixtures/market_context_v2.json`
- `trading_system/tests/fixtures/derivatives_snapshot_v2.json`
- `trading_system/tests/fixtures/FIXTURE_PROVENANCE.md`

### Notes on boundaries
- `market_regime/` owns classification only; it does not rank trade candidates.
- `universe/` owns inclusion/exclusion and coarse sector tagging only.
- `signals/trend_engine.py` produces candidate signals only; it never sizes or allocates.
- `portfolio/allocator.py` is the only module that chooses which candidates survive into execution.
- `portfolio/lifecycle_v2.py` owns state transitions after fill; it should not generate new entries.
- `risk/` remains reusable across engines and allocator decisions.

## Chunk 1: Foundation Types, Config, and Test Harness

### Task 1: Add pytest harness and shared fixtures

**Files:**
- Create: `trading_system/tests/conftest.py`
- Create: `trading_system/tests/test_market_regime.py`
- Create: `trading_system/tests/fixtures/account_snapshot_v2.json`
- Create: `trading_system/tests/fixtures/market_context_v2.json`
- Create: `trading_system/tests/fixtures/derivatives_snapshot_v2.json`
- Create: `trading_system/tests/fixtures/FIXTURE_PROVENANCE.md`

- [ ] **Step 1: Write the failing fixture-loading test**

```python
from pathlib import Path
import json


def test_v2_fixture_files_exist():
    base = Path("trading_system/tests/fixtures")
    assert json.loads((base / "account_snapshot_v2.json").read_text())
    assert json.loads((base / "market_context_v2.json").read_text())
    assert json.loads((base / "derivatives_snapshot_v2.json").read_text())
    assert (base / "FIXTURE_PROVENANCE.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_market_regime.py::test_v2_fixture_files_exist -v`
Expected: FAIL because fixtures and/or test file do not exist yet.

- [ ] **Step 3: Create fixtures and shared loader helpers**

Implementation notes:
- `conftest.py` should expose helpers like `load_fixture(name: str) -> dict`.
- `conftest.py` should also define `sample_trend_candidates` and `sample_rotation_candidates` fixtures used later by allocator tests.
- Fixture files must be deterministic and offline-safe.
- `FIXTURE_PROVENANCE.md` must state for each fixture whether it is sanitized from real snapshots or fully synthetic, plus the normalized schema it preserves.
- Fixture JSON should include at least:
  - BTC/ETH plus several alt names
  - normalized market context for daily/4h/1h fields in one concrete file: `market_context_v2.json`
  - derivatives samples with funding and OI deltas for majors

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_market_regime.py::test_v2_fixture_files_exist -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/tests/conftest.py trading_system/tests/fixtures
git commit -m "test: add v2 fixtures and pytest harness"
```

### Task 2: Extend core v2 config

**Files:**
- Modify: `trading_system/app/config.py`
- Create: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing config test**

```python
from trading_system.app.config import DEFAULT_CONFIG


def test_v2_config_exposes_regime_universe_allocator_sections():
    assert hasattr(DEFAULT_CONFIG, "regime")
    assert hasattr(DEFAULT_CONFIG, "universe")
    assert hasattr(DEFAULT_CONFIG, "allocator")
    assert hasattr(DEFAULT_CONFIG, "lifecycle")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_v2_config_exposes_regime_universe_allocator_sections -v`
Expected: FAIL because config sections do not exist yet.

- [ ] **Step 3: Implement minimal config dataclasses**

Implementation notes:
- Add dataclasses such as `RegimeConfig`, `UniverseConfig`, `AllocatorConfig`, `LifecycleConfig`.
- Keep env-driven defaults simple and explicit.
- Include placeholders for:
  - regime confidence thresholds
  - liquidity floor
  - sector cap
  - bucket weights
  - lifecycle R-multiple thresholds

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_v2_config_exposes_regime_universe_allocator_sections -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/config.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: add v2 config sections"
```

### Task 3: Extend shared types for v2

**Files:**
- Modify: `trading_system/app/types.py`
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing types test**

```python
from trading_system.app.types import RegimeSnapshot, EngineCandidate, AllocationDecision, LifecycleState


def test_v2_types_are_importable():
    assert RegimeSnapshot
    assert EngineCandidate
    assert AllocationDecision
    assert LifecycleState
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_v2_types_are_importable -v`
Expected: FAIL due to missing types.

- [ ] **Step 3: Implement minimal v2 dataclasses / enums**

Implementation notes:
- `RegimeSnapshot`: label, confidence, risk_multiplier, bucket_targets, suppression_rules
- `EngineCandidate`: engine, setup_type, symbol, side, score, timeframe_meta, sector, liquidity_meta
- `AllocationDecision`: accepted/downsized/rejected, reason codes, final risk budget, rank
- `LifecycleState`: enum for `INIT`, `CONFIRM`, `PAYLOAD`, `PROTECT`, `EXIT`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_v2_types_are_importable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/types.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: add v2 shared types"
```

## Chunk 2: Runtime Data Sources and Market Regime Layer

### Task 4: Implement runtime market and derivatives loaders

**Files:**
- Create: `trading_system/app/data_sources/__init__.py`
- Create: `trading_system/app/data_sources/market_loader.py`
- Create: `trading_system/app/data_sources/derivatives_loader.py`
- Modify: `trading_system/tests/test_market_regime.py`

- [ ] **Step 1: Write the failing loader tests**

```python
from trading_system.app.data_sources.market_loader import load_market_context
from trading_system.app.data_sources.derivatives_loader import load_derivatives_snapshot


def test_load_market_context_reads_single_runtime_contract(tmp_path, load_fixture):
    market_path = tmp_path / "market_context.json"
    market_path.write_text(__import__("json").dumps(load_fixture("market_context_v2.json")))
    rows = load_market_context(market_path)
    assert rows
    assert all("symbol" in row for row in rows)


def test_load_derivatives_snapshot_reads_majors_only_snapshot(tmp_path, load_fixture):
    deriv_path = tmp_path / "derivatives_snapshot.json"
    deriv_path.write_text(__import__("json").dumps(load_fixture("derivatives_snapshot_v2.json")))
    snap = load_derivatives_snapshot(deriv_path)
    assert snap
    assert all("symbol" in row for row in snap)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_market_regime.py::test_load_market_context_reads_single_runtime_contract trading_system/tests/test_market_regime.py::test_load_derivatives_snapshot_reads_majors_only_snapshot -v`
Expected: FAIL because loader modules do not exist yet.

- [ ] **Step 3: Implement minimal loaders**

Implementation notes:
- `market_loader.py` must read one concrete normalized runtime file: `trading_system/data/market_context.json`.
- `derivatives_loader.py` must read one concrete normalized runtime file: `trading_system/data/derivatives_snapshot.json`.
- Both loaders should also accept explicit path arguments for tests and env overrides.
- Keep schema validation light but explicit: fail fast if required keys are missing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_market_regime.py::test_load_market_context_reads_single_runtime_contract trading_system/tests/test_market_regime.py::test_load_derivatives_snapshot_reads_majors_only_snapshot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/data_sources/__init__.py trading_system/app/data_sources/market_loader.py trading_system/app/data_sources/derivatives_loader.py trading_system/tests/test_market_regime.py
git commit -m "feat: add v2 runtime data loaders"
```

### Task 5: Implement breadth helpers

**Files:**
- Create: `trading_system/app/market_regime/breadth.py`
- Create: `trading_system/tests/test_market_regime.py`

- [ ] **Step 1: Write the failing breadth test**

```python
from trading_system.app.market_regime.breadth import compute_breadth_metrics


def test_compute_breadth_metrics_counts_positive_participation(load_fixture):
    market = load_fixture("market_context_v2.json")
    metrics = compute_breadth_metrics(market)
    assert "pct_above_4h_ema20" in metrics
    assert "positive_momentum_share" in metrics
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_market_regime.py::test_compute_breadth_metrics_counts_positive_participation -v`
Expected: FAIL because module/function does not exist yet.

- [ ] **Step 3: Implement minimal breadth computation**

Implementation notes:
- Accept normalized market rows rather than raw API responses.
- Compute only the metrics the spec requires.
- Keep function pure and deterministic.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_market_regime.py::test_compute_breadth_metrics_counts_positive_participation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/market_regime/breadth.py trading_system/tests/test_market_regime.py
git commit -m "feat: add regime breadth metrics"
```

### Task 6: Implement derivatives helpers for regime use

**Files:**
- Create: `trading_system/app/market_regime/derivatives.py`
- Modify: `trading_system/tests/test_market_regime.py`

- [ ] **Step 1: Write the failing derivatives test**

```python
from trading_system.app.market_regime.derivatives import summarize_derivatives_risk


def test_summarize_derivatives_risk_detects_crowding(load_fixture):
    derivatives = load_fixture("derivatives_snapshot_v2.json")
    summary = summarize_derivatives_risk(derivatives)
    assert "crowding_bias" in summary
    assert "funding_heat" in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_market_regime.py::test_summarize_derivatives_risk_detects_crowding -v`
Expected: FAIL because module/function does not exist yet.

- [ ] **Step 3: Implement minimal derivatives summarizer**

Implementation notes:
- Focus on majors only.
- Summarize funding, OI change, and price/OI interaction.
- Return simple categorical fields plus numeric summaries.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_market_regime.py::test_summarize_derivatives_risk_detects_crowding -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/market_regime/derivatives.py trading_system/tests/test_market_regime.py
git commit -m "feat: add regime derivatives summary"
```

### Task 7: Implement regime classifier

**Files:**
- Create: `trading_system/app/market_regime/__init__.py`
- Create: `trading_system/app/market_regime/classifier.py`
- Modify: `trading_system/tests/test_market_regime.py`

- [ ] **Step 1: Write the failing classifier tests**

```python
from trading_system.app.market_regime.classifier import classify_regime


def test_classify_regime_returns_bucket_targets(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")
    regime = classify_regime(market, derivatives)
    assert regime.label
    assert regime.bucket_targets
    assert regime.risk_multiplier > 0


def test_low_confidence_regime_reduces_aggression(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")
    regime = classify_regime(market, derivatives, force_low_confidence=True)
    assert regime.confidence < 0.5
    assert regime.execution_policy in {"normal", "downsize", "suppress"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_market_regime.py -v`
Expected: FAIL due to missing classifier and return shape.

- [ ] **Step 3: Implement minimal classifier**

Implementation notes:
- Combine breadth + derivatives summaries + simple trend inputs.
- Output a `RegimeSnapshot`.
- Implement direct regime-to-bucket mapping plus confidence-based aggression scaling.
- Keep routing rules data-driven where possible.
- Add at least one deterministic fixture assertion where a fixed snapshot maps to one exact regime label.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_market_regime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/market_regime trading_system/tests/test_market_regime.py
git commit -m "feat: add v2 regime classifier"
```

## Chunk 3: Universe Construction

### Task 8: Add coarse sector map

**Files:**
- Create: `trading_system/app/universe/sector_map.py`
- Create: `trading_system/tests/test_universe_builder.py`

- [ ] **Step 1: Write the failing sector-map test**

```python
from trading_system.app.universe.sector_map import sector_for_symbol


def test_sector_for_symbol_uses_fallback_taxonomy():
    assert sector_for_symbol("BTCUSDT") == "majors"
    assert sector_for_symbol("DOGEUSDT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_universe_builder.py::test_sector_for_symbol_uses_fallback_taxonomy -v`
Expected: FAIL because file/function does not exist.

- [ ] **Step 3: Implement fallback taxonomy mapper**

Implementation notes:
- Use simple symbol-to-sector mapping plus catch-all `other_uncategorized`.
- Make the mapping explicit and easy to revise.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_universe_builder.py::test_sector_for_symbol_uses_fallback_taxonomy -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/universe/sector_map.py trading_system/tests/test_universe_builder.py
git commit -m "feat: add fallback sector taxonomy"
```

### Task 9: Add liquidity filter

**Files:**
- Create: `trading_system/app/universe/liquidity_filter.py`
- Modify: `trading_system/tests/test_universe_builder.py`

- [ ] **Step 1: Write the failing liquidity test**

```python
from trading_system.app.universe.liquidity_filter import passes_liquidity_filter


def test_passes_liquidity_filter_rejects_thin_symbols():
    assert passes_liquidity_filter({"rolling_notional": 5_000_000, "slippage_bps": 8})
    assert not passes_liquidity_filter({"rolling_notional": 10_000, "slippage_bps": 80})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_universe_builder.py::test_passes_liquidity_filter_rejects_thin_symbols -v`
Expected: FAIL because module/function does not exist.

- [ ] **Step 3: Implement minimal liquidity predicate**

Implementation notes:
- Keep thresholds config-driven.
- Support inputs for rolling notional, order-book depth proxy, slippage, listing age, and wick-risk flags.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_universe_builder.py::test_passes_liquidity_filter_rejects_thin_symbols -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/universe/liquidity_filter.py trading_system/tests/test_universe_builder.py
git commit -m "feat: add universe liquidity filter"
```

### Task 10: Implement universe builder

**Files:**
- Create: `trading_system/app/universe/__init__.py`
- Create: `trading_system/app/universe/builder.py`
- Modify: `trading_system/tests/test_universe_builder.py`

- [ ] **Step 1: Write the failing universe-builder tests**

```python
from trading_system.app.universe.builder import build_universes


def test_build_universes_returns_major_rotation_and_short_pools(load_fixture):
    market = load_fixture("market_context_v2.json")
    universes = build_universes(market)
    assert universes.major_universe
    assert hasattr(universes, "rotation_universe")
    assert hasattr(universes, "short_universe")


def test_rotation_universe_only_contains_liquid_mature_names(load_fixture):
    market = load_fixture("market_context_v2.json")
    universes = build_universes(market)
    for row in universes.rotation_universe:
        assert row["passes_liquidity"] is True
        assert row["listing_age_ok"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_universe_builder.py -v`
Expected: FAIL due to missing builder.

- [ ] **Step 3: Implement minimal universe builder**

Implementation notes:
- Output majors, rotation names, and shortable majors separately.
- Tag each name with sector and liquidity metadata.
- Treat rotation and short universes as P0 foundation only; building the pools does not imply their engines ship in P0.
- Do not yet handle every future engine rule; keep it P0-scoped.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_universe_builder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/universe trading_system/tests/test_universe_builder.py
git commit -m "feat: add v2 universe builder"
```

## Chunk 4: Trend Engine v2

### Task 11: Implement scoring helpers for trend candidates

**Files:**
- Create: `trading_system/app/signals/scoring.py`
- Create: `trading_system/tests/test_trend_engine.py`

- [ ] **Step 1: Write the failing scoring test**

```python
from trading_system.app.signals.scoring import score_trend_candidate


def test_score_trend_candidate_rewards_multi_timeframe_alignment():
    candidate = {
        "daily_bias": "up",
        "h4_structure": "intact",
        "h1_trigger": "confirmed",
        "volume_quality": 0.8,
    }
    score = score_trend_candidate(candidate)
    assert score["total"] > 0
    assert score["components"]["timeframe_alignment"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_trend_engine.py::test_score_trend_candidate_rewards_multi_timeframe_alignment -v`
Expected: FAIL because function does not exist.

- [ ] **Step 3: Implement minimal trend scoring helper**

Implementation notes:
- Keep weights explicit.
- Accept normalized feature dicts, not raw candles.
- Return both total score and factor breakdown if practical.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_trend_engine.py::test_score_trend_candidate_rewards_multi_timeframe_alignment -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/scoring.py trading_system/tests/test_trend_engine.py
git commit -m "feat: add trend scoring helpers"
```

### Task 12: Implement trend engine v2

**Files:**
- Create: `trading_system/app/signals/trend_engine.py`
- Modify: `trading_system/tests/test_trend_engine.py`

- [ ] **Step 1: Write the failing trend-engine tests**

```python
from trading_system.app.signals.trend_engine import generate_trend_candidates


def test_generate_trend_candidates_produces_engine_candidates(load_fixture):
    market = load_fixture("market_context_v2.json")
    candidates = generate_trend_candidates(market)
    assert candidates
    assert candidates[0].engine == "trend"


def test_trend_engine_only_emits_breakout_or_pullback_setup_types(load_fixture):
    market = load_fixture("market_context_v2.json")
    setup_types = {c.setup_type for c in generate_trend_candidates(market)}
    assert setup_types <= {"BREAKOUT_CONTINUATION", "PULLBACK_CONTINUATION"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_trend_engine.py -v`
Expected: FAIL because engine does not exist yet.

- [ ] **Step 3: Implement minimal trend engine**

Implementation notes:
- Use daily/4h/1h hierarchy from the spec.
- Restrict P0 to majors and optional high-liquidity strong names.
- Emit `EngineCandidate` values only; no sizing or allocation here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_trend_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/signals/trend_engine.py trading_system/tests/test_trend_engine.py
git commit -m "feat: add trend engine v2"
```

## Chunk 5: Portfolio Allocation and Risk Routing

### Task 13: Add regime-aware risk helpers

**Files:**
- Create: `trading_system/app/risk/regime_risk.py`
- Modify: `trading_system/app/risk/position_sizer.py`
- Modify: `trading_system/tests/test_allocator.py`

- [ ] **Step 1: Write the failing regime-risk test**

```python
from trading_system.app.risk.regime_risk import scaled_risk_budget


def test_scaled_risk_budget_respects_engine_tier_and_regime_confidence():
    budget = scaled_risk_budget(base_risk_pct=0.008, regime_multiplier=0.5, confidence=0.4)
    assert budget < 0.008
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_allocator.py::test_scaled_risk_budget_respects_engine_tier_and_regime_confidence -v`
Expected: FAIL because helper does not exist yet.

- [ ] **Step 3: Implement minimal regime-aware risk helper and integrate sizing**

Implementation notes:
- Keep existing sizing formula.
- Add engine-tier base risk plus regime multiplier plus confidence dampening.
- Avoid changing v1 callers more than necessary.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_allocator.py::test_scaled_risk_budget_respects_engine_tier_and_regime_confidence -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/risk/regime_risk.py trading_system/app/risk/position_sizer.py trading_system/tests/test_allocator.py
git commit -m "feat: add regime-aware risk sizing"
```

### Task 14: Add exposure helpers

**Files:**
- Create: `trading_system/app/portfolio/exposure.py`
- Modify: `trading_system/tests/test_allocator.py`

- [ ] **Step 1: Write the failing exposure test**

```python
from trading_system.app.portfolio.exposure import exposure_snapshot


def test_exposure_snapshot_summarizes_sector_and_direction(load_fixture):
    account = load_fixture("account_snapshot_v2.json")
    snapshot = exposure_snapshot(account)
    assert "net_long_notional" in snapshot
    assert "sector_risk" in snapshot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_allocator.py::test_exposure_snapshot_summarizes_sector_and_direction -v`
Expected: FAIL because module/function does not exist.

- [ ] **Step 3: Implement minimal exposure snapshot helper**

Implementation notes:
- Summarize active risk, sector concentration, symbol concentration, and net long/short posture.
- Use fallback sectors for non-mapped names.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_allocator.py::test_exposure_snapshot_summarizes_sector_and_direction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/exposure.py trading_system/tests/test_allocator.py
git commit -m "feat: add portfolio exposure snapshot"
```

### Task 15: Implement allocator

**Files:**
- Create: `trading_system/app/portfolio/allocator.py`
- Modify: `trading_system/app/risk/validator.py`
- Modify: `trading_system/app/risk/guardrails.py`
- Modify: `trading_system/tests/test_allocator.py`

- [ ] **Step 1: Write the failing allocator tests**

```python
from trading_system.app.portfolio.allocator import allocate_candidates


def test_allocator_rejects_candidates_from_suppressed_bucket(load_fixture, sample_rotation_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"suppressed_engines": ["rotation"], "bucket_targets": {"trend": 1.0, "rotation": 0.0, "short": 0.0}}
    decisions = allocate_candidates(account=account, candidates=sample_rotation_candidates, regime=regime)
    assert decisions
    assert all(d.status == "REJECTED" for d in decisions)
    assert all("suppressed" in " ".join(d.reasons).lower() for d in decisions)


def test_allocator_downweights_duplicate_trend_breakouts(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.6, "rotation": 0.2, "short": 0.2}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    assert any(d.status in {"ACCEPTED", "DOWNSIZED"} for d in decisions)
    assert any(d.status == "DOWNSIZED" for d in decisions[1:])


def test_allocator_respects_total_active_risk_cap(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.9, "rotation": 0.1, "short": 0.0}, "suppressed_engines": []}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted_risk = sum(d.final_risk_budget for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"})
    assert accepted_risk <= decisions[0].meta["portfolio_total_risk_cap"]


def test_allocator_respects_net_exposure_and_major_alt_balance(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    regime = {"bucket_targets": {"trend": 0.5, "rotation": 0.0, "short": 0.0}, "suppressed_engines": ["rotation", "short"]}
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates, regime=regime)
    accepted = [d for d in decisions if d.status in {"ACCEPTED", "DOWNSIZED"}]
    assert all(d.engine == "trend" for d in accepted)
    assert all(d.meta["net_exposure_after"] <= d.meta["net_exposure_cap"] for d in accepted)
    assert all(d.meta["major_alt_balance_ok"] is True for d in accepted)


def test_allocator_enforces_symbol_and_sector_caps(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any(d.meta.get("symbol_cap_checked") for d in decisions)
    assert any(d.meta.get("sector_cap_checked") for d in decisions)
    assert any(d.status == "REJECTED" and (d.meta.get("symbol_cap_hit") or d.meta.get("sector_cap_hit")) for d in decisions)


def test_allocator_checks_conflict_against_existing_exposure(load_fixture, sample_trend_candidates):
    account = load_fixture("account_snapshot_v2.json")
    decisions = allocate_candidates(account=account, candidates=sample_trend_candidates)
    assert any("existing exposure" in " ".join(d.reasons).lower() or d.meta.get("conflict_checked") for d in decisions)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_allocator.py -v`
Expected: FAIL because allocator does not exist.

- [ ] **Step 3: Implement minimal allocator**

Implementation notes:
- Input: account exposure, regime snapshot, candidate list, config.
- Output: ordered `AllocationDecision` list.
- Apply hard validation gates before ranking, using lightweight candidate validation plus the shared risk validator where applicable.
- Enforce:
  - suppressed engines rejected before ranking
  - bucket target awareness
  - sector cap
  - symbol cap
  - duplicate setup crowding penalty
  - regime-aware final risk sizing
- Keep implementation deterministic and auditable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_allocator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/allocator.py trading_system/app/risk/validator.py trading_system/app/risk/guardrails.py trading_system/tests/test_allocator.py
git commit -m "feat: add v2 portfolio allocator"
```

## Chunk 6: Lifecycle v2 and Orchestration

### Task 16: Implement lifecycle v2 state machine

**Files:**
- Create: `trading_system/app/portfolio/lifecycle_v2.py`
- Create: `trading_system/tests/test_lifecycle_v2.py`

- [ ] **Step 1: Write the failing lifecycle tests**

```python
from trading_system.app.portfolio.lifecycle_v2 import advance_lifecycle_state
from trading_system.app.types import LifecycleState


def test_lifecycle_moves_from_init_to_confirm_on_confirmation_signal():
    state = advance_lifecycle_state(LifecycleState.INIT, {"r_multiple": 0.8, "confirmed": True})
    assert state == LifecycleState.CONFIRM


def test_lifecycle_moves_to_protect_after_profit_threshold():
    state = advance_lifecycle_state(LifecycleState.PAYLOAD, {"r_multiple": 2.2, "trend_mature": True})
    assert state == LifecycleState.PROTECT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest trading_system/tests/test_lifecycle_v2.py -v`
Expected: FAIL because file/function does not exist yet.

- [ ] **Step 3: Implement minimal lifecycle state machine**

Implementation notes:
- Keep transition rules explicit and config-driven.
- Return reason codes for each transition.
- Add helper for stop-update intent generation only if needed for current state machine tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest trading_system/tests/test_lifecycle_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/portfolio/lifecycle_v2.py trading_system/tests/test_lifecycle_v2.py
git commit -m "feat: add lifecycle v2 state machine"
```

### Task 17: Extend state storage for v2 runtime fields

**Files:**
- Modify: `trading_system/app/storage/state_store.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing storage test**

```python
from dataclasses import replace
from trading_system.app.storage.state_store import build_state_store
from trading_system.app.config import DEFAULT_CONFIG


def test_state_store_persists_regime_candidates_and_allocations(tmp_path):
    config = replace(DEFAULT_CONFIG, state_file=tmp_path / "runtime_state.json")
    store = build_state_store(config)
    state = store.load()
    state.latest_regime = {"label": "RISK_ON_TREND", "confidence": 0.8}
    state.latest_universes = {"major_count": 4, "rotation_count": 6, "short_count": 2}
    state.latest_allocations = [{"symbol": "BTCUSDT", "status": "ACCEPTED"}]
    store.save(state)
    reloaded = store.load()
    assert reloaded.latest_regime["label"] == "RISK_ON_TREND"
    assert reloaded.latest_universes["rotation_count"] == 6
    assert reloaded.latest_allocations[0]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_state_store_persists_regime_candidates_and_allocations -v`
Expected: FAIL after test is expanded to assert new v2 state fields.

- [ ] **Step 3: Implement minimal v2 state persistence**

Implementation notes:
- Persist latest regime snapshot, universes summary, candidate list summary, allocation summary, lifecycle state per position.
- Preserve backwards compatibility with existing `runtime_state.json` keys where reasonable.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_state_store_persists_regime_candidates_and_allocations -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/storage/state_store.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: persist v2 runtime state"
```

### Task 18: Wire v2 pipeline through main loop

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/portfolio/lifecycle.py`
- Create: `trading_system/app/reporting/regime_report.py`
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing orchestration test**

```python
import json
from pathlib import Path
from trading_system.app.main import main


def test_main_v2_cycle_writes_regime_and_allocation_sections(monkeypatch, tmp_path, load_fixture):
    # Arrange fixture-backed inputs and temp state path
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))

    # Act: run one cycle
    main()

    # Assert: output/state includes regime, universe, candidates, allocations
    state = json.loads(Path(output_path).read_text())
    assert state["latest_regime"]["label"]
    assert "latest_universes" in state
    assert "latest_candidates" in state
    assert "latest_allocations" in state
    assert state.get("partial_v2_coverage") is True
    assert state.get("rotation_candidates", []) == []
    assert state.get("short_candidates", []) == []
```

- [ ] **Step 2: Run test to verify it fails meaningfully**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_main_v2_cycle_writes_regime_and_allocation_sections -v`
Expected: FAIL because `main()` does not yet produce v2 outputs.

- [ ] **Step 3: Implement minimal v2 orchestration**

Implementation notes:
- Preserve paper execution mode.
- Use concrete runtime entry points wherever possible:
  - account input from `trading_system/app/main.py::load_account_snapshot()` backed by `trading_system/data/account_snapshot.json` and overrideable via `TRADING_ACCOUNT_SNAPSHOT_FILE`
  - market input from new `trading_system/app/data_sources/market_loader.py`, reading one concrete normalized file at `trading_system/data/market_context.json` and overrideable via `TRADING_MARKET_CONTEXT_FILE`
  - derivatives input from new `trading_system/app/data_sources/derivatives_loader.py`, reading a majors-only normalized snapshot file at `trading_system/data/derivatives_snapshot.json` and overrideable via `TRADING_DERIVATIVES_SNAPSHOT_FILE`
- `market_context.json` is the single runtime contract for daily/4h/1h normalized market fields used by regime, universe, and trend-engine P0.
- In tests, point all three inputs at fixture-backed temp files so Task 17 remains deterministic and offline.
- Main flow should become:
  1. load account snapshot + market inputs + derivatives snapshot
  2. classify regime
  3. build universes
  4. generate trend candidates
  5. apply hard validation gates to candidates
  6. allocate validated candidates
  7. execute accepted intents
  8. advance lifecycle updates
  9. persist/report
- Keep rotation and short engines omitted in P0 and serialize them explicitly as empty outputs, not ambiguous stubs, while marking the cycle as partial-v2 coverage.
- Keep `reporting/regime_report.py` summary-only: serialize regime inputs/outputs and allocation summary, not attribution analytics.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_main_v2_cycle_writes_regime_and_allocation_sections -v`
Expected: PASS

- [ ] **Step 5: Run focused v2 test suite**

Run: `pytest trading_system/tests -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trading_system/app/main.py trading_system/app/portfolio/lifecycle.py trading_system/app/reporting/regime_report.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: wire v2 regime and allocation pipeline"
```

## Chunk 7: Documentation and Final Verification

### Task 19: Update docs for v2 P0

**Files:**
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/MVP_ARCHITECTURE.md`

- [ ] **Step 1: Write the failing docs checklist**

```text
README must explain:
- v2 pipeline order
- P0 scope versus full v2 scope
- current engines implemented
- how to run tests
```

- [ ] **Step 2: Inspect current docs and confirm missing items**

Run: `grep -n "partial v2\|allocator\|regime" trading_system/README.md trading_system/docs/MVP_ARCHITECTURE.md`
Expected: missing or incomplete references.

- [ ] **Step 3: Update docs minimally but clearly**

Implementation notes:
- state that P0 is partial v2 coverage
- note that rotation/short engines remain future work if still unimplemented
- document new tests and runtime expectations

- [ ] **Step 4: Verify docs updates**

Run: `grep -n "partial v2\|allocator\|regime" trading_system/README.md trading_system/docs/MVP_ARCHITECTURE.md`
Expected: matching lines found

- [ ] **Step 5: Commit**

```bash
git add trading_system/README.md trading_system/docs/MVP_ARCHITECTURE.md
git commit -m "docs: describe v2 p0 pipeline"
```

### Task 20: Verify repeated-cycle idempotence

**Files:**
- Modify: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing idempotence test**

```python
import json
from pathlib import Path
from trading_system.app.main import main


def test_main_v2_cycle_is_idempotent_for_same_inputs(monkeypatch, tmp_path, load_fixture):
    output_path = tmp_path / "runtime_state.json"
    account_path = tmp_path / "account_snapshot.json"
    market_path = tmp_path / "market_context.json"
    deriv_path = tmp_path / "derivatives_snapshot.json"
    account_path.write_text(json.dumps(load_fixture("account_snapshot_v2.json")))
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")))
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")))
    monkeypatch.setenv("TRADING_STATE_FILE", str(output_path))
    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(account_path))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(deriv_path))
    main()
    first = json.loads(Path(output_path).read_text())
    main()
    second = json.loads(Path(output_path).read_text())
    assert first.get("last_signal_ids") == second.get("last_signal_ids")
    assert first.get("latest_allocations") == second.get("latest_allocations")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_main_v2_cycle_is_idempotent_for_same_inputs -v`
Expected: FAIL until repeated-cycle handling is stable.

- [ ] **Step 3: Implement the minimal idempotence fix**

Implementation notes:
- Reuse existing execution idempotency paths.
- Confirm repeated runs with the same state and inputs do not create duplicate accepted intents or duplicated processed-signal markers.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest trading_system/tests/test_main_v2_cycle.py::test_main_v2_cycle_is_idempotent_for_same_inputs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_system/tests/test_main_v2_cycle.py trading_system/app/main.py trading_system/app/execution/idempotency.py
if git diff --cached --quiet; then echo "No staged changes"; else git commit -m "test: verify v2 cycle idempotence"; fi
```

### Task 21: Final end-to-end verification

**Files:**
- No new files required

- [ ] **Step 1: Run targeted tests again**

Run: `pytest trading_system/tests -v`
Expected: PASS

- [ ] **Step 2: Run one paper cycle manually**

Run: `TRADING_ACCOUNT_SNAPSHOT_FILE=trading_system/data/account_snapshot.json TRADING_MARKET_CONTEXT_FILE=trading_system/data/market_context.json TRADING_DERIVATIVES_SNAPSHOT_FILE=trading_system/data/derivatives_snapshot.json python -m trading_system.app.main`
Expected: JSON output containing at least `regime`, `portfolio`, and execution/allocation summaries without tracebacks.

- [ ] **Step 3: Inspect runtime state**

Run: `python - <<'PY'
import json
from pathlib import Path
p = Path('trading_system/data/runtime_state.json')
state = json.loads(p.read_text())
for key in ['positions', 'management_suggestions', 'latest_regime', 'latest_allocations']:
    print(key, key in state)
print('partial_v2_coverage', state.get('partial_v2_coverage'))
PY`
Expected: existing keys preserved; v2 regime/allocation fields present.

- [ ] **Step 4: Run lightweight baseline sanity comparison**

Run: `python3 - <<'PY'
import json
from pathlib import Path
state = json.loads(Path('trading_system/data/runtime_state.json').read_text())
v2 = {
    'accepted_count': len([x for x in state.get('latest_allocations', []) if x.get('status') in {'ACCEPTED', 'DOWNSIZED'}]),
    'total_allocated_risk': round(sum(x.get('final_risk_budget', 0) for x in state.get('latest_allocations', []) if x.get('status') in {'ACCEPTED', 'DOWNSIZED'}), 6),
    'partial_v2_coverage': state.get('partial_v2_coverage'),
}
# Flat-risk/v1-style baseline: first N validated trend candidates, equal per-trade risk proxy from saved candidate metadata
cands = state.get('latest_candidates', [])
base = cands[:v2['accepted_count']] if v2['accepted_count'] else []
baseline = {
    'candidate_count': len(base),
    'equal_weight_risk_proxy': round(sum(x.get('baseline_risk_proxy', 0) for x in base), 6),
}
out = {'v2': v2, 'baseline': baseline}
Path('trading_system/data/v2_baseline_compare.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps(out, ensure_ascii=False, indent=2))
PY`
Expected: writes `trading_system/data/v2_baseline_compare.json` with side-by-side v2 and flat-risk/v1-style baseline results on the same fixed input set.

- [ ] **Step 5: Commit verification-ready state**

```bash
git add trading_system
git commit -m "test: verify trading system v2 p0"
```

## Plan Notes

- P0 intentionally implements only the regime layer, dynamic universe, trend engine, allocator, lifecycle v2, and orchestration changes.
- Rotation engine and short engine are part of the approved v2 target, but they should remain out of this P0 implementation unless the current chunk explicitly expands scope.
- Add a minimal documented comparison note during final verification: compare P0 allocator-selected risk usage and accepted-candidate mix against the prior flat-risk/v1 flow on the same fixed fixtures, even if this remains a lightweight sanity check rather than a full benchmark framework.
- If any task reveals that `app/main.py` or `app/types.py` is becoming too large, split helpers into focused modules rather than continuing to grow monoliths.
- Prefer pure functions and fixture-driven tests for factor math and routing logic.
- Preserve compatibility with current paper-execution flow so verification can happen without live trading.

## Review checkpoints

After completing each chunk above, run a focused plan review before execution handoff:
- Chunk 1: foundation types/config/tests
- Chunk 2: regime layer
- Chunk 3: universe layer
- Chunk 4: trend engine
- Chunk 5: allocator/risk routing
- Chunk 6: lifecycle/orchestration
- Chunk 7: docs/final verification

Each review should check:
- file boundaries remain clear
- tests match intended behavior
- no task quietly expands beyond P0
- commands are concrete and reproducible

Plan complete and saved to `docs/superpowers/plans/2026-03-18-trading-system-v2-implementation.md`. Ready to execute?
