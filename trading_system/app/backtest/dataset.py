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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DERIVATIVE_EVIDENCE_TIMESTAMP_FIELDS = (
    "funding_timestamp",
    "open_interest_timestamp",
    "mark_price_timestamp",
    "index_price_timestamp",
)
_MARKET_EVIDENCE_TIMESTAMP_FIELDS = (
    "feature_timestamp",
    "label_timestamp",
    "regime_label_timestamp",
    "llm_label_timestamp",
    *_DERIVATIVE_EVIDENCE_TIMESTAMP_FIELDS,
)
_ACCOUNT_NON_NEGATIVE_NUMBER_FIELDS = (
    "available_balance",
    "availableBalance",
    "borrow_fee",
    "borrowFee",
    "entry",
    "entry_price",
    "commission",
    "cost",
    "cross_wallet_balance",
    "crossWalletBalance",
    "futures_wallet_balance",
    "futuresWalletBalance",
    "fee",
    "funding_fee",
    "fundingFee",
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
    "max_withdraw_amount",
    "maxWithdrawAmount",
    "margin_used",
    "marginUsed",
    "mark",
    "mark_price",
    "notional",
    "position_amt",
    "positionAmt",
    "qty",
    "realized_fee",
    "realizedFee",
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
    "realized_cost",
    "realizedCost",
    "unrealized_cost",
    "unrealizedCost",
)
_ACCOUNT_SPOT_BALANCE_NON_NEGATIVE_NUMBER_FIELDS = (
    "free",
    "locked",
    "total",
)
_ACCOUNT_BALANCE_WALLET_TOTAL_FIELDS = (
    "wallet_balance",
    "walletBalance",
)
_ACCOUNT_BALANCE_EQUAL_ALIAS_GROUPS = (
    ("crossWalletBalance", "cross_wallet_balance"),
    ("availableBalance", "available_balance"),
    ("maxWithdrawAmount", "max_withdraw_amount"),
    ("marginBalance", "margin_balance"),
    ("maintenanceMargin", "maintenance_margin"),
    ("initialMargin", "initial_margin"),
)
_ACCOUNT_BALANCE_SIGNED_EQUAL_ALIAS_GROUPS = (
    ("realizedPnl", "realized_pnl"),
    ("realizedPnl", "realizedProfit"),
)
_ACCOUNT_TOTAL_EQUAL_ALIAS_GROUPS = (
    ("futuresWalletBalance", "futures_wallet_balance"),
    ("totalWalletBalance", "total_wallet_balance"),
    ("totalInitialMargin", "total_initial_margin"),
    ("totalMaintMargin", "total_maint_margin"),
    ("totalMarginBalance", "total_margin_balance"),
)
_ACCOUNT_OPEN_POSITION_EQUAL_ALIAS_GROUPS = (
    ("collateralValue", "collateral_value"),
    ("marginUsed", "margin_used"),
    ("realizedCost", "realized_cost"),
    ("unrealizedCost", "unrealized_cost"),
)
_ACCOUNT_OPEN_POSITION_POSITIVE_PRICE_EQUAL_ALIAS_GROUPS = (
    ("liquidationPrice", "liquidation_price"),
    ("breakEvenPrice", "break_even_price"),
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
_ACCOUNT_IDENTITY_EQUAL_ALIAS_GROUPS = (
    ("accountId", "account_id"),
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
_ACCOUNT_ASSET_CODE_EQUAL_ALIAS_GROUPS = (
    ("feeAsset", "fee_asset"),
    ("feeCurrency", "fee_currency"),
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
_ACCOUNT_TIME_EQUAL_ALIAS_GROUPS = (
    ("lastUpdateTime", "last_update_time"),
    ("updateTime", "update_time"),
    ("eventTime", "event_time"),
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
    "positionStatus",
    "position_status",
    "source",
)
_ACCOUNT_OPEN_POSITION_STATUS_FIELDS = ("status", "positionStatus", "position_status")
_ACCOUNT_OPEN_POSITION_UNSAFE_STATUS_FIELDS = ("orderStatus",)
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
    "strategy_tag",
    "strategyTag",
    "intent_id",
    "intentId",
)
_ACCOUNT_OPEN_POSITION_IDENTIFIER_EQUAL_ALIAS_GROUPS = (
    ("positionId", "position_id"),
    ("tradeId", "trade_id"),
    ("executionId", "execution_id"),
    ("fillId", "fill_id"),
    ("orderId", "order_id"),
    ("clientOrderId", "client_order_id"),
    ("strategyId", "strategy_id"),
    ("setupId", "setup_id"),
    ("batchId", "batch_id"),
    ("sourceId", "source_id"),
    ("correlationId", "correlation_id"),
    ("parentOrderId", "parent_order_id"),
    ("exchangeOrderId", "exchange_order_id"),
    ("strategyTag", "strategy_tag"),
    ("intentId", "intent_id"),
)
_ACCOUNT_OPEN_POSITION_PROVENANCE_IDENTIFIER_FIELDS = (
    "signal_source",
    "signalSource",
    "strategy_source",
    "strategySource",
    "data_source",
    "dataSource",
)
_ACCOUNT_OPEN_POSITION_SOURCE_EQUAL_ALIAS_GROUPS = (
    ("positionSource", "position_source"),
    ("signalSource", "signal_source"),
    ("strategySource", "strategy_source"),
    ("dataSource", "data_source"),
    ("marginType", "margin_type"),
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
    "commission_asset",
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
_ACCOUNT_OPEN_POSITION_ASSET_CODE_EQUAL_ALIAS_GROUPS = (
    ("collateralAsset", "collateral_asset"),
    ("collateralCurrency", "collateral_currency"),
    ("feeAsset", "fee_asset"),
    ("feeCurrency", "fee_currency"),
    ("commissionAsset", "commission_asset"),
    ("fundingCurrency", "funding_currency"),
    ("pnlCurrency", "pnl_currency"),
    ("costCurrency", "cost_currency"),
)
_ACCOUNT_OPEN_POSITION_TIME_FIELDS = (
    "opened_at",
    "openedAt",
    "created_at",
    "createdAt",
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
_ACCOUNT_OPEN_POSITION_TIME_EQUAL_ALIAS_GROUPS = (
    ("openedAt", "opened_at"),
    ("updatedAt", "updated_at"),
    ("updateTime", "update_time"),
    ("closedAt", "closed_at"),
    ("lastUpdateTime", "last_update_time"),
    ("eventTime", "event_time"),
    ("tradeTime", "trade_time"),
    ("executionTime", "execution_time"),
    ("fillTime", "fill_time"),
    ("orderTime", "order_time"),
    ("closeTime", "close_time"),
    ("expiryTime", "expiry_time"),
    ("settlementTime", "settlement_time"),
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
_ACCOUNT_OPEN_POSITION_VENUE_EXCHANGE_ALIAS_GROUPS = (
    ("venue", "exchange"),
)
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
_ACCOUNT_OPEN_ORDER_POSITION_REFERENCE_FIELDS = (
    "position_id",
    "positionId",
)
_ACCOUNT_OPEN_ORDER_STRICT_BOOL_FIELDS = (
    "reduce_only",
    "reduceOnly",
    "close_position",
    "closePosition",
)
_ACCOUNT_OPEN_ORDER_POSITION_RECONCILIATION_BOOL_FIELDS = (
    "reduce_only",
    "reduceOnly",
    "close_position",
    "closePosition",
)
_ACCOUNT_OPEN_ORDER_SYMBOL_FIELDS = ("symbol",)
_ACCOUNT_OPEN_ORDER_SIDE_FIELDS = ("side", "orderSide")
_ACCOUNT_OPEN_ORDER_QTY_FIELDS = ("qty", "quantity", "origQty", "orig_qty")
_ACCOUNT_OPEN_ORDER_CREATED_TIME_FIELDS = ("created_at", "createdAt", "create_time", "createTime", "time")
_ACCOUNT_OPEN_ORDER_UPDATED_TIME_FIELDS = ("updated_at", "updatedAt", "update_time", "updateTime")
_ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS = ("filled_at", "filledAt", "fill_time", "fillTime")
_ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS = ("canceled_at", "canceledAt", "cancel_time", "cancelTime")
_ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS = ("expired_at", "expiredAt", "expire_time", "expireTime")
_ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS = ("rejected_at", "rejectedAt", "reject_time", "rejectTime")
_ACCOUNT_OPEN_ORDER_LIFECYCLE_TIME_FIELDS = (
    *_ACCOUNT_OPEN_ORDER_CREATED_TIME_FIELDS,
    *_ACCOUNT_OPEN_ORDER_UPDATED_TIME_FIELDS,
    *_ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
    *_ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
    *_ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
    *_ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
)
_ACCOUNT_OPEN_ORDER_CREATED_COUNTER_FIELDS = (
    "created_at_counter",
    "createdAtCounter",
    "create_time_counter",
    "createTimeCounter",
    "time_counter",
    "timeCounter",
)
_ACCOUNT_OPEN_ORDER_UPDATED_COUNTER_FIELDS = (
    "updated_at_counter",
    "updatedAtCounter",
    "update_time_counter",
    "updateTimeCounter",
)
_ACCOUNT_OPEN_ORDER_FILLED_EVENT_COUNTER_FIELDS = (
    "filled_at_counter",
    "filledAtCounter",
    "fill_time_counter",
    "fillTimeCounter",
)
_ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS = (
    "canceled_at_counter",
    "canceledAtCounter",
    "cancel_time_counter",
    "cancelTimeCounter",
)
_ACCOUNT_OPEN_ORDER_EXPIRED_EVENT_COUNTER_FIELDS = (
    "expired_at_counter",
    "expiredAtCounter",
    "expire_time_counter",
    "expireTimeCounter",
)
_ACCOUNT_OPEN_ORDER_REJECTED_EVENT_COUNTER_FIELDS = (
    "rejected_at_counter",
    "rejectedAtCounter",
    "reject_time_counter",
    "rejectTimeCounter",
)
_ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS = ("filled_qty", "filledQty", "executed_qty", "executedQty")
_ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS = ("canceled_qty", "canceledQty")
_ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS = ("expired_qty", "expiredQty")
_ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS = ("rejected_qty", "rejectedQty")
_ACCOUNT_OPEN_ORDER_LIFECYCLE_COUNTER_FIELDS = (
    *_ACCOUNT_OPEN_ORDER_CREATED_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_UPDATED_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_FILLED_EVENT_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_EXPIRED_EVENT_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_REJECTED_EVENT_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS,
    *_ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS,
)
_ACCOUNT_OPEN_ORDER_TERMINAL_STATUS_EVIDENCE = {
    "FILLED": (
        _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
        (*_ACCOUNT_OPEN_ORDER_FILLED_EVENT_COUNTER_FIELDS, *_ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS),
        "filled_at or fill_time",
    ),
    "CANCELED": (
        _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        (*_ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS, *_ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS),
        "canceled_at or cancel_time",
    ),
    "CANCELLED": (
        _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        (*_ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS, *_ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS),
        "canceled_at or cancel_time",
    ),
    "EXPIRED": (
        _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
        (*_ACCOUNT_OPEN_ORDER_EXPIRED_EVENT_COUNTER_FIELDS, *_ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS),
        "expired_at or expire_time",
    ),
    "REJECTED": (
        _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
        (*_ACCOUNT_OPEN_ORDER_REJECTED_EVENT_COUNTER_FIELDS, *_ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS),
        "rejected_at or reject_time",
    ),
}
_ACCOUNT_OPEN_ORDER_ACTIVE_STATUS_VALUES = {"NEW", "OPEN", "PENDING", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_PENDING"}
_ACCOUNT_OPEN_ORDER_CANONICAL_ACTIVE_STATUS_VALUES = {"new", "accepted", "partially_filled", "cancel_pending"}
_ACCOUNT_OPEN_ORDER_CANONICAL_TERMINAL_STATUS_VALUES = {"filled", "canceled", "rejected", "expired"}
_ACCOUNT_OPEN_ORDER_KNOWN_STATUS_VALUES = (
    _ACCOUNT_OPEN_ORDER_CANONICAL_ACTIVE_STATUS_VALUES
    | _ACCOUNT_OPEN_ORDER_CANONICAL_TERMINAL_STATUS_VALUES
    | _ACCOUNT_OPEN_ORDER_ACTIVE_STATUS_VALUES
    | set(_ACCOUNT_OPEN_ORDER_TERMINAL_STATUS_EVIDENCE)
)
_ACCOUNT_OPEN_POSITION_TERMINAL_STATUS_VALUES = {"CLOSED", "SKIPPED", "FAILED", "CANCELLED", "CANCELED", "FILLED"}
_ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES = {"OPEN"}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _instrument_optional_timestamp(value: object, *, field: str, path: Path) -> datetime:
    text = _instrument_canonical_string(value, field=field, path=path)
    try:
        parsed = _parse_timestamp(text)
    except ValueError as exc:
        raise ValueError(f"instrument {field} must be a canonical UTC ISO timestamp: {path}") from exc
    if _canonical_utc_timestamp(parsed) != text:
        raise ValueError(f"instrument {field} must be a canonical UTC ISO timestamp: {path}")
    return parsed


def _instrument_positive_float(value: object, *, field: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"instrument {field} must be a positive finite number: {path}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"instrument {field} must be a positive finite number: {path}")
    return number


def _instrument_rows(bundle_path: Path, *, decision_timestamp: datetime) -> tuple[InstrumentSnapshotRow, ...]:
    path = bundle_path / _INSTRUMENT_SNAPSHOT_FILENAME
    if not path.exists():
        return ()

    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"dataset bundle has invalid instrument snapshot: {path}")
    if "as_of" not in payload:
        raise ValueError(f"instrument_snapshot.json as_of is required: {path}")
    _payload_as_of_at_or_before_decision(
        payload,
        file_name=_INSTRUMENT_SNAPSHOT_FILENAME,
        decision_timestamp=decision_timestamp,
        path=path,
    )
    snapshot_as_of = _parse_timestamp(payload["as_of"])
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
        if "lifecycle_status" not in raw_row:
            raise ValueError(f"instrument lifecycle_status must be present: {path}")
        lifecycle_status = _instrument_canonical_string(raw_row["lifecycle_status"], field="lifecycle_status", path=path)
        if lifecycle_status not in {"listed", "delisted", "renamed", "contract_migrated"}:
            raise ValueError(f"dataset bundle has invalid instrument lifecycle_status: {path}")
        delisted_at = None
        if lifecycle_status == "delisted":
            if "delisted_at" not in raw_row:
                raise ValueError(f"instrument delisted_at must be present for delisted: {path}")
            delisted_at = _instrument_optional_timestamp(raw_row["delisted_at"], field="delisted_at", path=path)
        previous_symbol = None
        renamed_at = None
        if lifecycle_status == "renamed":
            if "previous_symbol" not in raw_row:
                raise ValueError(f"instrument previous_symbol must be present for renamed: {path}")
            if "renamed_at" not in raw_row:
                raise ValueError(f"instrument renamed_at must be present for renamed: {path}")
            previous_symbol = _instrument_canonical_string(raw_row["previous_symbol"], field="previous_symbol", path=path)
            renamed_at = _instrument_optional_timestamp(raw_row["renamed_at"], field="renamed_at", path=path)
        contract_migration = None
        if lifecycle_status == "contract_migrated":
            if not isinstance(raw_row.get("contract_migration"), dict):
                raise ValueError(f"instrument contract_migration must be an object for contract_migrated: {path}")
            contract_migration = dict(raw_row["contract_migration"])
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
                snapshot_as_of=snapshot_as_of,
                lifecycle_status=lifecycle_status,
                delisted_at=delisted_at,
                previous_symbol=previous_symbol,
                renamed_at=renamed_at,
                contract_migration=contract_migration,
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
    if key not in metadata:
        if key == "timestamp":
            raise ValueError("metadata.timestamp is required before timestamped evidence can be loaded")
        raise ValueError(f"metadata.{key} must be a canonical string")
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


def _payload_as_of_at_or_before_decision(
    payload: dict,
    *,
    file_name: str,
    decision_timestamp: datetime,
    path: Path,
) -> None:
    if "as_of" not in payload:
        return
    as_of = payload["as_of"]
    if not isinstance(as_of, str) or not as_of or as_of != as_of.strip():
        raise ValueError(f"{file_name} as_of must be a canonical UTC ISO timestamp: {path}")
    try:
        parsed_as_of = _parse_timestamp(as_of)
    except ValueError as exc:
        raise ValueError(f"{file_name} as_of must be a canonical UTC ISO timestamp: {path}") from exc
    if _canonical_utc_timestamp(parsed_as_of) != as_of:
        raise ValueError(f"{file_name} as_of must be a canonical UTC ISO timestamp: {path}")
    if parsed_as_of > decision_timestamp:
        raise ValueError(
            f"{file_name} as_of must be at or before metadata.timestamp: "
            f"as_of={as_of} metadata.timestamp={_canonical_utc_timestamp(decision_timestamp)} path={path}"
        )


def _require_evidence_timestamp_at_or_before_decision(
    value: object,
    *,
    evidence_path: str,
    file_name: str,
    decision_timestamp: datetime,
    path: Path,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{file_name} {evidence_path} must be a canonical UTC ISO timestamp: {path}")
    if not value.endswith("Z") or "T" not in value:
        raise ValueError(f"{file_name} {evidence_path} must be a canonical UTC ISO timestamp: {path}")
    try:
        parsed = _parse_timestamp(value)
    except ValueError as exc:
        raise ValueError(f"{file_name} {evidence_path} must be a canonical UTC ISO timestamp: {path}") from exc
    if _canonical_utc_timestamp(parsed) != value:
        raise ValueError(f"{file_name} {evidence_path} must be a canonical UTC ISO timestamp: {path}")
    if parsed > decision_timestamp:
        raise ValueError(
            f"{file_name} {evidence_path} must be at or before metadata.timestamp: "
            f"{evidence_path}={value} metadata.timestamp={_canonical_utc_timestamp(decision_timestamp)} path={path}"
        )
    return value


def _validate_market_evidence_timestamps(
    payload: object,
    *,
    file_name: str,
    decision_timestamp: datetime,
    path: Path,
    evidence_path: str = "",
) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = key if not evidence_path else f"{evidence_path}.{key}"
            if key in _MARKET_EVIDENCE_TIMESTAMP_FIELDS:
                _require_evidence_timestamp_at_or_before_decision(
                    value,
                    evidence_path=child_path,
                    file_name=file_name,
                    decision_timestamp=decision_timestamp,
                    path=path,
                )
                continue
            if isinstance(value, (dict, list)):
                _validate_market_evidence_timestamps(
                    value,
                    file_name=file_name,
                    decision_timestamp=decision_timestamp,
                    path=path,
                    evidence_path=child_path,
                )
        return
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                child_path = f"{evidence_path}[{index}]" if evidence_path else f"[{index}]"
                _validate_market_evidence_timestamps(
                    item,
                    file_name=file_name,
                    decision_timestamp=decision_timestamp,
                    path=path,
                    evidence_path=child_path,
                )


def _validate_derivative_evidence_timestamps(
    rows: list[dict],
    *,
    decision_timestamp: datetime,
    path: Path,
) -> None:
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows):
        symbol = row.get("symbol")
        if symbol is not None and (not isinstance(symbol, str) or not symbol or symbol != symbol.strip()):
            raise ValueError(f"derivatives_snapshot.json rows[{index}].symbol must be a canonical string: {path}")
        for field in _DERIVATIVE_EVIDENCE_TIMESTAMP_FIELDS:
            if field not in row:
                continue
            timestamp = _require_evidence_timestamp_at_or_before_decision(
                row[field],
                evidence_path=f"rows[{index}].{field}",
                file_name="derivatives_snapshot.json",
                decision_timestamp=decision_timestamp,
                path=path,
            )
            if symbol is None:
                continue
            identity = (symbol, field, timestamp)
            if identity in seen:
                raise ValueError(
                    "duplicate derivatives timestamp identity: "
                    f"symbol={symbol} field={field} timestamp={timestamp} path={path}"
                )
            seen.add(identity)


def _account_snapshot(account: dict, *, path: Path, decision_timestamp: datetime | None = None) -> dict:
    snapshot = dict(account)
    if decision_timestamp is not None:
        _payload_as_of_at_or_before_decision(
            snapshot,
            file_name=path.name,
            decision_timestamp=decision_timestamp,
            path=path,
        )
    validate_account_snapshot_identity(snapshot, path=path)
    _validate_account_numeric_fields(snapshot, path=path, field_path="account")
    return snapshot


def validate_account_snapshot_identity(account: object, *, path: Path) -> None:
    if not isinstance(account, dict):
        raise ValueError(f"dataset bundle has invalid account snapshot: {path}")
    for field in _ACCOUNT_IDENTITY_STRING_FIELDS:
        if field not in account:
            continue
        _require_account_identifier_string(account[field], field_path=f"account.{field}", path=path)
    _validate_account_string_alias_parity(
        account,
        field_path="account",
        path=path,
        alias_groups=_ACCOUNT_IDENTITY_EQUAL_ALIAS_GROUPS,
    )
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
    _validate_account_asset_code_alias_parity(
        account,
        field_path="account",
        path=path,
        alias_groups=_ACCOUNT_ASSET_CODE_EQUAL_ALIAS_GROUPS,
    )
    _validate_spot_balance_identity_fields(account, path=path)
    for field in _ACCOUNT_TIME_FIELDS:
        if field in account:
            _require_account_utc_iso_timestamp(account[field], field_path=f"account.{field}", path=path)
    _validate_account_timestamp_alias_parity(
        account,
        field_path="account",
        path=path,
        alias_groups=_ACCOUNT_TIME_EQUAL_ALIAS_GROUPS,
    )
    _validate_account_time_order(account, path=path)
    _validate_open_position_identity_fields(account, path=path)
    _validate_open_order_position_reconciliation(account, path=path)


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
        for field in _ACCOUNT_OPEN_POSITION_UNSAFE_STATUS_FIELDS:
            if field in position:
                raise ValueError(f"{field_prefix}.{field} is not an open position status field: {path}")
        status_values: list[tuple[str, str]] = []
        for field in _ACCOUNT_OPEN_POSITION_IDENTITY_STRING_FIELDS:
            if field in position:
                value = _require_account_canonical_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
                if field in _ACCOUNT_OPEN_POSITION_STATUS_FIELDS and value in _ACCOUNT_OPEN_POSITION_TERMINAL_STATUS_VALUES:
                    raise ValueError(f"{field_prefix}.{field} must not be a terminal open position state: {path}")
                if field in _ACCOUNT_OPEN_POSITION_STATUS_FIELDS:
                    status_values.append((field, value))
        if status_values:
            first_status_field, first_status_value = status_values[0]
            for field, value in status_values[1:]:
                if value != first_status_value:
                    raise ValueError(f"{field_prefix}.{field} must equal {first_status_field}: {path}")
            for field, value in status_values:
                if value not in _ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES:
                    allowed_values = ", ".join(sorted(_ACCOUNT_OPEN_POSITION_OPEN_STATUS_VALUES))
                    raise ValueError(f"{field_prefix}.{field} must be one of {allowed_values} or omitted for an open position: {path}")
        for field in _ACCOUNT_OPEN_POSITION_IDENTIFIER_FIELDS:
            if field in position:
                _require_account_identifier_string(position[field], field_path=f"{field_prefix}.{field}", path=path)
        _validate_account_string_alias_parity(
            position,
            field_path=field_prefix,
            path=path,
            alias_groups=_ACCOUNT_OPEN_POSITION_IDENTIFIER_EQUAL_ALIAS_GROUPS,
        )
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
        _validate_account_asset_code_alias_parity(
            position,
            field_path=field_prefix,
            path=path,
            alias_groups=_ACCOUNT_OPEN_POSITION_ASSET_CODE_EQUAL_ALIAS_GROUPS,
        )
        for field in _ACCOUNT_OPEN_POSITION_TIME_FIELDS:
            if field in position:
                _require_account_utc_iso_timestamp(position[field], field_path=f"{field_prefix}.{field}", path=path)
        _validate_account_timestamp_alias_parity(
            position,
            field_path=field_prefix,
            path=path,
            alias_groups=_ACCOUNT_OPEN_POSITION_TIME_EQUAL_ALIAS_GROUPS,
        )
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
        _validate_account_string_alias_parity(
            position,
            field_path=field_prefix,
            path=path,
            alias_groups=_ACCOUNT_OPEN_POSITION_VENUE_EXCHANGE_ALIAS_GROUPS,
        )
        _validate_account_string_alias_parity(
            position,
            field_path=field_prefix,
            path=path,
            alias_groups=_ACCOUNT_OPEN_POSITION_SOURCE_EQUAL_ALIAS_GROUPS,
        )
        for field in _ACCOUNT_OPEN_POSITION_STRICT_BOOL_FIELDS:
            if field in position:
                _require_account_strict_bool(position[field], field_path=f"{field_prefix}.{field}", path=path)


def _account_first_present_value(payload: dict, fields: tuple[str, ...]) -> tuple[str, object] | None:
    for field in fields:
        if field in payload:
            return field, payload[field]
    return None


def _account_position_identity_key(position: dict, *, index: int, path: Path) -> tuple[str, object] | None:
    position_id = _account_first_present_value(position, _ACCOUNT_OPEN_ORDER_POSITION_REFERENCE_FIELDS)
    if position_id is not None:
        field, value = position_id
        identifier = _require_account_identifier_string(
            value,
            field_path=f"account.open_positions[{index}].{field}",
            path=path,
        )
        return "position_id", identifier
    symbol = _account_first_present_value(position, _ACCOUNT_OPEN_ORDER_SYMBOL_FIELDS)
    if symbol is None:
        return None
    field, value = symbol
    return "symbol", _require_account_uppercase_canonical_string(
        value,
        field_path=f"account.open_positions[{index}].{field}",
        path=path,
    )


def _account_order_identity_key(order: dict, *, index: int, path: Path) -> tuple[str, object] | None:
    position_id = _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_POSITION_REFERENCE_FIELDS)
    if position_id is not None:
        field, value = position_id
        identifier = _require_account_identifier_string(
            value,
            field_path=f"account.open_orders[{index}].{field}",
            path=path,
        )
        return "position_id", identifier
    symbol = _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_SYMBOL_FIELDS)
    if symbol is None:
        return None
    field, value = symbol
    return "symbol", _require_account_uppercase_canonical_string(
        value,
        field_path=f"account.open_orders[{index}].{field}",
        path=path,
    )


def _account_order_has_position_reconciliation_evidence(order: dict) -> bool:
    if _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_POSITION_RECONCILIATION_BOOL_FIELDS) is not None:
        return True
    return _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_POSITION_REFERENCE_FIELDS) is not None


def _account_side_for_position(position: dict, *, index: int, path: Path) -> str | None:
    side = _account_first_present_value(position, ("side", "positionSide"))
    if side is None:
        return None
    field, value = side
    side_text = _require_account_canonical_string(
        value,
        field_path=f"account.open_positions[{index}].{field}",
        path=path,
    )
    if side_text not in {"LONG", "SHORT"}:
        raise ValueError(f"account.open_positions[{index}].{field} must be one of LONG, SHORT: {path}")
    return side_text


def _account_side_for_order(order: dict, *, index: int, path: Path) -> str | None:
    side = _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_SIDE_FIELDS)
    if side is None:
        return None
    field, value = side
    side_text = _require_account_canonical_string(value, field_path=f"account.open_orders[{index}].{field}", path=path)
    if side_text not in {"BUY", "SELL", "LONG", "SHORT"}:
        raise ValueError(f"account.open_orders[{index}].{field} must be one of BUY, LONG, SELL, SHORT: {path}")
    return side_text


def _account_order_reduce_only(order: dict, *, index: int, path: Path) -> bool:
    reduce_only = False
    for field in _ACCOUNT_OPEN_ORDER_STRICT_BOOL_FIELDS:
        if field not in order:
            continue
        value = _require_account_strict_bool(order[field], field_path=f"account.open_orders[{index}].{field}", path=path)
        reduce_only = reduce_only or value
    return reduce_only


def _account_order_qty(order: dict, *, index: int, path: Path) -> float | None:
    qty = _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_QTY_FIELDS)
    if qty is None:
        return None
    field, value = qty
    return _validate_account_positive_number(value, field_path=f"account.open_orders[{index}].{field}", path=path)


