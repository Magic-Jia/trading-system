from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from numbers import Real
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

_ASSET_CODE_RE = re.compile(r"^[A-Z0-9]+$")
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
    submitted_at: datetime
    first_fill_at: datetime | None = None
    last_fill_at: datetime | None = None
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


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("calibration numeric field must be numeric")
    return float(value)


def _required_float(row: Mapping[str, Any], *keys: str, field_name: str) -> float:
    for key in keys:
        if key not in row or row[key] is None or row[key] == "":
            continue
        value = row[key]
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be numeric")
        return float(value)
    raise ValueError(f"calibration record missing {field_name}")


def _validate_fee_asset_fields(row: Mapping[str, Any]) -> None:
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


def _validate_commission_fields(row: Mapping[str, Any]) -> None:
    for field in _COMMISSION_FIELDS:
        if field not in row or row[field] is None or row[field] == "":
            continue
        if isinstance(row[field], bool):
            raise ValueError(f"calibration record {field} must be numeric")
        float(row[field])


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


def _record_from_mapping(row: Mapping[str, Any]) -> PassiveOrderCalibrationRecord:
    _validate_fee_asset_fields(row)
    _validate_commission_fields(row)
    submitted_at = _parse_datetime(row.get("submitted_at"))
    if submitted_at is None:
        raise ValueError("calibration record missing submitted_at")
    return PassiveOrderCalibrationRecord(
        symbol=str(row.get("symbol", "")).strip().upper(),
        side=str(row.get("side", "")).strip().lower(),
        intended_limit_price=_required_float(
            row,
            "intended_limit_price",
            "limit_price",
            field_name="intended_limit_price",
        ),
        submitted_at=submitted_at,
        first_fill_at=_parse_datetime(row.get("first_fill_at")),
        last_fill_at=_parse_datetime(row.get("last_fill_at")),
        requested_qty=_float_or_none(row.get("requested_qty")),
        requested_notional=_float_or_none(row.get("requested_notional")),
        filled_qty=_float_or_none(row.get("filled_qty")),
        filled_notional=_float_or_none(row.get("filled_notional")),
        status=str(row.get("status", "")).strip().lower(),
        maker_taker=str(row.get("maker_taker")).strip().lower() if row.get("maker_taker") is not None else None,
        fees=_fee_float_or_none("fees", row.get("fees")),
        slippage_bps=_float_or_none(row.get("slippage_bps")),
        ref_price=_float_or_none(row.get("ref_price")),
        cancel_reason=str(row.get("cancel_reason")) if row.get("cancel_reason") is not None else None,
        expire_reason=str(row.get("expire_reason")) if row.get("expire_reason") is not None else None,
        latency_ms=_float_or_none(row.get("latency_ms")),
        setup_type=str(row.get("setup_type")).strip().upper() if row.get("setup_type") is not None else None,
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


def summarize_calibration_records(
    records: Iterable[PassiveOrderCalibrationRecord],
    *,
    evidence_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = tuple(records)
    maker_rows = tuple(record for record in rows if record.maker_taker == "maker")
    taker_rows = tuple(record for record in rows if record.maker_taker == "taker")
    source = dict(evidence_source or {"type": "unknown_offline_records"})
    source.setdefault("type", "unknown_offline_records")
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
    for key in ("submitted_at", "first_fill_at", "last_fill_at"):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
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
