from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .types import DatasetSnapshotRow, InstrumentSnapshotRow, SampleWindow

_REQUIRED_BUNDLE_FILES = ("metadata.json", "market_context.json", "derivatives_snapshot.json")
_BASELINE_ACCOUNT_FILENAME = "baseline_account_snapshot.json"
_INSTRUMENT_SNAPSHOT_FILENAME = "instrument_snapshot.json"
_IMPORT_MANIFEST_FILENAME = "import_manifest.json"
_ACCOUNT_NON_NEGATIVE_NUMBER_FIELDS = (
    "available_balance",
    "availableBalance",
    "borrow_fee",
    "entry",
    "entry_price",
    "commission",
    "cost",
    "futures_wallet_balance",
    "futuresWalletBalance",
    "fee",
    "funding_fee",
    "free",
    "initial_margin",
    "initialMargin",
    "isolated_margin",
    "isolatedMargin",
    "liquidation_price",
    "liquidationPrice",
    "locked",
    "margin",
    "maintenance_margin",
    "maintenanceMargin",
    "margin_balance",
    "marginBalance",
    "margin_used",
    "marginUsed",
    "mark",
    "mark_price",
    "notional",
    "position_amt",
    "positionAmt",
    "qty",
    "realized_fee",
    "slippage",
    "stop_price",
    "total_initial_margin",
    "totalInitialMargin",
    "total_maint_margin",
    "totalMaintMargin",
    "total_margin_balance",
    "totalMarginBalance",
    "total_wallet_balance",
    "totalWalletBalance",
    "wallet_balance",
    "walletBalance",
    "break_even_price",
    "breakEvenPrice",
    "collateral_value",
    "collateralValue",
    "equity",
    "risk_price",
    "unrealized_cost",
    "unrealizedCost",
)
_ACCOUNT_SPOT_BALANCE_NON_NEGATIVE_NUMBER_FIELDS = (
    "free",
    "locked",
    "total",
)
_ACCOUNT_POSITIVE_NUMBER_FIELDS = (
    "exposure_value",
    "exposureValue",
    "leverage",
    "market_value",
    "marketValue",
    "position_value",
    "positionValue",
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
_ACCOUNT_OPEN_POSITION_POSITIVE_PRICE_FIELDS = (
    "break_even_price",
    "breakEvenPrice",
    "entry_price",
    "entryPrice",
    "liquidation_price",
    "liquidationPrice",
    "mark_price",
    "markPrice",
    "notional",
    "risk_price",
    "stop_price",
    "stopPrice",
    "take_profit_price",
    "takeProfitPrice",
    "trailing_stop_price",
    "trailingStopPrice",
)
_ACCOUNT_RISK_EXPOSURE_RATIO_FIELDS = (
    "account_risk_pct",
    "exposure_pct",
    "margin_used_pct",
    "notional_pct",
    "risk_pct",
)
_ACCOUNT_RISK_EXPOSURE_BPS_FIELDS = (
    "exposure_bps",
    "risk_bps",
)
_ACCOUNT_SIGNED_NUMBER_FIELDS = (
    "total_unrealized_profit",
    "totalUnrealizedProfit",
    "unRealizedProfit",
    "realizedPnl",
    "realizedProfit",
    "realized_pnl",
    "unrealized_pnl",
    "unrealizedPnl",
    "unrealizedProfit",
    "pnl",
    "upl",
)
_ACCOUNT_IDENTITY_STRING_FIELDS = (
    "account_id",
    "accountId",
)
_ACCOUNT_ASSET_CODE_FIELDS = (
    "base_asset",
    "baseAsset",
    "base_currency",
    "baseCurrency",
    "quote_asset",
    "quoteAsset",
    "quote_currency",
    "quoteCurrency",
    "margin_asset",
    "marginAsset",
    "margin_currency",
    "marginCurrency",
    "collateral_asset",
    "collateralAsset",
    "collateral_currency",
    "collateralCurrency",
    "settlement_asset",
    "settlementAsset",
    "settlement_currency",
    "settlementCurrency",
    "fee_asset",
    "feeAsset",
    "fee_currency",
    "feeCurrency",
    "funding_asset",
    "fundingAsset",
    "funding_currency",
    "fundingCurrency",
    "pnl_asset",
    "pnlAsset",
    "pnl_currency",
    "pnlCurrency",
    "cost_asset",
    "costAsset",
    "cost_currency",
    "costCurrency",
)
_ACCOUNT_TIME_FIELDS = (
    "last_update_time",
    "lastUpdateTime",
    "update_time",
    "updateTime",
    "event_time",
    "eventTime",
    "as_of",
    "timestamp",
)
_ACCOUNT_ENUM_FIELDS = {
    "account_type": {"FUTURES", "MARGIN", "PORTFOLIO_MARGIN", "SPOT"},
    "accountType": {"FUTURES", "MARGIN", "PORTFOLIO_MARGIN", "SPOT"},
    "venue": {"BINANCE"},
    "exchange": {"BINANCE"},
    "margin_mode": {"CROSS", "ISOLATED"},
    "marginMode": {"CROSS", "ISOLATED"},
}
_ACCOUNT_OPEN_POSITION_IDENTITY_STRING_FIELDS = (
    "status",
    "source",
    "strategy_tag",
    "strategyTag",
    "intent_id",
    "intentId",
)
_ACCOUNT_OPEN_POSITION_IDENTIFIER_FIELDS = (
    "position_id",
    "positionId",
    "trade_id",
    "tradeId",
    "execution_id",
    "executionId",
    "fill_id",
    "fillId",
    "order_id",
    "orderId",
    "client_order_id",
    "clientOrderId",
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
_ACCOUNT_OPEN_POSITION_PROVENANCE_IDENTIFIER_FIELDS = (
    "signal_source",
    "signalSource",
    "strategy_source",
    "strategySource",
    "data_source",
    "dataSource",
)
_ACCOUNT_OPEN_POSITION_ASSET_CODE_FIELDS = (
    "base_asset",
    "baseAsset",
    "base_currency",
    "baseCurrency",
    "quote_asset",
    "quoteAsset",
    "quote_currency",
    "quoteCurrency",
    "margin_asset",
    "marginAsset",
    "margin_currency",
    "marginCurrency",
    "collateral_asset",
    "collateralAsset",
    "collateral_currency",
    "collateralCurrency",
    "settlement_asset",
    "settlementAsset",
    "settlement_currency",
    "settlementCurrency",
    "fee_asset",
    "feeAsset",
    "fee_currency",
    "feeCurrency",
    "commissionAsset",
    "funding_asset",
    "fundingAsset",
    "funding_currency",
    "fundingCurrency",
    "pnl_asset",
    "pnlAsset",
    "pnl_currency",
    "pnlCurrency",
    "cost_asset",
    "costAsset",
    "cost_currency",
    "costCurrency",
)
_ACCOUNT_OPEN_POSITION_TIME_FIELDS = (
    "opened_at",
    "openedAt",
    "updated_at",
    "updatedAt",
    "update_time",
    "updateTime",
    "closed_at",
    "closedAt",
    "as_of",
    "timestamp",
    "last_update_time",
    "lastUpdateTime",
    "event_time",
    "eventTime",
    "trade_time",
    "tradeTime",
    "execution_time",
    "executionTime",
    "fill_time",
    "fillTime",
    "order_time",
    "orderTime",
    "close_time",
    "closeTime",
    "expiry_time",
    "expiryTime",
    "settlement_time",
    "settlementTime",
)
_ACCOUNT_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ACCOUNT_ASSET_CODE_RE = re.compile(r"^[A-Z0-9]+$")
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
    "position_source": {"account_snapshot", "archive_fixture", "paper_execution"},
    "position_mode": {"ONE_WAY", "HEDGE", "BOTH", "SINGLE", "DUAL"},
    "positionMode": {"ONE_WAY", "HEDGE", "BOTH", "SINGLE", "DUAL"},
    "margin_type": {"CROSS", "ISOLATED"},
    "product_type": {"FUTURES", "MARGIN", "SPOT"},
    "productType": {"FUTURES", "MARGIN", "SPOT"},
    "order_type": {"LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"},
    "orderType": {"LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"},
    "time_in_force": {"GTC", "IOC", "FOK", "GTX"},
    "timeInForce": {"GTC", "IOC", "FOK", "GTX"},
}
_ACCOUNT_OPEN_POSITION_STRICT_BOOL_FIELDS = (
    "reduce_only",
    "reduceOnly",
    "post_only",
    "postOnly",
    "close_position",
    "closePosition",
)
_ACCOUNT_OPEN_POSITION_TERMINAL_STATUS_VALUES = {"CLOSED", "SKIPPED", "FAILED", "CANCELLED", "CANCELED"}
_ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES = {"OPEN"}


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
    for field, allowed in _ACCOUNT_ENUM_FIELDS.items():
        if field not in account:
            continue
        value = account[field]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"account.{field} must be a canonical string: {path}")
        if value not in allowed:
            allowed_values = ", ".join(sorted(allowed))
            raise ValueError(f"account.{field} must be one of {allowed_values}: {path}")
    for field in _ACCOUNT_ASSET_CODE_FIELDS:
        if field in account:
            _require_account_asset_code(account[field], field_path=f"account.{field}", path=path)
    _validate_spot_balance_identity_fields(account, path=path)
    for field in _ACCOUNT_TIME_FIELDS:
        if field in account:
            _require_account_utc_iso_timestamp(account[field], field_path=f"account.{field}", path=path)
    _validate_account_time_order(account, path=path)
    _validate_open_position_identity_fields(account, path=path)


