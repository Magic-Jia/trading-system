from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

SampleSplit = Literal["in_sample", "out_of_sample"]
UniverseExclusionReason = Literal[
    "listing_age_below_minimum",
    "quote_volume_below_minimum",
    "missing_funding_series",
    "missing_tradeability_metadata",
]
PortfolioSide = Literal["long", "short"]
PortfolioDecisionStatus = Literal["accepted", "resized", "rejected"]
TradeLedgerStatus = Literal["accepted", "resized"]


@dataclass(frozen=True, slots=True)
class ForwardReturnWindow:
    name: str
    hours: int


@dataclass(frozen=True, slots=True)
class SampleWindow:
    name: str
    start: datetime
    end: datetime
    split: SampleSplit = "in_sample"


@dataclass(frozen=True, slots=True)
class BacktestCosts:
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    funding_bps_per_day: float = 0.0
    fee_bps_by_market: dict[str, float] = field(default_factory=dict)
    slippage_bps_by_tier: dict[str, float] = field(default_factory=dict)
    funding_mode: Literal["historical_series"] | None = None


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
class PortfolioCandidate:
    symbol: str
    market_type: Literal["spot", "futures"]
    base_asset: str
    side: PortfolioSide
    entry_price: float
    stop_loss: float


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    symbol: str
    market_type: Literal["spot", "futures"]
    base_asset: str
    side: PortfolioSide
    risk_budget: float
    position_notional: float
    qty: float


@dataclass(frozen=True, slots=True)
class PortfolioState:
    initial_equity: float
    open_positions: tuple[PortfolioPosition, ...] = ()
    open_risk_fraction: float | None = None
    capital_usage_fraction: float | None = None
    active_positions: int | None = None


@dataclass(frozen=True, slots=True)
class PortfolioSizing:
    risk_budget: float
    position_notional: float
    qty: float


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    status: PortfolioDecisionStatus
    reasons: tuple[str, ...]
    final_risk_budget: float
    position_notional: float
    qty: float


@dataclass(frozen=True, slots=True)
class PortfolioDecisionLedgerRow:
    symbol: str
    market_type: Literal["spot", "futures"]
    base_asset: str
    status: PortfolioDecisionStatus
    reasons: tuple[str, ...]
    final_risk_budget: float
    position_notional: float
    qty: float


@dataclass(frozen=True, slots=True)
class TradeLedgerRow:
    symbol: str
    market_type: Literal["spot", "futures"]
    base_asset: str
    side: PortfolioSide
    status: TradeLedgerStatus
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    qty: float
    position_notional: float
    holding_hours: float
    gross_pnl: float
    net_pnl: float
    gross_return_pct: float
    net_return_pct: float
    fee_paid: float
    slippage_paid: float
    funding_paid: float


@dataclass(frozen=True, slots=True)
class ExperimentMetadata:
    name: str
    experiment_kind: str
    dataset_root: Path
    baseline_name: str
    variant_name: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    dataset_root: Path
    experiment_kind: str
    sample_windows: tuple[SampleWindow, ...]
    forward_return_windows: tuple[ForwardReturnWindow, ...]
    costs: BacktestCosts
    baseline_name: str
    variant_name: str
    universe: UniverseFilterConfig | None = None
    capital: CapitalModelConfig | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class UniverseExclusionRow:
    symbol: str
    market_type: Literal["spot", "futures"]
    reason_code: UniverseExclusionReason
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetSnapshotRow:
    timestamp: datetime
    run_id: str
    market: dict[str, Any]
    derivatives: list[dict[str, Any]]
    account: dict[str, Any] | None = None
    instrument_rows: tuple[InstrumentSnapshotRow, ...] = field(default_factory=tuple)
    forward_returns: dict[str, float] = field(default_factory=dict)
    forward_drawdowns: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class TradeSummaryRow:
    experiment_name: str
    symbol: str
    engine: str
    setup_type: str
    entry_timestamp: datetime
    exit_timestamp: datetime | None
    return_pct: float
    holding_hours: float
    outcome: Literal["win", "loss", "flat"]


@dataclass(frozen=True, slots=True)
class PortfolioScorecardRow:
    experiment_name: str
    total_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    turnover: float
    trade_count: int


@dataclass(frozen=True, slots=True)
class AttributionRow:
    experiment_name: str
    layer: str
    bucket: str
    metric: str
    value: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BaselineReplayResult:
    portfolio_summary: PortfolioScorecardRow
    trade_ledger: tuple[TradeLedgerRow, ...]
    rejection_ledger: tuple[PortfolioDecisionLedgerRow, ...]
    cost_breakdown: dict[str, float]
    gross_period_returns: tuple[float, ...]
    net_period_returns: tuple[float, ...]