def _validate_account_non_negative_number(value: object, *, field_path: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_path} must be a non-negative finite number: {path}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{field_path} must be a non-negative finite number: {path}")
    return number


def _account_order_lifecycle_number(
    order: dict,
    fields: tuple[str, ...],
    *,
    field_prefix: str,
    path: Path,
) -> tuple[str, float] | None:
    found = _account_first_present_value(order, fields)
    if found is None:
        return None
    field, value = found
    return field, _validate_account_non_negative_number(value, field_path=f"{field_prefix}.{field}", path=path)


def _account_order_lifecycle_timestamp(
    order: dict,
    fields: tuple[str, ...],
    *,
    field_prefix: str,
    path: Path,
) -> tuple[str, datetime] | None:
    found = _account_first_present_value(order, fields)
    if found is None:
        return None
    field, value = found
    timestamp = _require_account_utc_iso_timestamp(value, field_path=f"{field_prefix}.{field}", path=path)
    return field, _parse_timestamp(timestamp)


def _account_order_has_lifecycle_evidence(order: dict, fields: tuple[str, ...]) -> bool:
    return _account_first_present_value(order, fields) is not None


def _account_order_status_kind(status_text: str) -> str:
    if status_text in _ACCOUNT_OPEN_ORDER_CANONICAL_ACTIVE_STATUS_VALUES:
        return "active"
    if status_text in _ACCOUNT_OPEN_ORDER_CANONICAL_TERMINAL_STATUS_VALUES:
        return "terminal"
    upper = status_text.upper()
    if upper in _ACCOUNT_OPEN_ORDER_ACTIVE_STATUS_VALUES:
        return "active"
    if upper in _ACCOUNT_OPEN_ORDER_TERMINAL_STATUS_EVIDENCE:
        return "terminal"
    return "unknown"


