# Full-Market Baseline Backtest Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first auditable full-market baseline backtest for the current trading system so Claw can measure spot（现货）+ futures（合约） performance under shared capital, realistic friction, and portfolio-level constraints before changing strategy logic again.

**Architecture:** Extend the existing `trading_system.app.backtest` stack instead of creating a second research path. Keep the current signal engines as the source of candidate generation, then add three bounded layers around them: a richer dataset/universe layer that exposes tradeability metadata, a portfolio replay layer that applies shared-capital sizing and crowding rules, and a cost/reporting layer that turns accepted trades into auditable portfolio results. Land the work in one end-to-end slice: config/types first, data materialization second, then universe/portfolio/cost replay, then reporting/CLI/docs.

**Tech Stack:** Python 3.12, existing `trading_system.app.backtest.*` modules, `trading_system.app.signals.*`, `trading_system.app.portfolio.allocator`, raw-market archive importer, pytest, markdown runbooks.

---

## Working context

- Plan file: `docs/superpowers/plans/2026-04-12-full-market-baseline-backtest-implementation-plan.md`
- Spec to implement: `docs/superpowers/specs/2026-04-12-full-market-baseline-backtest-design.md`
- Current code anchors:
  - `trading_system/app/backtest/types.py:1-99`
  - `trading_system/app/backtest/config.py:1-85`
  - `trading_system/app/backtest/dataset.py:1-94`
  - `trading_system/app/backtest/engine.py:1-150`
  - `trading_system/app/backtest/metrics.py:1-93`
  - `trading_system/app/backtest/reporting.py:1-60`
  - `trading_system/app/backtest/cli.py:1-124`
  - `trading_system/app/backtest/archive/importer.py`
  - `trading_system/tests/test_backtest_dataset.py`
  - `trading_system/tests/test_backtest_engine.py`
  - `trading_system/tests/test_backtest_metrics.py`
  - `trading_system/tests/test_backtest_archive_dataset_importer.py`
  - `trading_system/docs/BACKTEST_DATA_SPEC.md`
  - `trading_system/docs/BACKTEST_RUNBOOK.md`
- Keep this slice TDD-first: red test, minimal code, focused green run, then commit.
- Do not change signal logic in `trading_system.app.signals.*`; the backtest must replay the current strategy, not invent a new one.

## File structure / responsibility map

- Modify: `trading_system/app/backtest/types.py`
  - Extend the canonical backtest dataclasses so config, dataset rows, portfolio decisions, cost assumptions, and audit outputs are all explicit and typed.
- Modify: `trading_system/app/backtest/config.py`
  - Parse the new full-market baseline config contract: replay window, market selection, liquidity filters, capital model, slippage tiers, and output flags.
- Modify: `trading_system/app/backtest/dataset.py`
  - Load richer historical rows plus tradeability metadata from imported dataset bundles; expose deterministic window slicing for full-market replay.
- Create: `trading_system/app/backtest/universe.py`
  - Apply listing-age, liquidity, completeness, and tradeability filters; emit inclusion / exclusion audit rows.
- Create: `trading_system/app/backtest/portfolio.py`
  - Own shared-capital sizing, dynamic position cap, base-asset crowding checks, accept / resize / reject decisions, and portfolio state transitions.
- Create: `trading_system/app/backtest/costs.py`
  - Apply market-specific fees, liquidity-tier slippage, and futures-only funding accrual.
- Modify: `trading_system/app/backtest/engine.py`
  - Orchestrate full-market replay: candidate generation, universe filtering, portfolio gating, fills, exits, and ledgers.
- Modify: `trading_system/app/backtest/metrics.py`
  - Compute portfolio summary metrics, cost drag, per-market / per-year breakdowns, and utilization stats.
- Modify: `trading_system/app/backtest/reporting.py`
  - Render the new baseline backtest summary, breakdown, and audit payloads.
- Modify: `trading_system/app/backtest/cli.py`
  - Add a dedicated `full_market_baseline` experiment path and write multi-artifact output bundles.
- Modify: `trading_system/app/backtest/archive/importer.py`
  - Materialize the symbol metadata the new dataset loader depends on from imported runtime bundles / raw-market enrichments.
