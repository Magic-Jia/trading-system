from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

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
class BacktestCosts:
    fee_bps: float
    slippage_bps: float
    funding_bps_per_day: float = 0.0


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
