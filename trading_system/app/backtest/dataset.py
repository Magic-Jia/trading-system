from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .types import DatasetSnapshotRow, InstrumentSnapshotRow, SampleWindow

_REQUIRED_BUNDLE_FILES = ("metadata.json", "market_context.json", "derivatives_snapshot.json")
_BASELINE_ACCOUNT_FILENAME = "baseline_account_snapshot.json"
_INSTRUMENT_SNAPSHOT_FILENAME = "instrument_snapshot.json"
_IMPORT_MANIFEST_FILENAME = "import_manifest.json"
_ACCOUNT_NON_NEGATIVE_NUMBER_FIELDS = (
    "available_balance",
    "entry",
    "entry_price",
    "futures_wallet_balance",
    "initial_margin",
    "isolated_margin",
    "liquidation_price",
    "liquidationPrice",
    "margin",
    "maintenance_margin",
    "margin_balance",
    "mark",
    "mark_price",
    "notional",
    "position_amt",
    "positionAmt",
    "qty",
    "stop_price",
    "total_initial_margin",
    "total_maint_margin",
    "total_margin_balance",
    "total_wallet_balance",
    "wallet_balance",
    "break_even_price",
    "breakEvenPrice",
    "equity",
    "risk_price",
)
_ACCOUNT_POSITIVE_NUMBER_FIELDS = (
    "leverage",
)
_ACCOUNT_RATIO_NUMBER_FIELDS = (
    "initial_margin_ratio",
    "initialMarginRatio",
    "maintenance_margin_ratio",
    "maintenanceMarginRatio",
    "marginRatio",
    "margin_ratio",
    "riskRatio",
    "risk_ratio",
)
_ACCOUNT_SIGNED_NUMBER_FIELDS = (
    "total_unrealized_profit",
    "unRealizedProfit",
    "realizedPnl",
    "realized_pnl",
    "unrealized_pnl",
    "unrealizedPnl",
    "pnl",
    "upl",
)
_ACCOUNT_IDENTITY_STRING_FIELDS = (
    "account_id",
    "venue",
    "exchange",
    "quote_currency",
    "margin_mode",
    "account_type",
)
_ACCOUNT_OPEN_POSITION_IDENTITY_STRING_FIELDS = (
    "status",
    "position_mode",
    "source",
    "strategy_tag",
    "strategyTag",
    "intent_id",
    "intentId",
)
_ACCOUNT_OPEN_POSITION_IDENTIFIER_FIELDS = (
    "position_id",
    "positionId",
    "order_id",
    "orderId",
    "client_order_id",
    "clientOrderId",
)
_ACCOUNT_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ACCOUNT_OPEN_POSITION_UPPERCASE_IDENTITY_FIELDS = (
    "symbol",
    "venue",
    "exchange",
)
_ACCOUNT_OPEN_POSITION_UPPERCASE_ENUM_FIELDS = {
    "venue": {"BINANCE"},
    "exchange": {"BINANCE"},
}
_ACCOUNT_OPEN_POSITION_ENUM_FIELDS = {
    "side": {"LONG", "SHORT"},
    "positionSide": {"LONG", "SHORT"},
    "margin_mode": {"CROSS", "ISOLATED"},
    "marginType": {"CROSS", "ISOLATED"},
    "source": {"account_snapshot", "archive_fixture", "paper_execution"},
    "origin": {"account_snapshot", "archive_fixture", "paper_execution"},
    "accountSource": {"account_snapshot", "archive_fixture"},
    "positionSource": {"account_snapshot", "archive_fixture", "paper_execution"},
}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _instrument_canonical_string(value: object, *, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"instrument {field} must be a canonical string: {path}")
    return value


def _instrument_bool(value: object, *, field: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"instrument {field} must be a boolean: {path}")
    return value


