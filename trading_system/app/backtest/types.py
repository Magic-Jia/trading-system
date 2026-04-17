from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

MarketType = Literal["spot", "futures"]
FundingMode = Literal["historical_series"]

SampleSplit = Literal["in_sample", "out_of_sample"]


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
    fee_bps: float | None = None
    slippage_bps: float | None = None
    funding_bps_per_day: float = 0.0
    fee_bps_by_market: dict[str, float] = field(default_factory=dict)
    slippage_bps_by_tier: dict[str, float] = field(default_factory=dict)
    funding_mode: FundingMode | None = None


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
    markets: tuple[MarketType, ...] = ()
    universe: UniverseFilterConfig | None = None
    capital: CapitalModelConfig | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetSnapshotRow:
    timestamp: datetime
    run_id: str
    market: dict[str, Any]
    derivatives: list[dict[str, Any]]
    account: dict[str, Any] | None = None
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