def _account_order_terminal_evidence_groups(status_text: str) -> tuple[tuple[str, ...], tuple[str, ...], str] | None:
    if status_text in _ACCOUNT_OPEN_ORDER_CANONICAL_TERMINAL_STATUS_VALUES:
        if status_text == "filled":
            return _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS, _ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS, "filled_at or fill_time"
        if status_text == "canceled":
            return _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS, _ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS, "canceled_at or cancel_time"
        if status_text == "expired":
            return _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS, _ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS, "expired_at or expire_time"
        if status_text == "rejected":
            return _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS, _ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS, "rejected_at or reject_time"
    return _ACCOUNT_OPEN_ORDER_TERMINAL_STATUS_EVIDENCE.get(status_text.upper())


def _validate_order_timestamps_at_or_before_account_as_of(
    order: dict,
    *,
    account_as_of: tuple[str, datetime] | None,
    field_prefix: str,
    path: Path,
) -> None:
    if account_as_of is None:
        return
    account_as_of_field, account_as_of_value = account_as_of
    for fields in (
        _ACCOUNT_OPEN_ORDER_CREATED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_UPDATED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
    ):
        timestamp = _account_order_lifecycle_timestamp(order, fields, field_prefix=field_prefix, path=path)
        if timestamp is None:
            continue
        timestamp_field, timestamp_value = timestamp
        if timestamp_value > account_as_of_value:
            raise ValueError(f"{field_prefix}.{timestamp_field} must be at or before account.{account_as_of_field}: {path}")


