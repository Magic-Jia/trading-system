# Historical Data and Backtest Dataset Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the professional-grade historical data and strategy-snapshot pipeline needed for serious backtesting: raw market history, derived feature-ready snapshots, and immutable strategy decision bundles that can feed the existing backtest stack.

**Architecture:** Use a dual-track data model. Track A stores raw exchange history and scheduled derivatives history for reproducibility and recalculation. Track B stores immutable per-run strategy bundles (`metadata + market_context + derivatives_snapshot + account_snapshot + runtime_state`) so the system can replay exactly what the strategy saw at each decision point. Add a lightweight archiver around the existing runtime outputs, plus importer/build scripts that convert archived raw data into backtest dataset roots compatible with `trading_system.app.backtest.dataset`.

**Tech Stack:** Python 3, existing `trading_system/app/*` modules, JSON bundle storage, pytest, uv, local filesystem archives, optional Binance historical APIs / scheduled collectors.

---

## File structure and responsibilities

### New files

- `trading_system/app/backtest/archive/__init__.py`
- `trading_system/app/backtest/archive/types.py`
- `trading_system/app/backtest/archive/paths.py`
- `trading_system/app/backtest/archive/runtime_bundle.py`
- `trading_system/app/backtest/archive/raw_market.py`
- `trading_system/app/backtest/archive/importer.py`
- `trading_system/app/backtest/archive/cli.py`
- `trading_system/tests/test_backtest_archive_runtime_bundle.py`
- `trading_system/tests/test_backtest_archive_importer.py`
- `trading_system/tests/test_backtest_archive_cli.py`
- `trading_system/tests/fixtures/archive_runtime/`
- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
- `trading_system/tests/fixtures/backtest/rotation_suppression_paper_template.json`

### Existing files to modify

- `trading_system/app/main.py`
- `trading_system/app/runtime_paths.py`
- `trading_system/app/storage/state_store.py`
- `trading_system/README.md`
- `trading_system/docs/BACKTEST_DATA_SPEC.md`
- `trading_system/docs/BACKTEST_RUNBOOK.md`

---

## Chunk 1: Runtime-bundle archiving (must land first)

### Task 1: Define archive path conventions and typed metadata
### Task 2: Archive a runtime cycle into an immutable strategy bundle
### Task 3: Add a minimal post-run archive hook

## Chunk 2: Raw-market history layer
### Task 4: Define raw-market archive manifest and storage rules
### Task 5: Add importer that assembles research-ready dataset roots

## Chunk 3: Archive/import CLI and runbook
### Task 6: Add historical-data CLI entrypoints
### Task 7: Add architecture doc explaining the professional data model

## Chunk 4: Real-study enablement
### Task 8: Add runtime archive validation and first real-study checklist

## Guardrails
- Do not start with broad raw exchange crawlers.
- Keep archived bundles immutable.
- Do not mix fixture data with real-study archive roots.
- Keep all timestamps UTC and deterministic.
- Every imported dataset bundle must trace back to source archive metadata.
