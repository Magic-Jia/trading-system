from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

_ASSET_CODE_RE = re.compile(r"^[A-Z0-9]+$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")
_LOWER_TOKEN_RE = re.compile(r"^[a-z0-9_]+$")
_UPPER_TOKEN_RE = re.compile(r"^[A-Z0-9_]+$")
_SAFE_EVIDENCE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_FEE_ASSET_FIELDS = (
    "fee_asset",
    "feeAsset",
    "fee_currency",
    "feeCurrency",
    "commission_asset",
    "commissionAsset",
    "commission_currency",
    "commissionCurrency",
)
_COMMISSION_FIELDS = ("commission",)
_TCA_RATE_FIELDS = (
    "expected_fill_probability",
    "expected_maker_rate",
    "expected_taker_rate",
    "expected_partial_fill_rate",
)
_TCA_BPS_FIELDS = (
    "expected_slippage_bps",
    "expected_adverse_selection_bps",
    "expected_fee_funding_bps",
)
_TCA_LATENCY_FIELDS = (
    "expected_ack_latency_ms",
    "expected_fill_latency_ms",
    "expected_cancel_latency_ms",
)
_TCA_REQUIRED_OBSERVED_METRICS = (
    "slippage_bps",
    "fill_probability",
    "maker_rate",
    "taker_rate",
    "ack_latency_ms",
    "fill_latency_ms",
    "cancel_latency_ms",
    "partial_fill_rate",
    "adverse_selection_bps",
    "fees_funding_bps",
    "reject_reasons",
)
_TCA_DEFAULT_TOLERANCES = {
    "slippage_bps": 1.0,
    "fill_probability": 0.05,
    "maker_rate": 0.05,
    "taker_rate": 0.05,
    "ack_latency_ms": 250.0,
    "fill_latency_ms": 500.0,
    "cancel_latency_ms": 500.0,
    "partial_fill_rate": 0.05,
    "adverse_selection_bps": 1.0,
    "fees_funding_bps": 1.0,
    "reject_reasons": 0.05,
}
_KNOWN_CALIBRATION_STATUSES = {
    "acknowledged",
    "cancelled",
    "canceled",
    "expired",
    "filled",
    "partial",
    "partially_filled",
    "conflict",
    "rejected",
    "submitted",
}
_LATENCY_EVENT_TYPES = ("ack", "fill", "cancel", "replace")
_LATENCY_EVENT_STATUSES = {
    "ack": {"acknowledged"},
    "fill": {"filled", "partial", "partially_filled"},
    "cancel": {"cancelled", "canceled", "expired", "rejected"},
    "replace": {"acknowledged"},
}
_CALIBRATION_FEEDBACK_COMPONENTS = {
    "tca_report",
    "latency_stress_summary",
    "partial_maker_fill_evidence",
    "race_condition_evidence",
    "cross_source_parity",
    "l2_replay_quality",
    "derivatives_risk",
    "drift_contract",
}


@dataclass(frozen=True, slots=True)
class PassiveOrderCalibrationRecord:
    symbol: str
    side: str
    intended_limit_price: float
    signal_at: datetime
    decision_at: datetime
    submitted_at: datetime
    exchange_ack_at: datetime
    cancel_requested_at: datetime | None = None
    replace_requested_at: datetime | None = None
    replace_ack_at: datetime | None = None
    first_fill_at: datetime | None = None
    last_fill_at: datetime | None = None
    cancel_ack_at: datetime | None = None
    requested_qty: float | None = None
    requested_notional: float | None = None
    filled_qty: float | None = None
    filled_notional: float | None = None
    status: str = ""
    maker_taker: str | None = None
    fees: float | None = None
    funding: float | None = None
    slippage_bps: float | None = None
    adverse_selection_bps: float | None = None
    ref_price: float | None = None
    cancel_reason: str | None = None
    expire_reason: str | None = None
    latency_ms: float | None = None
    cancel_latency_ms: float | None = None
    replace_latency_ms: float | None = None
    terminal_status: str | None = None
    partial_fill_before_cancel: bool = False
    exchange_race_partial_before_cancel_ack: bool = False
    setup_type: str | None = None
    client_order_id: str | None = None
    race_condition_status: str | None = None
    reason_codes: tuple[str, ...] = ()
    late_fill_quantity: float | None = None
    late_fill_notional: float | None = None


_LIFECYCLE_TIMESTAMP_FIELDS = (
    "signal_at",
    "decision_at",
    "submitted_at",
    "exchange_ack_at",
    "cancel_requested_at",
    "replace_requested_at",
    "replace_ack_at",
    "first_fill_at",
    "last_fill_at",
    "cancel_ack_at",
)
_CANCEL_TERMINAL_STATUSES = {"cancelled", "canceled", "expired", "rejected"}
_FILLED_STATUSES = {"filled", "partially_filled", "partial"}
_RACE_EVENT_STAGES = {
    "signal",
    "order_intent",
    "risk_check",
    "submit",
    "exchange_ack",
    "fill",
    "cancel_request",
    "cancel_ack",
    "cancel",
    "replace_request",
    "replace_ack",
    "position_reconcile",
}
_RACE_TERMINAL_CANCEL_STAGES = {"cancel_ack", "cancel"}
_RACE_FILL_STATUSES = {"filled", "partially_filled", "partial"}
_RACE_CANCEL_STATUSES = {"cancelled", "canceled", "expired", "rejected"}
_RACE_REASON_CODES = {
    "fill_after_cancel_request_before_ack",
    "fill_after_cancel_ack",
    "replace_ack_after_fill_terminal",
    "duplicate_exchange_timestamp",
    "ambiguous_ordering_same_timestamp",
    "missing_exchange_ack",
    "terminal_status_conflict",
}


def _parse_lifecycle_datetime(value: Any, *, field_name: str, required: bool = False) -> datetime | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"calibration record missing {field_name}")
        return None
    if type(value) is not str or not _is_canonical_utc_timestamp(value):
        raise ValueError(f"calibration record {field_name} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _float_or_none(field: str, value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"calibration record {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"calibration record {field} must be finite")
    return parsed


def _non_negative_float_or_none(field: str, value: Any) -> float | None:
    parsed = _float_or_none(field, value)
    if parsed is not None and parsed < 0.0:
        raise ValueError(f"calibration record {field} must be non-negative")
    return parsed


def _required_float(row: Mapping[str, Any], *keys: str, field_name: str) -> float:
    for key in keys:
        if key not in row or row[key] is None or row[key] == "":
            continue
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{field_name} must be numeric")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{field_name} must be finite")
        if parsed <= 0.0:
            raise ValueError(f"{field_name} must be positive")
        return parsed
    raise ValueError(f"calibration record missing {field_name}")


def _validate_fee_asset_fields(row: Mapping[str, Any]) -> None:
    values: set[str] = set()
    for field in _FEE_ASSET_FIELDS:
        if field not in row:
            continue
        value = row[field]
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or _ASSET_CODE_RE.fullmatch(value) is None
        ):
            raise ValueError(f"calibration record {field} must be an uppercase asset code")
        values.add(value)
    if len(values) > 1:
        raise ValueError("calibration record fee asset aliases conflict")


def _validate_commission_fields(row: Mapping[str, Any]) -> None:
    for field in _COMMISSION_FIELDS:
        if field not in row or row[field] is None or row[field] == "":
            continue
        value = row[field]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"calibration record {field} must be numeric, finite, and non-negative")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0.0:
            raise ValueError(f"calibration record {field} must be numeric, finite, and non-negative")


def _required_symbol(row: Mapping[str, Any]) -> str:
    value = row.get("symbol")
    if type(value) is not str or _SYMBOL_RE.fullmatch(value) is None:
        raise ValueError("calibration record symbol must be an uppercase symbol")
    return value


def _required_side(row: Mapping[str, Any]) -> str:
    value = row.get("side")
    if type(value) is not str or value not in {"buy", "sell"}:
        raise ValueError("calibration record side must be buy or sell")
    return value


def _canonical_lower_token_or_none(field: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or _LOWER_TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"calibration record {field} must be canonical")
    return value


def _canonical_upper_token_or_none(field: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or _UPPER_TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"calibration record {field} must be canonical")
    return value