def _instrument_positive_float(value: object, *, field: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"instrument {field} must be a positive finite number: {path}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"instrument {field} must be a positive finite number: {path}")
    return number


def _instrument_rows(bundle_path: Path) -> tuple[InstrumentSnapshotRow, ...]:
    path = bundle_path / _INSTRUMENT_SNAPSHOT_FILENAME
    if not path.exists():
        return ()

    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"dataset bundle has invalid instrument snapshot: {path}")
    raw_rows = payload.get("rows", [])
    if not isinstance(raw_rows, list):
        raise ValueError(f"dataset bundle has invalid instrument rows: {path}")

    rows: list[InstrumentSnapshotRow] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ValueError(f"dataset bundle has invalid instrument row payload: {path}")
        market_type = _instrument_canonical_string(raw_row["market_type"], field="market_type", path=path)
        if market_type not in {"spot", "futures"}:
            raise ValueError(f"dataset bundle has invalid instrument market_type: {path}")
        rows.append(
            InstrumentSnapshotRow(
                symbol=_instrument_canonical_string(raw_row["symbol"], field="symbol", path=path),
                market_type=market_type,
                base_asset=_instrument_canonical_string(raw_row["base_asset"], field="base_asset", path=path),
                listing_timestamp=_parse_timestamp(
                    _instrument_canonical_string(raw_row["listing_timestamp"], field="listing_timestamp", path=path)
                ),
                quote_volume_usdt_24h=_instrument_positive_float(
                    raw_row["quote_volume_usdt_24h"], field="quote_volume_usdt_24h", path=path
                ),
                liquidity_tier=_instrument_canonical_string(
                    raw_row["liquidity_tier"], field="liquidity_tier", path=path
                ),
                quantity_step=_instrument_positive_float(raw_row["quantity_step"], field="quantity_step", path=path),
                price_tick=_instrument_positive_float(raw_row["price_tick"], field="price_tick", path=path),
                has_complete_funding=_instrument_bool(
                    raw_row["has_complete_funding"], field="has_complete_funding", path=path
                ),
            )
        )

    return tuple(sorted(rows, key=lambda row: (row.market_type, row.symbol)))


def _bundle_dirs(dataset_root: Path) -> list[Path]:
    return sorted(path for path in dataset_root.iterdir() if path.is_dir())


def _baseline_account(dataset_root: Path) -> dict | None:
    path = dataset_root / _BASELINE_ACCOUNT_FILENAME
    if not path.exists():
        return None
    return _load_json(path)


def _metadata_canonical_string(metadata: dict, key: str) -> str:
    value = metadata[key]
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"metadata.{key} must be a canonical string")
    return value


def _metadata_mapping(metadata: dict, key: str) -> dict[str, object]:
    value = metadata.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"metadata.{key} must be an object")
    return dict(value)


def _metadata_metric_map(metadata: dict, key: str) -> dict[str, float]:
    values = _metadata_mapping(metadata, key)
    result: dict[str, float] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str) or not raw_key or raw_key != raw_key.strip():
            raise ValueError(f"metadata.{key} key must be a canonical string")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"metadata.{key}.{raw_key} must be a finite numeric value")
        number = float(raw_value)
        if not math.isfinite(number):
            raise ValueError(f"metadata.{key}.{raw_key} must be a finite numeric value")
        result[raw_key] = number
    return result


def _account_snapshot(account: dict, *, path: Path) -> dict:
    snapshot = dict(account)
    validate_account_snapshot_identity(snapshot, path=path)
    _validate_account_numeric_fields(snapshot, path=path, field_path="account")
    return snapshot


def validate_account_snapshot_identity(account: object, *, path: Path) -> None:
    if not isinstance(account, dict):
        raise ValueError(f"dataset bundle has invalid account snapshot: {path}")
    for field in _ACCOUNT_IDENTITY_STRING_FIELDS:
        if field not in account:
            continue
        value = account[field]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"account.{field} must be a canonical string: {path}")
    _validate_open_position_identity_fields(account, path=path)


def _validate_open_position_identity_fields(account: dict, *, path: Path) -> None:
    positions = account.get("open_positions")
    if positions is None:
        return
    if not isinstance(positions, list):
        raise ValueError(f"account.open_positions must be a list: {path}")
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            raise ValueError(f"account.open_positions[{index}] must be an object: {path}")
        field_prefix = f"account.open_positions[{index}]"
        for field in _ACCOUNT_OPEN_POSITION_IDENTITY_STRING_FIELDS:
            if field in position:
                _require_account_canonical_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
        for field in _ACCOUNT_OPEN_POSITION_IDENTIFIER_FIELDS:
            if field in position:
                _require_account_identifier_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
        for field in _ACCOUNT_OPEN_POSITION_UPPERCASE_IDENTITY_FIELDS:
            if field in position:
                value = _require_account_uppercase_canonical_string(
                    position[field],
                    field_path=f"{field_prefix}.{field}",
                    path=path,
                )
                allowed = _ACCOUNT_OPEN_POSITION_UPPERCASE_ENUM_FIELDS.get(field)
                if allowed is not None and value not in allowed:
                    allowed_values = ", ".join(sorted(allowed))
                    raise ValueError(f"{field_prefix}.{field} must be one of {allowed_values}: {path}")
        for field, allowed in _ACCOUNT_OPEN_POSITION_ENUM_FIELDS.items():
            if field in position:
                value = _require_account_canonical_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
                if value not in allowed:
                    allowed_values = ", ".join(sorted(allowed))
                    raise ValueError(f"{field_prefix}.{field} must be one of {allowed_values}: {path}")


