from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

EXCHANGE_REJECT_REASON_CODES = (
    "tick_size_violation",
    "lot_size_violation",
    "min_notional_violation",
    "post_only_would_cross",
    "reduce_only_invalid",
    "insufficient_margin",
    "insufficient_balance",
    "rate_limit",
    "exchange_outage",
    "duplicate_client_order_id",
)

_EXCHANGE_CODE_TO_REASON = {
    "POST_ONLY_WOULD_CROSS": "post_only_would_cross",
    "REDUCE_ONLY_INVALID": "reduce_only_invalid",
    "INSUFFICIENT_MARGIN": "insufficient_margin",
    "INSUFFICIENT_BALANCE": "insufficient_balance",
    "RATE_LIMIT": "rate_limit",
    "EXCHANGE_OUTAGE": "exchange_outage",
    "DUPLICATE_CLIENT_ORDER_ID": "duplicate_client_order_id",
}

_POST_ONLY_POLICIES = frozenset({"reject_would_cross", "allow"})
_REDUCE_ONLY_POLICIES = frozenset({"require_position", "allow"})
_PRODUCT_TYPES = frozenset(
    {"spot", "margin", "usdt_perpetual", "coin_perpetual", "linear_perpetual", "inverse_perpetual"}
)


def reject_reason_from_exchange_code(raw_code: str) -> str:
    if not isinstance(raw_code, str) or not raw_code.strip():
        raise ValueError("exchange reject code must be a non-empty string")
    normalized = raw_code.strip().upper()
    reason_code = _EXCHANGE_CODE_TO_REASON.get(normalized)
    if reason_code is None:
        raise ValueError(f"unknown exchange reject code: {raw_code}")
    return reason_code


