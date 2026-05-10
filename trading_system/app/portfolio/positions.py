from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
import re
from typing import Any

from .target_management import ensure_target_management_state, stage_completed, terminalize_all_unreachable_stages
from ..types import AccountSnapshot, BJ, ManagementActionIntent, OrderIntent, PositionSnapshot, RuntimeState


_POSITION_TAXONOMY_KEYS = (
    "taxonomy_stop_loss",
    "invalidation_source",
    "invalidation_reason",
    "stop_family",
    "stop_reference",
    "stop_policy_source",
)
_TARGET_MANAGEMENT_KEYS = (
    "structure_target_price",
    "first_target_price",
    "first_target_source",
    "first_target_status",
    "first_target_hit",
    "first_target_filled_qty",
    "second_target_price",
    "second_target_source",
    "second_target_status",
    "second_target_hit",
    "second_target_filled_qty",
    "runner_protected",
    "runner_stop_price",
    "original_position_qty",
    "remaining_position_qty",
    "scale_out_plan",
    "symbol_step_size",
    "min_order_qty",
    "legacy_partial_filled_qty",
)
_EXPLICIT_TARGET_MANAGEMENT_STATE_KEYS = (
    "first_target_price",
    "second_target_price",
    "first_target_status",
    "second_target_status",
    "first_target_filled_qty",
    "second_target_filled_qty",
    "runner_protected",
    "runner_stop_price",
    "scale_out_plan",
    "original_position_qty",
    "remaining_position_qty",
    "legacy_partial_filled_qty",
)
_TARGET_MANAGEMENT_NUMBER_KEYS = frozenset(
    {
        "structure_target_price",
        "first_target_price",
        "first_target_filled_qty",
        "second_target_price",
        "second_target_filled_qty",
        "runner_stop_price",
        "original_position_qty",
        "remaining_position_qty",
        "symbol_step_size",
        "min_order_qty",
        "legacy_partial_filled_qty",
    }
)
_TARGET_MANAGEMENT_STRING_KEYS = frozenset(
    {
        "first_target_source",
        "first_target_status",
        "second_target_source",
        "second_target_status",
    }
)
_TARGET_MANAGEMENT_BOOL_KEYS = frozenset(
    {
        "first_target_hit",
        "second_target_hit",
        "runner_protected",
    }
)
_POSITION_STATUS_VALUES = frozenset(
    {
        "OPEN",
        "PENDING",
        "SENT",
        "FILLED",
        "CLOSED",
        "SKIPPED",
        "FAILED",
        "CANCELLED",
        "CANCELED",
    }
)
_MANAGEMENT_ACTION_VALUES = frozenset(
    {
        "BREAK_EVEN",
        "ADD_PROTECTIVE_STOP",
        "PARTIAL_TAKE_PROFIT",
        "DE_RISK",
        "EXIT",
    }
)
_FRACTION_BASIS_VALUES = frozenset({"original_position"})
_EXIT_TRIGGER_VALUES = frozenset({"first_target_hit", "second_target_hit", "runner_stop_hit"})
_POSITION_SIDE_VALUES = frozenset({"LONG", "SHORT"})
_SNAPSHOT_IDENTITY_KEYS = (
    "signal_id",
    "signalId",
    "order_id",
    "orderId",
    "client_order_id",
    "clientOrderId",
)
_SNAPSHOT_REMAINING_IDENTITY_KEYS = (
    "trade_id",
    "tradeId",
    "execution_id",
    "executionId",
    "fill_id",
    "fillId",
    "strategy_id",
    "strategyId",
    "setup_id",
    "setupId",
    "batch_id",
    "batchId",
    "source_id",
    "sourceId",
    "correlation_id",
    "correlationId",
    "parent_order_id",
    "parentOrderId",
    "exchange_order_id",
    "exchangeOrderId",
)
_SNAPSHOT_PROVENANCE_TAXONOMY_STRING_FIELDS = {
    "source": frozenset({"account_snapshot", "paper_execution", "hybrid"}),
    "position_source": frozenset({"account_snapshot", "paper_execution"}),
    "signal_source": None,
    "strategy_source": None,
    "data_source": None,
    "margin_type": frozenset({"CROSS", "ISOLATED"}),
    "product_type": frozenset({"FUTURES", "MARGIN", "SPOT"}),
    "account_type": frozenset({"paper", "testnet"}),
    "venue": frozenset({"BINANCE"}),
    "exchange": frozenset({"BINANCE"}),
}
_SNAPSHOT_COST_METADATA_KEYS = (
    "fee_paid",
    "commission",
    "funding_paid",
    "funding_fee",
    "slippage_paid",
    "carry_cost",
    "borrow_fee",
)
_NON_NEGATIVE_SNAPSHOT_COST_METADATA_KEYS = frozenset(
    {
        "fee_paid",
        "commission",
        "slippage_paid",
        "carry_cost",
        "borrow_fee",
    }
)
_SNAPSHOT_ORDER_EXECUTION_STRING_FIELDS = {
    "order_type": frozenset({"LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"}),
    "time_in_force": frozenset({"GTC", "IOC", "FOK", "GTX"}),
    "execution_venue": frozenset({"binance_spot", "binance_futures", "binance_futures_testnet", "paper_simulator"}),
    "liquidity_role": frozenset({"maker", "taker"}),
    "maker_status": frozenset({"filled", "partial", "no_fill", "expired", "cancelled_replaced"}),
}
_SNAPSHOT_ORDER_EXECUTION_BOOL_FIELDS = ("reduce_only", "post_only")
_SNAPSHOT_RISK_PRICE_METADATA_KEYS = (
    "liquidation_price",
    "liquidationPrice",
    "break_even_price",
    "breakEvenPrice",
    "risk_price",
    "stop_price",
    "take_profit_price",
    "trailing_stop_price",
    "mark_spread_bps",
)
_POSITIVE_SNAPSHOT_RISK_PRICE_METADATA_KEYS = frozenset(
    {
        "liquidation_price",
        "liquidationPrice",
        "break_even_price",
        "breakEvenPrice",
        "risk_price",
        "stop_price",
        "take_profit_price",
        "trailing_stop_price",
    }
)
_NON_NEGATIVE_SNAPSHOT_RISK_PRICE_METADATA_KEYS = frozenset({"mark_spread_bps"})
_SNAPSHOT_POSITION_SIZING_METADATA_KEYS = (
    "position_value",
    "market_value",
    "exposure_value",
    "margin_used",
    "initial_margin",
    "maintenance_margin",
    "collateral_value",
    "risk_pct",
    "exposure_pct",
)
_POSITIVE_SNAPSHOT_POSITION_SIZING_METADATA_KEYS = frozenset({"position_value", "market_value", "exposure_value"})
_SNAPSHOT_POSITION_SIZING_RATIO_KEYS = frozenset({"risk_pct", "exposure_pct"})
_SNAPSHOT_ASSET_IDENTITY_KEYS = (
    "base_asset",
    "quote_asset",
    "margin_asset",
    "collateral_asset",
    "settlement_asset",
    "fee_asset",
    "funding_asset",
    "pnl_asset",
    "pnl_currency",
)
_SNAPSHOT_TIME_PROVENANCE_KEYS = (
    "opened_at",
    "updated_at",
    "as_of",
    "timestamp",
    "last_update_time",
    "event_time",
    "trade_time",
    "execution_time",
    "fill_time",
    "order_time",
    "close_time",
    "expiry_time",
    "settlement_time",
)
_CANONICAL_UTC_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _strict_canonical_symbol(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or value != value.strip() or value != value.upper() or not value.isalnum():
        raise ValueError(f"{field} must be a canonical symbol string")
    return value


def _now_bj() -> str:
    return datetime.now(BJ).isoformat()


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _position_notional(snapshot: PositionSnapshot) -> float:
    if snapshot.notional:
        return round(abs(float(snapshot.notional)), 4)
    reference_price = snapshot.mark_price or snapshot.entry_price
    return round(abs(float(snapshot.qty)) * reference_price, 4)


def _unrealized_pnl(side: str, qty: float, entry_price: float, mark_price: float | None, fallback: float) -> float:
    if qty <= 0 or entry_price <= 0 or mark_price is None or mark_price <= 0:
        return round(float(fallback), 4)
    if side == "LONG":
        return round((mark_price - entry_price) * qty, 4)
    return round((entry_price - mark_price) * qty, 4)


def _source(existing: dict[str, Any], from_snapshot: bool, from_intent: bool, field: str = "source") -> str:
    if "source" in existing and existing.get("source") is not None:
        _strict_optional_string(existing, "source", field)
    if from_snapshot and from_intent:
        return "hybrid"
    if from_intent:
        return "paper_execution"
    return _strict_optional_string(existing, "source", field) or "account_snapshot"


def _strict_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping when present")
    return value


def _strict_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number when present")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite when present")
    return number