- Modify: `trading_system/tests/test_backtest_dataset.py`
  - Lock the richer config and dataset contract.
- Modify: `trading_system/tests/test_backtest_archive_dataset_importer.py`
  - Verify imported bundles expose the metadata required for universe filters and cost tiers.
- Create: `trading_system/tests/test_backtest_universe.py`
  - Verify listing-age, liquidity, completeness, and tradeability filtering plus exclusion ledger rows.
- Create: `trading_system/tests/test_backtest_portfolio.py`
  - Verify fixed-risk sizing, shared-capital competition, dynamic cap, crowding rejects, and resize behavior.
- Modify: `trading_system/tests/test_backtest_engine.py`
  - Verify end-to-end replay produces accepted trades, rejections, ledgers, and deterministic outputs.
- Modify: `trading_system/tests/test_backtest_metrics.py`
  - Lock return / drawdown, cost drag, utilization, and breakdown metrics.
- Create: `trading_system/tests/test_backtest_reporting.py`
  - Verify summary / breakdown / audit report rendering.
- Modify: `trading_system/docs/BACKTEST_DATA_SPEC.md`
  - Document the enriched imported dataset contract.
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`
  - Document the full-market baseline workflow, assumptions, and output interpretation.

## Implementation notes to keep fixed

- Replay window is fixed to `2021-01-01` through the latest available historical data in the dataset.
- Scope is `spot` + `futures` together; this is not a futures-only replay.
- Costs must include:
  - fee（手续费）
  - slippage（滑点）
  - funding（资金费率，仅 futures）
- Universe is “full market after liquidity filtering,” not a hand-picked majors list.
- Capital model is shared-capital only; do not add isolated-per-symbol funding paths to the baseline run.
- Position sizing is fixed-risk-per-trade, derived from stop distance.
- Dynamic position cap must be portfolio-state driven; do not hardcode a global `max_positions = N` shortcut as the only gate.
- First-stage crowding rule is minimal and explicit: do not allow duplicate same-direction spot + futures exposure on the same base asset in the baseline run.
- Every exclusion / reject / resize reason must be written into an audit ledger.
- First implementation slice is auditable baseline only; do not add parameter search, walk-forward tuning, or order-book simulation here.

## Chunk 1: Full-market baseline backtest slice

### Task 1: Extend backtest config and typed contracts for the full-market baseline

**Files:**
- Modify: `trading_system/app/backtest/types.py:1-99`
- Modify: `trading_system/app/backtest/config.py:1-85`
- Modify: `trading_system/tests/test_backtest_dataset.py`

- [ ] **Step 1: Write the failing config-contract tests**

```python
def test_load_backtest_config_parses_full_market_baseline_contract(tmp_path: Path) -> None:
    config_path = tmp_path / "full_market_baseline.json"
    config_path.write_text(
        json.dumps(
            {
                "dataset_root": "./dataset",
                "experiment_kind": "full_market_baseline",
                "baseline_name": "current_system",
                "variant_name": "auditable_baseline",
                "sample_windows": [
                    {
                        "name": "full_history",
                        "start": "2021-01-01T00:00:00Z",
                        "end": "2026-01-01T00:00:00Z",
                        "split": "in_sample",
                    }
                ],
                "markets": ["spot", "futures"],
                "universe": {
                    "listing_age_days": 90,
                    "min_quote_volume_usdt_24h": {"spot": 5_000_000, "futures": 20_000_000},
                    "require_complete_funding": True,
                },
                "capital": {
                    "model": "shared_pool",
                    "initial_equity": 100000.0,
                    "risk_per_trade": 0.005,
                    "max_open_risk": 0.03,
                },
                "costs": {
                    "fee_bps": {"spot": 10.0, "futures": 5.0},
                    "slippage_tiers": {
                        "top": 4.0,
                        "high": 8.0,
                        "medium": 15.0,
                        "low": 30.0,
                    },
                    "funding_mode": "historical_series",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = load_backtest_config(config_path)

    assert loaded.experiment_kind == "full_market_baseline"
    assert loaded.markets == ("spot", "futures")
    assert loaded.capital.model == "shared_pool"
    assert loaded.capital.risk_per_trade == pytest.approx(0.005)
    assert loaded.universe.require_complete_funding is True
    assert loaded.costs.fee_bps_by_market["futures"] == pytest.approx(5.0)
    assert loaded.costs.slippage_bps_by_tier["medium"] == pytest.approx(15.0)
```

- [ ] **Step 2: Run the focused config tests to verify they fail**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py::test_load_backtest_config_parses_full_market_baseline_contract -q -p no:cacheprovider`

Expected: FAIL because the current config / type layer only supports flat `fee_bps`, `slippage_bps`, and `funding_bps_per_day` fields.

- [ ] **Step 3: Implement the minimal typed contract**

```python
@dataclass(frozen=True, slots=True)
class UniverseFilterConfig:
    listing_age_days: int
    min_quote_volume_usdt_24h: dict[str, float]
    require_complete_funding: bool = True


@dataclass(frozen=True, slots=True)
class CapitalModelConfig:
    model: Literal["shared_pool"]
    initial_equity: float
    risk_per_trade: float
    max_open_risk: float


@dataclass(frozen=True, slots=True)
class BacktestCosts:
    fee_bps_by_market: dict[str, float]
    slippage_bps_by_tier: dict[str, float]
    funding_mode: Literal["historical_series"]
```

Update `load_backtest_config(...)` to parse these nested objects without breaking the existing historical dataset path for non-`full_market_baseline` configs.

- [ ] **Step 4: Re-run the focused config tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py::test_load_backtest_config_parses_full_market_baseline_contract -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the config/type slice**

```bash
git add trading_system/app/backtest/types.py trading_system/app/backtest/config.py trading_system/tests/test_backtest_dataset.py
git commit -m "feat: add full-market backtest config contract"
```

### Task 2: Materialize the richer dataset rows the baseline replay needs

**Files:**
- Modify: `trading_system/app/backtest/archive/importer.py`
- Modify: `trading_system/app/backtest/dataset.py:1-94`
- Modify: `trading_system/app/backtest/types.py`
- Modify: `trading_system/tests/test_backtest_archive_dataset_importer.py`
- Modify: `trading_system/tests/test_backtest_dataset.py`

- [ ] **Step 1: Write the failing importer / dataset tests**

```python
def test_imported_dataset_bundle_exposes_tradeability_metadata(tmp_path: Path) -> None:
    dataset_root = build_imported_dataset_root_with_symbol(
        tmp_path,
        symbol="BTCUSDT",
        market_type="futures",
        base_asset="BTC",
        listing_timestamp="2020-01-01T00:00:00Z",
        quote_volume_usdt_24h=250_000_000.0,
        liquidity_tier="high",
        quantity_step="0.001",
        price_tick="0.1",
        funding_series_complete=True,
    )

    rows = load_historical_dataset(dataset_root)

    symbol_row = rows[0].instrument_rows[0]
    assert symbol_row.symbol == "BTCUSDT"
    assert symbol_row.market_type == "futures"
    assert symbol_row.base_asset == "BTC"
    assert symbol_row.quote_volume_usdt_24h == pytest.approx(250_000_000.0)
    assert symbol_row.liquidity_tier == "high"
    assert symbol_row.has_complete_funding is True
```

- [ ] **Step 2: Run the focused importer / dataset tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_dataset_importer.py trading_system/tests/test_backtest_dataset.py -q -p no:cacheprovider`

Expected: FAIL because current imported rows only expose `market`, `derivatives`, and `account`, not normalized instrument-level metadata.

- [ ] **Step 3: Implement the richer dataset materialization**

```python
@dataclass(frozen=True, slots=True)
class InstrumentSnapshotRow:
    symbol: str
    market_type: Literal["spot", "futures"]
    base_asset: str
    listing_timestamp: datetime
    quote_volume_usdt_24h: float
    liquidity_tier: str
    quantity_step: float
    price_tick: float
    has_complete_funding: bool
```

Teach `archive/importer.py` to derive these fields from imported runtime bundles / raw-market enrichments and teach `dataset.py` to load them into `DatasetSnapshotRow.instrument_rows`.

- [ ] **Step 4: Re-run the focused importer / dataset tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_archive_dataset_importer.py trading_system/tests/test_backtest_dataset.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the dataset-materialization slice**

```bash
git add trading_system/app/backtest/archive/importer.py trading_system/app/backtest/dataset.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_archive_dataset_importer.py trading_system/tests/test_backtest_dataset.py
git commit -m "feat: expose baseline backtest instrument metadata"
```

### Task 3: Add universe filtering and exclusion audit rows

**Files:**
- Create: `trading_system/app/backtest/universe.py`
- Modify: `trading_system/app/backtest/types.py`
- Create: `trading_system/tests/test_backtest_universe.py`

- [ ] **Step 1: Write the failing universe-filter tests**

```python
def test_filter_universe_excludes_symbols_that_fail_listing_age_or_liquidity() -> None:
    rows = [
        make_instrument(symbol="BTCUSDT", market_type="spot", listing_age_days=400, quote_volume_usdt_24h=20_000_000),
        make_instrument(symbol="NEWCOINUSDT", market_type="spot", listing_age_days=10, quote_volume_usdt_24h=50_000_000),
        make_instrument(symbol="THINUSDT", market_type="futures", listing_age_days=200, quote_volume_usdt_24h=500_000),
    ]

    included, excluded = filter_universe(rows, universe_config=sample_universe_config())

    assert [row.symbol for row in included] == ["BTCUSDT"]
    assert {(row.symbol, row.reason_code) for row in excluded} == {
        ("NEWCOINUSDT", "listing_age_below_minimum"),
        ("THINUSDT", "quote_volume_below_minimum"),
    }
```

- [ ] **Step 2: Run the focused universe tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_universe.py -q -p no:cacheprovider`

Expected: FAIL because `trading_system.app.backtest.universe` does not exist yet.

- [ ] **Step 3: Implement the universe filter module**

```python
def filter_universe(
    instrument_rows: Sequence[InstrumentSnapshotRow], *, universe_config: UniverseFilterConfig
) -> tuple[list[InstrumentSnapshotRow], list[UniverseExclusionRow]]:
    ...
```

Support at least four exclusion reasons:
- `listing_age_below_minimum`
- `quote_volume_below_minimum`
- `missing_funding_series`
- `missing_tradeability_metadata`

- [ ] **Step 4: Re-run the focused universe tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_universe.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the universe-filter slice**

```bash
git add trading_system/app/backtest/universe.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_universe.py
git commit -m "feat: add baseline universe filters"
```

### Task 4: Add shared-capital sizing, dynamic cap, and crowding decisions

**Files:**
- Create: `trading_system/app/backtest/portfolio.py`
- Modify: `trading_system/app/backtest/types.py`
- Create: `trading_system/tests/test_backtest_portfolio.py`

- [ ] **Step 1: Write the failing portfolio-decision tests**

```python
def test_allocate_candidate_respects_shared_capital_and_base_asset_crowding() -> None:
    state = make_portfolio_state(initial_equity=100000.0, open_positions=[make_position(symbol="BTCUSDT", market_type="spot", base_asset="BTC")])
    candidate = make_candidate(symbol="BTCUSDT_PERP", market_type="futures", base_asset="BTC", entry_price=60000.0, stop_loss=57000.0)

    decision = evaluate_candidate(candidate, state=state, capital=sample_capital_config())

    assert decision.status == "rejected"
    assert "base_asset_same_direction_crowding" in decision.reasons
```

```python
def test_allocate_candidate_resizes_when_risk_budget_is_partially_available() -> None:
    state = make_portfolio_state(initial_equity=100000.0, max_open_risk_remaining=0.0025)
    candidate = make_candidate(symbol="ETHUSDT", market_type="spot", base_asset="ETH", entry_price=3000.0, stop_loss=2850.0)

    decision = evaluate_candidate(candidate, state=state, capital=sample_capital_config())

    assert decision.status == "resized"
    assert decision.final_risk_budget == pytest.approx(0.0025)
    assert decision.position_notional > 0
```

- [ ] **Step 2: Run the focused portfolio tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_portfolio.py -q -p no:cacheprovider`

Expected: FAIL because `trading_system.app.backtest.portfolio` does not exist yet.

- [ ] **Step 3: Implement the minimal portfolio replay helpers**

```python
@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    status: Literal["accepted", "resized", "rejected"]
    reasons: tuple[str, ...]
    final_risk_budget: float
    position_notional: float
    qty: float
```

Implement helpers for:
- fixed-risk sizing from stop distance
- shared-pool risk accounting
- dynamic cap inputs from open risk + capital usage + active positions
- same-base-asset crowding rejects
- acceptance / resize / rejection ledgers

- [ ] **Step 4: Re-run the focused portfolio tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_portfolio.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the portfolio-decision slice**

```bash
git add trading_system/app/backtest/portfolio.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_portfolio.py
git commit -m "feat: add shared-capital backtest portfolio decisions"
```

### Task 5: Add realistic cost handling and end-to-end full-market replay

**Files:**
- Create: `trading_system/app/backtest/costs.py`
- Modify: `trading_system/app/backtest/engine.py:1-150`
- Modify: `trading_system/app/backtest/types.py`
- Modify: `trading_system/tests/test_backtest_engine.py`
- Modify: `trading_system/tests/test_backtest_metrics.py`

- [ ] **Step 1: Write the failing replay / cost tests**

```python
def test_replay_full_market_baseline_emits_trades_rejections_and_cost_drag(sample_baseline_dataset: Path) -> None:
    config = load_backtest_config(sample_baseline_config(sample_baseline_dataset))

    result = replay_full_market_baseline(config)

    assert result.portfolio_summary.trade_count > 0
    assert result.portfolio_summary.max_drawdown <= 0.0
    assert result.trade_ledger
    assert result.rejection_ledger
    assert result.cost_breakdown["fees"] > 0.0
    assert result.cost_breakdown["slippage"] > 0.0
    assert "funding" in result.cost_breakdown
```

- [ ] **Step 2: Run the focused replay / metrics tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_metrics.py -q -p no:cacheprovider`

Expected: FAIL because the current engine only replays one snapshot worth of candidates and returns assumptions, not a trade ledger or portfolio replay result.

- [ ] **Step 3: Implement the minimal cost + replay path**

```python
def replay_full_market_baseline(config: BacktestConfig) -> BaselineReplayResult:
    rows = load_historical_dataset(config.dataset_root)
    ...
    # filter universe -> generate candidates -> portfolio decision -> apply fees/slippage/funding -> update state
    return result
```

Keep the first implementation deterministic and bar-based:
- fees by market type
- slippage by liquidity tier
- funding only for futures holding intervals
- accepted / resized / rejected paths all produce ledger rows

- [ ] **Step 4: Re-run the focused replay / metrics tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_metrics.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the replay / cost slice**

```bash
git add trading_system/app/backtest/costs.py trading_system/app/backtest/engine.py trading_system/app/backtest/types.py trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_metrics.py
git commit -m "feat: add full-market baseline replay engine"
```

### Task 6: Add reporting, CLI output bundles, and runbook coverage

**Files:**
- Modify: `trading_system/app/backtest/reporting.py:1-60`
- Modify: `trading_system/app/backtest/cli.py:1-124`
- Create: `trading_system/tests/test_backtest_reporting.py`
- Modify: `trading_system/docs/BACKTEST_DATA_SPEC.md`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`

- [ ] **Step 1: Write the failing reporting / CLI tests**

```python
def test_render_full_market_baseline_report_contains_summary_breakdowns_and_audit_counts() -> None:
    report = render_full_market_baseline_report(sample_baseline_result())

    assert report["summary"]["total_return"] == pytest.approx(0.12)
    assert "by_market" in report["breakdowns"]
    assert "by_year" in report["breakdowns"]
    assert report["audit"]["rejection_count"] == 3
```

```python
def test_backtest_cli_writes_full_market_baseline_bundle(tmp_path: Path) -> None:
    exit_code = main([
        "run",
        "--config",
        str(sample_full_market_config(tmp_path)),
        "--output-dir",
        str(tmp_path / "out"),
    ])

    assert exit_code == 0
    assert (tmp_path / "out" / "full_market_baseline__current_system__auditable_baseline" / "summary.json").exists()
    assert (tmp_path / "out" / "full_market_baseline__current_system__auditable_baseline" / "audit.json").exists()
```

- [ ] **Step 2: Run the focused reporting / CLI tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_reporting.py trading_system/tests/test_backtest_engine.py -q -p no:cacheprovider`

Expected: FAIL because the current reporting / CLI stack only knows the regime-research bundle shape.

- [ ] **Step 3: Implement the reporting / CLI bundle shape**

```python
artifacts = [
    "manifest.json",
    "summary.json",
    "breakdowns.json",
    "audit.json",
]
```

Update the runbook with:
- exact config fields
- exact output files
- how to interpret summary vs breakdown vs audit ledgers
- the baseline limitations that remain intentionally out of scope

- [ ] **Step 4: Re-run the focused reporting / CLI tests**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_reporting.py trading_system/tests/test_backtest_engine.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 5: Commit the reporting / CLI slice**

```bash
git add trading_system/app/backtest/reporting.py trading_system/app/backtest/cli.py trading_system/tests/test_backtest_reporting.py trading_system/docs/BACKTEST_DATA_SPEC.md trading_system/docs/BACKTEST_RUNBOOK.md
git commit -m "feat: add full-market baseline backtest reporting"
```

### Task 7: Run the final focused verification for the full-market baseline slice

**Files:**
- Modify: `trading_system/tests/test_backtest_dataset.py`
- Modify: `trading_system/tests/test_backtest_archive_dataset_importer.py`
- Modify: `trading_system/tests/test_backtest_universe.py`
- Modify: `trading_system/tests/test_backtest_portfolio.py`
- Modify: `trading_system/tests/test_backtest_engine.py`
- Modify: `trading_system/tests/test_backtest_metrics.py`
- Modify: `trading_system/tests/test_backtest_reporting.py`
- Modify: `trading_system/docs/BACKTEST_RUNBOOK.md`

- [ ] **Step 1: Add any missing cross-layer regression tests**

```python
def test_full_market_baseline_replay_is_deterministic_for_same_dataset_and_config() -> None:
    first = replay_full_market_baseline(load_backtest_config(sample_config_path()))
    second = replay_full_market_baseline(load_backtest_config(sample_config_path()))

    assert first.portfolio_summary == second.portfolio_summary
    assert first.trade_ledger == second.trade_ledger
    assert first.rejection_ledger == second.rejection_ledger
```

- [ ] **Step 2: Run the full focused baseline suite**

Run:
`uv run --with pytest python -m pytest trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_archive_dataset_importer.py trading_system/tests/test_backtest_universe.py trading_system/tests/test_backtest_portfolio.py trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_metrics.py trading_system/tests/test_backtest_reporting.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 3: Smoke-test the CLI bundle path**

Run:
`uv run --with pytest python -m trading_system.app.backtest.cli run --config trading_system/tests/fixtures/backtest/full_market_baseline.json --output-dir /tmp/trading-system-backtest-smoke`

Expected: prints one output bundle directory and writes `manifest.json`, `summary.json`, `breakdowns.json`, and `audit.json`.

- [ ] **Step 4: Document any final runbook clarifications discovered during verification**

Update `trading_system/docs/BACKTEST_RUNBOOK.md` so an engineer can reproduce:
- the required dataset inputs
- the config contract
- the meaning of the audit ledgers
- the current intentional limitations of the baseline

- [ ] **Step 5: Commit the verification / docs closeout**

```bash
git add trading_system/tests/test_backtest_dataset.py trading_system/tests/test_backtest_archive_dataset_importer.py trading_system/tests/test_backtest_universe.py trading_system/tests/test_backtest_portfolio.py trading_system/tests/test_backtest_engine.py trading_system/tests/test_backtest_metrics.py trading_system/tests/test_backtest_reporting.py trading_system/docs/BACKTEST_RUNBOOK.md
git commit -m "test: verify full-market baseline backtest flow"
```

## Recommended execution order

1. Task 1 first — freeze the config / type contract before touching replay logic.
2. Task 2 second — expose the metadata the baseline run depends on.
3. Task 3 third — land universe filters before portfolio decisions so exclusions are explicit.
4. Task 4 fourth — add shared-capital and crowding decisions.
5. Task 5 fifth — connect costs + deterministic replay.
6. Task 6 sixth — make the outputs readable and runnable.
7. Task 7 last — run the focused suite and close the documentation loop.

Plan complete and saved to `docs/superpowers/plans/2026-04-12-full-market-baseline-backtest-implementation-plan.md`. Ready to execute?