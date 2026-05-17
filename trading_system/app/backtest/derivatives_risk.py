from __future__ import annotations

from datetime import UTC, datetime
import math
from numbers import Real
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "derivatives_position_risk_report.v1"

_VALID_SIDES = frozenset(("long", "short"))
_VALID_MARGIN_MODES = frozenset(("isolated", "cross"))
_VALID_POSITION_MODES = frozenset(("one_way", "hedge_long", "hedge_short"))
_MAX_LEVERAGE = 125.0


def _base_report(position: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": position.get("symbol"),
        "decision": "fail_closed",
        "reason_codes": [],
        "margin_mode": position.get("margin_mode"),
        "position_mode": position.get("position_mode"),
        "leverage": None,
        "entry_price": None,
        "mark_price": None,
        "maintenance_margin_rate": None,
        "wallet_balance": None,
        "notional": None,
        "unrealized_pnl": None,
        "estimated_liquidation_price": None,
        "funding_payment": None,
        "adl_risk_bucket": "unknown",
        "adl_quality": "missing",
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return parsed


def _positive_number(position: Mapping[str, Any], field: str, reasons: list[str]) -> float | None:
    parsed = _finite_number(position.get(field))
    if parsed is None or parsed <= 0.0:
        reasons.append(f"{field}_invalid")
        return None
    return parsed


def _non_negative_number(position: Mapping[str, Any], field: str, reasons: list[str]) -> float | None:
    parsed = _finite_number(position.get(field))
    if parsed is None or parsed < 0.0:
        reasons.append(f"{field}_invalid")
        return None
    return parsed


def _maintenance_margin_rate(position: Mapping[str, Any], reasons: list[str]) -> float | None:
    parsed = _finite_number(position.get("maintenance_margin_rate"))
    if parsed is None or parsed < 0.0 or parsed >= 1.0:
        reasons.append("maintenance_margin_rate_invalid")
        return None
    return parsed


def _leverage(position: Mapping[str, Any], reasons: list[str]) -> float | None:
    parsed = _finite_number(position.get("leverage"))
    if parsed is None or parsed <= 0.0 or parsed > _MAX_LEVERAGE:
        reasons.append("leverage_invalid")
        return None
    return parsed


def _canonical_domain(position: Mapping[str, Any], field: str, allowed: frozenset[str], reasons: list[str]) -> str | None:
    value = position.get(field)
    if type(value) is not str or value not in allowed:
        reasons.append(f"{field}_invalid")
        return None
    return value


def _parse_timestamp(value: Any, reasons: list[str]) -> datetime | None:
    if type(value) is not str or not value.endswith("Z"):
        reasons.append("funding_timestamp_invalid")
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        reasons.append("funding_timestamp_invalid")
        return None
    if parsed.tzinfo is None:
        reasons.append("funding_timestamp_invalid")
        return None
    return parsed.astimezone(UTC)


def _funding_period_duplicate(position: Mapping[str, Any], reasons: list[str]) -> None:
    period_id = position.get("funding_period_id")
    prior_ids = position.get("prior_funding_period_ids", ())
    if period_id is None:
        reasons.append("funding_period_identity_missing")
        return
    if type(period_id) is not str or not period_id:
        reasons.append("funding_period_identity_invalid")
        return
    if prior_ids is None:
        prior_ids = ()
    if not isinstance(prior_ids, Sequence) or isinstance(prior_ids, (str, bytes)):
        reasons.append("funding_period_identity_invalid")
        return
    seen: set[str] = set()
    for item in prior_ids:
        if type(item) is not str or not item:
            reasons.append("funding_period_identity_invalid")
            return
        if item in seen or item == period_id:
            reasons.append("funding_period_identity_duplicate")
            return
        seen.add(item)


def _funding_inputs(position: Mapping[str, Any], as_of: datetime, reasons: list[str]) -> tuple[float | None, float | None]:
    funding_rate = _finite_number(position.get("funding_rate"))
    if funding_rate is None:
        reasons.append("funding_rate_invalid")
    period_hours = _positive_number(position, "funding_period_hours", reasons)
    observed_at = _parse_timestamp(position.get("funding_observed_at"), reasons)
    if observed_at is not None and period_hours is not None:
        normalized_as_of = as_of.astimezone(UTC)
        age_seconds = (normalized_as_of - observed_at).total_seconds()
        if age_seconds < 0.0:
            reasons.append("funding_timestamp_after_as_of")
        elif age_seconds > period_hours * 3600.0:
            reasons.append("funding_timestamp_stale")
    _funding_period_duplicate(position, reasons)
    return funding_rate, period_hours


def _unrealized_pnl(*, side: str, quantity: float, entry_price: float, mark_price: float) -> float:
    if side == "long":
        return (mark_price - entry_price) * quantity
    return (entry_price - mark_price) * quantity


def _isolated_liquidation_price(
    *,
    side: str,
    quantity: float,
    entry_price: float,
    leverage: float,
    maintenance_margin_rate: float,
) -> float | None:
    entry_notional = entry_price * quantity
    initial_margin = entry_notional / leverage
    if side == "long":
        denominator = quantity * (1.0 - maintenance_margin_rate)
        if denominator <= 0.0:
            return None
        return (entry_notional - initial_margin) / denominator
    denominator = quantity * (1.0 + maintenance_margin_rate)
    if denominator <= 0.0:
        return None
    return (entry_notional + initial_margin) / denominator


def _funding_payment(*, side: str, notional: float, funding_rate: float) -> float:
    payment = notional * funding_rate
    return -payment if side == "long" else payment


def _adl_bucket(position: Mapping[str, Any], reasons: list[str]) -> tuple[str, str]:
    quantile = position.get("adl_quantile")
    if quantile is None:
        reasons.append("adl_evidence_missing")
        return "unknown", "missing"
    if isinstance(quantile, bool) or not isinstance(quantile, int) or quantile < 0 or quantile > 4:
        reasons.append("adl_quantile_invalid")
        return "unknown", "invalid"
    if quantile <= 1:
        return "low", "venue_reported_quantile"
    if quantile <= 3:
        return "medium", "venue_reported_quantile"
    return "high", "venue_reported_quantile"


def build_derivatives_position_risk_report(
    position: Mapping[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    report = _base_report(position)
    reasons: list[str] = []

    side = _canonical_domain(position, "side", _VALID_SIDES, reasons)
    margin_mode = _canonical_domain(position, "margin_mode", _VALID_MARGIN_MODES, reasons)
    position_mode = _canonical_domain(position, "position_mode", _VALID_POSITION_MODES, reasons)
    quantity = _positive_number(position, "quantity", reasons)
    leverage = _leverage(position, reasons)
    entry_price = _positive_number(position, "entry_price", reasons)
    if position.get("mark_price") is None:
        reasons.append("mark_price_missing")
        mark_price = None
    else:
        mark_price = _positive_number(position, "mark_price", reasons)
    maintenance_margin_rate = _maintenance_margin_rate(position, reasons)
    wallet_balance = _non_negative_number(position, "wallet_balance", reasons)
    funding_rate, _period_hours = _funding_inputs(position, as_of, reasons)
    adl_risk_bucket, adl_quality = _adl_bucket(position, reasons)

    report.update(
        {
            "margin_mode": margin_mode,
            "position_mode": position_mode,
            "leverage": leverage,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "maintenance_margin_rate": maintenance_margin_rate,
            "wallet_balance": wallet_balance,
            "adl_risk_bucket": adl_risk_bucket,
            "adl_quality": adl_quality,
        }
    )

    if not reasons and side is not None and quantity is not None and entry_price is not None and mark_price is not None:
        notional = abs(quantity) * mark_price
        report["notional"] = notional
        report["unrealized_pnl"] = _unrealized_pnl(
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            mark_price=mark_price,
        )
        if funding_rate is not None:
            report["funding_payment"] = _funding_payment(side=side, notional=notional, funding_rate=funding_rate)

    if reasons:
        report["decision"] = "fail_closed"
        report["reason_codes"] = reasons
        return report

    assert side is not None
    assert margin_mode is not None
    assert quantity is not None
    assert leverage is not None
    assert entry_price is not None
    assert maintenance_margin_rate is not None

    if margin_mode == "cross":
        report["decision"] = "review_hold"
        report["reason_codes"] = ["cross_margin_liquidation_formula_unknown"]
        report["estimated_liquidation_price"] = None
        return report

    liquidation_price = _isolated_liquidation_price(
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
    )
    if liquidation_price is None or liquidation_price <= 0.0:
        report["decision"] = "review_hold"
        report["reason_codes"] = ["isolated_liquidation_estimate_unavailable"]
        report["estimated_liquidation_price"] = None
        return report

    report["decision"] = "pass"
    report["reason_codes"] = []
    report["estimated_liquidation_price"] = liquidation_price
    return report
