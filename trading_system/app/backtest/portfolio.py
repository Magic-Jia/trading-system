from __future__ import annotations

from math import ceil

from .types import (
    CapitalModelConfig,
    PortfolioCandidate,
    PortfolioDecision,
    PortfolioDecisionLedgerRow,
    PortfolioPosition,
    PortfolioSizing,
    PortfolioState,
)

_EPSILON = 1e-12


def _equity(state: PortfolioState, capital: CapitalModelConfig) -> float:
    return max(float(state.initial_equity or capital.initial_equity), 0.0)


def _used_open_risk(state: PortfolioState) -> float:
    if state.open_risk_fraction is not None:
        return max(float(state.open_risk_fraction), 0.0)
    return sum(max(float(position.risk_budget), 0.0) for position in state.open_positions)


def _used_capital_fraction(state: PortfolioState, *, equity: float) -> float:
    if state.capital_usage_fraction is not None:
        return max(float(state.capital_usage_fraction), 0.0)
    if equity <= 0:
        return 0.0
    return sum(max(float(position.position_notional), 0.0) for position in state.open_positions) / equity


def _active_positions(state: PortfolioState) -> int:
    if state.active_positions is not None:
        return max(int(state.active_positions), 0)
    return len(state.open_positions)


def _stop_distance(candidate: PortfolioCandidate) -> float:
    return abs(float(candidate.entry_price) - float(candidate.stop_loss))


def _additional_slots(remaining_capacity: float, per_position_capacity: float) -> int:
    if remaining_capacity <= _EPSILON or per_position_capacity <= _EPSILON:
        return 0
    return int(ceil((remaining_capacity - _EPSILON) / per_position_capacity))


def _same_direction(a: PortfolioPosition | PortfolioCandidate, b: PortfolioCandidate) -> bool:
    return str(a.side).lower() == str(b.side).lower()


def _has_base_asset_crowding(candidate: PortfolioCandidate, state: PortfolioState) -> bool:
    for position in state.open_positions:
        if position.base_asset != candidate.base_asset:
            continue
        if position.market_type == candidate.market_type:
            continue
        if _same_direction(position, candidate):
            return True
    return False


def position_size_from_risk(candidate: PortfolioCandidate, *, equity: float, risk_budget: float) -> PortfolioSizing:
    stop_distance = _stop_distance(candidate)
    if equity <= 0 or risk_budget <= 0 or stop_distance <= 0 or candidate.entry_price <= 0:
        return PortfolioSizing(risk_budget=max(float(risk_budget), 0.0), position_notional=0.0, qty=0.0)

    risk_budget_usdt = equity * float(risk_budget)
    qty = risk_budget_usdt / stop_distance
    position_notional = qty * float(candidate.entry_price)
    return PortfolioSizing(
        risk_budget=float(risk_budget),
        position_notional=round(position_notional, 8),
        qty=round(qty, 8),
    )


def calculate_dynamic_position_cap(
    candidate: PortfolioCandidate,
    *,
    state: PortfolioState,
    capital: CapitalModelConfig,
) -> int:
    equity = _equity(state, capital)
    requested = position_size_from_risk(candidate, equity=equity, risk_budget=capital.risk_per_trade)
    requested_capital_fraction = (requested.position_notional / equity) if equity > 0 else 0.0
    remaining_risk = max(float(capital.max_open_risk) - _used_open_risk(state), 0.0)
    remaining_capital_fraction = max(1.0 - _used_capital_fraction(state, equity=equity), 0.0)
    additional_risk_slots = _additional_slots(remaining_risk, float(capital.risk_per_trade))
    additional_capital_slots = _additional_slots(remaining_capital_fraction, requested_capital_fraction)
    return _active_positions(state) + min(additional_risk_slots, additional_capital_slots)


def _decision(
    *,
    status: str,
    reasons: tuple[str, ...],
    final_risk_budget: float,
    position_notional: float,
    qty: float,
) -> PortfolioDecision:
    return PortfolioDecision(
        status=status,
        reasons=reasons,
        final_risk_budget=round(final_risk_budget, 10),
        position_notional=round(position_notional, 8),
        qty=round(qty, 8),
    )


def evaluate_candidate(
    candidate: PortfolioCandidate,
    *,
    state: PortfolioState,
    capital: CapitalModelConfig,
) -> PortfolioDecision:
    equity = _equity(state, capital)
    requested = position_size_from_risk(candidate, equity=equity, risk_budget=capital.risk_per_trade)
    if requested.qty <= 0:
        return _decision(
            status="rejected",
            reasons=("invalid_stop_distance",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )

    if _active_positions(state) >= calculate_dynamic_position_cap(candidate, state=state, capital=capital):
        return _decision(
            status="rejected",
            reasons=("dynamic_position_cap_reached",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )

    if _has_base_asset_crowding(candidate, state):
        return _decision(
            status="rejected",
            reasons=("base_asset_same_direction_crowding",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )

    remaining_risk = max(float(capital.max_open_risk) - _used_open_risk(state), 0.0)
    remaining_capital_fraction = max(1.0 - _used_capital_fraction(state, equity=equity), 0.0)

    final_risk_budget = min(float(capital.risk_per_trade), remaining_risk)
    reasons: list[str] = []
    if final_risk_budget <= _EPSILON:
        return _decision(
            status="rejected",
            reasons=("open_risk_budget_exhausted",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )
    if final_risk_budget + _EPSILON < float(capital.risk_per_trade):
        reasons.append("open_risk_budget_limited")

    final_sizing = position_size_from_risk(candidate, equity=equity, risk_budget=final_risk_budget)
    max_notional = equity * remaining_capital_fraction
    if final_sizing.position_notional > max_notional + _EPSILON:
        if max_notional <= _EPSILON or candidate.entry_price <= 0:
            return _decision(
                status="rejected",
                reasons=("capital_usage_exhausted",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            )
        resized_qty = max_notional / float(candidate.entry_price)
        resized_risk_budget = (resized_qty * _stop_distance(candidate) / equity) if equity > 0 else 0.0
        final_risk_budget = min(final_risk_budget, resized_risk_budget)
        final_sizing = PortfolioSizing(
            risk_budget=final_risk_budget,
            position_notional=max_notional,
            qty=resized_qty,
        )
        reasons.append("capital_usage_limited")

    if final_sizing.qty <= _EPSILON or final_sizing.position_notional <= _EPSILON or final_risk_budget <= _EPSILON:
        return _decision(
            status="rejected",
            reasons=tuple(reasons or ("insufficient_capacity",)),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )

    status = "accepted" if not reasons else "resized"
    return _decision(
        status=status,
        reasons=tuple(reasons),
        final_risk_budget=final_risk_budget,
        position_notional=final_sizing.position_notional,
        qty=final_sizing.qty,
    )


def decision_to_ledger_row(
    candidate: PortfolioCandidate,
    decision: PortfolioDecision,
) -> PortfolioDecisionLedgerRow:
    return PortfolioDecisionLedgerRow(
        symbol=candidate.symbol,
        market_type=candidate.market_type,
        base_asset=candidate.base_asset,
        status=decision.status,
        reasons=decision.reasons,
        final_risk_budget=decision.final_risk_budget,
        position_notional=decision.position_notional,
        qty=decision.qty,
    )
