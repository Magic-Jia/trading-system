from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal, cast

BJ = timezone(timedelta(hours=8))

Side = Literal["LONG", "SHORT"]
SignalSource = Literal["manual", "scanner", "strategy", "imported"]
ManagementAction = Literal["BREAK_EVEN", "PARTIAL_TAKE_PROFIT", "EXIT", "ADD_PROTECTIVE_STOP", "DE_RISK"]
ManagementPreviewKind = Literal[
    "STOP_LOSS_UPDATE",
    "PROTECTIVE_STOP_ADD",
    "REDUCE_ONLY_TP_CLOSE",
    "REDUCE_ONLY_DE_RISK_CLOSE",
    "CLOSE_POSITION",
    "UNSUPPORTED",
]

AllocationStatus = Literal["ACCEPTED", "DOWNSIZED", "REJECTED"]
_ALLOCATION_STATUS_SET = {"ACCEPTED", "DOWNSIZED", "REJECTED"}
ExecutionPolicy = Literal["normal", "downsize", "suppress"]


@dataclass(slots=True)
class TradeSignal:
    signal_id: str
    symbol: str
    side: Side
    entry_price: float
    stop_loss: float
    take_profit: float | None = None
    confidence: float | None = None
    source: SignalSource = "strategy"
    timeframe: str = "4h"
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)


@dataclass(slots=True)
class PositionSnapshot:
    symbol: str
    side: Side
    qty: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    notional: float = 0.0
    leverage: float | None = None
    strategy_tag: str | None = None


@dataclass(slots=True)
class AccountSnapshot:
    equity: float
    available_balance: float
    futures_wallet_balance: float
    open_positions: list[PositionSnapshot] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SizingResult:
    allowed: bool
    qty: float
    risk_budget_usdt: float
    planned_loss_usdt: float
    planned_notional_usdt: float
    stop_distance: float
    risk_pct_of_equity: float
    max_notional_cap_usdt: float
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    allowed: bool
    severity: Literal["INFO", "WARN", "BLOCK"]
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderIntent:
    intent_id: str
    signal_id: str
    symbol: str
    side: Side
    qty: float
    entry_price: float
    stop_loss: float
    take_profit: float | None = None
    status: Literal["PENDING", "SKIPPED", "SENT", "FILLED", "FAILED"] = "PENDING"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ManagementSuggestion:
    symbol: str
    action: ManagementAction
    side: Side
    reason: str
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    qty_fraction: float | None = None
    suggested_stop_loss: float | None = None
    reference_price: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ManagementActionIntent:
    intent_id: str
    symbol: str
    action: ManagementAction
    side: Side
    position_qty: float
    qty: float | None = None
    stop_loss: float | None = None
    reference_price: float | None = None
    reduce_only: bool = True
    status: Literal["PREVIEW", "UNSUPPORTED"] = "PREVIEW"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ManagementActionPreview:
    intent: ManagementActionIntent
    preview_kind: ManagementPreviewKind
    payload: dict[str, Any] | None = None
    open_protective_orders: list[dict[str, Any]] = field(default_factory=list)
    supported: bool = True
    reason: str | None = None


@dataclass(slots=True)
class RegimeSnapshot:
    label: str
    confidence: float
    risk_multiplier: float
    execution_policy: ExecutionPolicy = "normal"
    bucket_targets: dict[str, float] = field(default_factory=dict)
    suppression_rules: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EngineCandidate:
    engine: str
    setup_type: str
    symbol: str
    side: Side
    score: float
    stop_loss: float = 0.0
    invalidation_source: str = ""
    timeframe_meta: dict[str, Any] = field(default_factory=dict)
    sector: str | None = None
    liquidity_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AllocationDecision:
    status: AllocationStatus
    engine: str = ""
    reasons: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    final_risk_budget: float = 0.0
    rank: int = 0

    def __post_init__(self) -> None:
        status = str(self.status).upper()
        if status not in _ALLOCATION_STATUS_SET:
            raise ValueError(
                f"status must be one of {sorted(_ALLOCATION_STATUS_SET)}; got {self.status!r}"
            )
        self.status = cast(AllocationStatus, status)

    @property
    def reason_codes(self) -> list[str]:
        return self.reasons


class LifecycleState(str, Enum):
    INIT = "INIT"
    CONFIRM = "CONFIRM"
    PAYLOAD = "PAYLOAD"
    PROTECT = "PROTECT"
    EXIT = "EXIT"


@dataclass(slots=True)
class RuntimeState:
    updated_at_bj: str
    last_signal_ids: dict[str, str] = field(default_factory=dict)
    cooldowns: dict[str, str] = field(default_factory=dict)
    active_orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    circuit_breaker_until: str | None = None
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    management_suggestions: list[dict[str, Any]] = field(default_factory=list)
    management_action_previews: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "RuntimeState":
        return cls(updated_at_bj=datetime.now(BJ).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