def _validate_open_order_lifecycle(order: dict, *, field_prefix: str, path: Path) -> None:
    for field in _ACCOUNT_OPEN_ORDER_LIFECYCLE_TIME_FIELDS:
        if field in order:
            _require_account_utc_iso_timestamp(order[field], field_path=f"{field_prefix}.{field}", path=path)
    for field in _ACCOUNT_OPEN_ORDER_LIFECYCLE_COUNTER_FIELDS:
        if field in order:
            _validate_account_non_negative_number(order[field], field_path=f"{field_prefix}.{field}", path=path)

    created = _account_order_lifecycle_timestamp(
        order,
        _ACCOUNT_OPEN_ORDER_CREATED_TIME_FIELDS,
        field_prefix=field_prefix,
        path=path,
    )
    updated = _account_order_lifecycle_timestamp(
        order,
        _ACCOUNT_OPEN_ORDER_UPDATED_TIME_FIELDS,
        field_prefix=field_prefix,
        path=path,
    )
    if created is not None and updated is not None:
        created_field, created_at = created
        updated_field, updated_at = updated
        if updated_at < created_at:
            raise ValueError(f"{field_prefix}.{updated_field} must be at or after {created_field}: {path}")

    lower_bound = updated if updated is not None else created
    if lower_bound is not None:
        lower_field, lower_value = lower_bound
        for fields in (
            _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
        ):
            terminal = _account_order_lifecycle_timestamp(order, fields, field_prefix=field_prefix, path=path)
            if terminal is None:
                continue
            terminal_field, terminal_value = terminal
            if terminal_value < lower_value:
                raise ValueError(f"{field_prefix}.{terminal_field} must be at or after {lower_field}: {path}")

    created_counter = _account_order_lifecycle_number(
        order,
        _ACCOUNT_OPEN_ORDER_CREATED_COUNTER_FIELDS,
        field_prefix=field_prefix,
        path=path,
    )
    updated_counter = _account_order_lifecycle_number(
        order,
        _ACCOUNT_OPEN_ORDER_UPDATED_COUNTER_FIELDS,
        field_prefix=field_prefix,
        path=path,
    )
    if created_counter is not None and updated_counter is not None:
        created_field, created_value = created_counter
        updated_field, updated_value = updated_counter
        if updated_value < created_value:
            raise ValueError(f"{field_prefix}.{updated_field} must be at or after {created_field}: {path}")

    counter_lower_bound = updated_counter if updated_counter is not None else created_counter
    if counter_lower_bound is not None:
        lower_field, lower_value = counter_lower_bound
        for fields in (
            _ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_FILLED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS,
        ):
            terminal = _account_order_lifecycle_number(order, fields, field_prefix=field_prefix, path=path)
            if terminal is None:
                continue
            terminal_field, terminal_value = terminal
            if terminal_value < lower_value:
                raise ValueError(f"{field_prefix}.{terminal_field} must be at or after {lower_field}: {path}")

    status = _account_first_present_value(order, ("status", "order_status", "orderStatus", "state"))
    if status is None:
        return
    status_field, status_value = status
    status_text = _require_account_canonical_string(status_value, field_path=f"{field_prefix}.{status_field}", path=path)
    if status_text not in _ACCOUNT_OPEN_ORDER_KNOWN_STATUS_VALUES and status_text.upper() not in _ACCOUNT_OPEN_ORDER_KNOWN_STATUS_VALUES:
        raise ValueError(f"{field_prefix}.{status_field} must be a known fail-closed order lifecycle state: {path}")

    if created is None:
        raise ValueError(f"{field_prefix} requires created_at or create_time: {path}")
    if updated is None:
        raise ValueError(f"{field_prefix} requires updated_at or update_time: {path}")

    status_kind = _account_order_status_kind(status_text)
    if status_kind == "active":
        status_allows_fill_evidence = status_text in {"partially_filled", "cancel_pending"} or status_text.upper() in {
            "PARTIALLY_FILLED",
            "CANCEL_PENDING",
        }
        if _account_order_has_lifecycle_evidence(order, _ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS) and _account_order_has_lifecycle_evidence(
            order, _ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS
        ):
            if _account_first_present_value(order, _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS) is None or _account_first_present_value(
                order, _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS
            ) is None:
                raise ValueError(f"{field_prefix} has ambiguous fill/cancel evidence without event timestamps: {path}")
        for fields in (
            _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
            _ACCOUNT_OPEN_ORDER_CANCELED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_EVENT_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_CANCELED_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_EXPIRED_COUNTER_FIELDS,
            _ACCOUNT_OPEN_ORDER_REJECTED_COUNTER_FIELDS,
        ):
            if fields in (
                _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
                _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
                _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
            ):
                terminal = _account_order_lifecycle_timestamp(order, fields, field_prefix=field_prefix, path=path)
            else:
                terminal = _account_order_lifecycle_number(order, fields, field_prefix=field_prefix, path=path)
            if terminal is not None:
                terminal_field, _ = terminal
                raise ValueError(f"{field_prefix}.{terminal_field} must be omitted for active status: {path}")
        if not status_allows_fill_evidence:
            for fields in (
                _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
                _ACCOUNT_OPEN_ORDER_FILLED_EVENT_COUNTER_FIELDS,
                _ACCOUNT_OPEN_ORDER_FILLED_COUNTER_FIELDS,
            ):
                if fields == _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS:
                    terminal = _account_order_lifecycle_timestamp(order, fields, field_prefix=field_prefix, path=path)
                else:
                    terminal = _account_order_lifecycle_number(order, fields, field_prefix=field_prefix, path=path)
                if terminal is not None:
                    terminal_field, _ = terminal
                    raise ValueError(f"{field_prefix}.{terminal_field} must be omitted for active status: {path}")
        return
    terminal = _account_order_terminal_evidence_groups(status_text)
    if terminal is None:
        return
    time_fields, counter_fields, evidence_label = terminal
    if _account_first_present_value(order, time_fields) is None and _account_first_present_value(order, counter_fields) is None:
        raise ValueError(f"{field_prefix}.{status_field} requires {evidence_label}: {path}")
    terminal_time_fields_by_status = {
        "filled": _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
        "FILLED": _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
        "canceled": _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        "CANCELED": _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        "CANCELLED": _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        "expired": _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
        "EXPIRED": _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
        "rejected": _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
        "REJECTED": _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
    }
    expected_time_fields = terminal_time_fields_by_status.get(status_text) or terminal_time_fields_by_status.get(status_text.upper())
    for fields in (
        _ACCOUNT_OPEN_ORDER_FILLED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_CANCELED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_EXPIRED_TIME_FIELDS,
        _ACCOUNT_OPEN_ORDER_REJECTED_TIME_FIELDS,
    ):
        if fields == expected_time_fields:
            continue
        contradictory = _account_first_present_value(order, fields)
        if contradictory is not None:
            contradictory_field, _ = contradictory
            raise ValueError(f"{field_prefix}.{contradictory_field} contradicts terminal status {status_text}: {path}")


