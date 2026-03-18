# Trading System v2 Design

Date: 2026-03-18
Owner: Claw
Status: Draft approved by user for spec write-up

## 1. Goal

Trading System v2 upgrades the current rule-based crypto trading workflow into a regime-aware, portfolio-managed system built for balanced performance rather than single-metric optimization.

Primary target:
- balance return, drawdown, and win-rate at the portfolio level

Selected operating style:
- trend + rotation
- multi-timeframe
- majors + strong mid/small caps
- shorts enabled only for majors
- risk budget allocated by strategy tier rather than one flat setting

## 2. Design Principles

1. Risk comes before prediction.
2. Market regime decides how aggressive the system is allowed to be.
3. Different opportunity types must be separated into different engines.
4. Portfolio allocation is a first-class layer, not an afterthought.
5. Position lifecycle management is as important as entry quality.
6. The system should remain interpretable and auditable.
7. v2 should extend the current app skeleton rather than rewrite everything.

## 3. Non-Goals

v2 does not aim to include, in its first phase:
- machine learning prediction models
- heavy on-chain data dependence
- news or NLP sentiment as a primary driver
- all-market scanning of hundreds of illiquid coins
- ultra-short-term scalping
- many loosely-defined sub-strategies

## 4. High-Level Architecture

v2 is organized into four decision layers:

1. Market regime layer
2. Signal engine layer
3. Portfolio allocation layer
4. Execution and lifecycle layer

### 4.1 Market Regime Layer
Determines the current market environment and sets permissions plus risk multipliers for the rest of the system.

Outputs:
- regime label
- confidence score
- risk multiplier
- preference tilt toward majors, rotation, or defense
- bucket allocation guidance for trend, rotation, and short engines
- execution policy adjustments: normal, downsize, or suppress

Proposed regime labels:
- `RISK_ON_TREND`
- `RISK_ON_ROTATION`
- `MIXED`
- `RISK_OFF`
- `HIGH_VOL_DEFENSIVE`

Mechanical mapping requirement:
- every regime must publish bucket target ranges for trend, rotation, and short risk
- every regime must publish suppression rules for low-priority engines
- allocator must consume regime output directly rather than infer its own posture
- regime confidence must scale aggressiveness: lower confidence reduces bucket ceilings and lowers execution priority for borderline setups

### 4.2 Signal Engine Layer
Contains separate engines for distinct opportunity types.

Engines:
- trend engine
- rotation engine
- short engine

Each engine outputs candidate trade signals, not orders.

### 4.3 Portfolio Allocation Layer
Ranks and filters candidate signals using portfolio-aware constraints.

Responsibilities:
- assign risk budget by engine and market state
- manage net long/short exposure
- control concentration by symbol and sector
- prioritize which signals execute now
- reject or downsize lower-quality or redundant signals

### 4.4 Execution and Lifecycle Layer
Executes approved intents and manages existing positions through predefined states.

Responsibilities:
- order placement
- protective stop management
- staged profit-taking
- trailing protection
- invalidation exits
- state persistence and recovery

## 5. Strategy Set

## 5.1 Trend Engine
Purpose:
- capture continuation in major coins and high-liquidity strong names

Allowed instruments:
- majors by default
- optional high-liquidity strong coins if they pass regime and liquidity gates

Signal types:
- `BREAKOUT_CONTINUATION`
- `PULLBACK_CONTINUATION`

Core idea:
- daily timeframe defines directional bias
- 4h timeframe defines trade structure
- 1h timeframe defines execution timing

## 5.2 Rotation Engine
Purpose:
- capture relative-strength leadership among strong mid/small caps

Allowed instruments:
- strong mid/small caps that pass liquidity and quality filters

Universe inclusion requirements for rotation names:
- minimum rolling traded notional threshold
- minimum order-book depth or acceptable slippage estimate
- minimum listing age to avoid unstable fresh listings
- exclusion of names with repeated extreme wick behavior or unreliable execution quality
- optional market-cap banding once reliable data is available

Signal types:
- `RS_PULLBACK`
- `RS_REACCELERATION`

Core idea:
- only trade coins that already proved relative strength
- enter on controlled pullback or renewed acceleration
- avoid low-quality one-bar spikes and random oversold bounces

## 5.3 Short Engine
Purpose:
- provide downside participation and portfolio defense during risk contraction or major trend breaks

