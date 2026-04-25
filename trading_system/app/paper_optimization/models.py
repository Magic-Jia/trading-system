from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class PaperSignalFact:
    fact_type: str
    mode: str
    runtime_env: str
    regime_label: str
    regime_confidence: float | None
    symbol: str
    side: str
    engine: str
    setup_type: str
    score: float | None
    stop_loss: float | None
    invalidation_source: str
    validation_allowed: bool | None
    allocation_status: str | None
    allocation_rank: int | None
    final_risk_budget: float | None
    execution_status: str | None
    intent_id: str | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PaperTradeOutcome:
    fact_type: str
    mode: str
    runtime_env: str
    regime_label: str
    symbol: str
    side: str
    engine: str
    setup_type: str
    intent_id: str | None
    signal_id: str | None
    allocation_status: str | None
    execution_status: str | None
    outcome_status: str
    position_status: str | None
    score: float | None
    final_risk_budget: float | None
    filled_qty: float | None
    open_qty: float | None
    entry_price: float | None
    mark_price: float | None
    stop_loss: float | None
    take_profit: float | None
    unrealized_pnl: float | None
    realized_pnl: float | None
    pnl_basis: str | None
    opened_at_bj: str | None
    updated_at_bj: str | None
    recorded_at_bj: str | None

    def as_dict(self) -> dict:
        return asdict(self)