def _account_position_qty(position: dict, *, index: int, path: Path) -> float | None:
    if "qty" not in position:
        return None
    return _validate_account_positive_number(
        position["qty"],
        field_path=f"account.open_positions[{index}].qty",
        path=path,
    )


def _order_side_reduces_position(order_side: str, position_side: str) -> bool:
    if position_side == "LONG":
        return order_side in {"SELL", "SHORT"}
    if position_side == "SHORT":
        return order_side in {"BUY", "LONG"}
    return False


def _validate_open_order_position_reconciliation(account: dict, *, path: Path) -> None:
    orders = account.get("open_orders")
    if orders is None:
        return
    if type(orders) is not list:
        raise ValueError(f"account.open_orders must be a list: {path}")
    positions = account.get("open_positions")
    if positions is None:
        positions = []
    if type(positions) is not list:
        return
    account_as_of = _account_first_present_utc_timestamp(account, "as_of", "last_update_time", "lastUpdateTime", "update_time", "updateTime")

    positions_by_key: dict[tuple[str, object], tuple[int, dict]] = {}
    for position_index, position in enumerate(positions):
        if type(position) is not dict:
            continue
        key = _account_position_identity_key(position, index=position_index, path=path)
        if key is not None:
            positions_by_key.setdefault(key, (position_index, position))

    for order_index, order in enumerate(orders):
        if type(order) is not dict:
            raise ValueError(f"account.open_orders[{order_index}] must be an object: {path}")
        field_prefix = f"account.open_orders[{order_index}]"
        _validate_open_order_lifecycle(order, field_prefix=field_prefix, path=path)
        _validate_order_timestamps_at_or_before_account_as_of(
            order,
            account_as_of=account_as_of,
            field_prefix=field_prefix,
            path=path,
        )
        reduce_only = _account_order_reduce_only(order, index=order_index, path=path)
        if not _account_order_has_position_reconciliation_evidence(order):
            continue
        key = _account_order_identity_key(order, index=order_index, path=path)
        if key is None:
            continue
        position_match = positions_by_key.get(key)
        if position_match is None:
            raise ValueError(f"account.open_orders[{order_index}] references nonexistent open position: {path}")
        if not reduce_only:
            continue
        position_index, position = position_match
        position_side = _account_side_for_position(position, index=position_index, path=path)
        order_side = _account_side_for_order(order, index=order_index, path=path)
        if position_side is not None and order_side is not None and not _order_side_reduces_position(order_side, position_side):
            raise ValueError(
                f"account.open_orders[{order_index}].side must reduce account.open_positions[{position_index}]: {path}"
            )
        order_qty = _account_order_qty(order, index=order_index, path=path)
        position_qty = _account_position_qty(position, index=position_index, path=path)
        if order_qty is not None and position_qty is not None and order_qty > position_qty:
            raise ValueError(
                f"account.open_orders[{order_index}].qty must not exceed account.open_positions[{position_index}].qty: {path}"
            )


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
            ("order_time", "orderTime"),
            ("execution_time", "executionTime"),
            ("fill_time", "fillTime"),
            ("close_time", "closeTime"),
            ("closed_at", "closedAt"),
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
    expiry = _account_first_present_utc_timestamp(position, "expiry_time", "expiryTime")
    if expiry is not None and settlement is not None:
        expiry_field, expiry_time = expiry
        settlement_field, settlement_time = settlement
        if settlement_time < expiry_time:
            raise ValueError(f"{field_prefix}.{settlement_field} must be at or after {expiry_field}: {path}")
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
        type(value) is not str
        or not value
        or value != value.strip()
        or _ACCOUNT_ASSET_CODE_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field_path} must be an uppercase asset code: {path}")
    return value