Portfolio intent:
- shorts may be used defensively to offset long exposure and opportunistically to capture high-quality downside moves in majors
- shorts are not intended to become the dominant default posture unless the regime layer explicitly permits a defensive bias

Allowed instruments:
- majors only

Signal types:
- `BREAKDOWN_SHORT`
- `FAILED_BOUNCE_SHORT`

Core idea:
- short only when structure weakens clearly
- prefer breakdowns and failed bounces over blind top-calling

## 6. Factor Framework

The v2 factor set is intentionally compact and role-specific.

## 6.1 Regime Factors

### A. Broad trend factors
Inputs:
- daily 20 EMA / 50 EMA relationship on BTC, ETH, and a broad alt proxy basket
- 4h 20 EMA / 50 EMA relationship
- EMA slope
- rolling 10- and 20-bar returns

Purpose:
- identify whether the environment is trending or unstable

### B. Breadth factors
Inputs:
- share of universe above 4h 20 EMA
- share of universe with 4h 20 EMA above 50 EMA
- share of universe with positive short-term momentum
- new-high vs new-low counts

Purpose:
- distinguish isolated strength from broad participation

### C. Major-vs-alt relative strength factors
Inputs:
- return spread between major basket and rotation basket
- dominance proxy
- count of strong alt leaders

Purpose:
- decide whether to tilt the system toward majors, alts, or defense

### D. Derivatives risk factors
Inputs for majors:
- funding rate
- open interest change
- price/OI interaction
- basis where available

Purpose:
- detect healthy trend participation vs crowded leverage

### E. Volatility and stress factors
Inputs:
- ATR%
- realized volatility
- extreme single-bar expansion
- shock-event detection

Purpose:
- compress risk during unstable conditions

## 6.2 Trend Engine Factors
- multi-timeframe structure alignment
- trend strength
- breakout quality or pullback quality
- volume confirmation
- derivatives confirmation for majors

Suggested conditions:
- daily bias aligned with trade direction
- 4h structure intact
- 1h confirms entry timing
- avoid late-stage overextension

## 6.3 Rotation Engine Factors
- relative strength versus USDT and versus BTC/ETH proxies
- persistence of leadership over multiple windows
- pullback quality after leadership is established
- liquidity quality
- volatility quality

Timeframe hierarchy:
- daily provides broad participation context
- 4h defines rotation structure and leadership persistence
- 1h confirms pullback hold or renewed acceleration

The rotation engine should prefer leaders that remain strong through pullbacks, not coins that spike once and collapse.

## 6.4 Short Engine Factors
- structure break on 4h or daily
- failed bounce into resistance or moving averages
- bearish derivatives confirmation
- regime support for defensive posture

Timeframe hierarchy:
- daily defines whether the broader environment supports defensive or bearish positioning
- 4h defines breakdown or failed-bounce structure
- 1h confirms execution timing and risk placement

## 7. Scoring Model

v2 should not use one single undifferentiated score across all opportunity types.

Instead, use a three-step process:

### Step 1: Eligibility Gate
A candidate must first pass:
- liquidity threshold
- structural validity
- regime permission
- stop-loss sanity checks
- risk-budget feasibility
- conflict checks against existing exposure

Candidates that fail any hard gate are discarded.

### Step 2: Engine-Specific Scoring
Each engine scores its own candidates using engine-specific factors.

Trend engine score components:
- timeframe alignment
- trend strength
- setup quality
- volume quality
- derivatives confirmation

Rotation engine score components:
- relative strength rank
- persistence
- pullback quality
- liquidity quality
- volatility quality

Short engine score components:
- structure break quality
- failed-bounce quality
- derivatives weakness
- regime alignment

### Step 3: Portfolio Allocation Weighting
The allocator adjusts signal priority using portfolio context:
- current exposure crowding
- sector duplication
- net directional bias
- strategy bucket saturation
- remaining total risk budget

## 8. Risk Framework

## 8.1 Risk Philosophy
Risk is assigned by strategy class and adjusted by regime, not treated as one flat percentage for every trade.

## 8.2 Initial Risk Tiers
Suggested starting ranges:

- major trend longs: 0.60% to 0.80% per trade
- rotation longs: 0.35% to 0.55% per trade
- major shorts: 0.30% to 0.50% per trade