def _require_account_canonical_string(value: object, *, field_path: str, path: Path) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_path} must be a canonical string: {path}")
    return value


def _require_account_identifier_string(value: object, *, field_path: str, path: Path) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_path} must be a canonical identifier string: {path}")
    if _ACCOUNT_IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{field_path} must be a canonical identifier string: {path}")
    return value


def _require_account_uppercase_canonical_string(value: object, *, field_path: str, path: Path) -> str:
    text = _require_account_canonical_string(value, field_path=field_path, path=path)
    if text != text.upper():
        raise ValueError(f"{field_path} must be an uppercase canonical string: {path}")
    return text


def validate_account_snapshot_payload(account: object, *, path: Path) -> None:
    if not isinstance(account, dict):
        raise ValueError(f"dataset bundle has invalid account snapshot: {path}")
    validate_account_snapshot_identity(account, path=path)
    for field in _ACCOUNT_NON_NEGATIVE_NUMBER_FIELDS:
        if field not in account:
            continue
        value = account[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"account.{field} must be a non-negative finite number: {path}")
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise ValueError(f"account.{field} must be a non-negative finite number: {path}")
    _validate_account_numeric_fields(account, path=path, field_path="account")


def _validate_account_number(value: object, *, field_path: str, path: Path, qualifier: str, minimum: float | None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a {qualifier} number: {path}")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{field_path} must be a {qualifier} number: {path}")
    return number


def _validate_account_ratio(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a ratio in (0, 1]: {path}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0 or number > 1.0:
        raise ValueError(f"{field_path} must be a ratio in (0, 1]: {path}")
    return number


def _validate_account_numeric_fields(payload: object, *, path: Path, field_path: str) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{field_path}.{key}"
            if key in _ACCOUNT_RATIO_NUMBER_FIELDS:
                _validate_account_ratio(value, field_path=child_path, path=path)
            elif key in _ACCOUNT_POSITIVE_NUMBER_FIELDS:
                number = _validate_account_number(
                    value,
                    field_path=child_path,
                    path=path,
                    qualifier="positive finite",
                    minimum=0.0,
                )
                if number == 0.0:
                    raise ValueError(f"{child_path} must be a positive finite number: {path}")
            elif key in _ACCOUNT_NON_NEGATIVE_NUMBER_FIELDS:
                _validate_account_number(
                    value,
                    field_path=child_path,
                    path=path,
                    qualifier="non-negative finite",
                    minimum=0.0,
                )
            elif key in _ACCOUNT_SIGNED_NUMBER_FIELDS:
                _validate_account_number(
                    value,
                    field_path=child_path,
                    path=path,
                    qualifier="finite",
                    minimum=None,
                )
            elif isinstance(value, (dict, list)):
                _validate_account_numeric_fields(value, path=path, field_path=child_path)
        return
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                _validate_account_numeric_fields(item, path=path, field_path=f"{field_path}[{index}]")


def _row_from_bundle(bundle_path: Path, *, fallback_account: dict | None) -> DatasetSnapshotRow:
    for filename in _REQUIRED_BUNDLE_FILES:
        file_path = bundle_path / filename
        if not file_path.exists():
            raise FileNotFoundError(f"dataset bundle missing required file: {file_path}")

    metadata = _load_json(bundle_path / "metadata.json")
    market = _load_json(bundle_path / "market_context.json")
    if not isinstance(market, dict):
        raise ValueError(f"dataset bundle has invalid market context: {bundle_path / 'market_context.json'}")
    market_context = dict(market)
    derivatives_payload = _load_json(bundle_path / "derivatives_snapshot.json")
    if not isinstance(derivatives_payload, dict):
        raise ValueError(f"dataset bundle has invalid derivatives snapshot: {bundle_path / 'derivatives_snapshot.json'}")
    derivatives = derivatives_payload.get("rows", [])
    if not isinstance(derivatives, list):
        raise ValueError(f"dataset bundle has invalid derivatives rows: {bundle_path / 'derivatives_snapshot.json'}")
    derivative_rows: list[dict] = []
    for row in derivatives:
        if not isinstance(row, dict):
            raise ValueError(
                f"dataset bundle has invalid derivatives row payload: {bundle_path / 'derivatives_snapshot.json'}"
            )
        derivative_rows.append(dict(row))

    account_path = bundle_path / "account_snapshot.json"
    account = _load_json(account_path) if account_path.exists() else fallback_account
    if account is None:
        raise FileNotFoundError(
            f"dataset bundle missing account snapshot and no baseline provided: {bundle_path / 'account_snapshot.json'}"
        )
    if not isinstance(account, dict):
        raise ValueError(f"dataset bundle has invalid account snapshot: {bundle_path / 'account_snapshot.json'}")
    account_snapshot = _account_snapshot(
        account,
        path=account_path if account_path.exists() else bundle_path.parent / _BASELINE_ACCOUNT_FILENAME,
    )
    instrument_rows = _instrument_rows(bundle_path)

    forward_returns = _metadata_metric_map(metadata, "forward_returns")
    forward_drawdowns = _metadata_metric_map(metadata, "forward_drawdowns")
    meta = {
        key: value
        for key, value in metadata.items()
        if key not in {"timestamp", "run_id", "forward_returns", "forward_drawdowns"}
    }
    return DatasetSnapshotRow(
        timestamp=_parse_timestamp(_metadata_canonical_string(metadata, "timestamp")),
        run_id=_metadata_canonical_string(metadata, "run_id"),
        market=market_context,
        derivatives=derivative_rows,
        account=account_snapshot,
        instrument_rows=instrument_rows,
        forward_returns=forward_returns,
        forward_drawdowns=forward_drawdowns,
        meta=meta,
        source_path=bundle_path,
    )


def load_historical_dataset(dataset_root: str | Path) -> list[DatasetSnapshotRow]:
    root = Path(dataset_root)
    fallback_account = _baseline_account(root)
    rows = [_row_from_bundle(bundle_path, fallback_account=fallback_account) for bundle_path in _bundle_dirs(root)]
    return sorted(rows, key=lambda row: (row.timestamp, row.run_id))


def _manifest_canonical_string(manifest: dict[str, object], key: str) -> str | None:
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"import manifest {key} must be a canonical string")
    return value


