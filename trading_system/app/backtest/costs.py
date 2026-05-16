from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .types import BacktestCosts, PortfolioSide

_BPS_DENOMINATOR = 10_000.0
_FUNDING_INTERVAL_HOURS = 8.0


def _canonical_string(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a string")
    return value


def _canonical_provenance_string(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a canonical string")
    return value


def _finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _non_negative_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return parsed


def _portfolio_side(value: object) -> PortfolioSide:
    if type(value) is not str or value not in {"long", "short"}:
        raise ValueError("side must be a valid portfolio side")
    return value  # type: ignore[return-value]


def _funding_market_type(value: object) -> str:
    market_type = _canonical_string(value, field_name="market_type")
    if type(market_type) is not str:
        raise ValueError("market_type must be a string")
    if market_type not in {"spot", "futures"}:
        raise ValueError("market_type must be spot or futures")
    return market_type


def _funding_mode(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("funding_mode must be a string")
    if value != "historical_series":
        raise ValueError("funding_mode must be historical_series or None")
    return value


def _canonical_utc_timestamp(value: object, *, field_name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must be canonical UTC")
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{field_name} must be canonical UTC")
    return parsed


def _optional_canonical_utc_timestamp(value: object | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _canonical_utc_timestamp(value, field_name=field_name)


def _required_provenance_timestamp(provenance: Mapping[str, Any], key: str, *, kind: str) -> datetime:
    if key not in provenance:
        raise ValueError(f"{kind} provenance {key} is required")
    return _canonical_utc_timestamp(provenance[key], field_name=f"{kind} provenance {key}")


def _required_provenance_string(provenance: Mapping[str, Any], key: str, *, kind: str) -> str:
    if key not in provenance:
        raise ValueError(f"{kind} provenance {key} is required")
    return _canonical_provenance_string(provenance[key], field_name=f"{kind} provenance {key}")


def _provenance_mapping(value: object | None, *, kind: str) -> Mapping[str, Any]:
    if value is None:
        raise ValueError(f"{kind} provenance evidence is required")
    if not isinstance(value, Mapping):
        raise ValueError(f"{kind} provenance evidence must be an object")
    return value


def _expected_venue(costs: BacktestCosts, *, kind: str, market_type: str) -> str | None:
    source = costs.fee_venue_by_market if kind == "fee" else costs.funding_venue_by_market
    venue = source.get(market_type)
    if venue is None:
        return None
    return _canonical_provenance_string(venue, field_name=f"{kind}_venue_by_market.{market_type}")


def validate_cost_input_provenance(
    provenance: object | None,
    *,
    kind: str,
    rate: float,
    costs: BacktestCosts,
    market_type: str,
    symbol: str | None,
    side: PortfolioSide | None,
    timeframe: str | None,
    decision_time: object | None,
    fill_time: object | None,
) -> dict[str, Any]:
    if kind not in {"fee", "funding"}:
        raise ValueError("cost provenance kind must be fee or funding")
    evidence = _provenance_mapping(provenance, kind=kind)
    validated: dict[str, Any] = {}
    for field in ("schema_version", "kind", "account_id", "venue", "symbol", "side", "timeframe", "tier"):
        validated[field] = _required_provenance_string(evidence, field, kind=kind)
    if validated["kind"] != kind:
        raise ValueError(f"{kind} provenance kind must match {kind}")
    evidence_rate = _finite_number(evidence.get("rate"), field_name=f"{kind} provenance rate")
    if evidence_rate != rate:
        raise ValueError(f"{kind} provenance rate must match {rate}")
    validated["rate"] = evidence_rate
    effective_at = _required_provenance_timestamp(evidence, "effective_at", kind=kind)
    as_of = _required_provenance_timestamp(evidence, "as_of", kind=kind)
    observed_at = _required_provenance_timestamp(evidence, "observed_at", kind=kind)
    validated["effective_at"] = evidence["effective_at"]
    validated["as_of"] = evidence["as_of"]
    validated["observed_at"] = evidence["observed_at"]

    if symbol is not None and validated["symbol"] != symbol:
        raise ValueError(f"{kind} provenance symbol must match {symbol}")
    expected_venue = _expected_venue(costs, kind=kind, market_type=market_type)
    if expected_venue is not None and validated["venue"] != expected_venue:
        raise ValueError(f"{kind} provenance venue must match {expected_venue}")
    if side is not None and validated["side"] != side:
        raise ValueError(f"{kind} provenance side must match {side}")
    if timeframe is not None and validated["timeframe"] != timeframe:
        raise ValueError(f"{kind} provenance timeframe must match {timeframe}")

    decision_dt = _optional_canonical_utc_timestamp(decision_time, field_name="decision_time")
    fill_dt = _optional_canonical_utc_timestamp(fill_time, field_name="fill_time")
    if decision_dt is not None and effective_at > decision_dt:
        raise ValueError(f"{kind} provenance effective_at must not be after decision_time")
    if decision_dt is not None and as_of > decision_dt:
        raise ValueError(f"{kind} provenance as_of must not be after decision_time")
    if fill_dt is not None and observed_at > fill_dt:
        raise ValueError(f"{kind} provenance observed_at must not be after fill_time")
    return validated


def fee_bps_for_market(costs: BacktestCosts, market_type: str) -> float:
    market = _funding_market_type(market_type)
    return _non_negative_finite_number(
        costs.fee_bps_by_market.get(market, 0.0),
        field_name=f"fee_bps_by_market.{market}",
    )


def slippage_bps_for_tier(costs: BacktestCosts, liquidity_tier: str) -> float:
    tier = _canonical_string(liquidity_tier, field_name="liquidity_tier")
    tier_key = tier.lower()
    return _non_negative_finite_number(
        costs.slippage_bps_by_tier.get(tier_key, 0.0),
        field_name=f"slippage_bps_by_tier.{tier_key}",
    )


def fee_cost(
    *,
    position_notional: float,
    market_type: str,
    costs: BacktestCosts,
    symbol: str | None = None,
    side: PortfolioSide | None = None,
    timeframe: str | None = None,
    decision_time: object | None = None,
    fill_time: object | None = None,
    fee_provenance: object | None = None,
) -> float:
    notional = _non_negative_finite_number(position_notional, field_name="position_notional")
    if notional <= 0.0:
        return 0.0
    market = _funding_market_type(market_type)
    fee_bps = fee_bps_for_market(costs, market)
    if costs.require_fee_funding_provenance:
        validate_cost_input_provenance(
            fee_provenance,
            kind="fee",
            rate=fee_bps,
            costs=costs,
            market_type=market,
            symbol=symbol,
            side=side,
            timeframe=timeframe,
            decision_time=decision_time,
            fill_time=fill_time,
        )
    return (notional * 2.0 * fee_bps) / _BPS_DENOMINATOR


def slippage_cost(*, position_notional: float, liquidity_tier: str, costs: BacktestCosts) -> float:
    notional = _non_negative_finite_number(position_notional, field_name="position_notional")
    if notional <= 0.0:
        return 0.0
    return (notional * 2.0 * slippage_bps_for_tier(costs, liquidity_tier)) / _BPS_DENOMINATOR


def funding_cost(
    *,
    position_notional: float,
    market_type: str,
    side: PortfolioSide,
    funding_rate: float,
    holding_hours: float,
    costs: BacktestCosts,
    symbol: str | None = None,
    timeframe: str | None = None,
    decision_time: object | None = None,
    fill_time: object | None = None,
    funding_provenance: object | None = None,
) -> float:
    position = _non_negative_finite_number(position_notional, field_name="position_notional")
    rate = _finite_number(funding_rate, field_name="funding_rate")
    hours = _non_negative_finite_number(holding_hours, field_name="holding_hours")
    market = _funding_market_type(market_type)
    side_key = _portfolio_side(side)
    funding_mode = _funding_mode(costs.funding_mode)
    if costs.require_fee_funding_provenance and market == "futures" and funding_mode == "historical_series":
        validate_cost_input_provenance(
            funding_provenance,
            kind="funding",
            rate=rate,
            costs=costs,
            market_type=market,
            symbol=symbol,
            side=side_key,
            timeframe=timeframe,
            decision_time=decision_time,
            fill_time=fill_time,
        )
    if position <= 0.0 or market != "futures" or funding_mode != "historical_series":
        return 0.0
    if hours <= 0.0 or rate == 0.0:
        return 0.0
    intervals = hours / _FUNDING_INTERVAL_HOURS
    direction = 1.0 if side_key == "long" else -1.0
    return position * rate * intervals * direction