def _validate_account_time_order(account: dict, *, path: Path) -> None:
    event = _account_first_present_utc_timestamp(account, "event_time", "eventTime")
    update = _account_first_present_utc_timestamp(
        account,
        "last_update_time",
        "lastUpdateTime",
        "update_time",
        "updateTime",
    )
    if event is None or update is None:
        return
    event_field, event_time = event
    update_field, update_time = update
    if update_time < event_time:
        raise ValueError(f"account.{update_field} must be at or after {event_field}: {path}")


def _validate_spot_balance_identity_fields(account: dict, *, path: Path) -> None:
    if "spot" not in account:
        return
    spot = account["spot"]
    if not isinstance(spot, dict):
        raise ValueError(f"account.spot must be an object: {path}")
    if "nonzero_balances" not in spot:
        return
    balances = spot["nonzero_balances"]
    if not isinstance(balances, list):
        raise ValueError(f"account.spot.nonzero_balances must be a list: {path}")
    for index, balance in enumerate(balances):
        if type(balance) is not dict:
            raise ValueError(f"account.spot.nonzero_balances[{index}] must be an object: {path}")
        if "asset" in balance:
            _require_account_asset_code(
                balance["asset"],
                field_path=f"account.spot.nonzero_balances[{index}].asset",
                path=path,
            )