def _manifest_object_field(manifest: dict[str, object], key: str) -> dict[str, object]:
    value = manifest.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"import manifest {key} must be an object")
    return dict(value)


def _manifest_non_negative_int(manifest: dict[str, object], key: str) -> int:
    value = manifest.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"import manifest {key} must be a non-negative integer")
    return value


def _manifest_string_list(manifest: dict[str, object], key: str) -> list[str]:
    value = manifest.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"import manifest {key} must be a list")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ValueError(f"import manifest {key}[{index}] must be a canonical string")
        values.append(item)
    return values


def _manifest_list_count(manifest: dict[str, object], key: str) -> int:
    value = manifest.get(key)
    if value is None:
        return 0
    if not isinstance(value, list):
        raise ValueError(f"import manifest {key} must be a list")
    return len(value)


def load_dataset_root_metadata(dataset_root: str | Path) -> dict[str, object]:
    root = Path(dataset_root)
    manifest_path = root / _IMPORT_MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}

    manifest = _load_json(manifest_path)
    return {
        "dataset_root_type": "imported_archive",
        "import_manifest_path": str(manifest_path),
        "import_manifest": {
            "schema_version": _manifest_canonical_string(manifest, "schema_version"),
            "scope": _manifest_canonical_string(manifest, "scope"),
            "archive_root": _manifest_canonical_string(manifest, "archive_root"),
            "dataset_root": _manifest_canonical_string(manifest, "dataset_root"),
            "manifest_snapshot_count": _manifest_non_negative_int(manifest, "snapshot_count"),
            "symbols": _manifest_string_list(manifest, "symbols"),
            "start_timestamp": _manifest_canonical_string(manifest, "start_timestamp"),
            "end_timestamp": _manifest_canonical_string(manifest, "end_timestamp"),
            "bundle_count": _manifest_list_count(manifest, "bundle_dirs"),
            "source": _manifest_object_field(manifest, "source"),
            "coverage": _manifest_object_field(manifest, "coverage"),
        },
    }


def _window_rows(rows: Iterable[DatasetSnapshotRow], window: SampleWindow) -> list[DatasetSnapshotRow]:
    return [
        row
        for row in rows
        if window.start <= row.timestamp <= window.end
    ]


def split_rows_by_windows(
    rows: list[DatasetSnapshotRow], windows: tuple[SampleWindow, ...] | list[SampleWindow]
) -> dict[str, list[DatasetSnapshotRow]]:
    return {
        window.name: sorted(_window_rows(rows, window), key=lambda row: (row.timestamp, row.run_id))
        for window in windows
    }