## 8.3 Portfolio Risk Budget
Suggested total active risk ranges:

- normal conditions: 2.5% to 3.5%
- strong risk-on conditions: 3.5% to 4.5%
- risk-off conditions: 1.0% to 2.0%

## 8.4 Bucket Allocation Guidance
Bucket allocation must be regime-specific rather than global.

Default balanced reference profile under normal mixed conditions:
- major trend bucket: 40% to 60%
- rotation bucket: 20% to 35%
- short bucket: 0% to 25%

Override rule:
- regime output has priority over the reference profile
- in `RISK_OFF` or `HIGH_VOL_DEFENSIVE`, rotation exposure may be reduced toward zero and short exposure may expand within the active-risk cap
- in `RISK_ON_ROTATION`, the rotation bucket may exceed the normal mixed-condition reference range if the regime classifier explicitly authorizes it

## 8.5 Portfolio Constraints
Allocator must enforce:
- total active risk cap
- symbol concentration cap
- sector concentration cap
- net long/short exposure cap
- major-vs-alt exposure balance
- duplicate setup crowding limits

Minimum reproducible allocator definitions:
- sector concentration: no more than a configured share of active risk may sit in one sector bucket
- major-vs-alt balance: allocator must respect regime-provided bucket targets rather than static equal weighting
- duplicate setup crowding: multiple signals with the same engine, same direction, and highly similar setup type must be progressively down-weighted after the first accepted names
- trade suppression: if a bucket is disabled by regime, candidates from that bucket are rejected before ranking

## 9. Position Lifecycle Design

Each position must move through explicit lifecycle states.

States:
- `INIT`
- `CONFIRM`
- `PAYLOAD`
- `PROTECT`
- `EXIT`

## 9.1 INIT
- initial stop is active
- risk is fully defined
- no premature tightening without confirmation
- default entry state immediately after fill

Transition out of INIT:
- move to `CONFIRM` only after predefined confirmation criteria are met, such as favorable movement by a minimum R multiple, successful retest, or 1h structure confirmation

## 9.2 CONFIRM
- trade has started to work
- some risk may be reduced
- stop may be improved modestly if the structure justifies it

Transition out of CONFIRM:
- move to `PAYLOAD` when the trade shows sustained continuation and the remaining position is intended to run for the main trend leg
- move to `EXIT` if confirmation fails and the setup is invalidated

## 9.3 PAYLOAD
- the trade is in its main profit window
- the system should allow the position to run
- management should avoid noise-driven exits

Transition out of PAYLOAD:
- move to `PROTECT` after a meaningful profit threshold or mature trend condition is reached
- move to `EXIT` on structural failure or allocator-forced reduction that fully closes the position

## 9.4 PROTECT
- profit already exists and must be defended
- trailing logic becomes more important than extension logic

Transition out of PROTECT:
- move to `EXIT` when trailing protection, structure break, or risk-event logic closes the remaining position

## 9.5 EXIT
All exits must be classified clearly:
- stop-loss exit
- structure-invalidated exit
- target-based exit
- allocator-forced exit
- emergency risk exit

## 10. Stop and Profit Management

## 10.1 Initial Stop
Initial stop should be derived from structure plus volatility, not from a volatility template alone.

## 10.2 Break-Even Logic
Break-even upgrades should occur only after real confirmation, not immediately after minor favorable movement.

## 10.3 Trailing Logic
Two trailing modes should be supported:
- structure-based trailing using swing levels
- volatility-based trailing using ATR-style logic

Default preference:
- trend positions: structure-led trailing
- rotation positions: hybrid structure + volatility trailing
- short positions: faster protection and faster realization

## 10.4 Profit-Taking Logic
### Trend positions
- first reduction to recover some risk
- keep core size for the main trend leg
- final exit mostly driven by trailing or structure failure

### Rotation positions
- take more partial profit earlier
- leave reduced runner size afterward

### Short positions
- realize profits faster
- avoid overstaying once momentum is exhausted

## 11. Module Design

v2 should extend the existing `trading_system/app/` structure with focused modules.

### 11.1 New or expanded modules

#### `app/market_regime/`
- `classifier.py`
- `breadth.py`
- `derivatives.py`

Purpose:
- generate regime labels, confidence, and risk multipliers