def _validate_open_position_identity_fields(account: dict, *, path: Path) -> None:
    positions = account.get("open_positions")
    if positions is None:
        return
    if type(positions) is not list:
        raise ValueError(f"account.open_positions must be a list: {path}")
    for index, position in enumerate(positions):
        if type(position) is not dict:
            raise ValueError(f"account.open_positions[{index}] must be an object: {path}")
        field_prefix = f"account.open_positions[{index}]"
        for field in _ACCOUNT_OPEN_POSITION_IDENTITY_STRING_FIELDS:
            if field in position:
                value = _require_account_canonical_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
                if field == "status" and value in _ACCOUNT_OPEN_POSITION_TERMINAL_STATUS_VALUES:
                    raise ValueError(f"{field_prefix}.{field} must not be a terminal open position state: {path}")
                if field == "status" and value not in _ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES:
                    allowed_values = ", ".join(sorted(_ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES))
                    raise ValueError(f"{field_prefix}.{field} must be one of {allowed_values} or omitted for an open position: {path}")
        for field in _ACCOUNT_OPEN_POSITION_IDENTIFIER_FIELDS:
            if field in position:
                _require_account_identifier_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
        for field in _ACCOUNT_OPEN_POSITION_PROVENANCE_IDENTIFIER_FIELDS:
            if field in position:
                _require_account_provenance_identifier_string(
                    position[field],
                    field_path=f"{field_prefix}.{field}",
                    path=path,
                )
        for field in _ACCOUNT_OPEN_POSITION_ASSET_CODE_FIELDS:
            if field in position:
                _require_account_asset_code(position[field], field_path=f"{field_prefix}.{field}", path=path)
        for field in _ACCOUNT_OPEN_POSITION_TIME_FIELDS:
            if field in position:
                _require_account_utc_iso_timestamp(position[field], field_path=f"{field_prefix}.{field}", path=path)
        _validate_open_position_time_order(position, field_prefix=field_prefix, path=path)
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
        for field in _ACCOUNT_OPEN_POSITION_STRICT_BOOL_FIELDS:
            if field in position:
                _require_account_strict_bool(position[field], field_path=f"{field_prefix}.{field}", path=path)


def _account_utc_timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _account_first_present_utc_timestamp(position: dict, *fields: str) -> tuple[str, datetime] | None:
    for field in fields:
        if field in position:
            return field, _account_utc_timestamp_value(position[field])
    return None


