# Historical Data and Backtest Dataset Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the professional-grade historical data and strategy-snapshot pipeline needed for serious backtesting: raw market history, derived feature-ready snapshots, and immutable strategy decision bundles that can feed the existing backtest stack.

**Architecture:** Use a dual-track data model. Track A stores raw exchange history and scheduled derivatives history for reproducibility and recalculation. Phase 1 makes this raw-market layer Binance-first via Binance historical APIs, with futures as the first source-of-truth market and spot deferred. Track B stores immutable per-run strategy bundles (`metadata + market_context + derivatives_snapshot + account_snapshot + runtime_state`) so the system can replay exactly what the strategy saw at each decision point. Add a lightweight archiver around the existing runtime outputs, plus importer/build scripts that convert archived raw data into backtest dataset roots compatible with `trading_system.app.backtest.dataset`.

**Tech Stack:** Python 3, existing `trading_system/app/*` modules, JSON bundle storage, pytest, uv, local filesystem archives, Binance historical APIs for the first raw-market implementation, scheduled collectors.

---

## File structure and responsibilities

### New files

- `trading_system/app/backtest/archive/__init__.py`
  - Historical archive package entry.
- `trading_system/app/backtest/archive/types.py`
  - Typed payloads for archive manifests, bundle metadata, raw data manifests, and import configs.
- `trading_system/app/backtest/archive/paths.py`
  - Canonical path builders for raw-market history, strategy-bundle archives, and imported dataset roots.
- `trading_system/app/backtest/archive/runtime_bundle.py`
  - Archive one live/runtime cycle into an immutable historical bundle.
- `trading_system/app/backtest/archive/raw_market.py`
  - Persist raw market / derivatives history files with manifest metadata.
- `trading_system/app/backtest/archive/importer.py`
  - Build backtest dataset roots from archived strategy bundles and optional raw-market enrichments.
- `trading_system/app/backtest/archive/cli.py`
  - CLI commands for archive bundle creation, listing, validation, and dataset import.
- `trading_system/tests/test_backtest_archive_runtime_bundle.py`
  - Runtime bundle archiving tests.
- `trading_system/tests/test_backtest_archive_importer.py`
  - Dataset import / assembly tests.
- `trading_system/tests/test_backtest_archive_cli.py`
  - CLI tests for archive and import flows.
- `trading_system/tests/fixtures/archive_runtime/`
  - Fixture runtime outputs for archive tests.
- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
  - The durable data model: raw-market layer, strategy-bundle layer, imported backtest dataset layer.
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
  - How to capture, archive, validate, and import historical data.
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
  - Naming, retention, immutability, and pruning rules.
- `trading_system/tests/fixtures/backtest/rotation_suppression_paper_template.json`
  - Formalized real-study config template for rotation suppression research.

### Existing files to modify

- `trading_system/app/main.py`
  - Only if a minimal post-run archive hook is needed after runtime outputs are written.
- `trading_system/app/runtime_paths.py`
  - Add canonical archive paths if needed.
- `trading_system/app/storage/state_store.py`
  - Reuse serialization helpers if doing so avoids duplication.
- `trading_system/README.md`
  - Document archive CLI / data workflow entrypoints.
- `trading_system/docs/BACKTEST_DATA_SPEC.md`
  - Extend spec to explain imported dataset roots and raw/bundle provenance.
- `trading_system/docs/BACKTEST_RUNBOOK.md`
  - Link historical-data workflow before formal research runs.

---

## Scope and data model

### Track A: Raw-market history (professional source-of-truth layer)

Store immutable raw history needed to recompute features later. Phase 1 is Binance-first and futures-first: the first implementation pulls from Binance historical APIs, treats futures as the primary market scope, and explicitly defers spot to a later expansion phase.

Collection policy:

- initial capture uses full historical backfill from the chosen research start date
- ongoing sync uses incremental refresh after backfill
- fetch sizing is coverage-driven: repeatedly use the exchange API's maximum supported pagination until the target coverage window is filled
- document coverage as `coverage_start` / `coverage_end`, not as a fixed “rows per fetch” rule

Storage root and layout:

- raw-market archive root: `trading_system/data/archive/raw-market`
- canonical path: `trading_system/data/archive/raw-market/<exchange>/<market>/<dataset>/<symbol>/<timeframe?>/`

Required phase-1 datasets:

- OHLCV by timeframe (`1h`, `4h`, `1d` initially)
- funding history
- open interest history
- deferred expansion candidates only: basis / premium / long-short ratio / taker flow / liquidation history
- fetch manifest (source, endpoint, market, dataset, fetch time, symbol set, coverage)

### Track B: Strategy runtime bundles (exact-decision replay layer)

Archive what the strategy actually saw each cycle:

