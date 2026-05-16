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


@dataclass(frozen=True, slots=True)
class PassiveOrderCalibrationRecord:
    symbol: str
    side: str
    intended_limit_price: float
    signal_at: datetime
    decision_at: datetime
    submitted_at: datetime
    exchange_ack_at: datetime
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
    slippage_bps: float | None = None
    ref_price: float | None = None
    cancel_reason: str | None = None
    expire_reason: str | None = None
    latency_ms: float | None = None
    setup_type: str | None = None


_LIFECYCLE_TIMESTAMP_FIELDS = (
    "signal_at",
    "decision_at",
    "submitted_at",
    "exchange_ack_at",
    "first_fill_at",
    "last_fill_at",
    "cancel_ack_at",
)
_CANCEL_TERMINAL_STATUSES = {"cancelled", "canceled", "expired", "rejected"}
_FILLED_STATUSES = {"filled", "partially_filled", "partial"}


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


def _record_from_mapping(row: Mapping[str, Any]) -> PassiveOrderCalibrationRecord:
    _validate_fee_asset_fields(row)
    _validate_commission_fields(row)
    signal_at = _parse_lifecycle_datetime(row.get("signal_at"), field_name="signal_at", required=True)
    decision_at = _parse_lifecycle_datetime(row.get("decision_at"), field_name="decision_at", required=True)
    submitted_at = _parse_lifecycle_datetime(row.get("submitted_at"), field_name="submitted_at", required=True)
    exchange_ack_at = _parse_lifecycle_datetime(
        row.get("exchange_ack_at"), field_name="exchange_ack_at", required=True
    )
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
    if first_fill_at is not None and first_fill_at < exchange_ack_at:
        raise ValueError("calibration record first_fill_at must be at or after exchange_ack_at")
    if last_fill_at is not None and last_fill_at < exchange_ack_at:
        raise ValueError("calibration record last_fill_at must be at or after exchange_ack_at")
    if last_fill_at is not None and first_fill_at is not None and last_fill_at < first_fill_at:
        raise ValueError("calibration record last_fill_at must be at or after first_fill_at")
    status = _canonical_lower_token_or_none("status", row.get("status")) or ""
    if status in _FILLED_STATUSES and first_fill_at is None:
        raise ValueError("calibration record filled status requires first_fill_at")
    if cancel_ack_at is not None:
        last_lifecycle_fill_at = last_fill_at or first_fill_at
        if last_lifecycle_fill_at is not None and cancel_ack_at <= last_lifecycle_fill_at:
            raise ValueError("calibration record cancel_ack_at must be after last fill timestamp")
        if status not in _CANCEL_TERMINAL_STATUSES:
            raise ValueError("calibration record cancel_ack_at requires a cancelled, expired, or rejected status")
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
        slippage_bps=_float_or_none("slippage_bps", row.get("slippage_bps")),
        ref_price=_float_or_none("ref_price", row.get("ref_price")),
        cancel_reason=_canonical_lower_token_or_none("cancel_reason", row.get("cancel_reason")),
        expire_reason=_canonical_lower_token_or_none("expire_reason", row.get("expire_reason")),
        latency_ms=_float_or_none("latency_ms", row.get("latency_ms")),
        setup_type=_canonical_upper_token_or_none("setup_type", row.get("setup_type")),
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