def _fee_float_or_none(field: str, value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"calibration record {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"calibration record {field} must be finite")
    if parsed < 0.0:
        raise ValueError(f"calibration record {field} must be non-negative")
    return parsed


def _maker_taker_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or value not in {"maker", "taker"}:
        raise ValueError("calibration record maker_taker must be maker or taker")
    return value


def _bool_or_false(field: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    raise ValueError(f"calibration record {field} must be boolean")


def _reason_codes(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, list):
        raise ValueError("calibration record reason_codes must be a list")
    parsed: list[str] = []
    for item in value:
        if type(item) is not str or item not in _RACE_REASON_CODES:
            raise ValueError("calibration record reason_codes must be known race condition reason codes")
        if item not in parsed:
            parsed.append(item)
    return tuple(parsed)


def _race_condition_status(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or value not in {"clear", "hold_for_review"}:
        raise ValueError("calibration record race_condition_status must be clear or hold_for_review")
    return value


def _record_from_mapping(row: Mapping[str, Any]) -> PassiveOrderCalibrationRecord:
    _validate_fee_asset_fields(row)
    _validate_commission_fields(row)
    signal_at = _parse_lifecycle_datetime(row.get("signal_at"), field_name="signal_at", required=True)
    decision_at = _parse_lifecycle_datetime(row.get("decision_at"), field_name="decision_at", required=True)
    submitted_at = _parse_lifecycle_datetime(row.get("submitted_at"), field_name="submitted_at", required=True)
    exchange_ack_at = _parse_lifecycle_datetime(
        row.get("exchange_ack_at"), field_name="exchange_ack_at", required=True
    )
    cancel_requested_at = _parse_lifecycle_datetime(row.get("cancel_requested_at"), field_name="cancel_requested_at")
    replace_requested_at = _parse_lifecycle_datetime(row.get("replace_requested_at"), field_name="replace_requested_at")
    replace_ack_at = _parse_lifecycle_datetime(row.get("replace_ack_at"), field_name="replace_ack_at")
    first_fill_at = _parse_lifecycle_datetime(row.get("first_fill_at"), field_name="first_fill_at")
    last_fill_at = _parse_lifecycle_datetime(row.get("last_fill_at"), field_name="last_fill_at")
    cancel_ack_at = _parse_lifecycle_datetime(row.get("cancel_ack_at"), field_name="cancel_ack_at")

    assert signal_at is not None
    assert decision_at is not None
    assert submitted_at is not None
    assert exchange_ack_at is not None
    if decision_at < signal_at:
        raise ValueError("calibration record decision_at must be at or after signal_at")
    if submitted_at <= decision_at:
        raise ValueError("calibration record submitted_at must be after decision_at")
    if exchange_ack_at < submitted_at:
        raise ValueError("calibration record exchange_ack_at must be at or after submitted_at")
    if cancel_requested_at is not None and cancel_requested_at < exchange_ack_at:
        raise ValueError("calibration record cancel_requested_at must be at or after exchange_ack_at")
    if replace_requested_at is not None and replace_requested_at < exchange_ack_at:
        raise ValueError("calibration record replace_requested_at must be at or after exchange_ack_at")
    if replace_ack_at is not None and replace_requested_at is None:
        raise ValueError("calibration record replace_ack_at requires replace_requested_at")
    if replace_ack_at is not None and replace_requested_at is not None and replace_ack_at < replace_requested_at:
        raise ValueError("calibration record replace_ack_at must be at or after replace_requested_at")
    if first_fill_at is not None and first_fill_at < exchange_ack_at:
        raise ValueError("calibration record first_fill_at must be at or after exchange_ack_at")
    if last_fill_at is not None and last_fill_at < exchange_ack_at:
        raise ValueError("calibration record last_fill_at must be at or after exchange_ack_at")
    if last_fill_at is not None and first_fill_at is not None and last_fill_at < first_fill_at:
        raise ValueError("calibration record last_fill_at must be at or after first_fill_at")
    status = _canonical_lower_token_or_none("status", row.get("status")) or ""
    terminal_status = _canonical_lower_token_or_none("terminal_status", row.get("terminal_status")) or status
    if status and status not in _KNOWN_CALIBRATION_STATUSES:
        raise ValueError("calibration record status must be a known lifecycle status")
    if terminal_status and terminal_status not in _KNOWN_CALIBRATION_STATUSES:
        raise ValueError("calibration record terminal_status must be a known lifecycle status")
    partial_fill_before_cancel = _bool_or_false("partial_fill_before_cancel", row.get("partial_fill_before_cancel"))
    exchange_race_partial = _bool_or_false(
        "exchange_race_partial_before_cancel_ack", row.get("exchange_race_partial_before_cancel_ack")
    )
    if status in _FILLED_STATUSES and first_fill_at is None:
        raise ValueError("calibration record filled status requires first_fill_at")
    if cancel_ack_at is not None:
        if cancel_requested_at is not None and cancel_ack_at < cancel_requested_at:
            raise ValueError("calibration record cancel_ack_at must be at or after cancel_requested_at")
        last_lifecycle_fill_at = last_fill_at or first_fill_at
        if last_lifecycle_fill_at is not None and last_lifecycle_fill_at > cancel_ack_at and not exchange_race_partial:
            raise ValueError(
                "calibration record fill after terminal cancel requires exchange_race_partial_before_cancel_ack"
            )
        if last_lifecycle_fill_at is not None and cancel_ack_at <= last_lifecycle_fill_at and not exchange_race_partial:
            raise ValueError("calibration record cancel_ack_at must be after last fill timestamp")
        if status not in _CANCEL_TERMINAL_STATUSES and not exchange_race_partial:
            raise ValueError("calibration record cancel_ack_at requires a cancelled, expired, or rejected status")
        if partial_fill_before_cancel and first_fill_at is None:
            raise ValueError("calibration record partial_fill_before_cancel requires first_fill_at")
    return PassiveOrderCalibrationRecord(
        symbol=_required_symbol(row),
        side=_required_side(row),
        intended_limit_price=_required_float(
            row,
            "intended_limit_price",
            "limit_price",
            field_name="intended_limit_price",
        ),
        signal_at=signal_at,
        decision_at=decision_at,
        submitted_at=submitted_at,
        exchange_ack_at=exchange_ack_at,
        cancel_requested_at=cancel_requested_at,
        replace_requested_at=replace_requested_at,
        replace_ack_at=replace_ack_at,
        first_fill_at=first_fill_at,
        last_fill_at=last_fill_at,
        cancel_ack_at=cancel_ack_at,
        requested_qty=_float_or_none("requested_qty", row.get("requested_qty")),
        requested_notional=_float_or_none("requested_notional", row.get("requested_notional")),
        filled_qty=_float_or_none("filled_qty", row.get("filled_qty")),
        filled_notional=_float_or_none("filled_notional", row.get("filled_notional")),
        status=status,
        maker_taker=_maker_taker_or_none(row.get("maker_taker")),
        fees=_fee_float_or_none("fees", row.get("fees")),
        funding=_float_or_none("funding", row.get("funding")),
        slippage_bps=_float_or_none("slippage_bps", row.get("slippage_bps")),
        adverse_selection_bps=_float_or_none("adverse_selection_bps", row.get("adverse_selection_bps")),
        ref_price=_float_or_none("ref_price", row.get("ref_price")),
        cancel_reason=_canonical_lower_token_or_none("cancel_reason", row.get("cancel_reason")),
        expire_reason=_canonical_lower_token_or_none("expire_reason", row.get("expire_reason")),
        latency_ms=_non_negative_float_or_none("latency_ms", row.get("latency_ms")),
        cancel_latency_ms=_non_negative_float_or_none("cancel_latency_ms", row.get("cancel_latency_ms")),
        replace_latency_ms=_non_negative_float_or_none("replace_latency_ms", row.get("replace_latency_ms")),
        terminal_status=terminal_status,
        partial_fill_before_cancel=partial_fill_before_cancel,
        exchange_race_partial_before_cancel_ack=exchange_race_partial,
        setup_type=_canonical_upper_token_or_none("setup_type", row.get("setup_type")),
        client_order_id=_latency_client_order_id(row.get("client_order_id")) if row.get("client_order_id") not in (None, "") else None,
        race_condition_status=_race_condition_status(row.get("race_condition_status")),
        reason_codes=_reason_codes(row.get("reason_codes")),
        late_fill_quantity=_non_negative_float_or_none("late_fill_quantity", row.get("late_fill_quantity")),
        late_fill_notional=_non_negative_float_or_none("late_fill_notional", row.get("late_fill_notional")),
    )


def load_calibration_records(path: str | Path) -> tuple[PassiveOrderCalibrationRecord, ...]:
    source = Path(path)
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        return ()
    if text.startswith("["):
        raw_rows = json.loads(text)
    else:
        raw_rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(raw_rows, list):
        raise ValueError("calibration input must be a JSON array or JSONL records")
    records: list[PassiveOrderCalibrationRecord] = []
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise ValueError("calibration records must be objects")
        records.append(_record_from_mapping(row))
    return tuple(records)


def _is_filled(record: PassiveOrderCalibrationRecord) -> bool:
    if (record.filled_qty or 0.0) > 0.0 or (record.filled_notional or 0.0) > 0.0:
        return True
    return record.status in {"filled", "partially_filled", "partial"}


def _is_partial(record: PassiveOrderCalibrationRecord) -> bool:
    if not _is_filled(record):
        return False
    if record.status in {"partially_filled", "partial"}:
        return True
    if record.requested_qty and record.filled_qty is not None:
        return 0.0 < record.filled_qty < record.requested_qty
    if record.requested_notional and record.filled_notional is not None:
        return 0.0 < record.filled_notional < record.requested_notional
    return False


def _wait_seconds(record: PassiveOrderCalibrationRecord) -> float | None:
    fill_time = record.first_fill_at or record.last_fill_at
    if fill_time is None:
        return None
    return max(0.0, (fill_time - record.submitted_at).total_seconds())


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    value = ordered[lower] * (1.0 - weight) + ordered[upper] * weight
    return round(value) if percentile >= 0.9 else value


def _latency_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _metric_summary(values: Iterable[float]) -> dict[str, Any]:
    rows = list(values)
    return {
        "sample_count": len(rows),
        "median": median(rows) if rows else None,
        "p95": _percentile(rows, 0.95),
    }


def _realized_bps(record: PassiveOrderCalibrationRecord) -> float | None:
    if record.slippage_bps is not None:
        return record.slippage_bps
    if record.ref_price is None or record.ref_price <= 0.0:
        return None
    fill_price = None
    if record.filled_qty and record.filled_notional:
        fill_price = record.filled_notional / record.filled_qty
    elif record.intended_limit_price > 0.0:
        fill_price = record.intended_limit_price
    if fill_price is None:
        return None
    return ((fill_price / record.ref_price) - 1.0) * 10_000.0


def _summary(records: Iterable[PassiveOrderCalibrationRecord]) -> dict[str, Any]:
    rows = list(records)
    attempt_count = len(rows)
    filled = [record for record in rows if _is_filled(record)]
    partial = [record for record in rows if _is_partial(record)]
    waits = [value for record in rows if (value := _wait_seconds(record)) is not None]
    realized = [value for record in rows if (value := _realized_bps(record)) is not None]
    total_fees = sum(record.fees or 0.0 for record in rows)
    total_filled_notional = sum(record.filled_notional or 0.0 for record in rows)
    payload = {
        "attempt_count": attempt_count,
        "fill_rate": len(filled) / attempt_count if attempt_count else 0.0,
        "partial_fill_rate": len(partial) / attempt_count if attempt_count else 0.0,
        "missed_fill_rate": (attempt_count - len(filled)) / attempt_count if attempt_count else 0.0,
        "median_wait_seconds": median(waits) if waits else None,
        "p95_wait_seconds": _percentile(waits, 0.95),
        "median_realized_bps_vs_reference": median(realized) if realized else None,
        "fee_bps": (total_fees / total_filled_notional) * 10_000.0 if total_filled_notional > 0.0 else None,
    }
    return payload


def _latency_value(field: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"latency record {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"latency record {field} must be finite")
    if parsed < 0.0:
        raise ValueError(f"latency record {field} must be non-negative")
    return parsed


def _latency_observed_at(field: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is not str or not _is_canonical_utc_timestamp(value):
        raise ValueError(f"latency record {field} must be a canonical UTC timestamp")
    return value


def _latency_event_type(value: Any) -> str:
    if type(value) is not str or value not in _LATENCY_EVENT_TYPES:
        raise ValueError("latency record event_type must be one of ack, fill, cancel, replace")
    return value


def _latency_status(event_type: str, value: Any) -> str:
    if type(value) is not str or value not in _LATENCY_EVENT_STATUSES[event_type]:
        raise ValueError(f"latency record status must be one of {', '.join(sorted(_LATENCY_EVENT_STATUSES[event_type]))}")
    return value


def _latency_client_order_id(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip() or _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("latency record client_order_id must be a safe identifier")
    return value


def _latency_observation(
    *,
    event_type: str,
    status: str,
    latency_ms: float | None,
    observed_at: str | None,
    client_order_id: str,
    enforce_duplicate_identity: bool,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "status": status,
        "latency_ms": latency_ms,
        "observed_at": observed_at,
        "client_order_id": client_order_id,
        "enforce_duplicate_identity": enforce_duplicate_identity,
    }


def _latency_observations_from_record(record: PassiveOrderCalibrationRecord) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    observed_at = record.exchange_ack_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    ack_latency = record.latency_ms
    if ack_latency is None:
        ack_latency = (record.exchange_ack_at - record.submitted_at).total_seconds() * 1000.0
    observations.append(
        _latency_observation(
            event_type="ack",
            status="acknowledged",
            latency_ms=ack_latency,
            observed_at=observed_at,
            client_order_id=f"{record.symbol}:{id(record)}:{observed_at}:ack",
            enforce_duplicate_identity=False,
        )
    )
    if record.first_fill_at is not None:
        fill_observed_at = record.first_fill_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        observations.append(
            _latency_observation(
                event_type="fill",
                status=record.status if record.status in _LATENCY_EVENT_STATUSES["fill"] else "filled",
                latency_ms=(record.first_fill_at - record.exchange_ack_at).total_seconds() * 1000.0,
                observed_at=fill_observed_at,
                client_order_id=f"{record.symbol}:{id(record)}:{fill_observed_at}:fill",
                enforce_duplicate_identity=False,
            )
        )
    if record.cancel_ack_at is not None:
        cancel_observed_at = record.cancel_ack_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        cancel_latency = record.cancel_latency_ms
        if cancel_latency is None and record.cancel_requested_at is not None:
            cancel_latency = (record.cancel_ack_at - record.cancel_requested_at).total_seconds() * 1000.0
        observations.append(
            _latency_observation(
                event_type="cancel",
                status=record.terminal_status or record.status,
                latency_ms=cancel_latency,
                observed_at=cancel_observed_at,
                client_order_id=f"{record.symbol}:{id(record)}:{cancel_observed_at}:cancel",
                enforce_duplicate_identity=False,
            )
        )
    if record.replace_ack_at is not None:
        replace_observed_at = record.replace_ack_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        replace_latency = record.replace_latency_ms
        if replace_latency is None and record.replace_requested_at is not None:
            replace_latency = (record.replace_ack_at - record.replace_requested_at).total_seconds() * 1000.0
        observations.append(
            _latency_observation(
                event_type="replace",
                status="acknowledged",
                latency_ms=replace_latency,
                observed_at=replace_observed_at,
                client_order_id=f"{record.symbol}:{id(record)}:{replace_observed_at}:replace",
                enforce_duplicate_identity=False,
            )
        )
    return observations


def _latency_observations_from_mapping(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    event_type = _latency_event_type(row.get("event_type"))
    status = _latency_status(event_type, row.get("status"))
    observed_at = _latency_observed_at("observed_at", row.get("observed_at"))
    latency_ms = None
    if "latency_ms" in row and row["latency_ms"] not in (None, ""):
        latency_ms = _latency_value("latency_ms", row["latency_ms"])
    return [
        _latency_observation(
            event_type=event_type,
            status=status,
            latency_ms=latency_ms,
            observed_at=observed_at,
            client_order_id=_latency_client_order_id(row.get("client_order_id")),
            enforce_duplicate_identity=True,
        )
    ]


def _race_append(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _race_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"race evidence {field} must be a canonical string")
    return value


def _race_timestamp(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None or value == "":
        return None
    if type(value) is not str or not _is_canonical_utc_timestamp(value):
        raise ValueError(f"race evidence {field} must be a canonical UTC timestamp")
    return value


def _race_timestamp_dt(row: Mapping[str, Any], field: str) -> datetime:
    value = _race_timestamp(row, field)
    if value is None:
        raise ValueError(f"race evidence {field} must be a canonical UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _race_positive_number(row: Mapping[str, Any], *fields: str) -> float | None:
    for field in fields:
        if field not in row or row[field] is None or row[field] == "":
            continue
        value = row[field]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"race evidence {field} must be numeric")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"race evidence {field} must be finite")
        if parsed <= 0.0:
            raise ValueError(f"race evidence {field} must be positive")
        return parsed
    return None


def _coerce_race_event(row: Mapping[str, Any]) -> dict[str, Any]:
    stage = _race_string(row, "stage")
    if stage not in _RACE_EVENT_STAGES:
        raise ValueError("race evidence stage must be known")
    client_order_id = _race_string(row, "client_order_id")
    if _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(client_order_id) is None:
        raise ValueError("race evidence client_order_id must be a safe identifier")
    status = _race_string(row, "status")
    if status != status.lower() or _LOWER_TOKEN_RE.fullmatch(status) is None:
        raise ValueError("race evidence status must be canonical")
    occurred_at = _race_timestamp(row, "occurred_at")
    assert occurred_at is not None
    exchange_timestamp = _race_timestamp(row, "exchange_timestamp")
    event_id = row.get("event_id")
    if event_id is not None:
        if type(event_id) is not str or not event_id or event_id != event_id.strip():
            raise ValueError("race evidence event_id must be a canonical string")
    return {
        "client_order_id": client_order_id,
        "stage": stage,
        "status": status,
        "occurred_at": occurred_at,
        "occurred_dt": _race_timestamp_dt(row, "occurred_at"),
        "exchange_timestamp": exchange_timestamp,
        "event_id": event_id,
        "source": row,
    }


def _race_fill_amounts(row: Mapping[str, Any]) -> tuple[float, float]:
    quantity = _race_positive_number(row, "filled_qty", "quantity", "qty", "executed_qty", "executedQty")
    price = _race_positive_number(row, "price", "avg_price", "avgPrice")
    notional = _race_positive_number(row, "filled_notional", "notional")
    if quantity is None:
        raise ValueError("race evidence quantity must be positive")
    if notional is None:
        if price is None:
            raise ValueError("race evidence fill price must be positive")
        notional = quantity * price
    return quantity, notional


def build_execution_race_condition_evidence(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [_coerce_race_event(row) for row in events]
    if not rows:
        raise ValueError("race evidence events must be non-empty")
    client_order_ids = {row["client_order_id"] for row in rows}
    if len(client_order_ids) != 1:
        raise ValueError("mixed client_order_id")
    seen_event_ids: set[str] = set()
    seen_event_identity: set[tuple[str, str, str]] = set()
    for row in rows:
        event_id = row.get("event_id")
        if event_id is not None:
            if str(event_id) in seen_event_ids:
                raise ValueError("duplicate race evidence event identity")
            seen_event_ids.add(str(event_id))
        identity = (str(row["occurred_at"]), str(row["stage"]), str(row["status"]))
        if identity in seen_event_identity:
            raise ValueError("duplicate race evidence event identity")
        seen_event_identity.add(identity)

    ordered = sorted(rows, key=lambda row: row["occurred_dt"])
    reason_codes: list[str] = []
    exchange_timestamps = [row["exchange_timestamp"] for row in rows if row.get("exchange_timestamp") is not None]
    duplicate_exchange_timestamps = {value for value in exchange_timestamps if exchange_timestamps.count(value) > 1}
    if duplicate_exchange_timestamps:
        _race_append(reason_codes, "duplicate_exchange_timestamp")

    ack_rows = [row for row in rows if row["stage"] == "exchange_ack"]
    if not ack_rows:
        _race_append(reason_codes, "missing_exchange_ack")
    cancel_request_times = [row["occurred_dt"] for row in rows if row["stage"] == "cancel_request"]
    cancel_ack_times = [row["occurred_dt"] for row in rows if row["stage"] in _RACE_TERMINAL_CANCEL_STAGES]
    fill_rows = [row for row in rows if row["stage"] == "fill"]
    fill_times = [row["occurred_dt"] for row in fill_rows]
    fill_amounts_by_id: dict[int, tuple[float, float]] = {
        id(fill): _race_fill_amounts(fill["source"]) for fill in fill_rows
    }

    late_fill_quantity = 0.0
    late_fill_notional = 0.0
    for fill in fill_rows:
        fill_time = fill["occurred_dt"]
        is_late = False
        if any(cancel_request < fill_time for cancel_request in cancel_request_times) and not any(
            cancel_ack <= fill_time for cancel_ack in cancel_ack_times
        ):
            _race_append(reason_codes, "fill_after_cancel_request_before_ack")
            is_late = True
        if any(cancel_ack < fill_time for cancel_ack in cancel_ack_times):
            _race_append(reason_codes, "fill_after_cancel_ack")
            is_late = True
        if any(cancel_ack == fill_time for cancel_ack in cancel_ack_times):
            _race_append(reason_codes, "ambiguous_ordering_same_timestamp")
            is_late = True
        if is_late:
            fill_quantity, fill_notional = fill_amounts_by_id[id(fill)]
            late_fill_quantity += fill_quantity
            late_fill_notional += fill_notional

    terminal_fill_time = max(fill_times) if any(row["status"] == "filled" for row in fill_rows) else None
    if terminal_fill_time is not None and any(
        row["stage"] == "replace_ack" and row["occurred_dt"] > terminal_fill_time for row in rows
    ):
        _race_append(reason_codes, "replace_ack_after_fill_terminal")

    terminal_statuses: set[str] = set()
    if any(row["stage"] == "fill" and row["status"] == "filled" for row in rows):
        terminal_statuses.add("filled")
    terminal_statuses.update(row["status"] for row in rows if row["stage"] in _RACE_TERMINAL_CANCEL_STAGES)
    has_strict_terminal_conflict = any(fill_time != cancel_time for fill_time in fill_times for cancel_time in cancel_ack_times)
    if len(terminal_statuses) > 1 and has_strict_terminal_conflict:
        _race_append(reason_codes, "terminal_status_conflict")
    if len(terminal_statuses) == 1:
        terminal_status = next(iter(terminal_statuses))
    elif terminal_statuses:
        terminal_status = "conflict"
    else:
        terminal_status = ordered[-1]["status"]

    return {
        "schema_version": "execution_race_condition_evidence.v1",
        "client_order_id": str(next(iter(client_order_ids))),
        "terminal_status": terminal_status,
        "race_condition_status": "hold_for_review" if reason_codes else "clear",
        "reason_codes": reason_codes,
        "first_timestamp": ordered[0]["occurred_at"],
        "last_timestamp": ordered[-1]["occurred_at"],
        "late_fill_quantity": late_fill_quantity if late_fill_quantity > 0.0 else None,
        "late_fill_notional": late_fill_notional if late_fill_notional > 0.0 else None,
    }


def _coerce_latency_observations(value: PassiveOrderCalibrationRecord | Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(value, PassiveOrderCalibrationRecord):
        return _latency_observations_from_record(value)
    if isinstance(value, Mapping):
        if "event_type" in value or "observed_at" in value or "client_order_id" in value:
            return _latency_observations_from_mapping(value)
        return _latency_observations_from_record(_record_from_mapping(value))
    raise ValueError("latency calibration records must be calibration record objects or mappings")


def _latency_distribution(observations: list[Mapping[str, Any]], *, evaluated_at: str | None, stale_after_seconds: int | None) -> dict[str, Any]:
    present = [float(item["latency_ms"]) for item in observations if item.get("latency_ms") is not None]
    missing_count = len(observations) - len(present)
    stale_count = 0
    if evaluated_at is not None and stale_after_seconds is not None:
        eval_time = datetime.fromisoformat(evaluated_at[:-1] + "+00:00")
        for item in observations:
            observed_at = item.get("observed_at")
            if type(observed_at) is not str:
                continue
            observed_time = datetime.fromisoformat(observed_at[:-1] + "+00:00")
            age_seconds = (eval_time - observed_time).total_seconds()
            if age_seconds < 0.0 or age_seconds > stale_after_seconds:
                stale_count += 1
    count = len(present)
    return {
        "count": count,
        "min": min(present) if present else None,
        "max": max(present) if present else None,
        "p50": _latency_percentile(present, 0.50),
        "p90": _latency_percentile(present, 0.90),
        "p95": _latency_percentile(present, 0.95),
        "p99": _latency_percentile(present, 0.99),
        "mean": (sum(present) / count) if count else None,
        "missing_rate": missing_count / len(observations) if observations else 0.0,
        "stale_rate": stale_count / len(observations) if observations else 0.0,
    }


def _parse_latency_freshness_options(
    evaluated_at: datetime | str | None,
    stale_after_seconds: int | None,
) -> tuple[str | None, int | None]:
    evaluated_at_text = None
    if evaluated_at is not None:
        evaluated_at_text = _tca_canonical_timestamp(evaluated_at, field_name="evaluated_at")
    if stale_after_seconds is not None:
        if isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, int):
            raise ValueError("stale_after_seconds must be an integer")
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
    return evaluated_at_text, stale_after_seconds


def compute_latency_distribution_metrics(
    records: Iterable[PassiveOrderCalibrationRecord | Mapping[str, Any]],
    *,
    evaluated_at: datetime | str | None = None,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    evaluated_at_text, parsed_stale_after_seconds = _parse_latency_freshness_options(evaluated_at, stale_after_seconds)
    observations: list[dict[str, Any]] = []
    seen_identities: set[tuple[str | None, str, str]] = set()
    for record in records:
        for observation in _coerce_latency_observations(record):
            identity = (
                observation.get("observed_at"),
                str(observation["client_order_id"]),
                str(observation["event_type"]),
            )
            if observation.get("enforce_duplicate_identity") is True:
                if identity in seen_identities:
                    raise ValueError("duplicate latency event identity")
                seen_identities.add(identity)
            latency_ms = observation.get("latency_ms")
            if latency_ms is not None:
                _latency_value("latency_ms", latency_ms)
            observations.append(observation)
    by_event_type = {
        event_type: _latency_distribution(
            [item for item in observations if item["event_type"] == event_type],
            evaluated_at=evaluated_at_text,
            stale_after_seconds=parsed_stale_after_seconds,
        )
        for event_type in _LATENCY_EVENT_TYPES
        if any(item["event_type"] == event_type for item in observations)
    }
    return {
        "schema_version": "latency_distribution_metrics.v1",
        "overall": _latency_distribution(
            observations,
            evaluated_at=evaluated_at_text,
            stale_after_seconds=parsed_stale_after_seconds,
        ),
        "by_event_type": by_event_type,
    }


def build_latency_stress_summary(
    records: Iterable[PassiveOrderCalibrationRecord | Mapping[str, Any]],
    *,
    evaluated_at: datetime | str | None = None,
    stale_after_seconds: int | None = None,
    min_samples: int = 30,
) -> dict[str, Any]:
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        raise ValueError("min_samples must be an integer")
    if min_samples < 0:
        raise ValueError("min_samples must be non-negative")
    metrics = compute_latency_distribution_metrics(
        records,
        evaluated_at=evaluated_at,
        stale_after_seconds=stale_after_seconds,
    )
    overall = metrics["overall"]
    sample_count = int(overall["count"])
    p99 = overall["p99"]
    maximum = overall["max"]
    recommended = None
    if isinstance(p99, Real) and isinstance(maximum, Real):
        recommended = max(float(p99), float(maximum))
    elif isinstance(p99, Real):
        recommended = float(p99)
    elif isinstance(maximum, Real):
        recommended = float(maximum)
    fail_closed_reason_codes: list[str] = []
    sample_size_quality = "sufficient" if sample_count >= min_samples else "insufficient"
    if sample_size_quality == "insufficient":
        fail_closed_reason_codes.append("insufficient_latency_samples")
    latency_quality = "usable"
    if sample_count == 0 or overall["missing_rate"] > 0.0:
        latency_quality = "missing"
        fail_closed_reason_codes.append("missing_latency_observations")
    elif overall["stale_rate"] > 0.0:
        latency_quality = "stale"
        fail_closed_reason_codes.append("stale_latency_evidence")
    return {
        "schema_version": "latency_stress_calibration_summary.v1",
        "recommended_latency_buffer_ms": recommended,
        "latency_quality": latency_quality,
        "sample_size_quality": sample_size_quality,
        "fail_closed_reason_codes": fail_closed_reason_codes,
        "metrics": metrics,
    }


def _slippage_summary(records: Iterable[PassiveOrderCalibrationRecord]) -> dict[str, Any]:
    values = [value for record in records if (value := _realized_bps(record)) is not None]
    return {
        "sample_count": len(values),
        "median_slippage_bps": median(values) if values else None,
        "p95_slippage_bps": _percentile(values, 0.95),
    }


def _group(records: tuple[PassiveOrderCalibrationRecord, ...], field_name: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[PassiveOrderCalibrationRecord]] = {}
    for record in records:
        value = getattr(record, field_name)
        if value is None or value == "":
            continue
        buckets.setdefault(str(value), []).append(record)
    return {key: _summary(value) for key, value in sorted(buckets.items())}


def _is_canonical_utc_timestamp(value: str) -> bool:
    if _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") == value


def _validate_calibration_summary_evidence_source(evidence_source: Mapping[str, Any] | None) -> dict[str, Any]:
    if evidence_source is not None and not isinstance(evidence_source, Mapping):
        raise ValueError("calibration summary evidence_source must be an object")
    raw_source = evidence_source or {"type": "unknown_offline_records"}
    for key in raw_source:
        if type(key) is not str:
            raise ValueError("calibration summary evidence_source.<key> must be a string")
        if not key.strip():
            raise ValueError("calibration summary evidence_source.<key> must be non-empty")
        if key != key.strip():
            raise ValueError("calibration summary evidence_source.<key> must be canonical")

    source = dict(raw_source)
    source.setdefault("type", "unknown_offline_records")
    unknown_source_fields = sorted(set(source) - {"type", "run_id", "exported_at"})
    if unknown_source_fields:
        raise ValueError("unknown calibration summary evidence_source field: " + ", ".join(unknown_source_fields))

    source_type = source["type"]
    if type(source_type) is not str:
        raise ValueError("calibration summary evidence_source.type must be a string")
    if not source_type.strip():
        raise ValueError("calibration summary evidence_source.type must be non-empty")
    if source_type != source_type.strip():
        raise ValueError("calibration summary evidence_source.type must be canonical")
    if _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(source_type) is None:
        raise ValueError("calibration summary evidence_source.type must be a safe identifier")

    for optional_field in ("run_id", "exported_at"):
        optional_value = source.get(optional_field)
        if optional_value is None:
            continue
        if type(optional_value) is not str:
            raise ValueError(f"calibration summary evidence_source.{optional_field} must be a string")
        if not optional_value.strip():
            raise ValueError(f"calibration summary evidence_source.{optional_field} must be non-empty")
        if optional_value != optional_value.strip():
            raise ValueError(f"calibration summary evidence_source.{optional_field} must be canonical")
        if optional_field == "run_id" and _SAFE_EVIDENCE_IDENTIFIER_RE.fullmatch(optional_value) is None:
            raise ValueError("calibration summary evidence_source.run_id must be a safe identifier")
        if optional_field == "exported_at" and not _is_canonical_utc_timestamp(optional_value):
            raise ValueError("calibration summary evidence_source.exported_at must be a canonical UTC timestamp")

    return source


def summarize_calibration_records(
    records: Iterable[PassiveOrderCalibrationRecord],
    *,
    evidence_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = tuple(records)
    maker_rows = tuple(record for record in rows if record.maker_taker == "maker")
    taker_rows = tuple(record for record in rows if record.maker_taker == "taker")
    source = _validate_calibration_summary_evidence_source(evidence_source)
    return {
        "schema_version": "passive_order_calibration_summary.v1",
        "evidence_source": source,
        "overall": _summary(rows),
        "by_symbol": _group(rows, "symbol"),
        "by_side": _group(rows, "side"),
        "by_setup_type": _group(rows, "setup_type"),
        "by_maker_taker": _group(rows, "maker_taker"),
        "passive_maker": _summary(maker_rows),
        "taker_slippage": _slippage_summary(taker_rows),
        "records": [_record_payload(record) for record in rows],
        "caveats": [
            "Offline calibration only; no exchange calls or order placement are performed.",
            "Synthetic records are accepted, so promotion decisions must check provenance before using these metrics.",
        ],
    }


def _record_payload(record: PassiveOrderCalibrationRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["reason_codes"] = list(payload["reason_codes"])
    lifecycle_timestamps: dict[str, str | None] = {}
    for key in _LIFECYCLE_TIMESTAMP_FIELDS:
        value = payload.get(key)
        if isinstance(value, datetime):
            canonical_value = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            payload[key] = canonical_value
            lifecycle_timestamps[key] = canonical_value
        else:
            lifecycle_timestamps[key] = None
    payload["lifecycle_timestamps"] = lifecycle_timestamps
    return payload


def write_calibration_summary(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    evidence_source: Mapping[str, Any] | None = None,
) -> Path:
    records = load_calibration_records(input_path)
    summary = summarize_calibration_records(records, evidence_source=evidence_source)
    output_path = Path(output_dir) / "passive_order_calibration_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def _strict_tca_float(field: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"tca assumption {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"tca assumption {field} must be finite")
    return parsed


def _validate_tca_assumptions(assumptions: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(assumptions, Mapping):
        raise ValueError("tca assumptions must be an object")
    allowed_fields = set(_TCA_RATE_FIELDS) | set(_TCA_BPS_FIELDS) | set(_TCA_LATENCY_FIELDS) | {
        "expected_reject_reason_rates"
    }
    unknown_fields = sorted(set(assumptions) - allowed_fields)
    if unknown_fields:
        raise ValueError("unknown tca assumption field: " + ", ".join(unknown_fields))
    parsed: dict[str, Any] = {}
    for field in _TCA_RATE_FIELDS:
        value = _strict_tca_float(field, assumptions.get(field))
        if value < 0.0 or value > 1.0:
            raise ValueError(f"tca assumption {field} must be between 0 and 1")
        parsed[field] = value
    for field in (*_TCA_BPS_FIELDS, *_TCA_LATENCY_FIELDS):
        value = _strict_tca_float(field, assumptions.get(field))
        if value < 0.0:
            raise ValueError(f"tca assumption {field} must be non-negative")
        parsed[field] = value
    reject_rates = assumptions.get("expected_reject_reason_rates")
    if not isinstance(reject_rates, Mapping):
        raise ValueError("tca assumption expected_reject_reason_rates must be an object")
    parsed_reject_rates: dict[str, float] = {}
    for key, value in reject_rates.items():
        if type(key) is not str or _LOWER_TOKEN_RE.fullmatch(key) is None:
            raise ValueError("tca assumption reject reason keys must be canonical")
        rate = _strict_tca_float(f"expected_reject_reason_rates.{key}", value)
        if rate < 0.0 or rate > 1.0:
            raise ValueError(f"tca assumption expected_reject_reason_rates.{key} must be between 0 and 1")
        parsed_reject_rates[key] = rate
    parsed["expected_reject_reason_rates"] = parsed_reject_rates
    return parsed


def _validate_tca_thresholds(thresholds: Mapping[str, Any] | None) -> dict[str, float]:
    if thresholds is None:
        return dict(_TCA_DEFAULT_TOLERANCES)
    if not isinstance(thresholds, Mapping):
        raise ValueError("tca tolerance thresholds must be an object")
    unknown_fields = sorted(set(thresholds) - set(_TCA_DEFAULT_TOLERANCES))
    if unknown_fields:
        raise ValueError("unknown tca tolerance field: " + ", ".join(unknown_fields))
    parsed = dict(_TCA_DEFAULT_TOLERANCES)
    for field, value in thresholds.items():
        tolerance = _strict_tca_float(field, value)
        if tolerance < 0.0:
            raise ValueError(f"tca tolerance {field} must be non-negative")
        parsed[field] = tolerance
    return parsed


def _tca_canonical_timestamp(value: datetime | str, *, field_name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if type(value) is str and _is_canonical_utc_timestamp(value):
        return value
    raise ValueError(f"{field_name} must be a canonical UTC timestamp")


def _coerce_calibration_record(value: PassiveOrderCalibrationRecord | Mapping[str, Any]) -> PassiveOrderCalibrationRecord:
    if isinstance(value, PassiveOrderCalibrationRecord):
        return value
    if isinstance(value, Mapping):
        return _record_from_mapping(value)
    raise ValueError("tca calibration records must be calibration record objects or mappings")


def _tca_observed(records: tuple[PassiveOrderCalibrationRecord, ...]) -> dict[str, Any]:
    sample_count = len(records)
    filled = [record for record in records if _is_filled(record)]
    partial = [record for record in records if _is_partial(record)]
    maker = [record for record in records if record.maker_taker == "maker"]
    taker = [record for record in records if record.maker_taker == "taker"]
    ack_latencies = [(record.exchange_ack_at - record.submitted_at).total_seconds() * 1000.0 for record in records]
    fill_latencies = [
        (record.first_fill_at - record.exchange_ack_at).total_seconds() * 1000.0
        for record in filled
        if record.first_fill_at is not None
    ]
    cancel_latencies = [
        (record.cancel_ack_at - record.submitted_at).total_seconds() * 1000.0
        for record in records
        if record.cancel_ack_at is not None
    ]
    slippage = [value for record in filled if (value := _realized_bps(record)) is not None]
    adverse = [record.adverse_selection_bps for record in filled if record.adverse_selection_bps is not None]
    fee_funding = [
        ((record.fees or 0.0) + (record.funding or 0.0)) / record.filled_notional * 10_000.0
        for record in filled
        if record.filled_notional is not None and record.filled_notional > 0.0
    ]
    reject_reasons: dict[str, dict[str, Any]] = {}
    terminal_statuses: dict[str, dict[str, Any]] = {}
    for record in records:
        reason = record.cancel_reason or record.expire_reason
        if not reason:
            pass
        else:
            bucket = reject_reasons.setdefault(reason, {"count": 0, "rate": 0.0})
            bucket["count"] += 1
        terminal_status = record.terminal_status or record.status
        if terminal_status:
            terminal_bucket = terminal_statuses.setdefault(terminal_status, {"count": 0, "rate": 0.0})
            terminal_bucket["count"] += 1
    for bucket in reject_reasons.values():
        bucket["rate"] = bucket["count"] / sample_count if sample_count else 0.0
    for bucket in terminal_statuses.values():
        bucket["rate"] = bucket["count"] / sample_count if sample_count else 0.0
    return {
        "slippage_bps": _metric_summary(slippage),
        "fill_probability": len(filled) / sample_count if sample_count else None,
        "maker_rate": len(maker) / sample_count if sample_count else None,
        "taker_rate": len(taker) / sample_count if sample_count else None,
        "ack_latency_ms": _metric_summary(ack_latencies),
        "fill_latency_ms": _metric_summary(fill_latencies),
        "cancel_latency_ms": _metric_summary(cancel_latencies),
        "partial_fill_rate": len(partial) / sample_count if sample_count else None,
        "adverse_selection_bps": _metric_summary(adverse),
        "fees_funding_bps": _metric_summary(fee_funding),
        "reject_reasons": reject_reasons,
        "terminal_status": terminal_statuses,
    }


def _observed_scalar(observed: Mapping[str, Any], metric: str) -> float | None:
    value = observed.get(metric)
    if isinstance(value, Mapping):
        value = value.get("median")
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _has_required_tca_metric(observed: Mapping[str, Any], metric: str) -> bool:
    if metric == "reject_reasons":
        return isinstance(observed.get(metric), Mapping)
    return _observed_scalar(observed, metric) is not None


def _tca_comparisons(
    observed: Mapping[str, Any],
    assumptions: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    metric_to_assumption = {
        "slippage_bps": "expected_slippage_bps",
        "fill_probability": "expected_fill_probability",
        "maker_rate": "expected_maker_rate",
        "taker_rate": "expected_taker_rate",
        "ack_latency_ms": "expected_ack_latency_ms",
        "fill_latency_ms": "expected_fill_latency_ms",
        "cancel_latency_ms": "expected_cancel_latency_ms",
        "partial_fill_rate": "expected_partial_fill_rate",
        "adverse_selection_bps": "expected_adverse_selection_bps",
        "fees_funding_bps": "expected_fee_funding_bps",
    }
    comparisons: dict[str, Any] = {}
    for metric, assumption_field in metric_to_assumption.items():
        actual = _observed_scalar(observed, metric)
        expected = float(assumptions[assumption_field])
        tolerance = float(thresholds[metric])
        delta = None if actual is None else actual - expected
        comparisons[metric] = {
            "expected": expected,
            "observed": actual,
            "delta": delta,
            "tolerance": tolerance,
            "within_tolerance": delta is not None and abs(delta) <= tolerance,
        }
    expected_reject_rates = assumptions["expected_reject_reason_rates"]
    reject_reasons = _as_reject_rates(observed.get("reject_reasons"))
    reject_comparisons: dict[str, Any] = {}
    for reason in sorted(set(expected_reject_rates) | set(reject_reasons)):
        expected = float(expected_reject_rates.get(reason, 0.0))
        actual = float(reject_reasons.get(reason, 0.0))
        tolerance = float(thresholds["reject_reasons"])
        delta = actual - expected
        reject_comparisons[reason] = {
            "expected": expected,
            "observed": actual,
            "delta": delta,
            "tolerance": tolerance,
            "within_tolerance": abs(delta) <= tolerance,
        }
    comparisons["reject_reasons"] = reject_comparisons
    return comparisons


def _as_reject_rates(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    rates: dict[str, float] = {}
    for key, bucket in value.items():
        if type(key) is not str or not isinstance(bucket, Mapping):
            continue
        rate = bucket.get("rate")
        if isinstance(rate, bool) or not isinstance(rate, Real):
            continue
        parsed = float(rate)
        if math.isfinite(parsed):
            rates[key] = parsed
    return rates


def _all_comparisons_within_tolerance(comparisons: Mapping[str, Any]) -> bool:
    for metric, comparison in comparisons.items():
        if metric == "reject_reasons":
            if not all(bucket.get("within_tolerance") is True for bucket in comparison.values()):
                return False
            continue
        if not isinstance(comparison, Mapping) or comparison.get("within_tolerance") is not True:
            return False
    return True


def build_tca_calibration_report(
    records: Iterable[PassiveOrderCalibrationRecord],
    *,
    assumptions: Mapping[str, Any],
    evidence_source: Mapping[str, Any],
    evaluated_at: datetime | str,
    min_samples: int,
    max_evidence_age_seconds: int | None = None,
    tolerance_thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = tuple(_coerce_calibration_record(record) for record in records)
    parsed_assumptions = _validate_tca_assumptions(assumptions)
    parsed_thresholds = _validate_tca_thresholds(tolerance_thresholds)
    source = _validate_calibration_summary_evidence_source(evidence_source)
    evaluated_at_text = _tca_canonical_timestamp(evaluated_at, field_name="evaluated_at")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        raise ValueError("min_samples must be an integer")
    if min_samples < 0:
        raise ValueError("min_samples must be non-negative")
    if max_evidence_age_seconds is not None:
        if isinstance(max_evidence_age_seconds, bool) or not isinstance(max_evidence_age_seconds, int):
            raise ValueError("max_evidence_age_seconds must be an integer")
        if max_evidence_age_seconds < 0:
            raise ValueError("max_evidence_age_seconds must be non-negative")
    observed = _tca_observed(rows)
    comparisons = _tca_comparisons(observed, parsed_assumptions, parsed_thresholds)
    required_metrics_present = all(_has_required_tca_metric(observed, metric) for metric in _TCA_REQUIRED_OBSERVED_METRICS)
    sample_count_met = len(rows) >= min_samples
    evidence_fresh = True
    evidence_age_seconds = None
    exported_at = source.get("exported_at")
    if max_evidence_age_seconds is not None:
        if type(exported_at) is not str or not _is_canonical_utc_timestamp(exported_at):
            evidence_fresh = False
        else:
            evidence_time = datetime.fromisoformat(exported_at[:-1] + "+00:00")
            eval_time = datetime.fromisoformat(evaluated_at_text[:-1] + "+00:00")
            evidence_age_seconds = (eval_time - evidence_time).total_seconds()
            evidence_fresh = 0.0 <= evidence_age_seconds <= max_evidence_age_seconds
    all_metrics_within_tolerance = _all_comparisons_within_tolerance(comparisons)
    reasons: list[str] = []
    if not sample_count_met:
        reasons.append("insufficient_sample_count")
    if not evidence_fresh:
        reasons.append("stale_evidence")
    for metric in _TCA_REQUIRED_OBSERVED_METRICS:
        if not _has_required_tca_metric(observed, metric):
            reasons.append(f"missing_required_metric: {metric}")
    if required_metrics_present and not all_metrics_within_tolerance:
        for metric, comparison in comparisons.items():
            if metric == "reject_reasons":
                for reason, bucket in comparison.items():
                    if bucket.get("within_tolerance") is not True:
                        reasons.append(f"metric_breached: reject_reasons.{reason}")
                continue
            if comparison.get("within_tolerance") is not True:
                reasons.append(f"metric_breached: {metric}")
    checks = {
        "sample_count_met": sample_count_met,
        "evidence_fresh": evidence_fresh,
        "required_metrics_present": required_metrics_present,
        "all_metrics_within_tolerance": all_metrics_within_tolerance,
    }
    decision = "pass" if all(checks.values()) else "fail_closed"
    return {
        "schema_version": "tca_calibration_report.v1",
        "decision": decision,
        "evidence_source": source,
        "evaluated_at": evaluated_at_text,
        "sample_count": len(rows),
        "min_samples": min_samples,
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "evidence_age_seconds": evidence_age_seconds,
        "assumptions": parsed_assumptions,
        "tolerance_thresholds": parsed_thresholds,
        "observed": observed,
        "comparisons": comparisons,
        "checks": checks,
        "reasons": reasons,
        "caveats": [
            "Simulated-live calibration only; this report performs no real-money or real-exchange side effects.",
            "Promotion consumers must fail closed unless this report is fresh, sufficiently sampled, and passing.",
        ],
    }


def write_tca_calibration_report(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    assumptions: Mapping[str, Any],
    evidence_source: Mapping[str, Any],
    evaluated_at: datetime | str,
    min_samples: int,
    max_evidence_age_seconds: int | None = None,
    tolerance_thresholds: Mapping[str, Any] | None = None,
) -> Path:
    records = load_calibration_records(input_path)
    report = build_tca_calibration_report(
        records,
        assumptions=assumptions,
        evidence_source=evidence_source,
        evaluated_at=evaluated_at,
        min_samples=min_samples,
        max_evidence_age_seconds=max_evidence_age_seconds,
        tolerance_thresholds=tolerance_thresholds,
    )
    output_path = Path(output_dir) / "tca_calibration_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def _feedback_required_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"calibration feedback {field} must be an object")
    return value


def _feedback_optional_mapping(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _feedback_required_mapping(value, field)


def _feedback_finite_float(value: Any, field: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"calibration feedback {field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"calibration feedback {field} must be finite")
    if non_negative and parsed < 0.0:
        raise ValueError(f"calibration feedback {field} must be non-negative")
    return parsed


def _feedback_optional_float(value: Any, field: str, *, non_negative: bool = False) -> float | None:
    if value is None:
        return None
    return _feedback_finite_float(value, field, non_negative=non_negative)


def _feedback_ratio(value: Any, field: str) -> float:
    parsed = _feedback_finite_float(value, field, non_negative=True)
    if parsed > 1.0:
        raise ValueError(f"calibration feedback {field} must be between 0 and 1")
    return parsed


def _feedback_bool(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"calibration feedback {field} must be boolean")
    return value


def _feedback_window(value: Mapping[str, Any]) -> dict[str, str]:
    window = _feedback_required_mapping(value, "calibration_window")
    unknown_fields = sorted(set(window) - {"start", "end"})
    if unknown_fields:
        raise ValueError("unknown calibration feedback calibration_window field: " + ", ".join(unknown_fields))
    start = _tca_canonical_timestamp(window.get("start"), field_name="calibration_window.start")
    end = _tca_canonical_timestamp(window.get("end"), field_name="calibration_window.end")
    if datetime.fromisoformat(end[:-1] + "+00:00") <= datetime.fromisoformat(start[:-1] + "+00:00"):
        raise ValueError("calibration feedback calibration_window.end must be after start")
    return {"start": start, "end": end}


def _feedback_reject_rates(value: Any) -> dict[str, float]:
    rates = _as_reject_rates(value)
    for key, rate in rates.items():
        if _LOWER_TOKEN_RE.fullmatch(key) is None:
            raise ValueError("calibration feedback reject_rate_by_reason keys must be canonical")
        if rate < 0.0 or rate > 1.0:
            raise ValueError("calibration feedback reject_rate_by_reason values must be between 0 and 1")
    return dict(sorted(rates.items()))


def _feedback_latency_percentile(summary: Mapping[str, Any], percentile: str) -> float | None:
    metrics = _feedback_required_mapping(summary.get("metrics"), "latency_stress_summary.metrics")
    overall = _feedback_required_mapping(metrics.get("overall"), "latency_stress_summary.metrics.overall")
    return _feedback_optional_float(
        overall.get(percentile),
        f"latency_stress_summary.metrics.overall.{percentile}",
        non_negative=True,
    )


def _feedback_component_manifest(
    *,
    tca_report: Mapping[str, Any],
    latency_stress_summary: Mapping[str, Any],
    partial_maker_fill_evidence: Mapping[str, Any] | None,
    race_condition_evidence: Mapping[str, Any] | None,
    cross_source_parity: Mapping[str, Any] | None,
    l2_replay_quality: Mapping[str, Any] | None,
    derivatives_risk: Mapping[str, Any] | None,
    drift_contract: Mapping[str, Any] | None,
    additional_components: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, str | None]]:
    components = [
        ("tca_report", tca_report),
        ("latency_stress_summary", latency_stress_summary),
        ("partial_maker_fill_evidence", partial_maker_fill_evidence),
        ("race_condition_evidence", race_condition_evidence),
        ("cross_source_parity", cross_source_parity),
        ("l2_replay_quality", l2_replay_quality),
        ("derivatives_risk", derivatives_risk),
        ("drift_contract", drift_contract),
    ]
    manifest: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for component, payload in components:
        if payload is None:
            continue
        identity = component
        if identity in seen:
            raise ValueError("duplicate calibration feedback component identity")
        seen.add(identity)
        schema_version = payload.get("schema_version")
        if schema_version is not None and type(schema_version) is not str:
            raise ValueError(f"calibration feedback {component}.schema_version must be a string")
        manifest.append(
            {
                "component": component,
                "identity": identity,
                "schema_version": schema_version if isinstance(schema_version, str) else None,
            }
        )
    if additional_components is not None:
        for item in additional_components:
            component_item = _feedback_required_mapping(item, "additional_components[]")
            component = component_item.get("component")
            identity = component_item.get("identity")
            if type(component) is not str or component not in _CALIBRATION_FEEDBACK_COMPONENTS:
                raise ValueError("calibration feedback additional component must be known")
            if type(identity) is not str or not identity or identity != identity.strip():
                raise ValueError("calibration feedback additional component identity must be canonical")
            if identity in seen:
                raise ValueError("duplicate calibration feedback component identity")
            seen.add(identity)
            manifest.append({"component": component, "identity": identity, "schema_version": None})
    return manifest


def build_calibration_feedback_artifact(
    *,
    tca_report: Mapping[str, Any],
    latency_stress_summary: Mapping[str, Any],
    generated_at: datetime | str,
    calibration_window: Mapping[str, Any],
    min_samples: int,
    max_evidence_age_seconds: int | None,
    partial_maker_fill_evidence: Mapping[str, Any] | None = None,
    race_condition_evidence: Mapping[str, Any] | None = None,
    cross_source_parity: Mapping[str, Any] | None = None,
    l2_replay_quality: Mapping[str, Any] | None = None,
    derivatives_risk: Mapping[str, Any] | None = None,
    drift_contract: Mapping[str, Any] | None = None,
    additional_components: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        raise ValueError("calibration feedback min_samples must be an integer")
    if min_samples < 0:
        raise ValueError("calibration feedback min_samples must be non-negative")
    if max_evidence_age_seconds is not None:
        if isinstance(max_evidence_age_seconds, bool) or not isinstance(max_evidence_age_seconds, int):
            raise ValueError("calibration feedback max_evidence_age_seconds must be an integer")
        if max_evidence_age_seconds < 0:
            raise ValueError("calibration feedback max_evidence_age_seconds must be non-negative")

    tca = _feedback_required_mapping(tca_report, "tca_report")
    latency = _feedback_required_mapping(latency_stress_summary, "latency_stress_summary")
    partial_maker = _feedback_optional_mapping(partial_maker_fill_evidence, "partial_maker_fill_evidence")
    race = _feedback_optional_mapping(race_condition_evidence, "race_condition_evidence")
    parity = _feedback_optional_mapping(cross_source_parity, "cross_source_parity")
    l2_quality = _feedback_optional_mapping(l2_replay_quality, "l2_replay_quality")
    derivatives = _feedback_optional_mapping(derivatives_risk, "derivatives_risk")
    drift = _feedback_optional_mapping(drift_contract, "drift_contract")

    generated_at_text = _tca_canonical_timestamp(generated_at, field_name="generated_at")
    window = _feedback_window(calibration_window)
    components = _feedback_component_manifest(
        tca_report=tca,
        latency_stress_summary=latency,
        partial_maker_fill_evidence=partial_maker,
        race_condition_evidence=race,
        cross_source_parity=parity,
        l2_replay_quality=l2_quality,
        derivatives_risk=derivatives,
        drift_contract=drift,
        additional_components=additional_components,
    )

    sample_count = tca.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise ValueError("calibration feedback tca_report.sample_count must be an integer")
    sample_count_met = sample_count >= min_samples
    tca_checks = _feedback_required_mapping(tca.get("checks"), "tca_report.checks")
    evidence_fresh = tca_checks.get("evidence_fresh")
    if not isinstance(evidence_fresh, bool):
        raise ValueError("calibration feedback tca_report.checks.evidence_fresh must be boolean")
    tca_reasons = tca.get("reasons")
    if tca_reasons is None:
        tca_reasons = []
    if not isinstance(tca_reasons, list) or any(type(reason) is not str for reason in tca_reasons):
        raise ValueError("calibration feedback tca_report.reasons must be a string list")
    evidence_fresh = evidence_fresh and "stale_evidence" not in tca_reasons
    tca_decision = tca.get("decision")
    if tca_decision not in {"pass", "fail_closed"}:
        raise ValueError("calibration feedback tca_report.decision must be pass or fail_closed")
    tca_ready = tca_decision == "pass"

    latency_quality = latency.get("latency_quality")
    if latency_quality not in {"usable", "missing", "stale"}:
        raise ValueError("calibration feedback latency_stress_summary.latency_quality must be known")
    latency_sample_quality = latency.get("sample_size_quality")
    if latency_sample_quality not in {"sufficient", "insufficient"}:
        raise ValueError("calibration feedback latency_stress_summary.sample_size_quality must be known")
    latency_ready = latency_quality == "usable" and latency_sample_quality == "sufficient"

    partial_ready = True
    fill_floor = _observed_scalar(_feedback_required_mapping(tca.get("observed"), "tca_report.observed"), "fill_probability")
    maker_queue_haircut = 0.0
    if partial_maker is not None:
        partial_status = partial_maker.get("status")
        if partial_status not in {"pass", "fail_closed", "hold_for_review"}:
            raise ValueError("calibration feedback partial_maker_fill_evidence.status must be known")
        partial_ready = partial_status == "pass"
        fill_floor = _feedback_ratio(partial_maker.get("fill_probability_floor"), "fill_probability_floor")
        maker_queue_haircut = _feedback_ratio(partial_maker.get("maker_queue_haircut"), "maker_queue_haircut")
    if fill_floor is None:
        raise ValueError("calibration feedback fill_probability_floor must be numeric")
    fill_floor = _feedback_ratio(fill_floor, "fill_probability_floor")

    race_status = "clear"
    race_condition_haircut = 0.0
    if race is not None:
        race_status = race.get("race_condition_status")
        if race_status not in {"clear", "hold_for_review"}:
            raise ValueError("calibration feedback race_condition_status must be clear or hold_for_review")
        if race_status == "hold_for_review":
            race_condition_haircut = 1.0
    race_ordering_clear = race_status == "clear"

    parity_met = True
    if parity is not None:
        parity_status = parity.get("drift_status")
        if parity_status not in {"pass", "fail"}:
            raise ValueError("calibration feedback cross_source_parity drift_status must be pass or fail")
        parity_met = parity_status == "pass"

    l2_met = True
    if l2_quality is not None:
        l2_status = l2_quality.get("quality_status")
        if l2_status not in {"pass", "review"}:
            raise ValueError("calibration feedback l2_replay_quality quality_status must be pass or review")
        l2_met = l2_status == "pass"

    drift_hold_absent = True
    if drift is not None:
        drift_checks = _feedback_required_mapping(drift.get("checks"), "drift_contract.checks")
        drift_absent = drift_checks.get("paper_live_shadow_material_drift_absent")
        if not isinstance(drift_absent, bool):
            raise ValueError("calibration feedback drift_contract material drift check must be boolean")
        drift_hold_absent = drift_absent

    observed = _feedback_required_mapping(tca.get("observed"), "tca_report.observed")
    slippage = _feedback_required_mapping(observed.get("slippage_bps"), "tca_report.observed.slippage_bps")
    assumptions = _feedback_required_mapping(tca.get("assumptions"), "tca_report.assumptions")
    _feedback_optional_float(slippage.get("p95"), "slippage_bps_adjustment", non_negative=False)
    observed_slippage = _feedback_optional_float(slippage.get("median"), "slippage_bps_adjustment", non_negative=False)
    expected_slippage = _feedback_finite_float(
        assumptions.get("expected_slippage_bps"),
        "tca_report.assumptions.expected_slippage_bps",
        non_negative=True,
    )
    if observed_slippage is None:
        raise ValueError("calibration feedback slippage_bps_adjustment must be numeric")
    slippage_adjustment = max(0.0, observed_slippage - expected_slippage)

    latency_ms_p95_raw = _feedback_latency_percentile(latency, "p95")
    latency_ms_p99_raw = _feedback_latency_percentile(latency, "p99")
    latency_ms_p95 = round(latency_ms_p95_raw, 6) if latency_ms_p95_raw is not None else None
    latency_ms_p99 = round(latency_ms_p99_raw, 6) if latency_ms_p99_raw is not None else None
    reject_rate_by_reason = _feedback_reject_rates(observed.get("reject_reasons"))
    funding_conservative = _feedback_bool(
        derivatives.get("funding_conservatism_required") if derivatives is not None else None,
        "derivatives_risk.funding_conservatism_required",
    )
    margin_conservative = _feedback_bool(
        derivatives.get("margin_conservatism_required") if derivatives is not None else None,
        "derivatives_risk.margin_conservatism_required",
    )

    checks = {
        "sample_count_met": sample_count_met,
        "evidence_fresh": evidence_fresh,
        "tca_ready": tca_ready,
        "latency_ready": latency_ready,
        "partial_maker_fill_ready": partial_ready,
        "race_ordering_clear": race_ordering_clear,
        "drift_hold_absent": drift_hold_absent,
        "cross_source_parity_met": parity_met,
        "l2_replay_quality_met": l2_met,
    }
    reasons: list[str] = []
    if not sample_count_met:
        reasons.append("insufficient_sample_count")
    if not evidence_fresh:
        reasons.append("stale_evidence")
    if not tca_ready:
        reasons.append("tca_not_ready")
    if not latency_ready:
        reasons.append("latency_not_ready")
    if not partial_ready:
        reasons.append("partial_maker_fill_not_ready")
    if not race_ordering_clear:
        reasons.append("ambiguous_race_ordering")
    if not drift_hold_absent:
        reasons.append("drift_hold")
    if not parity_met:
        reasons.append("cross_source_parity_drift")
    if not l2_met:
        reasons.append("l2_replay_quality_review")
    decision = "ready" if all(checks.values()) else "fail_closed"

    if decision != "ready":
        fill_floor = 0.0
        maker_queue_haircut = 1.0
        race_condition_haircut = 1.0

    return {
        "schema_version": "calibration_feedback_artifact.v1",
        "generated_at": generated_at_text,
        "calibration_window": window,
        "decision": decision,
        "min_samples": min_samples,
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "sample_count": sample_count,
        "model_inputs": {
            "slippage_bps_adjustment": slippage_adjustment,
            "latency_ms_p95": latency_ms_p95,
            "latency_ms_p99": latency_ms_p99,
            "fill_probability_floor": fill_floor,
            "maker_queue_haircut": maker_queue_haircut,
            "reject_rate_by_reason": reject_rate_by_reason,
            "race_condition_haircut": race_condition_haircut,
            "funding_conservatism_required": funding_conservative,
            "margin_conservatism_required": margin_conservative,
        },
        "checks": checks,
        "reasons": reasons,
        "components": components,
        "side_effect_boundary": "offline_local_only",
        "strategy_config_mutation": "forbidden",
        "caveats": [
            "This artifact is an explicit downstream calibration input contract only.",
            "Strategy and live execution configuration are not mutated by this builder.",
        ],
    }


def write_calibration_feedback_artifact(
    output_dir: str | Path,
    **kwargs: Any,
) -> Path:
    artifact = build_calibration_feedback_artifact(**kwargs)
    output_path = Path(output_dir) / "calibration_feedback_artifact.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path