def _strict_optional_number(payload: Mapping[str, Any], key: str, field: str | None = None, default: float | None = None) -> float:
    label = field or key
    if key not in payload or payload.get(key) is None:
        if default is None:
            raise ValueError(f"{label} must be present")
        return default
    return _strict_finite_number(payload.get(key), label)


def _strict_optional_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{label} must not be blank when present")
    return normalized


def _strict_optional_canonical_lower_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    value = _strict_optional_string(payload, key, label)
    if value and payload.get(key) != value:
        raise ValueError(f"{label} must be canonical when present")
    return value


def _strict_optional_identity_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be canonical when present")
    return value


def _strict_present_remaining_identity_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a canonical string when present")
    if not value or value != value.strip() or any(char.isspace() or ord(char) < 32 for char in value) or "/" in value:
        raise ValueError(f"{label} must be a canonical string when present")
    return value


def _strict_optional_iso_datetime_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a canonical ISO timestamp when present")
    if "T" not in value:
        raise ValueError(f"{label} must be a canonical ISO timestamp when present")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical ISO timestamp when present") from exc
    return value


def _strict_present_canonical_utc_timestamp(payload: Mapping[str, Any], key: str, field: str | None = None) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a canonical UTC ISO timestamp when present")
    if not _CANONICAL_UTC_ISO_TIMESTAMP_RE.fullmatch(value):
        raise ValueError(f"{label} must be a canonical UTC ISO timestamp when present")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UTC ISO timestamp when present") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{label} must be a canonical UTC ISO timestamp when present")
    return value


