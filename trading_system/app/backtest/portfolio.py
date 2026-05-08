from __future__ import annotations

from math import ceil, isfinite

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


def _finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _non_negative_number(value: object, *, field_name: str) -> float:
    result = _finite_number(value, field_name=field_name)
    if result < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return result


def _positive_number(value: object, *, field_name: str) -> float:
    result = _finite_number(value, field_name=field_name)
    if result <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _non_negative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _equity(state: PortfolioState, capital: CapitalModelConfig) -> float:
    raw_equity = state.initial_equity if state.initial_equity is not None else capital.initial_equity
    return max(_finite_number(raw_equity, field_name="state.initial_equity"), 0.0)


def _used_open_risk(state: PortfolioState) -> float:
    if state.open_risk_fraction is not None:
        return max(_non_negative_number(state.open_risk_fraction, field_name="state.open_risk_fraction"), 0.0)
    return sum(
        max(_non_negative_number(position.risk_budget, field_name="position.risk_budget"), 0.0)
        for position in state.open_positions
    )


def _used_capital_fraction(state: PortfolioState, *, equity: float) -> float:
    if state.capital_usage_fraction is not None:
        return max(_non_negative_number(state.capital_usage_fraction, field_name="state.capital_usage_fraction"), 0.0)
    if equity <= 0:
        return 0.0
    return (
        sum(
            max(_non_negative_number(position.position_notional, field_name="position.position_notional"), 0.0)
            for position in state.open_positions
        )
        / equity
    )


def _active_positions(state: PortfolioState) -> int:
    if state.active_positions is not None:
        return _non_negative_int(state.active_positions, field_name="state.active_positions")
    return len(state.open_positions)


def _stop_distance(candidate: PortfolioCandidate) -> float:
    entry_price = _positive_number(candidate.entry_price, field_name="candidate.entry_price")
    stop_loss = _positive_number(candidate.stop_loss, field_name="candidate.stop_loss")
    return abs(entry_price - stop_loss)


def _additional_slots(remaining_capacity: float, per_position_capacity: float) -> int:
    if remaining_capacity <= _EPSILON or per_position_capacity <= _EPSILON:
        return 0
    return int(ceil((remaining_capacity - _EPSILON) / per_position_capacity))


def _canonical_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a canonical string")
    return value


def _decision_status(value: object) -> str:
    status = _canonical_string(value, field_name="decision.status")
    if status not in {"accepted", "resized", "rejected"}:
        raise ValueError("decision.status must be a portfolio decision status")
    return status


def _decision_reasons(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError("decision.reasons must be a tuple")
    return tuple(_canonical_string(item, field_name="decision.reasons[]") for item in value)


def _portfolio_side(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a portfolio side")
    side = value.strip().lower()
    if side not in {"long", "short"}:
        raise ValueError(f"{field_name} must be a portfolio side")
    return side


def _same_direction(a: PortfolioPosition | PortfolioCandidate, b: PortfolioCandidate) -> bool:
    return _portfolio_side(a.side, field_name="position.side") == _portfolio_side(
        b.side, field_name="candidate.side"
    )


def _has_base_asset_crowding(candidate: PortfolioCandidate, state: PortfolioState) -> bool:
    candidate_base_asset = _canonical_string(candidate.base_asset, field_name="candidate.base_asset")
    candidate_market_type = _canonical_string(candidate.market_type, field_name="candidate.market_type")
    for position in state.open_positions:
        position_base_asset = _canonical_string(position.base_asset, field_name="position.base_asset")
        position_market_type = _canonical_string(position.market_type, field_name="position.market_type")
        if position_base_asset != candidate_base_asset:
            continue
        if position_market_type == candidate_market_type:
            continue
        if _same_direction(position, candidate):
            return True
    return False


def position_size_from_risk(candidate: PortfolioCandidate, *, equity: float, risk_budget: float) -> PortfolioSizing:
    equity_value = _positive_number(equity, field_name="equity")
    risk_budget_value = _non_negative_number(risk_budget, field_name="risk_budget")
    entry_price = _positive_number(candidate.entry_price, field_name="candidate.entry_price")
    stop_distance = _stop_distance(candidate)
    if risk_budget_value <= 0 or stop_distance <= 0:
        return PortfolioSizing(risk_budget=risk_budget_value, position_notional=0.0, qty=0.0)

    risk_budget_usdt = equity_value * risk_budget_value
    qty = risk_budget_usdt / stop_distance
    position_notional = qty * entry_price
    return PortfolioSizing(
        risk_budget=risk_budget_value,
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
    remaining_risk = max(_non_negative_number(capital.max_open_risk, field_name="capital.max_open_risk") - _used_open_risk(state), 0.0)
    remaining_capital_fraction = max(1.0 - _used_capital_fraction(state, equity=equity), 0.0)
    risk_per_trade = _non_negative_number(capital.risk_per_trade, field_name="capital.risk_per_trade")
    additional_risk_slots = _additional_slots(remaining_risk, risk_per_trade)
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

    remaining_risk = max(_non_negative_number(capital.max_open_risk, field_name="capital.max_open_risk") - _used_open_risk(state), 0.0)
    remaining_capital_fraction = max(1.0 - _used_capital_fraction(state, equity=equity), 0.0)

    risk_per_trade = _non_negative_number(capital.risk_per_trade, field_name="capital.risk_per_trade")
    final_risk_budget = min(risk_per_trade, remaining_risk)
    reasons: list[str] = []
    if final_risk_budget <= _EPSILON:
        return _decision(
            status="rejected",
            reasons=("open_risk_budget_exhausted",),
            final_risk_budget=0.0,
            position_notional=0.0,
            qty=0.0,
        )
    if final_risk_budget + _EPSILON < risk_per_trade:
        reasons.append("open_risk_budget_limited")

    final_sizing = position_size_from_risk(candidate, equity=equity, risk_budget=final_risk_budget)
    max_notional = equity * remaining_capital_fraction
    if final_sizing.position_notional > max_notional + _EPSILON:
        if max_notional <= _EPSILON:
            return _decision(
                status="rejected",
                reasons=("capital_usage_exhausted",),
                final_risk_budget=0.0,
                position_notional=0.0,
                qty=0.0,
            )
        entry_price = _positive_number(candidate.entry_price, field_name="candidate.entry_price")
        resized_qty = max_notional / entry_price
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
        symbol=_canonical_string(candidate.symbol, field_name="candidate.symbol"),
        market_type=_canonical_string(candidate.market_type, field_name="candidate.market_type"),
        base_asset=_canonical_string(candidate.base_asset, field_name="candidate.base_asset"),
        status=_decision_status(decision.status),
        reasons=_decision_reasons(decision.reasons),
        final_risk_budget=_non_negative_number(decision.final_risk_budget, field_name="decision.final_risk_budget"),
        position_notional=_non_negative_number(decision.position_notional, field_name="decision.position_notional"),
        qty=_non_negative_number(decision.qty, field_name="decision.qty"),
    )