#### `app/universe/`
- `builder.py`
- `liquidity_filter.py`
- `sector_map.py`

Purpose:
- build tradeable universes for majors, rotation names, and shortable majors
- provide a temporary fallback taxonomy until a richer sector map exists

Temporary fallback taxonomy:
- majors
- L1/L2 infrastructure
- DeFi
- AI/data
- meme/speculative
- exchange / platform
- other / uncategorized

#### `app/signals/`
- `trend_engine.py`
- `rotation_engine.py`
- `short_engine.py`
- `scoring.py`

Purpose:
- generate and score engine-specific candidate signals

#### `app/portfolio/`
- `allocator.py`
- `lifecycle_v2.py`
- `exposure.py`

Purpose:
- allocate risk and manage state transitions for live positions

#### `app/risk/`
- extend `validator.py`
- extend `position_sizer.py`
- extend `guardrails.py`
- add `regime_risk.py`

Purpose:
- convert raw setup quality into allowed size under regime-aware constraints

#### `app/reporting/`
- `performance_attribution.py`
- `regime_report.py`

Purpose:
- attribute performance by engine, regime, sector, and setup type

## 12. Runtime Data Flow

Each trading cycle should follow this order:

1. load market, derivatives, account, and state data
2. classify market regime
3. build tradeable universes
4. run all signal engines
5. apply hard validation gates
6. allocate portfolio risk and select execution candidates
7. execute approved intents
8. update position lifecycle states
9. persist state, journal decisions, and produce reports

## 13. Scheduling Model

Different layers should run at different frequencies.

### Daily layer
- update broad regime context
- update baseline risk posture

### 4h layer
- primary signal-generation cycle
- update structures and candidate lists

### 1h layer
- refine execution timing
- manage lifecycle transitions

### 5-15 minute risk patrol layer
- verify protective orders
- detect abnormal movement
- detect stale or broken order state
- trigger emergency defense if needed

## 14. Implementation Priority

## 14.1 P0
Must-have components for v2 minimum viability:
- market regime classifier
- dynamic universe builder with liquidity filtering
- trend engine v2
- portfolio allocator
- lifecycle manager v2

## 14.2 P1
Second-wave enhancements:
- rotation engine
- short engine
- performance attribution
- richer derivatives integration

Note on scope:
- shorts are part of the approved v2 operating style, but the first implementation phase may still defer the short engine until after regime, trend, allocator, and lifecycle foundations are stable
- until the short engine ships, the live system should be treated as partial v2 coverage rather than full three-engine parity

## 15. Minimum Viable v2

The minimum viable v2 is intentionally partial rather than the full approved end-state.

A minimum viable v2 is reached when the system can:
- classify market regime
- build a non-hardcoded tradable universe
- generate multi-timeframe trend signals
- allocate risk using portfolio-aware logic
- manage active positions through upgraded lifecycle rules

At MVP stage:
- trend engine is required
- rotation and short engines may still be pending if the regime, allocator, and lifecycle foundation is not yet stable
- the live system must therefore be labeled partial v2 coverage until all approved engines are implemented

This is enough to move beyond a lightweight rule script and into a regime-aware portfolio trading system, while still stopping short of the final approved v2 scope.

## 16. Testing and Validation Expectations

The design should be implemented with evidence-first validation.

Required validation themes:
- factor calculations are deterministic and testable
- regime labels are reproducible from fixed snapshots
- engine scoring behaves correctly on representative cases
- allocator respects all hard caps
- lifecycle state transitions are test-covered
- execution remains idempotent under repeated cycles
- portfolio-level evaluation compares v2 against flat-risk or single-engine baselines to verify whether balance improves in practice rather than only in theory

## 17. Open Questions for Later Phases

These are intentionally deferred and should not block v2 P0:
- exact sector taxonomy for alt buckets
- exact breadth thresholds by regime
- exact OI/funding thresholds for confirmation
- exact trailing-stop formula variants
- whether basis and liquidation data are available reliably enough for always-on use

## 18. Final Summary

Trading System v2 is a market-regime-aware, multi-engine crypto trading architecture built to balance performance at the portfolio level.

It separates:
- environment detection
- opportunity generation
- portfolio allocation
- execution and lifecycle control

Its edge should come not from complexity for its own sake, but from cleaner structure, better portfolio coordination, and stronger lifecycle management than the current v1 workflow.