def _strict_position_status(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return "OPEN"
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    if value != value.strip() or not value:
        raise ValueError(f"{label} must not be blank when present")
    if value not in _POSITION_STATUS_VALUES:
        raise ValueError(f"{label} must be one of {sorted(_POSITION_STATUS_VALUES)} when present")
    return value


def _strict_position_side(payload: Mapping[str, Any], key: str, field: str | None = None) -> str:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return ""
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    if value != value.strip() or not value:
        raise ValueError(f"{label} must not be blank when present")
    if value not in _POSITION_SIDE_VALUES:
        raise ValueError(f"{label} must be one of {sorted(_POSITION_SIDE_VALUES)} when present")
    return value


def _strict_optional_bool(payload: Mapping[str, Any], field: str, default: bool = False) -> bool:
    if field not in payload or payload.get(field) is None:
        return default
    value = payload.get(field)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a bool when present")


def _strict_present_optional_bool(payload: Mapping[str, Any], key: str, field: str | None = None) -> bool | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{label} must be a strict boolean when present")


def _strict_present_supported_string(
    payload: Mapping[str, Any], key: str, allowed: frozenset[str], field: str | None = None
) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a supported canonical string when present")
    if value not in allowed:
        raise ValueError(f"{label} must be a supported canonical string when present")
    return value


def _strict_present_supported_taxonomy_string(
    payload: Mapping[str, Any], key: str, allowed: frozenset[str], field: str | None = None
) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a supported canonical string when present")
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a supported canonical string when present")
    if value not in allowed:
        raise ValueError(f"{label} must be a supported canonical string when present")
    return value


def _strict_present_identifier_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a canonical string when present")
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must be a canonical string when present")
    if not all(char.islower() or char.isdigit() or char == "_" for char in value):
        raise ValueError(f"{label} must be a canonical identifier string when present")
    return value


def _strict_present_asset_identity_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a canonical asset string when present")
    if not value or value != value.strip() or value != value.upper() or not value.isalnum():
        raise ValueError(f"{label} must be a canonical asset string when present")
    return value


def _strict_present_optional_number(payload: Mapping[str, Any], key: str, field: str | None = None) -> float | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    return _strict_finite_number(payload.get(key), label)


def _strict_present_optional_positive_number(
    payload: Mapping[str, Any], key: str, field: str | None = None
) -> float | None:
    label = field or key
    value = _strict_present_optional_number(payload, key, label)
    if value is None:
        return None
    if value <= 0.0:
        raise ValueError(f"{label} must be positive when present")
    return value


def _strict_snapshot_provenance_taxonomy_metadata(snapshot: PositionSnapshot) -> dict[str, str]:
    payload: dict[str, str] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key, allowed in _SNAPSHOT_PROVENANCE_TAXONOMY_STRING_FIELDS.items():
        if allowed is None:
            value = _strict_present_identifier_string(
                {key: getattr(snapshot, key, None)},
                key,
                f"{snapshot_label}.{key}",
            )
        else:
            value = _strict_present_supported_taxonomy_string(
                {key: getattr(snapshot, key, None)},
                key,
                allowed,
                f"{snapshot_label}.{key}",
            )
        if value is not None:
            payload[key] = value
    return payload


def _strict_snapshot_cost_metadata(snapshot: PositionSnapshot) -> dict[str, float]:
    payload: dict[str, float] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_COST_METADATA_KEYS:
        value = _strict_present_optional_number(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is None:
            continue
        if key in _NON_NEGATIVE_SNAPSHOT_COST_METADATA_KEYS and value < 0.0:
            raise ValueError(f"{snapshot_label}.{key} must be non-negative when present")
        payload[key] = value
    return payload


def _strict_snapshot_order_execution_metadata(snapshot: PositionSnapshot) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key, allowed in _SNAPSHOT_ORDER_EXECUTION_STRING_FIELDS.items():
        value = _strict_present_supported_string(
            {key: getattr(snapshot, key, None)},
            key,
            allowed,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    for key in _SNAPSHOT_ORDER_EXECUTION_BOOL_FIELDS:
        value = _strict_present_optional_bool(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    return payload


def _strict_snapshot_risk_price_metadata(snapshot: PositionSnapshot) -> dict[str, float]:
    payload: dict[str, float] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_RISK_PRICE_METADATA_KEYS:
        value = _strict_present_optional_number(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is None:
            continue
        if key in _POSITIVE_SNAPSHOT_RISK_PRICE_METADATA_KEYS and value <= 0.0:
            raise ValueError(f"{snapshot_label}.{key} must be positive when present")
        if key in _NON_NEGATIVE_SNAPSHOT_RISK_PRICE_METADATA_KEYS and value < 0.0:
            raise ValueError(f"{snapshot_label}.{key} must be non-negative when present")
        payload[key] = value
    return payload


def _strict_snapshot_position_sizing_metadata(snapshot: PositionSnapshot) -> dict[str, float]:
    payload: dict[str, float] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_POSITION_SIZING_METADATA_KEYS:
        value = _strict_present_optional_number(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is None:
            continue
        if key in _SNAPSHOT_POSITION_SIZING_RATIO_KEYS:
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{snapshot_label}.{key} must be a bounded non-negative ratio when present")
        elif key in _POSITIVE_SNAPSHOT_POSITION_SIZING_METADATA_KEYS and value <= 0.0:
            raise ValueError(f"{snapshot_label}.{key} must be positive when present")
        elif value < 0.0:
            raise ValueError(f"{snapshot_label}.{key} must be non-negative when present")
        payload[key] = value
    return payload


def _strict_snapshot_asset_identity_metadata(snapshot: PositionSnapshot) -> dict[str, str]:
    payload: dict[str, str] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_ASSET_IDENTITY_KEYS:
        value = _strict_present_asset_identity_string(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    return payload


def _strict_snapshot_remaining_identity_metadata(snapshot: PositionSnapshot) -> dict[str, str]:
    payload: dict[str, str] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_REMAINING_IDENTITY_KEYS:
        value = _strict_present_remaining_identity_string(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    return payload


def _canonical_utc_timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_snapshot_time_provenance_order(payload: Mapping[str, str], *, snapshot_label: str) -> None:
    if "order_time" in payload and "execution_time" in payload:
        order_time = _canonical_utc_timestamp_value(payload["order_time"])
        execution_time = _canonical_utc_timestamp_value(payload["execution_time"])
        if execution_time < order_time:
            raise ValueError(f"{snapshot_label}.execution_time must be at or after order_time")
    if "execution_time" in payload and "fill_time" in payload:
        execution_time = _canonical_utc_timestamp_value(payload["execution_time"])
        fill_time = _canonical_utc_timestamp_value(payload["fill_time"])
        if fill_time < execution_time:
            raise ValueError(f"{snapshot_label}.fill_time must be at or after execution_time")


def _strict_snapshot_time_provenance_metadata(snapshot: PositionSnapshot) -> dict[str, str]:
    payload: dict[str, str] = {}
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    for key in _SNAPSHOT_TIME_PROVENANCE_KEYS:
        value = _strict_present_canonical_utc_timestamp(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    _validate_snapshot_time_provenance_order(payload, snapshot_label=snapshot_label)
    return payload


def _strict_non_negative_quantity(payload: Mapping[str, Any], field: str, default: float | None = None) -> float:
    if field not in payload or payload.get(field) is None:
        if default is None:
            raise ValueError(f"{field} must be present")
        return default
    try:
        qty = _strict_finite_number(payload.get(field), field)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc
    if not math.isfinite(qty) or qty < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return qty


def _partial_take_profit_stage(intent: ManagementActionIntent) -> str:
    if intent.action != "PARTIAL_TAKE_PROFIT":
        return ""
    meta = intent.meta or {}
    if "target_stage" not in meta or meta.get("target_stage") is None:
        return ""
    stage = meta.get("target_stage")
    if not isinstance(stage, str) or stage not in {"first", "second"}:
        raise ValueError("target_stage must be absent or one of: first, second")
    return stage


def _partial_take_profit_fraction_basis(intent: ManagementActionIntent) -> str:
    if intent.action != "PARTIAL_TAKE_PROFIT":
        return ""
    meta = intent.meta or {}
    if "fraction_basis" not in meta or meta.get("fraction_basis") is None:
        return ""
    fraction_basis = meta.get("fraction_basis")
    if not isinstance(fraction_basis, str):
        raise ValueError("fraction_basis must be a string when present")
    if not fraction_basis or fraction_basis != fraction_basis.strip():
        raise ValueError("fraction_basis must be a canonical string when present")
    if fraction_basis not in _FRACTION_BASIS_VALUES:
        raise ValueError("fraction_basis must be one of: original_position")
    return fraction_basis


def _management_exit_trigger(intent: ManagementActionIntent) -> str:
    meta = intent.meta or {}
    if "exit_trigger" not in meta or meta.get("exit_trigger") is None:
        return ""
    exit_trigger = meta.get("exit_trigger")
    if not isinstance(exit_trigger, str):
        raise ValueError("exit_trigger must be a string when present")
    if not exit_trigger or exit_trigger != exit_trigger.strip():
        raise ValueError("exit_trigger must be a canonical string when present")
    if exit_trigger not in _EXIT_TRIGGER_VALUES:
        raise ValueError("exit_trigger must be one of: first_target_hit, second_target_hit, runner_stop_hit")
    return exit_trigger


def _management_action(intent: ManagementActionIntent) -> str:
    action = intent.action
    if not isinstance(action, str) or action != action.strip() or not action:
        raise ValueError("management action must be a canonical string")
    if action not in _MANAGEMENT_ACTION_VALUES:
        raise ValueError("management action must be a canonical management action")
    return action


def _taxonomy_fields(existing: dict[str, Any], field_prefix: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _POSITION_TAXONOMY_KEYS:
        value = _strict_present_optional_number(existing, key, f"{field_prefix}{key}") if key == "taxonomy_stop_loss" else _strict_taxonomy_string(existing, key, f"{field_prefix}{key}")
        if value is not None:
            payload[key] = value
    return payload


def _strict_taxonomy_string(payload: Mapping[str, Any], key: str, field: str | None = None) -> str | None:
    label = field or key
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string when present")
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{label} must not be blank when present")
    return value


def _order_taxonomy_fields(order: OrderIntent, existing: dict[str, Any]) -> dict[str, Any]:
    meta = _strict_mapping(order.meta, "order.meta")
    payload = _taxonomy_fields(existing)
    taxonomy_stop_loss = _strict_present_optional_number(meta, "taxonomy_stop_loss")
    if taxonomy_stop_loss is None:
        taxonomy_stop_loss = order.stop_loss
    payload["taxonomy_stop_loss"] = round(_strict_finite_number(taxonomy_stop_loss, "taxonomy_stop_loss"), 8)
    for key in _POSITION_TAXONOMY_KEYS[1:]:
        value = _strict_taxonomy_string(meta, key)
        if value is not None:
            payload[key] = value
    return payload


def _snapshot_taxonomy_fields(snapshot: PositionSnapshot, existing: dict[str, Any]) -> dict[str, Any]:
    snapshot_label = f"account.open_positions[{snapshot.symbol}]"
    payload = _taxonomy_fields(existing, f"positions[{snapshot.symbol}].")
    taxonomy_stop_loss = _strict_present_optional_number(
        {"taxonomy_stop_loss": getattr(snapshot, "taxonomy_stop_loss", None)},
        "taxonomy_stop_loss",
        f"{snapshot_label}.taxonomy_stop_loss",
    )
    if taxonomy_stop_loss is not None:
        payload["taxonomy_stop_loss"] = round(taxonomy_stop_loss, 8)
    for key in _POSITION_TAXONOMY_KEYS[1:]:
        value = _strict_taxonomy_string(
            {key: getattr(snapshot, key, None)},
            key,
            f"{snapshot_label}.{key}",
        )
        if value is not None:
            payload[key] = value
    return payload


def _target_management_fields(existing: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _TARGET_MANAGEMENT_KEYS:
        if key in existing:
            payload[key] = existing.get(key)
    return payload


def _order_target_management_fields(order: OrderIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    meta = _strict_mapping(order.meta, "order.meta")
    for key in _TARGET_MANAGEMENT_KEYS:
        if key in meta:
            if key in _TARGET_MANAGEMENT_NUMBER_KEYS:
                payload[key] = _strict_present_optional_number(meta, key)
            elif key in _TARGET_MANAGEMENT_STRING_KEYS:
                payload[key] = _strict_taxonomy_string(meta, key)
            elif key in _TARGET_MANAGEMENT_BOOL_KEYS:
                payload[key] = _strict_optional_bool(meta, key)
            else:
                payload[key] = meta.get(key)
    return payload


def _has_explicit_target_management_state(existing: dict[str, Any]) -> bool:
    if not any(key in existing for key in _EXPLICIT_TARGET_MANAGEMENT_STATE_KEYS):
        return False

    def _positive(value: Any) -> bool:
        try:
            return float(value) > 0.0
        except (TypeError, ValueError):
            return False

    statuses: dict[str, str] = {}
    for key in ("first_target_status", "second_target_status"):
        status = _strict_optional_string(existing, key)
        statuses[key] = status
        if status and status != "pending":
            return True

    if _positive(existing.get("first_target_filled_qty")) or _positive(existing.get("second_target_filled_qty")):
        return True
    if _strict_optional_bool(existing, "runner_protected") or existing.get("runner_stop_price") is not None:
        return True
    if _positive(existing.get("legacy_partial_filled_qty")):
        return True

    first_source = _strict_optional_string(existing, "first_target_source")
    second_source = _strict_optional_string(existing, "second_target_source")
    has_legacy_or_structure_seed = existing.get("take_profit") is not None or existing.get("structure_target_price") is not None
    pending_only = all(not value or value == "pending" for value in statuses.values())
    fallback_seed_only = first_source == "fallback_1r" and second_source == "fixed_2r"

    if pending_only and fallback_seed_only and not has_legacy_or_structure_seed:
        return False

    return True


def _position_close_event_payload(symbol: str, position: dict[str, Any], now_bj: str) -> dict[str, Any]:
    return {
        "event": "POSITION_CLOSED",
        "symbol": symbol,
        "side": position.get("side"),
        "intent_id": position.get("intent_id"),
        "signal_id": position.get("signal_id"),
        "entry_price": position.get("entry_price"),
        "stop_loss": position.get("stop_loss"),
        "take_profit": position.get("take_profit"),
        "opened_at_bj": position.get("opened_at_bj"),
        "closed_at_bj": now_bj,
        "notified": False,
    }


def _validate_existing_position_identities(positions: Mapping[str, dict[str, Any]]) -> None:
    for symbol, position in positions.items():
        _strict_canonical_symbol(symbol, "position key")
        if "symbol" in position and position.get("symbol") is not None:
            embedded_symbol = position.get("symbol")
            _strict_canonical_symbol(embedded_symbol, f"positions[{symbol}].symbol")
            if embedded_symbol != symbol:
                raise ValueError(f"positions[{symbol}].symbol must match position key")
        _strict_position_side(position, "side", f"positions[{symbol}].side")
        _strict_position_status(position, "status", f"positions[{symbol}].status")
        for key in ("intent_id", "signal_id", "strategy_tag", "setup_type", "engine"):
            _strict_optional_identity_string(position, key, f"positions[{symbol}].{key}")
        for key in ("opened_at_bj", "updated_at_bj"):
            _strict_optional_iso_datetime_string(position, key, f"positions[{symbol}].{key}")
        source = _strict_optional_string(position, "source", f"positions[{symbol}].source")
        if source and position.get("source") != position.get("source").strip():
            raise ValueError(f"positions[{symbol}].source must not be blank when present")


def _validate_snapshot_position_identities(open_positions: list[PositionSnapshot]) -> None:
    for snapshot in open_positions:
        _strict_canonical_symbol(snapshot.symbol, f"account.open_positions[{snapshot.symbol}].symbol")
        _strict_position_side({"side": snapshot.side}, "side", f"account.open_positions[{snapshot.symbol}].side")
        _strict_position_status({"status": getattr(snapshot, "status", None)}, "status", f"account.open_positions[{snapshot.symbol}].status")
        _strict_optional_canonical_lower_string(
            {"strategy_tag": getattr(snapshot, "strategy_tag", None)},
            "strategy_tag",
            f"account.open_positions[{snapshot.symbol}].strategy_tag",
        )
        for key in _SNAPSHOT_IDENTITY_KEYS:
            _strict_optional_identity_string(
                {key: getattr(snapshot, key, None)},
                key,
                f"account.open_positions[{snapshot.symbol}].{key}",
            )
        _strict_snapshot_remaining_identity_metadata(snapshot)
        _strict_snapshot_provenance_taxonomy_metadata(snapshot)
        _strict_present_optional_number(
            {"taxonomy_stop_loss": getattr(snapshot, "taxonomy_stop_loss", None)},
            "taxonomy_stop_loss",
            f"account.open_positions[{snapshot.symbol}].taxonomy_stop_loss",
        )
        for key in _POSITION_TAXONOMY_KEYS[1:]:
            _strict_taxonomy_string(
                {key: getattr(snapshot, key, None)},
                key,
                f"account.open_positions[{snapshot.symbol}].{key}",
            )
        _strict_snapshot_cost_metadata(snapshot)
        _strict_snapshot_order_execution_metadata(snapshot)
        _strict_snapshot_risk_price_metadata(snapshot)
        _strict_snapshot_position_sizing_metadata(snapshot)
        _strict_snapshot_asset_identity_metadata(snapshot)
        _strict_snapshot_time_provenance_metadata(snapshot)


def _mark_intent_position_closed(state: RuntimeState, symbol: str, position: dict[str, Any], now_bj: str) -> None:
    position["qty"] = 0.0
    position["remaining_position_qty"] = 0.0
    position["status"] = "CLOSED"
    position["closed_at_bj"] = now_bj
    position["tracked_from_snapshot"] = False
    position["source"] = _source(position, from_snapshot=False, from_intent=True)
    position["updated_at_bj"] = now_bj
    position["last_synced_from"] = "account_snapshot_closed"
    state.active_orders[f"position-closed-{symbol}"] = _position_close_event_payload(symbol, position, now_bj)


def sync_positions_from_account(state: RuntimeState, account: AccountSnapshot) -> list[dict[str, Any]]:
    now_bj = _now_bj()
    seen_symbols: set[str] = set()
    account_meta = _strict_mapping(account.meta, "account.meta")
    snapshot_source = _strict_optional_canonical_lower_string(
        account_meta,
        "snapshot_source",
        "account.meta.snapshot_source",
    )
    if not snapshot_source:
        snapshot_source = _strict_optional_canonical_lower_string(account_meta, "source", "account.meta.source")
    _validate_existing_position_identities(state.positions)
    _validate_snapshot_position_identities(account.open_positions)

    for snapshot in account.open_positions:
        snapshot_label = f"account.open_positions[{snapshot.symbol}]"
        snapshot_qty = _strict_finite_number(snapshot.qty, f"{snapshot_label}.qty")
        snapshot_entry_price = _strict_finite_number(snapshot.entry_price, f"{snapshot_label}.entry_price")
        snapshot_mark_price = (
            None
            if snapshot.mark_price is None
            else _strict_finite_number(snapshot.mark_price, f"{snapshot_label}.mark_price")
        )
        snapshot_notional = _strict_finite_number(snapshot.notional, f"{snapshot_label}.notional")
        snapshot_unrealized_pnl = _strict_finite_number(snapshot.unrealized_pnl, f"{snapshot_label}.unrealized_pnl")
        snapshot_leverage = _strict_present_optional_positive_number(
            {"leverage": snapshot.leverage},
            "leverage",
            f"{snapshot_label}.leverage",
        )
        snapshot_provenance_taxonomy_metadata = _strict_snapshot_provenance_taxonomy_metadata(snapshot)
        snapshot_cost_metadata = _strict_snapshot_cost_metadata(snapshot)
        snapshot_order_execution_metadata = _strict_snapshot_order_execution_metadata(snapshot)
        snapshot_risk_price_metadata = _strict_snapshot_risk_price_metadata(snapshot)
        snapshot_position_sizing_metadata = _strict_snapshot_position_sizing_metadata(snapshot)
        snapshot_asset_identity_metadata = _strict_snapshot_asset_identity_metadata(snapshot)
        snapshot_remaining_identity_metadata = _strict_snapshot_remaining_identity_metadata(snapshot)
        snapshot_time_provenance_metadata = _strict_snapshot_time_provenance_metadata(snapshot)

        if snapshot_qty <= 0:
            continue

        existing = state.positions.get(snapshot.symbol, {})
        carry_existing = existing if existing.get("side") == snapshot.side else {}
        tracked_from_intent = _strict_optional_bool(carry_existing, "tracked_from_intent")
        seen_symbols.add(snapshot.symbol)

        carry_qty = _strict_optional_number(
            carry_existing,
            "qty",
            f"positions[{snapshot.symbol}].qty",
            default=0.0,
        )
        carry_entry_price = _strict_optional_number(
            carry_existing,
            "entry_price",
            f"positions[{snapshot.symbol}].entry_price",
            default=0.0,
        )
        preserve_paper_position = (
            tracked_from_intent
            and "testnet" not in snapshot_source
            and "binance" not in snapshot_source
            and carry_qty > 0.0
            and carry_entry_price > 0.0
        )
        qty = round(abs(carry_qty), 6) if preserve_paper_position else round(abs(snapshot_qty), 6)
        entry_price = (
            _round_price(carry_entry_price) or 0.0
            if preserve_paper_position
            else (_round_price(snapshot_entry_price) or 0.0)
        )
        mark_price = _round_price(snapshot_mark_price)
        reference_price = mark_price or entry_price
        notional = (
            round(qty * reference_price, 4)
            if preserve_paper_position
            else _position_notional(
                PositionSnapshot(
                    symbol=snapshot.symbol,
                    side=snapshot.side,
                    qty=snapshot_qty,
                    entry_price=snapshot_entry_price,
                    mark_price=snapshot_mark_price,
                    unrealized_pnl=snapshot_unrealized_pnl,
                    notional=snapshot_notional,
                    leverage=snapshot_leverage,
                    strategy_tag=snapshot.strategy_tag,
                )
            )
        )
        unrealized_pnl = (
            _unrealized_pnl(snapshot.side, qty, entry_price, mark_price, snapshot_unrealized_pnl)
            if preserve_paper_position
            else round(snapshot_unrealized_pnl, 4)
        )

        synced_position = {
            "symbol": snapshot.symbol,
            "side": snapshot.side,
            "qty": qty,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
            "notional": notional,
            "leverage": snapshot_leverage,
            "stop_loss": carry_existing.get("stop_loss"),
            "take_profit": carry_existing.get("take_profit"),
            "status": "OPEN",
            "intent_id": carry_existing.get("intent_id"),
            "signal_id": carry_existing.get("signal_id"),
            **_snapshot_taxonomy_fields(snapshot, carry_existing),
            **snapshot_provenance_taxonomy_metadata,
            **snapshot_cost_metadata,
            **snapshot_order_execution_metadata,
            **snapshot_risk_price_metadata,
            **snapshot_position_sizing_metadata,
            **snapshot_asset_identity_metadata,
            **snapshot_remaining_identity_metadata,
            **snapshot_time_provenance_metadata,
            "source": _source(
                carry_existing,
                from_snapshot=True,
                from_intent=tracked_from_intent,
                field=f"positions[{snapshot.symbol}].source",
            ),
            "tracked_from_snapshot": True,
            "tracked_from_intent": tracked_from_intent,
            "opened_at_bj": carry_existing.get("opened_at_bj", now_bj),
            "updated_at_bj": now_bj,
            "last_synced_from": "account_snapshot",
            **_target_management_fields(carry_existing),
        }
        synced_position["remaining_position_qty"] = round(qty, 8)
        state.positions[snapshot.symbol] = ensure_target_management_state(synced_position)
        state.active_orders.pop(f"position-closed-{snapshot.symbol}", None)

    stale_symbols: list[str] = []
    for symbol, position in state.positions.items():
        if symbol in seen_symbols:
            continue
        if position.get("tracked_from_snapshot") and not position.get("tracked_from_intent"):
            stale_symbols.append(symbol)
            continue
        status = _strict_position_status(position, "status", f"positions[{symbol}].status")
        if position.get("tracked_from_intent") and status not in {"CLOSED", "SKIPPED", "FAILED", "CANCELLED"}:
            _mark_intent_position_closed(state, symbol, position, now_bj)
            continue
        if position.get("tracked_from_snapshot"):
            position["tracked_from_snapshot"] = False
            position["source"] = _source(position, from_snapshot=False, from_intent=True, field=f"positions[{symbol}].source")
            position["updated_at_bj"] = now_bj
            position["last_synced_from"] = "state_only"

    for symbol in stale_symbols:
        state.positions.pop(symbol, None)

    return list(state.positions.values())


def apply_executed_intent(state: RuntimeState, order: OrderIntent) -> dict[str, Any]:
    now_bj = _now_bj()
    existing = state.positions.get(order.symbol, {})
    tracked_from_snapshot = _strict_optional_bool(existing, "tracked_from_snapshot")
    same_side = existing.get("side") == order.side
    carry_existing = existing if same_side else {}
    _strict_mapping(order.meta, "order.meta")
    order_qty = _strict_finite_number(order.qty, "order.qty")
    order_entry_price = _strict_finite_number(order.entry_price, "order.entry_price")
    order_stop_loss = _strict_finite_number(order.stop_loss, "order.stop_loss")
    order_take_profit = None if order.take_profit is None else _strict_finite_number(order.take_profit, "order.take_profit")
    existing_qty = (
        _strict_optional_number(carry_existing, "qty", f"positions[{order.symbol}].qty", default=0.0) if same_side else 0.0
    )
    aggregate_qty = existing_qty + order_qty
    if aggregate_qty > 0 and existing_qty > 0:
        existing_entry_price = _strict_optional_number(
            carry_existing,
            "entry_price",
            f"positions[{order.symbol}].entry_price",
            default=order_entry_price,
        )
        weighted_entry = (
            existing_qty * existing_entry_price + order_qty * order_entry_price
        ) / aggregate_qty
    else:
        weighted_entry = order_entry_price

    target_management_fields = _target_management_fields(carry_existing)
    order_target_management_fields = _order_target_management_fields(order)
    if order_target_management_fields:
        if not target_management_fields.get("first_target_price"):
            for key in ("first_target_price", "first_target_source"):
                if key in order_target_management_fields:
                    target_management_fields[key] = order_target_management_fields.get(key)
            for key in ("first_target_status", "first_target_hit", "first_target_filled_qty"):
                if key in order_target_management_fields and target_management_fields.get(key) is None:
                    target_management_fields[key] = order_target_management_fields.get(key)

        if not target_management_fields.get("second_target_price"):
            for key in ("second_target_price", "second_target_source"):
                if key in order_target_management_fields:
                    target_management_fields[key] = order_target_management_fields.get(key)
            for key in (
                "second_target_status",
                "second_target_hit",
                "second_target_filled_qty",
                "runner_protected",
                "runner_stop_price",
            ):
                if key in order_target_management_fields and target_management_fields.get(key) is None:
                    target_management_fields[key] = order_target_management_fields.get(key)

        for key in (
            "structure_target_price",
            "original_position_qty",
            "remaining_position_qty",
            "scale_out_plan",
            "symbol_step_size",
            "min_order_qty",
            "legacy_partial_filled_qty",
        ):
            if key in order_target_management_fields and target_management_fields.get(key) is None:
                target_management_fields[key] = order_target_management_fields.get(key)

    position = {
        "symbol": order.symbol,
        "side": order.side,
        "qty": round(aggregate_qty if aggregate_qty > 0 else order_qty, 6),
        "entry_price": round(weighted_entry, 8),
        "mark_price": carry_existing.get("mark_price", round(order_entry_price, 8)),
        "unrealized_pnl": round(
            _strict_optional_number(
                carry_existing,
                "unrealized_pnl",
                f"positions[{order.symbol}].unrealized_pnl",
                default=0.0,
            ),
            4,
        ),
        "notional": round((aggregate_qty if aggregate_qty > 0 else order_qty) * weighted_entry, 4),
        "leverage": carry_existing.get("leverage"),
        "stop_loss": round(order_stop_loss, 8),
        "take_profit": _round_price(order_take_profit),
        "status": "OPEN" if order.status in {"FILLED", "SENT"} else order.status,
        "intent_id": order.intent_id,
        "signal_id": order.signal_id,
        **_order_taxonomy_fields(order, carry_existing),
        **target_management_fields,
        "remaining_position_qty": round(aggregate_qty if aggregate_qty > 0 else order_qty, 8),
        "source": _source(
            carry_existing,
            from_snapshot=tracked_from_snapshot and same_side,
            from_intent=True,
            field=f"positions[{order.symbol}].source",
        ),
        "tracked_from_snapshot": tracked_from_snapshot and same_side,
        "tracked_from_intent": True,
        "opened_at_bj": carry_existing.get("opened_at_bj", now_bj),
        "updated_at_bj": now_bj,
        "last_synced_from": "executed_intent",
    }
    updated_position = ensure_target_management_state(position)
    state.positions[order.symbol] = updated_position
    return updated_position


def apply_management_action_fill(state: RuntimeState, intent: ManagementActionIntent) -> dict[str, Any]:
    existing = state.positions.get(intent.symbol)
    if not existing:
        return {}

    action = _management_action(intent)
    _management_exit_trigger(intent)
    _partial_take_profit_fraction_basis(intent)
    position = dict(existing)
    current_qty = _strict_non_negative_quantity(position, "qty", default=0.0)
    _strict_non_negative_quantity(position, "remaining_position_qty", default=current_qty)
    filled_qty = round(
        _strict_non_negative_quantity(
            {"qty": intent.qty} if intent.qty is not None else {},
            "qty",
            default=0.0,
        ),
        8,
    )
    remaining_qty = max(round(current_qty - filled_qty, 8), 0.0)
    position["qty"] = remaining_qty
    position["remaining_position_qty"] = remaining_qty

    stage = _partial_take_profit_stage(intent)
    if action == "PARTIAL_TAKE_PROFIT" and stage in {"first", "second"}:
        key = f"{stage}_target_filled_qty"
        stage_filled_qty = _strict_non_negative_quantity(position, key, default=0.0)
        position[key] = round(stage_filled_qty + filled_qty, 8)
        if stage_completed(position, stage=stage):
            position[f"{stage}_target_status"] = "filled"
            position[f"{stage}_target_hit"] = True
            if stage == "second" and remaining_qty > 0:
                position["runner_protected"] = _strict_optional_bool(intent.meta or {}, "runner_protected")
                position["runner_stop_price"] = _strict_present_optional_number(
                    intent.meta or {},
                    "runner_stop_price",
                )
            elif stage == "second":
                position["runner_protected"] = False
                position["runner_stop_price"] = None
        else:
            position[f"{stage}_target_status"] = "pending"
            position[f"{stage}_target_hit"] = False
            if stage == "second":
                position["runner_protected"] = False
                position["runner_stop_price"] = None
    elif action == "EXIT":
        position["qty"] = 0.0
        position["remaining_position_qty"] = 0.0
        position["runner_protected"] = False
        position["runner_stop_price"] = None

    updated = terminalize_all_unreachable_stages(position)
    state.positions[intent.symbol] = updated
    return updated