- `metadata.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `account_snapshot.json`
- `runtime_state.json`
- optional `latest.json` / derived summary pointers

### Imported backtest dataset roots

Produce deterministic dataset roots compatible with the current backtest loader:

- `baseline_account_snapshot.json`
- `<bundle>/metadata.json`
- `<bundle>/market_context.json`
- `<bundle>/derivatives_snapshot.json`
- `<bundle>/account_snapshot.json`

This separates:
- raw exchange truth
- actual strategy inputs
- research-ready imported bundles

---

## Chunk 1: Runtime-bundle archiving (must land first)

### Task 1: Define archive path conventions and typed metadata

**Files:**
- Create: `trading_system/app/backtest/archive/__init__.py`
- Create: `trading_system/app/backtest/archive/types.py`
- Create: `trading_system/app/backtest/archive/paths.py`
- Test: `trading_system/tests/test_backtest_archive_runtime_bundle.py`

- [ ] **Step 1: Write the failing test**

Add tests that define the expected archive path layout for:
- raw-market history root
- runtime bundle root
- imported dataset root

Also assert bundle metadata requires:
- `timestamp`
- `run_id`
- `runtime_env`
- `source_kind`
- `schema_version`

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_runtime_bundle.py::test_archive_path_layout -q -p no:cacheprovider`
Expected: FAIL because archive path/types helpers do not exist.

- [ ] **Step 3: Write minimal implementation**

Implement typed metadata payloads and path builders using immutable timestamped directories.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/archive/__init__.py trading_system/app/backtest/archive/types.py trading_system/app/backtest/archive/paths.py trading_system/tests/test_backtest_archive_runtime_bundle.py
git commit -m "feat: add historical archive path model"
```

### Task 2: Archive a runtime cycle into an immutable strategy bundle

**Files:**
- Create: `trading_system/app/backtest/archive/runtime_bundle.py`
- Create: `trading_system/tests/fixtures/archive_runtime/`
- Test: `trading_system/tests/test_backtest_archive_runtime_bundle.py`

- [ ] **Step 1: Write the failing test**

Add a test that takes fixture runtime outputs and asserts archive creation writes:
- `metadata.json`
- `market_context.json`
- `derivatives_snapshot.json`
- `account_snapshot.json`
- `runtime_state.json`

with deterministic names and no in-place mutation of source files.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_runtime_bundle.py::test_archive_runtime_bundle_writes_expected_files -q -p no:cacheprovider`
Expected: FAIL because archive writer is missing.

- [ ] **Step 3: Write minimal implementation**

Implement bundle archiving for existing runtime outputs under `trading_system/data/runtime/*` and root fallback files.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/archive/runtime_bundle.py trading_system/tests/fixtures/archive_runtime trading_system/tests/test_backtest_archive_runtime_bundle.py
git commit -m "feat: archive runtime strategy bundles"
```

### Task 3: Add a minimal post-run archive hook

**Files:**
- Modify: `trading_system/app/main.py`
- Modify: `trading_system/app/runtime_paths.py` (if needed)
- Test: `trading_system/tests/test_main_v2_cycle.py`

- [ ] **Step 1: Write the failing test**

Add a test asserting a completed runtime cycle can optionally emit an archive bundle when archive mode is enabled.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_main_v2_cycle.py -q -p no:cacheprovider`
Expected: FAIL because no archive hook exists.

- [ ] **Step 3: Write minimal implementation**

Implement an opt-in post-run archive hook. Do not make archiving mandatory for every run until verified.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/main.py trading_system/app/runtime_paths.py trading_system/tests/test_main_v2_cycle.py
git commit -m "feat: archive runtime bundles after completed cycles"
```

---

## Chunk 2: Raw-market history layer

### Task 4: Define raw-market archive manifest and storage rules

**Files:**
- Create: `trading_system/app/backtest/archive/raw_market.py`
- Create: `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
- Test: `trading_system/tests/test_backtest_archive_importer.py`

- [ ] **Step 1: Write the failing test**

Add tests asserting raw-market storage and manifest rules for the phase-1 Binance futures layout:
- source / exchange name
- market (`futures`)
- dataset
- symbol
- timeframe when applicable
- coverage start-end
- file checksum
- fetch timestamp
- canonical path under `trading_system/data/archive/raw-market/<exchange>/<market>/<dataset>/<symbol>/<timeframe?>/`

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_importer.py::test_raw_market_manifest_metadata -q -p no:cacheprovider`
Expected: FAIL because raw-market manifest code is missing.

- [ ] **Step 3: Write minimal implementation**

Implement manifest helpers and retention docs so the storage contract explicitly matches the approved raw-market policy: Binance-first via Binance historical APIs, futures-first for phase 1, full backfill plus incremental refresh, and coverage-driven sync sizing using repeated exchange-max pagination until the requested window is filled. No broad downloader yet; focus on storage format, manifest metadata, and canonical archive paths first.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/archive/raw_market.py trading_system/docs/HISTORICAL_DATA_RETENTION.md trading_system/tests/test_backtest_archive_importer.py
git commit -m "feat: define raw market archive manifests"
```

### Task 5: Add importer that assembles research-ready dataset roots