def build_exchange_reject_event(
    *,
    venue: str,
    symbol: str,
    client_order_id: str,
    raw_code: str,
    generated_at: str,
    max_age_seconds: int,
    now: str,
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    client_order_id_value = _required_non_empty_string(client_order_id, "client_order_id")
    generated_at_dt = _parse_utc_timestamp(generated_at, "generated_at")
    now_dt = _parse_utc_timestamp(now, "now")
    max_age = _strict_positive_int(max_age_seconds, "max_age_seconds")
    if (now_dt - generated_at_dt).total_seconds() > max_age:
        raise ValueError("stale generated_at")
    return {
        "schema_version": "exchange_reject_event.v1",
        "venue": venue_value,
        "symbol": symbol_value,
        "client_order_id": client_order_id_value,
        "generated_at": generated_at,
        "raw_code": raw_code.strip().upper(),
        "reason_code": reject_reason_from_exchange_code(raw_code),
    }


def build_exchange_constraint_report(
    *,
    venue: str,
    symbol: str,
    generated_at: str,
    order: Mapping[str, Any],
    constraints: Mapping[str, Any],
    rulebook: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    if order.get("symbol") is not None and _required_non_empty_string(order.get("symbol"), "order.symbol") != symbol_value:
        raise ValueError("order symbol must match report symbol")
    rulebook_provenance = _validate_rulebook_for_constraint_report(rulebook, venue=venue_value, symbol=symbol_value)
    tick_size = _strict_positive_number(constraints.get("price_tick_size"), "price_tick_size")
    lot_size = _strict_positive_number(constraints.get("quantity_step_size"), "quantity_step_size")
    min_notional = _strict_non_negative_number(constraints.get("min_notional", 0.0), "min_notional")
    reason_codes: list[str] = []

    quantity = _strict_non_negative_number(order.get("quantity"), "quantity")
    if quantity == 0.0 or not _aligned_to_increment(quantity, lot_size):
        reason_codes.append("lot_size_violation")

    for label in ("price", "stop_price", "take_profit_stop_price"):
        value = order.get(label)
        if value is not None and not _aligned_to_increment(_strict_non_negative_number(value, label), tick_size):
            reason_codes.append("tick_size_violation")

    price = order.get("price")
    if price is not None:
        notional = quantity * _strict_non_negative_number(price, "price")
        if min_notional and notional < min_notional:
            reason_codes.append("min_notional_violation")

    if order.get("post_only") is True and _post_only_would_cross(order):
        reason_codes.append("post_only_would_cross")
    if order.get("reduce_only") is True and not order.get("has_position", True):
        reason_codes.append("reduce_only_invalid")

    report = {
        "schema_version": "exchange_reject_report.v1",
        "venue": venue_value,
        "symbol": symbol_value,
        "generated_at": generated_at,
        "validation_passed": not reason_codes,
        "reason_codes": reason_codes,
    }
    if rulebook_provenance is not None:
        report["rulebook_version"] = rulebook_provenance["rulebook_version"]
        report["rulebook_source"] = rulebook_provenance["source"]
        report["provenance"] = rulebook_provenance
    return report


def build_venue_rulebook_report(
    *,
    venue: str,
    symbol: str,
    product_type: str | None = None,
    rulebook_version: str,
    generated_at: str,
    effective_at: str,
    source: str,
    price_tick_size: Any,
    quantity_step_size: Any,
    min_notional: Any,
    post_only_policy: str,
    reduce_only_policy: str,
    now: str,
    max_age_seconds: int,
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    version_value = _required_non_empty_string(rulebook_version, "rulebook_version")
    source_value = _required_non_empty_string(source, "source")
    generated_at_dt = _parse_canonical_utc_timestamp(generated_at, "generated_at")
    effective_at_dt = _parse_canonical_utc_timestamp(effective_at, "effective_at")
    now_dt = _parse_canonical_utc_timestamp(now, "now")
    max_age = _strict_positive_int(max_age_seconds, "max_age_seconds")
    if generated_at_dt > now_dt:
        raise ValueError("generated_at must not be in the future")
    if (now_dt - generated_at_dt).total_seconds() > max_age:
        raise ValueError("stale generated_at")
    if effective_at_dt > now_dt:
        raise ValueError("effective_at must not be in the future")
    post_only_policy_value = _policy_string(post_only_policy, "post_only_policy", _POST_ONLY_POLICIES)
    reduce_only_policy_value = _policy_string(reduce_only_policy, "reduce_only_policy", _REDUCE_ONLY_POLICIES)
    report = {
        "schema_version": "venue_rulebook_report.v1",
        "venue": venue_value,
        "symbol": symbol_value,
        "rulebook_version": version_value,
        "generated_at": generated_at,
        "effective_at": effective_at,
        "source": source_value,
        "constraints": {
            "price_tick_size": _strict_positive_number(price_tick_size, "price_tick_size"),
            "quantity_step_size": _strict_positive_number(quantity_step_size, "quantity_step_size"),
            "min_notional": _strict_non_negative_number(min_notional, "min_notional"),
            "post_only_policy": post_only_policy_value,
            "reduce_only_policy": reduce_only_policy_value,
        },
        "provenance": {
            "source": source_value,
            "rulebook_version": version_value,
        },
    }
    if product_type is not None:
        report["product_type"] = _product_type_string(product_type)
    return report


def build_venue_rulebook_catalog(
    reports: Iterable[Mapping[str, Any]],
    *,
    generated_at: str,
    effective_at: str,
    required_symbols: Iterable[tuple[str, str, str]] = (),
    max_age_seconds: int,
    allow_future: bool = False,
) -> dict[str, Any]:
    generated_at_dt = _parse_canonical_utc_timestamp(generated_at, "generated_at")
    effective_at_dt = _parse_canonical_utc_timestamp(effective_at, "effective_at")
    max_age = _strict_positive_int(max_age_seconds, "max_age_seconds")
    normalized_reports = [
        _validated_catalog_rulebook(
            report,
            generated_at_dt=generated_at_dt,
            effective_at_dt=effective_at_dt,
            max_age_seconds=max_age,
            allow_future=allow_future,
        )
        for report in reports
    ]
    schema_versions = {report["schema_version"] for report in normalized_reports}
    if len(schema_versions) > 1:
        raise ValueError("mixed schema versions")

    duplicate_keys = _duplicate_active_keys(normalized_reports)
    if duplicate_keys:
        raise ValueError("duplicate active rulebook keys")

    conflicting_versions = _conflicting_versions(normalized_reports)
    stale_rulebooks = [
        _catalog_identity(report)
        for report in normalized_reports
        if (generated_at_dt - _parse_canonical_utc_timestamp(report["generated_at"], "generated_at")).total_seconds()
        > max_age
    ]
    missing_required_symbols = _missing_required_symbols(normalized_reports, required_symbols)
    reason_codes: list[str] = []
    if missing_required_symbols:
        reason_codes.append("missing_required_symbols")
    if stale_rulebooks:
        reason_codes.append("stale_rulebooks")
    if duplicate_keys:
        reason_codes.append("duplicate_keys")
    if conflicting_versions:
        reason_codes.append("conflicting_versions")

    return {
        "schema_version": "venue_rulebook_catalog.v1",
        "generated_at": generated_at,
        "effective_at": effective_at,
        "rulebooks": sorted(normalized_reports, key=_catalog_sort_key),
        "coverage_report": {
            "schema_version": "venue_rulebook_catalog_coverage.v1",
            "venue_count": len({report["venue"] for report in normalized_reports}),
            "symbol_count": len({report["symbol"] for report in normalized_reports}),
            "product_type_count": len({report["product_type"] for report in normalized_reports}),
            "missing_required_symbols": missing_required_symbols,
            "stale_rulebooks": stale_rulebooks,
            "duplicate_keys": duplicate_keys,
            "conflicting_versions": conflicting_versions,
            "quality_status": "pass" if not reason_codes else "fail_closed",
            "reason_codes": reason_codes,
        },
    }


def lookup_venue_rulebook(
    catalog: Mapping[str, Any],
    *,
    venue: str,
    symbol: str,
    product_type: str,
    generated_at: str,
    effective_at: str,
    allow_future: bool = False,
) -> dict[str, Any]:
    venue_value = _required_non_empty_string(venue, "venue")
    symbol_value = _required_non_empty_string(symbol, "symbol")
    product_type_value = _product_type_string(product_type)
    generated_at_dt = _parse_canonical_utc_timestamp(generated_at, "generated_at")
    effective_at_dt = _parse_canonical_utc_timestamp(effective_at, "effective_at")
    if catalog.get("schema_version") != "venue_rulebook_catalog.v1":
        raise ValueError("catalog schema_version must be venue_rulebook_catalog.v1")
    rulebooks = catalog.get("rulebooks")
    if not isinstance(rulebooks, list):
        raise ValueError("catalog rulebooks must be a list")
    matches = []
    for report in rulebooks:
        if not isinstance(report, Mapping):
            raise ValueError("catalog rulebooks must contain mappings")
        if (
            report.get("venue") == venue_value
            and report.get("symbol") == symbol_value
            and report.get("product_type") == product_type_value
        ):
            report_generated_at_dt = _parse_canonical_utc_timestamp(report.get("generated_at"), "rulebook generated_at")
            report_effective_at_dt = _parse_canonical_utc_timestamp(report.get("effective_at"), "rulebook effective_at")
            if report_generated_at_dt <= generated_at_dt and (allow_future or report_effective_at_dt <= effective_at_dt):
                matches.append(report)
    if not matches:
        raise ValueError("no matching venue rulebook")
    matches.sort(key=_catalog_sort_key, reverse=True)
    winner = matches[0]
    if len(matches) > 1 and _catalog_sort_key(matches[0]) == _catalog_sort_key(matches[1]):
        raise ValueError("duplicate active rulebook keys")
    return dict(winner)


def _required_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _strict_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _strict_positive_number(value: Any, label: str) -> float:
    number = _strict_number(value, label)
    if number <= 0.0:
        raise ValueError(f"{label} must be a positive finite number")
    return number


def _strict_non_negative_number(value: Any, label: str) -> float:
    number = _strict_number(value, label)
    if number < 0.0:
        raise ValueError(f"{label} must be a non-negative finite number")
    return number


def _strict_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-bool number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite non-bool number")
    return number


def _policy_string(value: Any, label: str, allowed: frozenset[str]) -> str:
    normalized = _required_non_empty_string(value, label)
    if normalized not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _product_type_string(value: Any) -> str:
    normalized = _required_non_empty_string(value, "product_type")
    if normalized not in _PRODUCT_TYPES:
        raise ValueError(f"product_type must be one of: {', '.join(sorted(_PRODUCT_TYPES))}")
    return normalized


def _validated_catalog_rulebook(
    report: Mapping[str, Any],
    *,
    generated_at_dt: datetime,
    effective_at_dt: datetime,
    max_age_seconds: int,
    allow_future: bool,
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ValueError("rulebook report must be a mapping")
    if report.get("schema_version") != "venue_rulebook_report.v1":
        raise ValueError("mixed schema versions")
    constraints = report.get("constraints")
    if not isinstance(constraints, Mapping):
        raise ValueError("rulebook constraints must be a mapping")
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("rulebook provenance must be a mapping")
    normalized = {
        "schema_version": "venue_rulebook_report.v1",
        "venue": _required_non_empty_string(report.get("venue"), "venue"),
        "symbol": _required_non_empty_string(report.get("symbol"), "symbol"),
        "product_type": _product_type_string(report.get("product_type")),
        "rulebook_version": _required_non_empty_string(report.get("rulebook_version"), "rulebook_version"),
        "generated_at": report.get("generated_at"),
        "effective_at": report.get("effective_at"),
        "source": _required_non_empty_string(report.get("source"), "source"),
        "constraints": {
            "price_tick_size": _strict_positive_number(constraints.get("price_tick_size"), "price_tick_size"),
            "quantity_step_size": _strict_positive_number(constraints.get("quantity_step_size"), "quantity_step_size"),
            "min_notional": _strict_non_negative_number(constraints.get("min_notional"), "min_notional"),
            "post_only_policy": _policy_string(
                constraints.get("post_only_policy"), "post_only_policy", _POST_ONLY_POLICIES
            ),
            "reduce_only_policy": _policy_string(
                constraints.get("reduce_only_policy"), "reduce_only_policy", _REDUCE_ONLY_POLICIES
            ),
        },
        "provenance": {
            "source": _required_non_empty_string(provenance.get("source"), "rulebook source"),
            "rulebook_version": _required_non_empty_string(provenance.get("rulebook_version"), "rulebook_version"),
        },
    }
    report_generated_at_dt = _parse_canonical_utc_timestamp(normalized["generated_at"], "generated_at")
    report_effective_at_dt = _parse_canonical_utc_timestamp(normalized["effective_at"], "effective_at")
    if report_generated_at_dt > generated_at_dt:
        raise ValueError("generated_at must not be in the future")
    if (generated_at_dt - report_generated_at_dt).total_seconds() > max_age_seconds:
        raise ValueError("stale generated_at")
    if report_effective_at_dt > effective_at_dt and not allow_future:
        raise ValueError("future effective_at")
    return normalized


def _catalog_identity(report: Mapping[str, Any]) -> dict[str, str]:
    return {
        "venue": str(report["venue"]),
        "symbol": str(report["symbol"]),
        "product_type": str(report["product_type"]),
    }


def _catalog_active_key(report: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(report["venue"]),
        str(report["symbol"]),
        str(report["product_type"]),
        str(report["generated_at"]),
        str(report["effective_at"]),
    )


def _catalog_sort_key(report: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(report["venue"]),
        str(report["symbol"]),
        str(report["product_type"]),
        str(report["effective_at"]),
        str(report["generated_at"]),
        str(report["rulebook_version"]),
    )


def _duplicate_active_keys(reports: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    duplicates: list[dict[str, str]] = []
    emitted: set[tuple[str, str, str, str, str]] = set()
    for report in reports:
        key = _catalog_active_key(report)
        if key in seen and key not in emitted:
            duplicate = _catalog_identity(report)
            duplicate["generated_at"] = str(report["generated_at"])
            duplicate["effective_at"] = str(report["effective_at"])
            duplicates.append(duplicate)
            emitted.add(key)
        seen.add(key)
    return duplicates


def _conflicting_versions(reports: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    versions_by_key: dict[tuple[str, str, str, str, str], set[str]] = {}
    for report in reports:
        key = _catalog_active_key(report)
        versions_by_key.setdefault(key, set()).add(str(report["rulebook_version"]))
    conflicts: list[dict[str, Any]] = []
    for (venue, symbol, product_type, generated_at, effective_at), versions in sorted(versions_by_key.items()):
        if len(versions) > 1:
            conflicts.append(
                {
                    "venue": venue,
                    "symbol": symbol,
                    "product_type": product_type,
                    "generated_at": generated_at,
                    "effective_at": effective_at,
                    "rulebook_versions": sorted(versions),
                }
            )
    return conflicts


def _missing_required_symbols(
    reports: list[Mapping[str, Any]],
    required_symbols: Iterable[tuple[str, str, str]],
) -> list[dict[str, str]]:
    present = {(str(report["venue"]), str(report["symbol"]), str(report["product_type"])) for report in reports}
    missing = []
    for venue, symbol, product_type in required_symbols:
        required = (
            _required_non_empty_string(venue, "required venue"),
            _required_non_empty_string(symbol, "required symbol"),
            _product_type_string(product_type),
        )
        if required not in present:
            missing.append({"venue": required[0], "symbol": required[1], "product_type": required[2]})
    return sorted(missing, key=lambda item: (item["venue"], item["symbol"], item["product_type"]))


def _validate_rulebook_for_constraint_report(
    rulebook: Mapping[str, Any] | None,
    *,
    venue: str,
    symbol: str,
) -> dict[str, str] | None:
    if rulebook is None:
        return None
    if _required_non_empty_string(rulebook.get("venue"), "rulebook venue") != venue:
        raise ValueError("rulebook venue must match report venue")
    if _required_non_empty_string(rulebook.get("symbol"), "rulebook symbol") != symbol:
        raise ValueError("rulebook symbol must match report symbol")
    provenance = rulebook.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("rulebook provenance must be a mapping")
    source = _required_non_empty_string(provenance.get("source"), "rulebook source")
    rulebook_version = _required_non_empty_string(provenance.get("rulebook_version"), "rulebook_version")
    return {"rulebook_version": rulebook_version, "source": source}


def _aligned_to_increment(value: float, increment: float) -> bool:
    try:
        return Decimal(str(value)) % Decimal(str(increment)) == 0
    except InvalidOperation as exc:
        raise ValueError("unsupported exchange constraint increment validation") from exc


def _post_only_would_cross(order: Mapping[str, Any]) -> bool:
    side = order.get("side")
    price = order.get("price")
    if side not in {"BUY", "SELL"} or price is None:
        return False
    order_price = _strict_non_negative_number(price, "price")
    if side == "BUY" and order.get("best_ask") is not None:
        return order_price >= _strict_non_negative_number(order.get("best_ask"), "best_ask")
    if side == "SELL" and order.get("best_bid") is not None:
        return order_price <= _strict_non_negative_number(order.get("best_bid"), "best_bid")
    return False


def _parse_utc_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)


def _parse_canonical_utc_timestamp(value: str, label: str) -> datetime:
    parsed = _parse_utc_timestamp(value, label)
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if value != canonical:
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    return parsed
