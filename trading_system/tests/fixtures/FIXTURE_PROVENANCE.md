# v2 Fixture Provenance

All fixtures in this directory are deterministic and offline-safe.

## `account_snapshot_v2.json`

- Source: fully synthetic account state.
- Purpose: stable account payload for v2 tests.
- Preserved schema shape:
  - top-level `as_of`, balances, `open_positions`, `open_orders`, `meta`
  - positions include symbol/side/qty/pricing/notional/leverage/strategy metadata

## `market_context_v2.json`

- Source: synthetic but modeled after normalized exchange snapshot conventions.
- Purpose: single normalized market context contract with multi-timeframe fields.
- Preserved schema shape:
  - top-level `as_of`, `schema_version`, `symbols`
  - per-symbol `sector`, `liquidity_tier`, and `daily`/`4h`/`1h` metric blocks

## `derivatives_snapshot_v2.json`

- Source: fully synthetic derivatives metrics.
- Purpose: deterministic majors-focused funding/open-interest context with price-aware OI interaction inputs.
- Preserved schema shape:
  - top-level `as_of`, `schema_version`, `rows`
  - row fields include `symbol`, `funding_rate`, `open_interest_usdt`,
    `open_interest_change_24h_pct`, `mark_price_change_24h_pct`,
    `taker_buy_sell_ratio`, `basis_bps`
