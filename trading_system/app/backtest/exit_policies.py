from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Literal, Protocol

from .types import ExitPolicyParams, PortfolioSide

ExitPriceSource = Literal["trade_print", "none"]
ExitFillQuality = Literal["evidence_backed", "no_evidence"]
_BPS_EPSILON = 1e-9


class TradePrintLike(Protocol):
    timestamp: datetime
    price: float


@dataclass(frozen=True, slots=True)
class ExitPolicyEvaluation:
    triggered: bool
    exit_price: float | None
    exit_timestamp: datetime | None
    exit_policy_reason: str
    exit_price_source: ExitPriceSource
    fill_quality: ExitFillQuality


def evaluate_exit_policy(
    *,
    side: PortfolioSide,
    entry_price: float,
    entry_timestamp: datetime,
    fixed_exit_timestamp: datetime,
    trade_prints: Iterable[TradePrintLike],
    policy: ExitPolicyParams,
    costs_bps: float | None = None,
    total_cost_bps: float | None = None,
) -> ExitPolicyEvaluation:
    _validate_policy_name(policy.name)
    if side not in {"long", "short"}:
        raise ValueError(f"Unknown side: {side}")
    if entry_price <= 0.0:
        raise ValueError("entry_price must be positive")

    total_costs_bps = float(costs_bps if costs_bps is not None else (total_cost_bps or 0.0))
    eligible_prints = _eligible_prints(
        trade_prints=trade_prints,
        entry_timestamp=entry_timestamp,
        fixed_exit_timestamp=fixed_exit_timestamp,
    )

    if policy.name == "after_cost_breakeven_stop":
        return _evaluate_after_cost_breakeven_stop(
            side=side,
            entry_price=entry_price,
            entry_timestamp=entry_timestamp,
            trade_prints=eligible_prints,
            costs_bps=total_costs_bps,
            policy=policy,
        )
    if policy.name == "mfe_giveback_cut":
        return _evaluate_mfe_giveback_cut(
            side=side,
            entry_price=entry_price,
            entry_timestamp=entry_timestamp,
            trade_prints=eligible_prints,
            costs_bps=total_costs_bps,
            policy=policy,
        )
    return _evaluate_no_breakeven_time_stop(
        side=side,
        entry_price=entry_price,
        entry_timestamp=entry_timestamp,
        trade_prints=eligible_prints,
        costs_bps=total_costs_bps,
        policy=policy,
    )


def _evaluate_after_cost_breakeven_stop(
    *,
    side: PortfolioSide,
    entry_price: float,
    entry_timestamp: datetime,
    trade_prints: tuple[TradePrintLike, ...],
    costs_bps: float,
    policy: ExitPolicyParams,
) -> ExitPolicyEvaluation:
    activation_timestamp = entry_timestamp + timedelta(minutes=policy.activation_minute)
    breakeven_bps = costs_bps + policy.after_cost_buffer_bps
    for trade in trade_prints:
        if trade.timestamp < activation_timestamp:
            continue
        if _meets_or_exceeds(
            _favorable_move_bps(side=side, entry_price=entry_price, price=float(trade.price)),
            breakeven_bps,
        ):
            return _triggered(trade=trade, reason="after_cost_breakeven_stop")
    return _not_triggered()


def _evaluate_mfe_giveback_cut(
    *,
    side: PortfolioSide,
    entry_price: float,
    entry_timestamp: datetime,
    trade_prints: tuple[TradePrintLike, ...],
    costs_bps: float,
    policy: ExitPolicyParams,
) -> ExitPolicyEvaluation:
    activation_timestamp = entry_timestamp + timedelta(minutes=policy.activation_minute)
    activation_threshold_bps = max(costs_bps * 2.0, 20.0)
    giveback_fraction = 0.5 if policy.giveback_fraction is None else policy.giveback_fraction
    giveback_min_bps = 25.0 if policy.giveback_min_bps is None else policy.giveback_min_bps
    mfe_bps: float | None = None

    for trade in trade_prints:
        if trade.timestamp < activation_timestamp:
            continue
        move_bps = _favorable_move_bps(side=side, entry_price=entry_price, price=float(trade.price))
        mfe_bps = move_bps if mfe_bps is None else max(mfe_bps, move_bps)
        if not _meets_or_exceeds(mfe_bps, activation_threshold_bps):
            continue

        giveback_bps = mfe_bps - move_bps
        required_giveback_bps = max(mfe_bps * giveback_fraction, giveback_min_bps)
        if _meets_or_exceeds(giveback_bps, required_giveback_bps):
            return _triggered(trade=trade, reason="mfe_giveback_cut")

    return _not_triggered()


def _evaluate_no_breakeven_time_stop(
    *,
    side: PortfolioSide,
    entry_price: float,
    entry_timestamp: datetime,
    trade_prints: tuple[TradePrintLike, ...],
    costs_bps: float,
    policy: ExitPolicyParams,
) -> ExitPolicyEvaluation:
    stop_minute = policy.no_breakeven_time_stop_minute or 0
    time_stop_timestamp = entry_timestamp + timedelta(minutes=stop_minute)
    breakeven_bps = costs_bps + policy.after_cost_buffer_bps

    for trade in trade_prints:
        if trade.timestamp > time_stop_timestamp:
            break
        if _meets_or_exceeds(
            _favorable_move_bps(side=side, entry_price=entry_price, price=float(trade.price)),
            breakeven_bps,
        ):
            return _not_triggered()

    for trade in trade_prints:
        if trade.timestamp >= time_stop_timestamp:
            return _triggered(trade=trade, reason="no_breakeven_time_stop")

    return _not_triggered()


def _eligible_prints(
    *,
    trade_prints: Iterable[TradePrintLike],
    entry_timestamp: datetime,
    fixed_exit_timestamp: datetime,
) -> tuple[TradePrintLike, ...]:
    return tuple(
        sorted(
            (
                trade
                for trade in trade_prints
                if entry_timestamp <= trade.timestamp <= fixed_exit_timestamp
            ),
            key=lambda trade: trade.timestamp,
        )
    )


def _favorable_move_bps(*, side: PortfolioSide, entry_price: float, price: float) -> float:
    if price <= 0.0:
        raise ValueError("trade print price must be positive")
    if side == "long":
        return (price / entry_price - 1.0) * 10_000.0
    return (entry_price / price - 1.0) * 10_000.0


def _validate_policy_name(name: str) -> None:
    if name not in {"after_cost_breakeven_stop", "mfe_giveback_cut", "no_breakeven_time_stop"}:
        raise ValueError(f"Unknown exit policy: {name}")


def _meets_or_exceeds(value: float, threshold: float) -> bool:
    return value + _BPS_EPSILON >= threshold


def _triggered(*, trade: TradePrintLike, reason: str) -> ExitPolicyEvaluation:
    return ExitPolicyEvaluation(
        triggered=True,
        exit_price=float(trade.price),
        exit_timestamp=trade.timestamp,
        exit_policy_reason=reason,
        exit_price_source="trade_print",
        fill_quality="evidence_backed",
    )


def _not_triggered() -> ExitPolicyEvaluation:
    return ExitPolicyEvaluation(
        triggered=False,
        exit_price=None,
        exit_timestamp=None,
        exit_policy_reason="not_triggered",
        exit_price_source="none",
        fill_quality="no_evidence",
    )