def _validate_open_position_time_order(position: dict, *, field_prefix: str, path: Path) -> None:
    opened = _account_first_present_utc_timestamp(position, "opened_at", "openedAt")
    if opened is not None:
        opened_field, opened_at = opened
        for fields in (
            ("updated_at", "updatedAt"),
            ("update_time", "updateTime"),
            ("as_of",),
            ("timestamp",),
            ("last_update_time", "lastUpdateTime"),
            ("event_time", "eventTime"),
            ("trade_time", "tradeTime"),
            ("close_time", "closeTime"),
            ("settlement_time", "settlementTime"),
            ("expiry_time", "expiryTime"),
        ):
            candidate = _account_first_present_utc_timestamp(position, *fields)
            if candidate is not None:
                field, value = candidate
                if value < opened_at:
                    raise ValueError(f"{field_prefix}.{field} must be at or after {opened_field}: {path}")
    close = _account_first_present_utc_timestamp(position, "close_time", "closeTime")
    closed = _account_first_present_utc_timestamp(position, "closed_at", "closedAt")
    if close is not None and closed is not None:
        close_field, close_time = close
        closed_field, closed_at = closed
        if closed_at < close_time:
            raise ValueError(f"{field_prefix}.{closed_field} must be at or after {close_field}: {path}")
    settlement = _account_first_present_utc_timestamp(position, "settlement_time", "settlementTime")
    if close is not None and settlement is not None:
        close_field, close_time = close
        settlement_field, settlement_time = settlement
        if settlement_time < close_time:
            raise ValueError(f"{field_prefix}.{settlement_field} must be at or after {close_field}: {path}")
    order = _account_first_present_utc_timestamp(position, "order_time", "orderTime")
    execution = _account_first_present_utc_timestamp(position, "execution_time", "executionTime")
    if order is not None and execution is not None:
        order_field, order_time = order
        execution_field, execution_time = execution
        if execution_time < order_time:
            raise ValueError(f"{field_prefix}.{execution_field} must be at or after {order_field}: {path}")
    fill = _account_first_present_utc_timestamp(position, "fill_time", "fillTime")
    if execution is not None and fill is not None:
        execution_field, execution_time = execution
        fill_field, fill_time = fill
        if fill_time < execution_time:
            raise ValueError(f"{field_prefix}.{fill_field} must be at or after {execution_field}: {path}")


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


def _require_account_provenance_identifier_string(value: object, *, field_path: str, path: Path) -> str:
    text = _require_account_canonical_string(value, field_path=field_path, path=path)
    if _ACCOUNT_IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{field_path} must be a canonical identifier string: {path}")
    return text


def _require_account_utc_iso_timestamp(value: object, *, field_path: str, path: Path) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{field_path} must be a canonical UTC ISO timestamp: {path}")
    if not value.endswith("Z") or "T" not in value:
        raise ValueError(f"{field_path} must be a canonical UTC ISO timestamp: {path}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_path} must be a canonical UTC ISO timestamp: {path}") from exc
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise ValueError(f"{field_path} must be a canonical UTC ISO timestamp: {path}")
    return value


def _require_account_uppercase_canonical_string(value: object, *, field_path: str, path: Path) -> str:
    text = _require_account_canonical_string(value, field_path=field_path, path=path)
    if text != text.upper():
        raise ValueError(f"{field_path} must be an uppercase canonical string: {path}")
    return text


def _require_account_asset_code(value: object, *, field_path: str, path: Path) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _ACCOUNT_ASSET_CODE_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field_path} must be an uppercase asset code: {path}")
    return value


def _require_account_strict_bool(value: object, *, field_path: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_path} must be a strict boolean: {path}")
    return value


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


def _validate_account_positive_number(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a positive finite number: {path}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{field_path} must be a positive finite number: {path}")
    return number


def _validate_account_ratio(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a ratio in (0, 1]: {path}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0 or number > 1.0:
        raise ValueError(f"{field_path} must be a ratio in (0, 1]: {path}")
    return number


def _validate_account_bounded_non_negative_ratio(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a bounded non-negative ratio strict number: {path}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{field_path} must be a bounded non-negative ratio strict number: {path}")
    return number


def _validate_account_bps(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be bounded non-negative finite strict bps: {path}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 10000.0:
        raise ValueError(f"{field_path} must be bounded non-negative finite strict bps: {path}")
    return number


def _validate_account_numeric_fields(payload: object, *, path: Path, field_path: str) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{field_path}.{key}"
            if (
                field_path.startswith("account.spot.nonzero_balances[")
                and key in _ACCOUNT_SPOT_BALANCE_NON_NEGATIVE_NUMBER_FIELDS
            ):
                _validate_account_number(
                    value,
                    field_path=child_path,
                    path=path,
                    qualifier="non-negative finite",
                    minimum=0.0,
                )
                continue
            if field_path.startswith("account.open_positions[") and key == "qty":
                _validate_account_positive_number(value, field_path=child_path, path=path)
                continue
            if field_path.startswith("account.open_positions[") and key in _ACCOUNT_OPEN_POSITION_POSITIVE_PRICE_FIELDS:
                _validate_account_positive_number(value, field_path=child_path, path=path)
                continue
            if key in _ACCOUNT_RATIO_NUMBER_FIELDS:
                _validate_account_ratio(value, field_path=child_path, path=path)
            elif key in _ACCOUNT_RISK_EXPOSURE_RATIO_FIELDS:
                _validate_account_bounded_non_negative_ratio(value, field_path=child_path, path=path)
            elif key in _ACCOUNT_RISK_EXPOSURE_BPS_FIELDS:
                _validate_account_bps(value, field_path=child_path, path=path)
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