def _validate_account_asset_code_alias_parity(
    payload: dict,
    *,
    field_path: str,
    path: Path,
    alias_groups: tuple[tuple[str, str], ...],
) -> None:
    for canonical, alias in alias_groups:
        if canonical not in payload or alias not in payload:
            continue
        if payload[alias] != payload[canonical]:
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")


def _validate_account_timestamp_alias_parity(
    payload: dict,
    *,
    field_path: str,
    path: Path,
    alias_groups: tuple[tuple[str, str], ...],
) -> None:
    for canonical, alias in alias_groups:
        if canonical not in payload or alias not in payload:
            continue
        if payload[alias] != payload[canonical]:
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")


def _validate_account_string_alias_parity(
    payload: dict,
    *,
    field_path: str,
    path: Path,
    alias_groups: tuple[tuple[str, str], ...],
) -> None:
    for canonical, alias in alias_groups:
        if canonical not in payload or alias not in payload:
            continue
        if payload[alias] != payload[canonical]:
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")


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


def _validate_spot_balance_total_parity(balance: dict, *, field_path: str, path: Path) -> None:
    if not all(field in balance for field in ("free", "locked", "total")):
        return
    free = _validate_account_number(
        balance["free"],
        field_path=f"{field_path}.free",
        path=path,
        qualifier="non-negative finite",
        minimum=0.0,
    )
    locked = _validate_account_number(
        balance["locked"],
        field_path=f"{field_path}.locked",
        path=path,
        qualifier="non-negative finite",
        minimum=0.0,
    )
    total = _validate_account_number(
        balance["total"],
        field_path=f"{field_path}.total",
        path=path,
        qualifier="non-negative finite",
        minimum=0.0,
    )
    if not math.isclose(total, free + locked, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{field_path}.total must equal free + locked: {path}")


def _validate_account_balance_wallet_total_parity(balance: dict, *, field_path: str, path: Path) -> None:
    for canonical, alias in _ACCOUNT_BALANCE_EQUAL_ALIAS_GROUPS:
        if canonical not in balance or alias not in balance:
            continue
        canonical_value = _validate_account_number(
            balance[canonical],
            field_path=f"{field_path}.{canonical}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        alias_value = _validate_account_number(
            balance[alias],
            field_path=f"{field_path}.{alias}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        if not math.isclose(alias_value, canonical_value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")
    for canonical, alias in _ACCOUNT_BALANCE_SIGNED_EQUAL_ALIAS_GROUPS:
        if canonical not in balance or alias not in balance:
            continue
        canonical_value = _validate_account_number(
            balance[canonical],
            field_path=f"{field_path}.{canonical}",
            path=path,
            qualifier="finite",
            minimum=None,
        )
        alias_value = _validate_account_number(
            balance[alias],
            field_path=f"{field_path}.{alias}",
            path=path,
            qualifier="finite",
            minimum=None,
        )
        if not math.isclose(alias_value, canonical_value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")
    if not all(field in balance for field in ("free", "locked")):
        return
    free = _validate_account_number(
        balance["free"],
        field_path=f"{field_path}.free",
        path=path,
        qualifier="non-negative finite",
        minimum=0.0,
    )
    locked = _validate_account_number(
        balance["locked"],
        field_path=f"{field_path}.locked",
        path=path,
        qualifier="non-negative finite",
        minimum=0.0,
    )
    total = free + locked
    for field in _ACCOUNT_BALANCE_WALLET_TOTAL_FIELDS:
        if field not in balance:
            continue
        wallet_total = _validate_account_number(
            balance[field],
            field_path=f"{field_path}.{field}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        if not math.isclose(wallet_total, total, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{field} must equal free + locked: {path}")


def _validate_account_total_alias_parity(account: dict, *, field_path: str, path: Path) -> None:
    for canonical, alias in _ACCOUNT_TOTAL_EQUAL_ALIAS_GROUPS:
        if canonical not in account or alias not in account:
            continue
        canonical_value = _validate_account_number(
            account[canonical],
            field_path=f"{field_path}.{canonical}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        alias_value = _validate_account_number(
            account[alias],
            field_path=f"{field_path}.{alias}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        if not math.isclose(alias_value, canonical_value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")


def _validate_open_position_alias_parity(position: dict, *, field_path: str, path: Path) -> None:
    for canonical, alias in _ACCOUNT_OPEN_POSITION_EQUAL_ALIAS_GROUPS:
        if canonical not in position or alias not in position:
            continue
        canonical_value = _validate_account_number(
            position[canonical],
            field_path=f"{field_path}.{canonical}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        alias_value = _validate_account_number(
            position[alias],
            field_path=f"{field_path}.{alias}",
            path=path,
            qualifier="non-negative finite",
            minimum=0.0,
        )
        if not math.isclose(alias_value, canonical_value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")
    for canonical, alias in _ACCOUNT_OPEN_POSITION_POSITIVE_PRICE_EQUAL_ALIAS_GROUPS:
        if canonical not in position or alias not in position:
            continue
        canonical_value = _validate_account_positive_number(
            position[canonical],
            field_path=f"{field_path}.{canonical}",
            path=path,
        )
        alias_value = _validate_account_positive_number(
            position[alias],
            field_path=f"{field_path}.{alias}",
            path=path,
        )
        if not math.isclose(alias_value, canonical_value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{field_path}.{alias} must equal {canonical}: {path}")


def _account_first_present_number(payload: dict, *fields: str, path: Path, field_path: str) -> tuple[str, float] | None:
    for field in fields:
        if field in payload:
            return field, _validate_account_number(
                payload[field],
                field_path=f"{field_path}.{field}",
                path=path,
                qualifier="finite",
                minimum=None,
            )
    return None


def _validate_futures_balance_total_parity(account: dict, *, path: Path, field_path: str) -> None:
    wallet = _account_first_present_number(
        account,
        "wallet_balance",
        "walletBalance",
        "total_wallet_balance",
        "totalWalletBalance",
        path=path,
        field_path=field_path,
    )
    margin = _account_first_present_number(
        account,
        "margin_balance",
        "marginBalance",
        "total_margin_balance",
        "totalMarginBalance",
        path=path,
        field_path=field_path,
    )
    unrealized = _account_first_present_number(
        account,
        "total_unrealized_profit",
        "totalUnrealizedProfit",
        "unRealizedProfit",
        path=path,
        field_path=field_path,
    )
    if wallet is None or margin is None or unrealized is None:
        return
    wallet_field, wallet_value = wallet
    margin_field, margin_value = margin
    unrealized_field, unrealized_value = unrealized
    if not math.isclose(margin_value, wallet_value + unrealized_value, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{field_path}.{margin_field} must equal {wallet_field} + {unrealized_field}: {path}")


def _validate_account_numeric_fields(payload: object, *, path: Path, field_path: str) -> None:
    if isinstance(payload, dict):
        if field_path.startswith("account.spot.nonzero_balances["):
            _validate_spot_balance_total_parity(payload, field_path=field_path, path=path)
        if field_path.startswith("account.balances["):
            _validate_account_balance_wallet_total_parity(payload, field_path=field_path, path=path)
        if field_path.startswith("account.open_positions["):
            _validate_open_position_alias_parity(payload, field_path=field_path, path=path)
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
        if field_path == "account":
            _validate_account_total_alias_parity(payload, field_path=field_path, path=path)
            _validate_futures_balance_total_parity(payload, field_path=field_path, path=path)
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
    decision_timestamp = _parse_timestamp(_metadata_canonical_string(metadata, "timestamp"))
    market = _load_json(bundle_path / "market_context.json")
    if not isinstance(market, dict):
        raise ValueError(f"dataset bundle has invalid market context: {bundle_path / 'market_context.json'}")
    _payload_as_of_at_or_before_decision(
        market,
        file_name="market_context.json",
        decision_timestamp=decision_timestamp,
        path=bundle_path / "market_context.json",
    )
    _validate_market_evidence_timestamps(
        market,
        file_name="market_context.json",
        decision_timestamp=decision_timestamp,
        path=bundle_path / "market_context.json",
    )
    market_context = dict(market)
    derivatives_payload = _load_json(bundle_path / "derivatives_snapshot.json")
    if not isinstance(derivatives_payload, dict):
        raise ValueError(f"dataset bundle has invalid derivatives snapshot: {bundle_path / 'derivatives_snapshot.json'}")
    _payload_as_of_at_or_before_decision(
        derivatives_payload,
        file_name="derivatives_snapshot.json",
        decision_timestamp=decision_timestamp,
        path=bundle_path / "derivatives_snapshot.json",
    )
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
    _validate_derivative_evidence_timestamps(
        derivative_rows,
        decision_timestamp=decision_timestamp,
        path=bundle_path / "derivatives_snapshot.json",
    )

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
        decision_timestamp=decision_timestamp,
    )
    instrument_rows = _instrument_rows(bundle_path, decision_timestamp=decision_timestamp)

    forward_returns = _metadata_metric_map(metadata, "forward_returns")
    forward_drawdowns = _metadata_metric_map(metadata, "forward_drawdowns")
    meta = {
        key: value
        for key, value in metadata.items()
        if key not in {"timestamp", "run_id", "forward_returns", "forward_drawdowns"}
    }
    return DatasetSnapshotRow(
        timestamp=decision_timestamp,
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
    seen: set[tuple[datetime, str]] = set()
    for row in rows:
        identity = (row.timestamp, row.run_id)
        if identity in seen:
            raise ValueError(
                "duplicate dataset snapshot temporal identity: "
                f"timestamp={_canonical_utc_timestamp(row.timestamp)} run_id={row.run_id}"
            )
        seen.add(identity)
    return sorted(rows, key=lambda row: (row.timestamp, row.run_id))


def _manifest_canonical_string(manifest: dict[str, object], key: str) -> str | None:
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"import manifest {key} must be a canonical string")
    return value


def _manifest_canonical_utc_timestamp(manifest: dict[str, object], key: str) -> str | None:
    value = _manifest_canonical_string(manifest, key)
    if value is None:
        return None
    try:
        parsed = _parse_timestamp(value)
    except ValueError as exc:
        raise ValueError(f"import manifest {key} must be a canonical UTC ISO timestamp") from exc
    if _canonical_utc_timestamp(parsed) != value:
        raise ValueError(f"import manifest {key} must be a canonical UTC ISO timestamp")
    return value


def _manifest_object_field(manifest: dict[str, object], key: str) -> dict[str, object]:
    value = manifest.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"import manifest {key} must be an object")
    return dict(value)


def _manifest_required_object_field(manifest: dict[str, object], key: str) -> dict[str, object]:
    value = manifest.get(key)
    if value is None:
        raise ValueError(f"import manifest {key} is required")
    if not isinstance(value, dict):
        raise ValueError(f"import manifest {key} must be an object")
    return dict(value)


def _manifest_coverage_field(manifest: dict[str, object]) -> dict[str, object]:
    coverage = _manifest_object_field(manifest, "coverage")
    _validate_manifest_coverage_value(coverage, field_path="import manifest coverage")
    return coverage


def _manifest_lineage_sha256(lineage: dict[str, object], key: str) -> str:
    value = lineage.get(key)
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"import manifest lineage.{key} must be a lowercase 64-hex SHA-256")
    return value


def _manifest_lineage_string(lineage: dict[str, object], key: str) -> str:
    value = lineage.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"import manifest lineage.{key} must be a canonical string")
    return value


def _manifest_lineage_utc_timestamp(lineage: dict[str, object], key: str) -> str:
    value = _manifest_lineage_string(lineage, key)
    try:
        parsed = _parse_timestamp(value)
    except ValueError as exc:
        raise ValueError(f"import manifest lineage.{key} must be a canonical UTC ISO timestamp") from exc
    if _canonical_utc_timestamp(parsed) != value:
        raise ValueError(f"import manifest lineage.{key} must be a canonical UTC ISO timestamp")
    return value


def _manifest_lineage_string_list(lineage: dict[str, object], key: str) -> list[str]:
    value = lineage.get(key)
    if not isinstance(value, list):
        raise ValueError(f"import manifest lineage.{key} must be a list")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ValueError(f"import manifest lineage.{key}[{index}] must be a canonical string")
        values.append(item)
    if len(set(values)) != len(values):
        raise ValueError(f"import manifest lineage.{key} must not contain duplicates")
    return values


def _manifest_lineage_field(
    manifest: dict[str, object],
    *,
    manifest_symbols: list[str],
    start_timestamp: str | None,
    end_timestamp: str | None,
    source: dict[str, object],
) -> dict[str, object]:
    raw_lineage = _manifest_required_object_field(manifest, "lineage")
    lineage = {
        "raw_sha256": _manifest_lineage_sha256(raw_lineage, "raw_sha256"),
        "importer_version": _manifest_lineage_string(raw_lineage, "importer_version"),
        "importer_config_sha256": _manifest_lineage_sha256(raw_lineage, "importer_config_sha256"),
        "artifact_sha256": _manifest_lineage_sha256(raw_lineage, "artifact_sha256"),
        "exchange": _manifest_lineage_string(raw_lineage, "exchange"),
        "market": _manifest_lineage_string(raw_lineage, "market"),
        "symbols": _manifest_lineage_string_list(raw_lineage, "symbols"),
        "timeframes": _manifest_lineage_string_list(raw_lineage, "timeframes"),
        "coverage_start": _manifest_lineage_utc_timestamp(raw_lineage, "coverage_start"),
        "coverage_end": _manifest_lineage_utc_timestamp(raw_lineage, "coverage_end"),
    }
    if lineage["symbols"] != manifest_symbols:
        raise ValueError("import manifest lineage.symbols must match symbols")
    if start_timestamp is not None and lineage["coverage_start"] != start_timestamp:
        raise ValueError("import manifest lineage.coverage_start must match start_timestamp")
    if end_timestamp is not None and lineage["coverage_end"] != end_timestamp:
        raise ValueError("import manifest lineage.coverage_end must match end_timestamp")
    for key in ("exchange", "market"):
        source_value = source.get(key)
        if source_value is not None and source_value != lineage[key]:
            raise ValueError(f"import manifest lineage.{key} must match source.{key}")
    return lineage


def _validate_manifest_coverage_value(value: object, *, field_path: str) -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip() or raw_key != raw_key.strip():
                raise ValueError(f"{field_path} keys must be canonical strings")
            _validate_manifest_coverage_value(raw_value, field_path=f"{field_path}.{raw_key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_manifest_coverage_value(item, field_path=f"{field_path}[{index}]")
        return
    if isinstance(value, bool):
        if field_path.endswith(".available"):
            return
        raise ValueError(f"{field_path} must be a finite numeric value")
    if isinstance(value, str):
        if not value.strip() or value != value.strip():
            raise ValueError(f"{field_path} must be a canonical string")
        if "[" in field_path or ".not_materialized." in field_path:
            return
        raise ValueError(f"{field_path} must be a finite numeric value")
    if value is None:
        raise ValueError(f"{field_path} must not be null")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_path} must be a finite numeric value")
        return
    raise ValueError(f"{field_path} contains unsupported metadata value")


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
    if not isinstance(manifest, dict):
        raise ValueError(f"import manifest must be a JSON object: {manifest_path}")
    start_timestamp = _manifest_canonical_utc_timestamp(manifest, "start_timestamp")
    end_timestamp = _manifest_canonical_utc_timestamp(manifest, "end_timestamp")
    if start_timestamp is not None and end_timestamp is not None:
        if _parse_timestamp(start_timestamp) > _parse_timestamp(end_timestamp):
            raise ValueError("import manifest start_timestamp must be at or before end_timestamp")
    symbols = _manifest_string_list(manifest, "symbols")
    source = _manifest_object_field(manifest, "source")
    coverage = _manifest_coverage_field(manifest)
    return {
        "dataset_root_type": "imported_archive",
        "import_manifest_path": str(manifest_path),
        "import_manifest": {
            "schema_version": _manifest_canonical_string(manifest, "schema_version"),
            "scope": _manifest_canonical_string(manifest, "scope"),
            "archive_root": _manifest_canonical_string(manifest, "archive_root"),
            "dataset_root": _manifest_canonical_string(manifest, "dataset_root"),
            "manifest_snapshot_count": _manifest_non_negative_int(manifest, "snapshot_count"),
            "symbols": symbols,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "bundle_count": _manifest_list_count(manifest, "bundle_dirs"),
            "source": source,
            "lineage": _manifest_lineage_field(
                manifest,
                manifest_symbols=symbols,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                source=source,
            ),
            "coverage": coverage,
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