**Files:**
- Create: `trading_system/app/backtest/archive/importer.py`
- Modify: `trading_system/docs/BACKTEST_DATA_SPEC.md`
- Test: `trading_system/tests/test_backtest_archive_importer.py`

- [ ] **Step 1: Write the failing test**

Add tests asserting the importer can take archived runtime bundles and produce a `load_historical_dataset`-compatible dataset root.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_importer.py::test_import_runtime_bundles_into_backtest_dataset_root -q -p no:cacheprovider`
Expected: FAIL because importer is missing.

- [ ] **Step 3: Write minimal implementation**

Implement importer that builds:
- `baseline_account_snapshot.json`
- bundle directories with metadata / market / derivatives / account files

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/archive/importer.py trading_system/docs/BACKTEST_DATA_SPEC.md trading_system/tests/test_backtest_archive_importer.py
git commit -m "feat: import archived bundles into backtest datasets"
```

---

## Chunk 3: Archive/import CLI and runbook

### Task 6: Add historical-data CLI entrypoints

**Files:**
- Create: `trading_system/app/backtest/archive/cli.py`
- Modify: `trading_system/README.md`
- Modify: `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- Test: `trading_system/tests/test_backtest_archive_cli.py`

- [ ] **Step 1: Write the failing test**

Add tests for commands such as:
- archive one runtime bundle
- import archive root into dataset root
- validate a dataset root

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_cli.py -q -p no:cacheprovider`
Expected: FAIL because archive CLI is missing.

- [ ] **Step 3: Write minimal implementation**

Implement narrow CLI commands only; avoid adding broad raw-data download orchestration yet.

- [ ] **Step 4: Run test to verify it passes**

Run the same command.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_system/app/backtest/archive/cli.py trading_system/docs/HISTORICAL_DATA_RUNBOOK.md trading_system/README.md trading_system/tests/test_backtest_archive_cli.py
git commit -m "feat: add historical archive CLI"
```

### Task 7: Add architecture doc explaining the professional data model

**Files:**
- Create: `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`
- Modify: `trading_system/tests/fixtures/backtest/rotation_suppression_paper_template.json`
- Test: none (docs/config only)

- [ ] **Step 1: Draft the doc**

Explain clearly:
- raw-market history layer
- strategy-bundle layer
- imported dataset layer
- why both are needed
- how this supports regime / suppression / allocation research

- [ ] **Step 2: Update the runbook**

Link historical-data workflow before formal research runs.

- [ ] **Step 3: Turn the rotation-suppression template into the documented official starting template**

Make comments/adjacent docs clear about required real dataset root replacement and intended study windows.

- [ ] **Step 4: Commit**

```bash
git add trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md trading_system/docs/BACKTEST_RUNBOOK.md trading_system/tests/fixtures/backtest/rotation_suppression_paper_template.json
git commit -m "docs: add historical data architecture guide"
```

---

## Chunk 4: Real-study enablement

### Task 8: Add runtime archive validation and first real-study checklist

**Files:**
- Modify: `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`
- Test: optional docs-only

- [ ] **Step 1: Document runtime archive validation checklist**

Checklist must include:
- minimum bundle count
- timestamp continuity
- required file presence
- account snapshot fallback rules
- no duplicate run ids

- [ ] **Step 2: Document first real study checklist**

Checklist must include:
- confirm dataset root points to historical bundles, not fixture data
- choose baseline / variant pair
- set train/validation windows
- record fee/slippage/funding assumptions
- archive resulting research bundle

- [ ] **Step 3: Commit**

```bash
git add trading_system/docs/HISTORICAL_DATA_RUNBOOK.md trading_system/docs/BACKTEST_RUNBOOK.md
git commit -m "docs: add historical data validation checklists"
```

---

## Recommended execution order

1. Chunk 1 first — get immutable runtime-bundle archiving working.
2. Chunk 2 second — make archived bundles importable into the current backtest loader.
3. Chunk 3 third — add operator-facing archive/import CLI and docs.
4. Chunk 4 last — document how to actually use the pipeline for the first real rotation-suppression study.

## Guardrails

- Do **not** start with broad raw exchange crawlers. First make runtime bundle archiving and dataset importing work.
- Do **not** add spot raw-market capture in phase 1.
- Keep archived bundles immutable; never rewrite historical bundle contents in place.
- Do not mix fixture data with real-study archive roots.
- Keep all timestamps UTC and deterministic.
- Every imported dataset bundle must be traceable back to source archive metadata.
- Prefer append-only archives and manifests over mutable “latest-only” files.
- Treat raw-market sync sizing as coverage-driven; do not redefine the research contract as fixed rows per fetch.

## What this plan enables when finished

- Professional-grade historical data capture for backtesting
- Exact replay of what the strategy actually saw on each cycle
- Real rotation-suppression studies on archived production/paper inputs
- Separation between raw exchange truth, strategy snapshots, and research-ready dataset roots

---

Plan complete and saved to `docs/superpowers/plans/2026-03-31-historical-data-and-backtest-dataset-plan.md`. Ready to execute?
