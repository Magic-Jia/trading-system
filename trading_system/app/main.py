from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, is_dataclass, replace
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any, Mapping

from .config import RiskConfig, build_config, normalize_engine_names, normalize_setup_types, runtime_path_defaults_enabled
from .data_sources import load_derivatives_snapshot, load_market_context
from .data_sources.testnet_exchange_metadata import load_testnet_exchange_metadata
from .execution.executor import OrderExecutor
from .execution.idempotency import already_processed, intent_id, mark_processed, replay_processed_execution
from .execution.testnet_preview import build_validated_order_preview
from .market_regime import classify_regime, summarize_derivatives_risk
from .market_regime.derivatives import is_late_stage_long_blowoff, symbol_derivatives_features
from .paper_optimization.collector import collect_signal_facts
from .paper_optimization.metrics import write_daily_metrics_and_health_report
from .paper_optimization.outcomes import collect_trade_outcomes
from .paper_optimization.promotion import write_promotion_decision
from .paper_optimization.recommendations import write_recommendations
from .portfolio.allocator import allocate_candidates
from .portfolio.lifecycle import advance_lifecycle_positions, build_management_action_intents, evaluate_portfolio
from .portfolio.positions import apply_executed_intent, sync_positions_from_account
from .portfolio.target_management import derive_target_management_fields, terminalize_all_unreachable_stages
from .reporting.daily_report import build_lifecycle_report, build_rotation_report, build_short_report, build_trend_report
from .reporting.regime_report import build_regime_summary
from .risk.stop_policy import build_stop_policy
from .signals.rotation_engine import (
    _extension_pct as _rotation_extension_pct,
    _passes_absolute_strength_gate as _rotation_passes_absolute_strength_gate,
    _reject_price_extension_overheat as _rotation_reject_price_extension_overheat,
    _setup_type as _rotation_setup_type,
    _trend_intact as _rotation_trend_intact,
    generate_rotation_candidates,
)
from .signals.short_engine import (
    _reject_crowded_short_squeeze_risk,
    _setup_type as _short_setup_type,
    _trend_broken,
    generate_short_candidates,
)
from .risk.validator import validate_candidate_for_allocation, validate_candidate_for_execution, validate_signal
from .signals.trend_engine import (
    _extension_pct as _trend_extension_pct,
    _is_uptrend as _trend_is_uptrend,
    _passes_absolute_strength_gate as _trend_passes_absolute_strength_gate,
    _reject_price_extension_overheat as _trend_reject_price_extension_overheat,
    _setup_type as _trend_setup_type,
    generate_trend_candidates,
)
from .storage.state_store import build_state_store
from .universe.builder import UniverseBuildResult, build_universes
from .types import AccountSnapshot, OrderIntent, PositionSnapshot, TradeSignal

BASE = Path(__file__).resolve().parents[1]
ACCOUNT_SNAPSHOT = BASE / "data" / "account_snapshot.json"
MARKET_CONTEXT = BASE / "data" / "market_context.json"
DERIVATIVES_SNAPSHOT = BASE / "data" / "derivatives_snapshot.json"
ACCOUNT_SNAPSHOT_FILE_ENV = "TRADING_ACCOUNT_SNAPSHOT_FILE"
MARKET_CONTEXT_FILE_ENV = "TRADING_MARKET_CONTEXT_FILE"
DERIVATIVES_SNAPSHOT_FILE_ENV = "TRADING_DERIVATIVES_SNAPSHOT_FILE"
STATE_FILE_ENV = "TRADING_STATE_FILE"
_PAPER_SAFE_ACCOUNT_TYPES = {"paper", "testnet"}


def _testnet_existing_position_entry_block(state: Any, symbol: str) -> dict[str, Any] | None:
    position = getattr(state, "positions", {}).get(symbol)
    if not isinstance(position, dict):
        return None
    try:
        qty = float(position.get("qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        qty = 0.0
    status = str(position.get("status", "")).upper()
    if qty <= 0.0 or status in {"CLOSED", "EXITED", "FLAT"}:
        return None
    return {
        "status": "SKIPPED",
        "reason": "testnet_existing_position_open",
        "existing_intent_id": position.get("intent_id"),
        "existing_qty": qty,
    }


@dataclass(frozen=True, slots=True)
class RuntimeInputPaths:
    account_snapshot: Path
    market_context: Path
    derivatives_snapshot: Path
    state_file: Path


def _float(row: dict, *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _strict_account_float(row: Mapping[str, Any], keys: tuple[str, ...], *, field_path: str, default: float | None = None) -> float:
    for key in keys:
        if key not in row or row[key] is None:
            continue
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_path}.{key} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field_path}.{key} must be finite")
        return number
    if default is not None:
        return default
    raise ValueError(f"{field_path}.{keys[0]} is required")


def _strict_position_float(row: Mapping[str, Any], keys: tuple[str, ...], *, field_path: str, default: float = 0.0) -> float:
    for key in keys:
        if key not in row or row[key] is None:
            continue
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_path}.{key} must be a number when present")
        return float(value)
    return default


def _resolve_default_data_file(config: Any, filename: str, legacy_path: Path) -> Path:
    if runtime_path_defaults_enabled() and hasattr(config, "state_file"):
        runtime_default = Path(config.state_file).parent / filename
        if runtime_default.exists():
            return runtime_default
    return legacy_path


def _runtime_bucket_account_snapshot_path(config: Any | None) -> Path | None:
    if config is None or not runtime_path_defaults_enabled() or not hasattr(config, "state_file"):
        return None
    return Path(config.state_file).parent / "account_snapshot.json"


def _resolve_account_snapshot_input(path: str | Path | None = None, *, config: Any | None = None) -> tuple[Path, str]:
    if path is not None:
        return Path(path), "argument"
    env_value = os.environ.get(ACCOUNT_SNAPSHOT_FILE_ENV)
    if env_value:
        return Path(env_value), "environment"
    runtime_default = _runtime_bucket_account_snapshot_path(config)
    if runtime_default is not None and runtime_default.exists():
        return runtime_default, "runtime_bucket"
    return ACCOUNT_SNAPSHOT, "legacy_default"


def _resolve_account_snapshot_path(path: str | Path | None = None, *, config: Any | None = None) -> Path:
    resolved_path, _ = _resolve_account_snapshot_input(path, config=config)
    return resolved_path


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _resolve_market_context_path(*, config: Any | None = None) -> Path:
    env_value = os.environ.get(MARKET_CONTEXT_FILE_ENV)
    if env_value:
        return Path(env_value)
    if config is not None:
        return _resolve_default_data_file(config, "market_context.json", MARKET_CONTEXT)
    return MARKET_CONTEXT


def _resolve_derivatives_snapshot_path(*, config: Any | None = None) -> Path:
    env_value = os.environ.get(DERIVATIVES_SNAPSHOT_FILE_ENV)
    if env_value:
        return Path(env_value)
    if config is not None:
        return _resolve_default_data_file(config, "derivatives_snapshot.json", DERIVATIVES_SNAPSHOT)
    return DERIVATIVES_SNAPSHOT


def _positions_from_rows(rows: list[dict[str, Any]]) -> list[PositionSnapshot]:
    positions: list[PositionSnapshot] = []
    for index, row in enumerate(rows):
        field_path = f"open_positions[{index}]"
        if not isinstance(row, Mapping):
            raise ValueError(f"{field_path} must be an object")
        qty = abs(_strict_position_float(row, ("qty", "position_amt", "positionAmt", "amt"), field_path=field_path))
        if qty <= 0:
            continue
        positions.append(
            PositionSnapshot(
                symbol=str(row["symbol"]),
                side=str(row.get("side", row.get("positionSide", "LONG"))),
                qty=qty,
                entry_price=_strict_position_float(row, ("entry_price", "entryPrice", "entry"), field_path=field_path),
                mark_price=_strict_position_float(row, ("mark_price", "markPrice", "mark"), field_path=field_path),
                unrealized_pnl=_strict_position_float(
                    row, ("unrealized_pnl", "upl", "unRealizedProfit"), field_path=field_path
                ),
                notional=_strict_position_float(row, ("notional",), field_path=field_path),
                leverage=_strict_position_float(row, ("leverage",), field_path=field_path)
                if row.get("leverage") is not None
                else None,
                strategy_tag=row.get("strategy_tag"),
                status=row.get("status"),
                signal_id=row.get("signal_id"),
                signalId=row.get("signalId"),
                order_id=row.get("order_id"),
                orderId=row.get("orderId"),
                client_order_id=row.get("client_order_id"),
                clientOrderId=row.get("clientOrderId"),
            )
        )
    return positions


def _load_v1_account_snapshot(raw: dict[str, Any]) -> AccountSnapshot:
    futures = raw["futures"]
    if not isinstance(futures, Mapping):
        raise ValueError("futures must be an object")
    open_orders = futures.get("open_orders", futures.get("openOrders", raw.get("open_orders", raw.get("openOrders", []))))
    if not isinstance(open_orders, list):
        raise ValueError("futures.open_orders must be a list")
    raw_positions = futures.get("positions", [])
    if not isinstance(raw_positions, list):
        raise ValueError("futures.positions must be a list")
    positions = _positions_from_rows(raw_positions)
    return AccountSnapshot(
        equity=_strict_account_float(futures, ("total_wallet_balance",), field_path="futures"),
        available_balance=_strict_account_float(
            futures,
            ("available_balance",),
            field_path="futures",
            default=_strict_account_float(futures, ("total_wallet_balance",), field_path="futures"),
        ),
        futures_wallet_balance=_strict_account_float(futures, ("total_wallet_balance",), field_path="futures"),
        open_positions=positions,
        open_orders=open_orders,
        meta={"source": "account_snapshot.json"},
    )


def _load_v2_account_snapshot(raw: dict[str, Any]) -> AccountSnapshot:
    open_positions = raw.get("open_positions", raw.get("positions", []))
    if not isinstance(open_positions, list):
        raise ValueError("open_positions must be a list")
    open_orders = raw.get("open_orders", raw.get("openOrders", []))
    if not isinstance(open_orders, list):
        raise ValueError("open_orders must be a list")

    meta = raw.get("meta", {})
    if meta is None:
        meta = {}
    if not isinstance(meta, Mapping):
        raise ValueError("meta must be an object")

    equity = _strict_account_float(raw, ("equity", "total_wallet_balance"), field_path="account")
    available_balance = _strict_account_float(raw, ("available_balance",), field_path="account", default=equity)
    if available_balance <= 0:
        available_balance = equity
    futures_wallet_balance = _strict_account_float(
        raw, ("futures_wallet_balance", "total_wallet_balance"), field_path="account", default=equity
    )
    if futures_wallet_balance <= 0:
        futures_wallet_balance = equity

    return AccountSnapshot(
        equity=equity,
        available_balance=available_balance,
        futures_wallet_balance=futures_wallet_balance,
        open_positions=_positions_from_rows(open_positions),
        open_orders=open_orders,
        meta=dict(meta),
    )


def _load_account_snapshot_payload(raw: dict[str, Any]) -> AccountSnapshot:
    if not isinstance(raw, Mapping):
        raise ValueError("account snapshot must be an object")
    if "futures" in raw:
        return _load_v1_account_snapshot(raw)
    return _load_v2_account_snapshot(raw)


def _explicit_live_snapshot_path(path: Path) -> bool:
    if _same_path(path, ACCOUNT_SNAPSHOT):
        return True
    stem = path.stem.lower()
    return "live" in stem and "snapshot" in stem


def _ensure_paper_runtime_account_snapshot_present(path: Path, *, config: Any | None = None, source: str) -> None:
    if source != "legacy_default":
        return
    runtime_default = _runtime_bucket_account_snapshot_path(config)
    if runtime_default is None or runtime_default.exists() or not _same_path(path, ACCOUNT_SNAPSHOT):
        return
    raise RuntimeError(
        f"paper 模式缺少独立的 paper account snapshot：{runtime_default}；禁止回退到默认 live account_snapshot.json"
    )


def _ensure_paper_safe_account_snapshot(path: Path, raw: dict[str, Any], *, source: str) -> None:
    if source == "legacy_default":
        raise RuntimeError("paper 模式禁止默认回退到 live account_snapshot.json；请显式提供独立的 paper account snapshot")
    if _explicit_live_snapshot_path(path):
        raise RuntimeError(f"paper 模式拒绝读取疑似 live account snapshot：{path}")

    meta = raw.get("meta")
    if not isinstance(meta, Mapping):
        return

    account_type = str(meta.get("account_type", "")).strip().lower()
    if account_type and account_type not in _PAPER_SAFE_ACCOUNT_TYPES:
        raise RuntimeError(f"paper 模式拒绝读取 {account_type} account snapshot：{path}")

    source_hint = str(meta.get("source", "") or meta.get("snapshot_source", "")).strip().lower()
    if source_hint and any(token in source_hint for token in ("live", "production", "real")):
        raise RuntimeError(f"paper 模式拒绝读取 live account snapshot source：{source_hint}")


def load_account_snapshot(path: str | Path | None = None, *, config: Any | None = None) -> AccountSnapshot:
    resolved_path, source = _resolve_account_snapshot_input(path, config=config)
    if getattr(getattr(config, "execution", None), "mode", None) == "paper":
        _ensure_paper_runtime_account_snapshot_present(resolved_path, config=config, source=source)
    raw = json.loads(resolved_path.read_text())
    if getattr(getattr(config, "execution", None), "mode", None) == "paper":
        _ensure_paper_safe_account_snapshot(resolved_path, raw, source=source)
    return _load_account_snapshot_payload(raw)


def load_config():
    return build_config()


def _market_payload(market_rows: list[dict[str, Any]]) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    for row in market_rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        payload = {key: value for key, value in row.items() if key != "symbol"}
        symbols[symbol] = payload
    return {"symbols": symbols}


def _candidate_row(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        row = dict(candidate)
        _validate_candidate_metadata(row)
        return row
    if is_dataclass(candidate):
        row = asdict(candidate)
        _validate_candidate_metadata(row)
        return row
    row = {
        "engine": str(getattr(candidate, "engine", "")),
        "setup_type": str(getattr(candidate, "setup_type", "")),
        "symbol": str(getattr(candidate, "symbol", "")),
        "side": str(getattr(candidate, "side", "LONG")),
        "score": _float({"score": getattr(candidate, "score", 0.0)}, "score"),
        "stop_loss": _float({"stop_loss": getattr(candidate, "stop_loss", 0.0)}, "stop_loss"),
        "invalidation_source": str(getattr(candidate, "invalidation_source", "") or ""),
        "sector": getattr(candidate, "sector", None),
    }
    for field in ("meta", "timeframe_meta", "liquidity_meta"):
        if hasattr(candidate, field):
            row[field] = getattr(candidate, field)
    _validate_candidate_metadata(row)
    return row


def _validate_candidate_metadata(row: dict[str, Any]) -> None:
    for field in ("meta", "timeframe_meta", "liquidity_meta"):
        if field in row and row[field] is not None and not isinstance(row[field], Mapping):
            raise ValueError(f"candidate.{field} must be a mapping when present")
        if isinstance(row.get(field), Mapping):
            row[field] = dict(row[field])


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
    return (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", "")), str(row.get("engine", "")))


def _candidate_symbol(candidate: Any) -> str:
    if isinstance(candidate, Mapping):
        return str(candidate.get("symbol", "")).upper().strip()
    return str(getattr(candidate, "symbol", "")).upper().strip()


def _timeframe_row(payload: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    row = payload.get(timeframe)
    if isinstance(row, Mapping):
        return row
    return {}


def _trend_review_notes(
    *,
    market: Mapping[str, Any],
    major_universe: list[Mapping[str, Any]],
    trend_candidates: list[Any],
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None,
    entry_profile: Any = None,
) -> list[dict[str, Any]]:
    candidate_symbols = {_candidate_symbol(candidate) for candidate in trend_candidates}
    market_symbols = market.get("symbols", {})
    if not isinstance(market_symbols, Mapping):
        return []

    notes: list[dict[str, Any]] = []
    for universe_row in major_universe:
        symbol = str(universe_row.get("symbol", "")).upper().strip()
        if not symbol or symbol in candidate_symbols:
            continue
        payload = market_symbols.get(symbol)
        if not isinstance(payload, Mapping):
            continue

        daily = _timeframe_row(payload, "daily")
        h4 = _timeframe_row(payload, "4h")
        h1 = _timeframe_row(payload, "1h")
        if not _trend_is_uptrend(daily, h4, h1):
            continue

        setup_type = _trend_setup_type(payload)
        if not _trend_passes_absolute_strength_gate(payload, entry_profile):
            daily_return_pct_7d = round(_float(daily, "return_pct_7d"), 6)
            h4_return_pct_3d = round(_float(h4, "return_pct_3d"), 6)
            h1_return_pct_24h = round(_float(h1, "return_pct_24h"), 6)
            notes.append(
                {
                    "symbol": symbol,
                    "setup_type": setup_type,
                    "reason": "absolute_strength_floor",
                    "daily_return_pct_7d": daily_return_pct_7d,
                    "h4_return_pct_3d": h4_return_pct_3d,
                    "h1_return_pct_24h": h1_return_pct_24h,
                    "message": (
                        f"{symbol} {setup_type} suppressed: absolute strength floor failed "
                        f"(7d {daily_return_pct_7d:.3f}, 3d {h4_return_pct_3d:.3f}, 24h {h1_return_pct_24h:.3f})."
                    ),
                }
            )
            continue

        derivatives_features = symbol_derivatives_features(derivatives, symbol)
        h4_extension_pct = round(_trend_extension_pct(h4), 6)
        h1_extension_pct = round(_trend_extension_pct(h1), 6)
        if is_late_stage_long_blowoff(
            derivatives_features,
            h4_extension_pct=h4_extension_pct,
            h1_extension_pct=h1_extension_pct,
        ):
            funding_rate = round(float(derivatives_features.get("funding_rate", 0.0) or 0.0), 6)
            basis_bps = round(float(derivatives_features.get("basis_bps", 0.0) or 0.0), 6)
            oi_change_24h_pct = round(float(derivatives_features.get("open_interest_change_24h_pct", 0.0) or 0.0), 6)
            mark_price_change_24h_pct = round(float(derivatives_features.get("mark_price_change_24h_pct", 0.0) or 0.0), 6)
            funding_basis_blowoff = funding_rate >= 0.0002 and basis_bps >= 25.0
            if funding_basis_blowoff:
                notes.append(
                    {
                        "symbol": symbol,
                        "setup_type": setup_type,
                        "reason": "funding_basis_blowoff",
                        "funding_rate": funding_rate,
                        "basis_bps": basis_bps,
                        "message": (
                            f"{symbol} {setup_type} suppressed: funding/basis blowoff remained elevated "
                            f"(funding {funding_rate:.5f}, basis {basis_bps:.1f} bps)."
                        ),
                    }
                )
            else:
                notes.append(
                    {
                        "symbol": symbol,
                        "setup_type": setup_type,
                        "reason": "late_stage_long_blowoff",
                        "h4_extension_pct": h4_extension_pct,
                        "h1_extension_pct": h1_extension_pct,
                        "open_interest_change_24h_pct": oi_change_24h_pct,
                        "mark_price_change_24h_pct": mark_price_change_24h_pct,
                        "message": (
                            f"{symbol} {setup_type} suppressed: late-stage long blowoff remained elevated "
                            f"(4h extension {h4_extension_pct:.3f}, 1h extension {h1_extension_pct:.3f}, "
                            f"OI 24h {oi_change_24h_pct:.3f}, price 24h {mark_price_change_24h_pct:.3f})."
                        ),
                    }
                )
            continue
        if _trend_reject_price_extension_overheat(payload):
            notes.append(
                {
                    "symbol": symbol,
                    "setup_type": setup_type,
                    "reason": "price_extension_overheat",
                    "h4_extension_pct": h4_extension_pct,
                    "h1_extension_pct": h1_extension_pct,
                    "message": (
                        f"{symbol} {setup_type} suppressed: overheat remained elevated "
                        f"(4h extension {h4_extension_pct:.3f}, 1h extension {h1_extension_pct:.3f})."
                    ),
                }
            )

    return sorted(notes, key=lambda row: (str(row.get("symbol", "")), str(row.get("setup_type", ""))))


def _rotation_review_notes(
    *,
    market: Mapping[str, Any],
    rotation_universe: list[Mapping[str, Any]],
    rotation_candidates: list[Any],
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None,
    entry_profile: Any = None,
) -> list[dict[str, Any]]:
    candidate_symbols = {_candidate_symbol(candidate) for candidate in rotation_candidates}
    market_symbols = market.get("symbols", {})
    if not isinstance(market_symbols, Mapping):
        return []

    notes: list[dict[str, Any]] = []
    for universe_row in rotation_universe:
        symbol = str(universe_row.get("symbol", "")).upper().strip()
        if not symbol or symbol in candidate_symbols:
            continue
        payload = market_symbols.get(symbol)
        if not isinstance(payload, Mapping):
            continue
        if not _rotation_trend_intact(payload):
            continue

        setup_type = _rotation_setup_type(payload)
        if not _rotation_passes_absolute_strength_gate(payload, entry_profile):
            daily = _timeframe_row(payload, "daily")
            h4 = _timeframe_row(payload, "4h")
            h1 = _timeframe_row(payload, "1h")
            daily_return_pct_7d = round(_float(daily, "return_pct_7d"), 6)
            h4_return_pct_3d = round(_float(h4, "return_pct_3d"), 6)
            h1_return_pct_24h = round(_float(h1, "return_pct_24h"), 6)
            notes.append(
                {
                    "symbol": symbol,
                    "setup_type": setup_type,
                    "reason": "absolute_strength_floor",
                    "daily_return_pct_7d": daily_return_pct_7d,
                    "h4_return_pct_3d": h4_return_pct_3d,
                    "h1_return_pct_24h": h1_return_pct_24h,
                    "message": (
                        f"{symbol} {setup_type} suppressed: absolute strength floor failed "
                        f"(7d {daily_return_pct_7d:.3f}, 3d {h4_return_pct_3d:.3f}, 24h {h1_return_pct_24h:.3f})."
                    ),
                }
            )
            continue

        h4 = _timeframe_row(payload, "4h")
        h1 = _timeframe_row(payload, "1h")
        h4_extension_pct = round(_rotation_extension_pct(h4), 6)
        h1_extension_pct = round(_rotation_extension_pct(h1), 6)
        derivatives_features = symbol_derivatives_features(derivatives, symbol)
        if is_late_stage_long_blowoff(
            derivatives_features,
            h4_extension_pct=h4_extension_pct,
            h1_extension_pct=h1_extension_pct,
        ):
            funding_rate = round(float(derivatives_features.get("funding_rate", 0.0) or 0.0), 6)
            basis_bps = round(float(derivatives_features.get("basis_bps", 0.0) or 0.0), 6)
            oi_change_24h_pct = round(float(derivatives_features.get("open_interest_change_24h_pct", 0.0) or 0.0), 6)
            mark_price_change_24h_pct = round(float(derivatives_features.get("mark_price_change_24h_pct", 0.0) or 0.0), 6)
            funding_basis_blowoff = funding_rate >= 0.0002 and basis_bps >= 25.0
            if funding_basis_blowoff:
                notes.append(
                    {
                        "symbol": symbol,
                        "setup_type": setup_type,
                        "reason": "funding_basis_blowoff",
                        "funding_rate": funding_rate,
                        "basis_bps": basis_bps,
                        "message": (
                            f"{symbol} {setup_type} suppressed: funding/basis blowoff remained elevated "
                            f"(funding {funding_rate:.5f}, basis {basis_bps:.1f} bps)."
                        ),
                    }
                )
            else:
                notes.append(
                    {
                        "symbol": symbol,
                        "setup_type": setup_type,
                        "reason": "late_stage_long_blowoff",
                        "h4_extension_pct": h4_extension_pct,
                        "h1_extension_pct": h1_extension_pct,
                        "open_interest_change_24h_pct": oi_change_24h_pct,
                        "mark_price_change_24h_pct": mark_price_change_24h_pct,
                        "message": (
                            f"{symbol} {setup_type} suppressed: late-stage long blowoff remained elevated "
                            f"(4h extension {h4_extension_pct:.3f}, 1h extension {h1_extension_pct:.3f}, "
                            f"OI 24h {oi_change_24h_pct:.3f}, price 24h {mark_price_change_24h_pct:.3f})."
                        ),
                    }
                )
            continue
        if _rotation_reject_price_extension_overheat(payload):
            notes.append(
                {
                    "symbol": symbol,
                    "setup_type": setup_type,
                    "reason": "price_extension_overheat",
                    "h4_extension_pct": h4_extension_pct,
                    "h1_extension_pct": h1_extension_pct,
                    "message": (
                        f"{symbol} {setup_type} suppressed: overheat remained elevated "
                        f"(4h extension {h4_extension_pct:.3f}, 1h extension {h1_extension_pct:.3f})."
                    ),
                }
            )

    return sorted(notes, key=lambda row: (str(row.get("symbol", "")), str(row.get("setup_type", ""))))


def _short_review_notes(
    *,
    market: Mapping[str, Any],
    short_universe: list[Mapping[str, Any]],
    short_candidates: list[Any],
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    candidate_symbols = {_candidate_symbol(candidate) for candidate in short_candidates}
    market_symbols = market.get("symbols", {})
    if not isinstance(market_symbols, Mapping):
        return []

    notes: list[dict[str, Any]] = []
    for universe_row in short_universe:
        symbol = str(universe_row.get("symbol", "")).upper().strip()
        if not symbol or symbol in candidate_symbols:
            continue
        payload = market_symbols.get(symbol)
        if not isinstance(payload, Mapping):
            continue
        if not _trend_broken(payload):
            continue
        setup_type = _short_setup_type(payload)
        if not setup_type:
            continue

        derivatives_features = symbol_derivatives_features(derivatives, symbol)
        if not _reject_crowded_short_squeeze_risk(derivatives_features):
            continue

        crowding_bias = str(derivatives_features.get("crowding_bias", "balanced") or "balanced")
        basis_bps = round(_float({"basis_bps": derivatives_features.get("basis_bps")}, "basis_bps"), 6)
        notes.append(
            {
                "symbol": symbol,
                "setup_type": setup_type,
                "reason": "crowded_short_squeeze_risk",
                "crowding_bias": crowding_bias,
                "basis_bps": basis_bps,
                "message": (
                    f"{symbol} {setup_type} suppressed: crowded-short squeeze risk remained elevated "
                    f"(crowding bias {crowding_bias}, basis {basis_bps:.1f} bps)."
                ),
            }
        )

    return sorted(notes, key=lambda row: (str(row.get("symbol", "")), str(row.get("setup_type", ""))))


def _regime_payload(regime: Any) -> Mapping[str, Any] | None:
    if isinstance(regime, Mapping):
        return regime
    if is_dataclass(regime):
        return asdict(regime)
    return None


def _is_active_paper_first_rotation_probe_row(row: Mapping[str, Any]) -> bool:
    meta = row.get("meta") if isinstance(row.get("meta"), Mapping) else {}
    return (
        str(row.get("engine", "")).strip().lower() == "rotation"
        and str(row.get("side", "LONG")).strip().upper() == "LONG"
        and bool(meta.get("active_paper_first_rotation_probe"))
    )


def _apply_active_paper_probe_stop_floor(row: dict[str, Any], *, risk_config: RiskConfig | None = None) -> None:
    if not _is_active_paper_first_rotation_probe_row(row):
        return
    risk = risk_config or RiskConfig()
    min_stop_distance_pct = max(float(risk.min_stop_distance_pct), 0.0)
    max_stop_distance_pct = max(float(risk.max_stop_distance_pct), 0.0)
    if min_stop_distance_pct <= 0.0 or min_stop_distance_pct > max_stop_distance_pct:
        return
    entry_price = _float(row, "entry_price")
    stop_loss = _float(row, "stop_loss")
    if entry_price <= 0.0:
        return
    stop_distance_pct = (entry_price - stop_loss) / entry_price if stop_loss > 0.0 else 0.0
    if stop_distance_pct >= min_stop_distance_pct:
        return
    floored_distance_pct = min(min_stop_distance_pct * 1.001, max_stop_distance_pct)
    floored_stop = entry_price * (1.0 - floored_distance_pct)
    if floored_stop <= 0.0 or floored_stop >= entry_price:
        return
    row["stop_loss"] = round(floored_stop, 8)
    candidate_meta = dict(row.get("meta") or {}) if isinstance(row.get("meta"), Mapping) else {}
    candidate_meta["active_paper_probe_stop_floor_applied"] = True
    candidate_meta["active_paper_probe_original_stop_loss"] = round(stop_loss, 8)
    candidate_meta["active_paper_probe_min_stop_distance_pct"] = round(min_stop_distance_pct, 6)
    row["meta"] = candidate_meta


def _candidate_with_stop_taxonomy(
    candidate: Any,
    market: Mapping[str, Any],
    regime: Any | None = None,
    *,
    risk_config: RiskConfig | None = None,
) -> dict[str, Any]:
    row = _candidate_row(candidate)
    if row.get("meta") is not None and not isinstance(row.get("meta"), Mapping):
        raise ValueError("candidate.meta must be a mapping when present")
    symbol = str(row.get("symbol", "")).upper()
    payload = market.get("symbols", {}).get(symbol, {})
    if not isinstance(payload, Mapping):
        return row

    policy = build_stop_policy(
        payload,
        engine=str(row.get("engine", "")),
        setup_type=str(row.get("setup_type", "")),
        side=str(row.get("side", "LONG")),
        regime=_regime_payload(regime),
    )
    if policy is None:
        return row

    candidate_meta = dict(row.get("meta") or {}) if isinstance(row.get("meta"), Mapping) else {}
    candidate_meta.update(
        {
            "stop_policy_source": "shared_taxonomy",
            "stop_family": policy.stop_family,
            "stop_reference": policy.stop_reference,
            "invalidation_source": policy.invalidation_source,
            "invalidation_reason": policy.invalidation_reason,
        }
    )
    row["meta"] = candidate_meta
    row["stop_loss"] = round(policy.stop_loss, 8)
    row["invalidation_source"] = policy.invalidation_source
    row["stop_family"] = policy.stop_family
    row["stop_reference"] = policy.stop_reference
    row["invalidation_reason"] = policy.invalidation_reason
    row["stop_policy_source"] = "shared_taxonomy"
    if _float(row, "entry_price") <= 0.0:
        daily = dict(payload.get("daily", {}))
        h4 = dict(payload.get("4h", {}))
        entry_reference = _float(daily, "close") or _float(h4, "close")
        if entry_reference > 0.0:
            row["entry_price"] = round(entry_reference, 8)
    _apply_active_paper_probe_stop_floor(row, risk_config=risk_config)
    return row


def _candidate_signal(
    candidate: Mapping[str, Any],
    market: Mapping[str, Any],
    regime: Any | None = None,
    *,
    risk_config: RiskConfig | None = None,
) -> TradeSignal:
    candidate_row = _candidate_with_stop_taxonomy(candidate, market, regime, risk_config=risk_config)
    execution_validation = validate_candidate_for_execution(candidate_row)
    if not execution_validation.allowed:
        raise ValueError("; ".join(execution_validation.reasons) or "candidate execution validation failed")

    symbol = str(candidate_row.get("symbol", "")).upper()
    side = str(candidate_row.get("side", "LONG")).upper()
    candidate_meta = candidate_row.get("meta") if isinstance(candidate_row.get("meta"), Mapping) else {}
    payload = dict(market.get("symbols", {}).get(symbol, {}))
    daily = dict(payload.get("daily", {}))
    entry_price = _float(candidate_row, "entry_price")
    if entry_price <= 0:
        entry_price = _float(candidate_meta, "entry_price")
    if entry_price <= 0:
        entry_price = _float(daily, "close")
    if entry_price <= 0:
        entry_price = 1.0
    stop_loss = _float(candidate_row, "stop_loss")
    if stop_loss <= 0:
        stop_loss = _float(candidate_meta, "stop_loss")
    take_profit = _float(candidate_row, "take_profit")
    if take_profit <= 0:
        take_profit = _float(candidate_meta, "take_profit")
    if take_profit <= 0:
        stop_loss_for_default = _float(candidate_row, "stop_loss")
        if stop_loss_for_default <= 0:
            stop_loss_for_default = _float(candidate_meta, "stop_loss")
        if stop_loss_for_default > 0:
            risk_per_unit = abs(entry_price - stop_loss_for_default)
            if risk_per_unit > 0:
                take_profit = entry_price + risk_per_unit * 1.5 if side == "LONG" else entry_price - risk_per_unit * 1.5
    if take_profit <= 0:
        take_profit = None
    structure_target_price = round(take_profit, 8) if take_profit and take_profit > 0 else None
    invalidation_source = str(candidate_row.get("invalidation_source") or candidate_meta.get("invalidation_source") or "").strip()
    invalidation_reason = str(candidate_row.get("invalidation_reason") or candidate_meta.get("invalidation_reason") or "").strip()
    stop_family = str(candidate_row.get("stop_family") or candidate_meta.get("stop_family") or "").strip()
    stop_reference = str(candidate_row.get("stop_reference") or candidate_meta.get("stop_reference") or "").strip()
    stop_policy_source = str(candidate_row.get("stop_policy_source") or candidate_meta.get("stop_policy_source") or "").strip()
    setup_type = str(candidate_row.get("setup_type", "trend")).lower()
    signal_id = f"v2-{candidate_row.get('engine', 'trend')}-{setup_type}-{symbol}".lower()
    signal_meta = {
        "setup_type": candidate_row.get("setup_type"),
        "score": candidate_row.get("score"),
        "invalidation_source": invalidation_source,
    }
    if structure_target_price is not None:
        signal_meta["structure_target_price"] = structure_target_price
    for key, value in (
        ("invalidation_reason", invalidation_reason),
        ("stop_family", stop_family),
        ("stop_reference", stop_reference),
        ("stop_policy_source", stop_policy_source),
    ):
        if value:
            signal_meta[key] = value
    return TradeSignal(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        entry_price=round(entry_price, 8),
        stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8) if take_profit is not None and take_profit > 0 else None,
        source="strategy",
        timeframe="4h",
        tags=["v2", str(candidate_row.get("engine", "trend"))],
        meta=signal_meta,
    )


def _order_qty(account: AccountSnapshot, signal: TradeSignal, allocation: Mapping[str, Any]) -> float:
    if "execution_risk_budget" in allocation:
        final_risk_budget = _finite_numeric_or_default(
            allocation.get("execution_risk_budget"),
            "execution_risk_budget",
            default=0.0,
        )
    else:
        final_risk_budget = _finite_numeric_or_default(
            allocation.get("final_risk_budget"),
            "final_risk_budget",
            default=0.0,
        )
    risk_per_unit = abs(signal.entry_price - signal.stop_loss)
    if final_risk_budget <= 0 or risk_per_unit <= 0:
        return 0.0
    risk_budget_usdt = float(account.equity) * final_risk_budget
    qty = risk_budget_usdt / risk_per_unit
    return round(max(qty, 0.0), 8)


def _cap_order_qty_by_notional(qty: float, entry_price: float, max_notional_usdt: float | None) -> float:
    if qty <= 0.0 or entry_price <= 0.0:
        return 0.0
    if max_notional_usdt is None or max_notional_usdt <= 0.0:
        return round(max(qty, 0.0), 8)
    notional_capped_qty = max_notional_usdt / entry_price
    capped_qty = max(min(qty, notional_capped_qty), 0.0)
    return math.floor(capped_qty * 100_000_000) / 100_000_000


def _testnet_order_qty(
    account: AccountSnapshot,
    signal: TradeSignal,
    allocation: Mapping[str, Any],
    *,
    max_notional_usdt: float | None,
) -> float:
    risk_sized_qty = _order_qty(account, signal, allocation)
    return _cap_order_qty_by_notional(risk_sized_qty, signal.entry_price, max_notional_usdt)


def _floor_to_increment(value: float, increment: float) -> float:
    if increment <= 0.0:
        return value
    decimal_value = Decimal(str(value))
    decimal_increment = Decimal(str(increment))
    units = (decimal_value / decimal_increment).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * decimal_increment)


def _default_take_profit_price(order: OrderIntent, r_multiple: float) -> float | None:
    risk_per_unit = abs(float(order.entry_price) - float(order.stop_loss))
    if risk_per_unit <= 0.0:
        return None
    if order.side == "LONG":
        return float(order.entry_price) + risk_per_unit * r_multiple
    return float(order.entry_price) - risk_per_unit * r_multiple


def _finite_numeric_or_default(value: Any, field: str, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite numeric value")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be a finite numeric value")
    return numeric


def _mapping_copy(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return dict(value)


def _canonical_string_allowlist(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise TypeError(f"{field} must be a sequence of canonical strings")
    try:
        iterator = iter(value)
    except TypeError as exc:
        raise TypeError(f"{field} must be a sequence of canonical strings") from exc

    allowlist: list[str] = []
    for entry in iterator:
        if not isinstance(entry, str):
            raise TypeError(f"{field} entries must be canonical strings")
        if not entry or entry != entry.strip() or entry != entry.upper():
            raise ValueError(f"{field} entries must be canonical strings")
        allowlist.append(entry)
    return allowlist


def _strict_bool_or_default(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a bool")
    return value


def _testnet_preview_inputs(config: Any) -> tuple[list[str], bool]:
    execution = config.execution
    allowlist = _canonical_string_allowlist(
        getattr(execution, "testnet_allowed_symbols", ()),
        "testnet_allowed_symbols",
    )
    submission_enabled = _strict_bool_or_default(
        getattr(execution, "testnet_order_submission_enabled", False),
        "testnet_order_submission_enabled",
        default=False,
    )
    return allowlist, submission_enabled


def _align_testnet_order_to_exchange_filters(
    order: OrderIntent,
    symbol_metadata: Mapping[str, Any],
    *,
    max_notional_usdt: float,
) -> OrderIntent:
    tick_size = _finite_numeric_or_default(symbol_metadata.get("price_tick_size"), "price_tick_size", default=0.0)
    step_size = _finite_numeric_or_default(
        symbol_metadata.get("quantity_step_size"),
        "quantity_step_size",
        default=0.0,
    )
    entry_price = _floor_to_increment(float(order.entry_price), tick_size)
    stop_loss = _floor_to_increment(float(order.stop_loss), tick_size)
    meta = _mapping_copy(order.meta, "meta")
    take_profit = order.take_profit
    if take_profit is None:
        primary_take_profit = _default_take_profit_price(replace(order, entry_price=entry_price, stop_loss=stop_loss), 1.5)
        second_take_profit = _default_take_profit_price(replace(order, entry_price=entry_price, stop_loss=stop_loss), 2.0)
        take_profit = primary_take_profit
        if take_profit is not None:
            meta["default_take_profit_generated"] = True
            meta["default_take_profit_r_multiple"] = 1.5
            if second_take_profit is not None:
                meta["second_take_profit"] = _floor_to_increment(float(second_take_profit), tick_size)
                meta["second_take_profit_r_multiple"] = 2.0
    take_profit = _floor_to_increment(float(take_profit), tick_size) if take_profit is not None else None
    qty = _cap_order_qty_by_notional(float(order.qty), entry_price, max_notional_usdt)
    qty = _floor_to_increment(qty, step_size)
    return replace(order, qty=qty, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit, meta=meta)


def _build_testnet_order_preview(order: OrderIntent, config: Any) -> dict[str, Any]:
    allowlist, submission_enabled = _testnet_preview_inputs(config)
    metadata = load_testnet_exchange_metadata(allowlist)
    return build_validated_order_preview(
        order,
        exchange_metadata=metadata,
        allowlist=allowlist,
        max_order_notional_usdt=float(config.execution.testnet_max_order_notional_usdt),
        submission_enabled=submission_enabled,
        preview_source="accepted_signal",
        entry_order_policy=config.execution.entry_order_policy,
        maker_entry_timeout_seconds=config.execution.maker_entry_timeout_seconds,
    )


def _execution_risk_budget(
    account: AccountSnapshot,
    signal: TradeSignal,
    allocation: Mapping[str, Any],
    risk_config: RiskConfig,
) -> float:
    final_risk_budget = float(allocation.get("final_risk_budget", 0.0) or 0.0)
    if final_risk_budget <= 0.0:
        return 0.0
    if not _is_active_paper_first_rotation_probe_row(allocation):
        return final_risk_budget
    if signal.side != "LONG" or "rotation" not in {tag.lower() for tag in signal.tags}:
        return final_risk_budget
    if account.open_positions or account.open_orders:
        return final_risk_budget
    if account.equity <= 0.0 or signal.entry_price <= 0.0:
        return final_risk_budget

    stop_distance_pct = signal.risk_per_unit() / signal.entry_price
    if stop_distance_pct <= 0.0:
        return final_risk_budget

    notional_cap_pct = min(
        max(float(risk_config.max_total_risk_pct), 0.0),
        max(float(risk_config.max_symbol_risk_pct), 0.0),
        max(float(risk_config.max_net_exposure_pct), 0.0),
        max(float(risk_config.max_notional_pct), 0.0),
    )
    if notional_cap_pct <= 0.0:
        return 0.0
    probe_budget = notional_cap_pct * stop_distance_pct * 0.999
    return round(max(min(final_risk_budget, probe_budget), 0.0), 8)


def _with_state_file_override(config: Any) -> Any:
    env_state_file = os.environ.get(STATE_FILE_ENV)
    if not env_state_file:
        return config
    if hasattr(config, "state_file"):
        return replace(config, state_file=Path(env_state_file))
    return config


def resolve_runtime_input_paths(config: Any | None = None) -> RuntimeInputPaths:
    resolved_config = _with_state_file_override(config if config is not None else load_config())
    return RuntimeInputPaths(
        account_snapshot=_resolve_account_snapshot_path(config=resolved_config),
        market_context=_resolve_market_context_path(config=resolved_config),
        derivatives_snapshot=_resolve_derivatives_snapshot_path(config=resolved_config),
        state_file=Path(resolved_config.state_file),
    )


def _allocation_summary_payload(decision: Any) -> dict[str, Any]:
    if is_dataclass(decision):
        return asdict(decision)
    if isinstance(decision, Mapping):
        return dict(decision)
    raise ValueError("decision must be a mapping or dataclass")


def _allocation_summary_required_canonical_string(
    candidate: Mapping[str, Any],
    field: str,
    *,
    transform: str = "identity",
    allowed: set[str] | None = None,
) -> str:
    value = candidate.get(field)
    if value is None:
        return "LONG" if field == "side" else ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical string")
    canonical = value.strip()
    if not canonical:
        return ""
    if transform == "upper":
        normalized = canonical.upper()
    elif transform == "lower":
        normalized = canonical.lower()
    else:
        normalized = canonical
    if canonical != normalized:
        raise ValueError(f"{field} must be canonical")
    if allowed is not None and normalized not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return normalized


def _allocation_summary_numeric(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{field} must be numeric")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _allocation_summary(decision: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    payload = _allocation_summary_payload(decision)
    decision_meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
    payload["symbol"] = _allocation_summary_required_canonical_string(candidate, "symbol", transform="upper")
    payload["side"] = _allocation_summary_required_canonical_string(
        candidate, "side", transform="upper", allowed={"LONG", "SHORT"}
    )
    payload["setup_type"] = _allocation_summary_required_canonical_string(candidate, "setup_type", transform="upper")
    payload["score"] = _allocation_summary_numeric(candidate.get("score"), "score", default=0.0)
    timeframe_meta = candidate.get("timeframe_meta")
    if timeframe_meta is None:
        payload["timeframe_meta"] = {}
    elif not isinstance(timeframe_meta, Mapping):
        raise ValueError("timeframe_meta must be a mapping")
    else:
        payload["timeframe_meta"] = dict(timeframe_meta)
    for key in (
        "aggressiveness_multiplier",
        "quality_multiplier",
        "crowding_multiplier",
        "execution_friction_multiplier",
        "regime_hazard_multiplier",
        "late_stage_heat_multiplier",
    ):
        value = decision_meta.get(key)
        if value is not None:
            payload[key] = _allocation_summary_numeric(value, key)
    compression_reasons: list[str] = []
    if _allocation_summary_numeric(decision_meta.get("regime_hazard_multiplier"), "regime_hazard_multiplier", default=1.0) < 1.0:
        compression_reasons.append("regime_hazard")
    if _allocation_summary_numeric(decision_meta.get("late_stage_heat_multiplier"), "late_stage_heat_multiplier", default=1.0) < 1.0:
        compression_reasons.append("late_stage_heat")
    if compression_reasons:
        payload["compression_reasons"] = compression_reasons
    candidate_meta = candidate.get("meta") if isinstance(candidate.get("meta"), Mapping) else {}
    for key in (
        "entry_price",
        "stop_loss",
        "take_profit",
        "invalidation_source",
        "invalidation_reason",
        "stop_family",
        "stop_reference",
        "stop_policy_source",
    ):
        value = candidate.get(key)
        if value is None:
            value = candidate_meta.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _universes_payload(universes: UniverseBuildResult) -> dict[str, Any]:
    return {
        "major_universe": universes.major_universe,
        "rotation_universe": universes.rotation_universe,
        "short_universe": universes.short_universe,
        "major_count": len(universes.major_universe),
        "rotation_count": len(universes.rotation_universe),
        "short_count": len(universes.short_universe),
    }


def _jsonl_row_count(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0

    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _paper_trading_summary(mode: str, ledger_path: Path | None, execution_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if mode != "paper":
        return {}

    intents: list[dict[str, Any]] = []
    emitted_count = 0
    replayed_count = 0
    for row in execution_rows:
        execution = row.get("execution") if isinstance(row.get("execution"), Mapping) else {}
        intent = {
            "symbol": row.get("symbol"),
            "status": row.get("status"),
            "intent_id": row.get("intent_id"),
        }
        if execution.get("replayed"):
            replayed_count += 1
            replay_source = execution.get("replay_source")
            if replay_source is not None:
                intent["replay_source"] = replay_source
        elif execution.get("mode") == "paper":
            emitted_count += 1
        intents.append(intent)

    return {
        "mode": "paper",
        "ledger_path": str(ledger_path) if ledger_path is not None else None,
        "ledger_event_count": _jsonl_row_count(ledger_path),
        "emitted_count": emitted_count,
        "replayed_count": replayed_count,
        "intents": intents,
    }


def run_management_terminalization_pass(state: Any) -> None:
    for symbol, position in list(state.positions.items()):
        state.positions[symbol] = terminalize_all_unreachable_stages(dict(position))


def _notify_testnet_position_close_events(state: Any, executor: OrderExecutor) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    for key, event in list(getattr(state, "active_orders", {}).items()):
        if not isinstance(event, dict):
            continue
        if event.get("event") != "POSITION_CLOSED" or event.get("notified"):
            continue
        symbol = event.get("symbol") or key.replace("position-closed-", "")
        message = " | ".join(
            [
                "Trading testnet CLOSED",
                f"symbol={symbol}",
                f"side={event.get('side')}",
                f"intent_id={event.get('intent_id')}",
                f"entry={event.get('entry_price')}",
                f"tp={event.get('take_profit')}",
                f"sl={event.get('stop_loss')}",
                f"closed_at={event.get('closed_at_bj')}",
            ]
        )
        try:
            if executor.mode == "testnet" and executor.config.execution.feishu_notifications_enabled:
                executor.feishu_notifier(message)
            event["notified"] = True
            event["notified_at_bj"] = event.get("closed_at_bj")
            sent.append({"key": key, "symbol": symbol, "message": message})
        except Exception:
            event["notification_error"] = "feishu_notification_failed"
    return sent


def _candidate_setup_type(candidate: Any) -> str:
    if isinstance(candidate, Mapping):
        return str(candidate.get("setup_type", "") or "").strip().upper()
    return str(getattr(candidate, "setup_type", "") or "").strip().upper()


def _filter_disabled_setup_types(
    candidates: list[Any], disabled_setup_types: frozenset[str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    if not disabled_setup_types:
        return list(candidates), []

    kept: list[Any] = []
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        setup_type = _candidate_setup_type(candidate)
        if setup_type in disabled_setup_types:
            row = _candidate_row(candidate)
            row["reason"] = "disabled_setup_type"
            row["disabled_by"] = setup_type
            filtered.append(row)
            continue
        kept.append(candidate)
    return kept, filtered


def main() -> None:
    config = _with_state_file_override(load_config())
    disabled_engines = frozenset(normalize_engine_names(getattr(config.execution, "disabled_engines", ())))
    disabled_setup_types = frozenset(normalize_setup_types(getattr(config.execution, "disabled_setup_types", ())))
    if config.execution.mode == "live" and not config.execution.allow_live_execution:
        raise RuntimeError("live execution is disabled unless TRADING_ALLOW_LIVE_EXECUTION is explicitly enabled")
    if config.execution.mode == "live":
        raise RuntimeError("live 模式尚未启用；当前 MVP 仅支持 paper / dry-run")
    store = build_state_store(config)
    state = store.load()
    account = load_account_snapshot(config=config)
    market_rows = load_market_context(_resolve_market_context_path(config=config))
    market = _market_payload(market_rows)
    derivatives = load_derivatives_snapshot(_resolve_derivatives_snapshot_path(config=config))
    derivatives_summary = summarize_derivatives_risk(derivatives)
    regime = classify_regime(market_rows, derivatives, disabled_engines=disabled_engines)
    universes = build_universes(market, derivatives=derivatives)

    trend_candidates = (
        []
        if "trend" in disabled_engines
        else generate_trend_candidates(
            market,
            derivatives=derivatives,
            include_high_liquidity_strong_names=False,
            regime=regime,
            entry_profile=config.entry_profile,
        )
    )
    rotation_candidates = (
        []
        if "rotation" in disabled_engines
        else generate_rotation_candidates(
            market,
            rotation_universe=universes.rotation_universe,
            derivatives=derivatives,
            regime=regime,
            entry_profile=config.entry_profile,
        )
    )
    short_candidates = (
        []
        if "short" in disabled_engines
        else generate_short_candidates(
            market,
            short_universe=universes.short_universe,
            derivatives=derivatives,
            regime=regime,
        )
    )
    disabled_setup_type_filtered_candidates: list[dict[str, Any]] = []
    trend_candidates, filtered = _filter_disabled_setup_types(trend_candidates, disabled_setup_types)
    disabled_setup_type_filtered_candidates.extend(filtered)
    rotation_candidates, filtered = _filter_disabled_setup_types(rotation_candidates, disabled_setup_types)
    disabled_setup_type_filtered_candidates.extend(filtered)
    short_candidates, filtered = _filter_disabled_setup_types(short_candidates, disabled_setup_types)
    disabled_setup_type_filtered_candidates.extend(filtered)
    candidate_rows: list[dict[str, Any]] = []
    validated_rows: list[dict[str, Any]] = []
    for candidate in [*trend_candidates, *rotation_candidates, *short_candidates]:
        row = _candidate_with_stop_taxonomy(candidate, market, regime)
        validation = validate_candidate_for_allocation(row, account)
        row["validation"] = {"allowed": validation.allowed, "reasons": list(validation.reasons), "metrics": validation.metrics}
        row["baseline_risk_proxy"] = round(max(float(row.get("score", 0.0) or 0.0) * 0.001, 0.0), 6)
        candidate_rows.append(row)
        if validation.allowed:
            validated_rows.append(row)

    ranked_candidates = sorted(validated_rows, key=_candidate_sort_key)
    decisions = allocate_candidates(account=account, candidates=validated_rows, regime=regime, config=config)
    allocation_rows = [_allocation_summary(decision, candidate) for decision, candidate in zip(decisions, ranked_candidates)]

    executor = OrderExecutor(
        config,
        mode=config.execution.mode,
        persist_state=store.save if config.execution.mode != "dry-run" else None,
    )
    executor.execution_log_path = config.state_file.parent / "execution_log.jsonl"
    executor.execution_log_path.parent.mkdir(parents=True, exist_ok=True)
    sync_positions_from_account(state, account)
    _notify_testnet_position_close_events(state, executor)

    execution_rows: list[dict[str, Any]] = []
    for allocation in allocation_rows:
        if allocation.get("status") not in {"ACCEPTED", "DOWNSIZED"}:
            continue
        if str(allocation.get("engine", "")).lower() == "short":
            allocation["execution"] = {"status": "SKIPPED", "reason": "short_execution_not_enabled"}
            continue
        try:
            signal = _candidate_signal(allocation, market, regime=regime, risk_config=config.risk)
        except ValueError as exc:
            allocation["execution"] = {"status": "BLOCKED", "reason": str(exc)}
            continue
        allocation["entry_price"] = signal.entry_price
        allocation["stop_loss"] = signal.stop_loss
        if signal.take_profit is not None:
            allocation["take_profit"] = signal.take_profit
        execution_risk_budget = _execution_risk_budget(account, signal, allocation, config.risk)
        if execution_risk_budget < float(allocation.get("final_risk_budget", 0.0) or 0.0):
            allocation["execution_risk_budget"] = execution_risk_budget
            allocation["execution_probe_downsized"] = True
        signal_validation, _signal_context = validate_signal(
            signal,
            account,
            config.risk,
            risk_pct_override=execution_risk_budget if execution_risk_budget > 0 else None,
        )
        if not signal_validation.allowed:
            allocation["execution"] = {
                "status": "BLOCKED",
                "reason": "; ".join(signal_validation.reasons) or "signal validation failed",
            }
            continue
        replayed_execution = replay_processed_execution(
            state,
            signal,
            executor.execution_log_path,
            executor.paper_ledger_path if config.execution.mode == "paper" else None,
        )
        if replayed_execution:
            if config.execution.mode != "dry-run" and not already_processed(state, signal):
                fingerprint = mark_processed(state, signal)
                store.record_signal(state, signal.symbol, fingerprint, config.risk.cooldown_minutes)
                store.save(state)
            allocation["execution"] = {
                "status": replayed_execution.get("status"),
                "intent_id": replayed_execution.get("intent_id"),
            }
            execution_rows.append(
                {
                    "symbol": signal.symbol,
                    "status": replayed_execution.get("status"),
                    "intent_id": replayed_execution.get("intent_id"),
                    "qty": 0.0,
                    "execution": {
                        "replayed": True,
                        "replay_source": replayed_execution.get("replay_source"),
                    },
                }
            )
            continue
        if store.circuit_breaker_active(state):
            allocation["execution"] = {"status": "BLOCKED", "reason": "circuit_breaker_active"}
            continue
        if config.execution.mode == "testnet":
            existing_position_block = _testnet_existing_position_entry_block(state, signal.symbol)
            if existing_position_block is not None:
                allocation["execution"] = existing_position_block
                continue
        if already_processed(state, signal):
            allocation["execution"] = {
                "status": "SKIPPED",
                "reason": "already_processed",
            }
            continue
        if store.in_cooldown(state, signal.symbol):
            allocation["execution"] = {"status": "SKIPPED", "reason": "cooldown_active"}
            continue

        qty = _order_qty(account, signal, allocation)
        if config.execution.mode == "testnet":
            qty = _testnet_order_qty(
                account,
                signal,
                allocation,
                max_notional_usdt=float(config.execution.testnet_max_order_notional_usdt),
            )
        if qty <= 0:
            allocation["execution"] = {"status": "SKIPPED", "reason": "invalid_qty"}
            continue

        order = OrderIntent(
            intent_id=intent_id(signal),
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            qty=qty,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status="PENDING",
            meta={
                "engine": allocation.get("engine"),
                "setup_type": allocation.get("setup_type"),
                "final_risk_budget": allocation.get("final_risk_budget", 0.0),
                "execution_risk_budget": allocation.get("execution_risk_budget", allocation.get("final_risk_budget", 0.0)),
                "invalidation_source": signal.meta.get("invalidation_source"),
                "invalidation_reason": signal.meta.get("invalidation_reason"),
                "stop_family": signal.meta.get("stop_family"),
                "stop_reference": signal.meta.get("stop_reference"),
                "stop_policy_source": signal.meta.get("stop_policy_source"),
                "taxonomy_stop_loss": signal.stop_loss,
                "structure_target_price": signal.meta.get("structure_target_price"),
                "original_position_qty": qty,
                **derive_target_management_fields(
                    side=signal.side,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    structure_target_price=signal.meta.get("structure_target_price"),
                    legacy_take_profit=signal.take_profit,
                    original_position_qty=qty,
                ),
            },
        )
        order.meta["remaining_position_qty"] = qty
        if config.execution.mode == "testnet":
            exchange_metadata = load_testnet_exchange_metadata(config.execution.testnet_allowed_symbols)
            symbol_metadata = exchange_metadata.get(order.symbol, {})
            order = _align_testnet_order_to_exchange_filters(
                order,
                symbol_metadata,
                max_notional_usdt=float(config.execution.testnet_max_order_notional_usdt),
            )
            order.meta["original_position_qty"] = order.qty
            order.meta["remaining_position_qty"] = order.qty
            order.meta["validated_order_preview"] = build_validated_order_preview(
                order,
                exchange_metadata=exchange_metadata,
                allowlist=list(config.execution.testnet_allowed_symbols),
                max_order_notional_usdt=float(config.execution.testnet_max_order_notional_usdt),
                submission_enabled=bool(config.execution.testnet_order_submission_enabled),
                preview_source="accepted_signal",
                entry_order_policy=config.execution.entry_order_policy,
                maker_entry_timeout_seconds=config.execution.maker_entry_timeout_seconds,
            )
        execution = executor.execute(order, state)
        if config.execution.mode != "dry-run":
            if config.execution.mode != "paper":
                apply_executed_intent(state, order)
            fingerprint = mark_processed(state, signal)
            store.record_signal(state, signal.symbol, fingerprint, config.risk.cooldown_minutes)
            store.save(state)
        allocation["execution"] = {"status": order.status, "intent_id": order.intent_id}
        execution_rows.append(
            {
                "symbol": order.symbol,
                "status": order.status,
                "intent_id": order.intent_id,
                "qty": order.qty,
                "execution": execution,
            }
        )

    regime_payload = {
        **asdict(regime),
        "late_stage_heat": derivatives_summary.get("late_stage_heat", "none"),
        "execution_hazard": derivatives_summary.get("execution_hazard", "none"),
    }
    management = evaluate_portfolio(state, regime=regime_payload)
    management_intents = build_management_action_intents(state, management)
    run_management_terminalization_pass(state)
    management = evaluate_portfolio(state, regime=regime_payload)
    management_intents = build_management_action_intents(state, management)
    management_previews = executor.preview_management_actions(management_intents, account.open_orders)
    if config.execution.mode == "paper":
        executor.execute_management_actions(management_intents, state)
        run_management_terminalization_pass(state)
        management = evaluate_portfolio(state, regime=regime_payload)
        management_intents = build_management_action_intents(state, management)
        management_previews = executor.preview_management_actions(management_intents, account.open_orders)
    lifecycle_updates = advance_lifecycle_positions(state, config.lifecycle)
    lifecycle_summary = build_lifecycle_report(
        lifecycle_updates=lifecycle_updates,
        management_suggestions=management,
    )

    state.latest_regime = regime_payload
    state.latest_entry_profile = config.entry_profile.as_dict()
    state.latest_universes = _universes_payload(universes)
    state.latest_candidates = candidate_rows
    state.latest_allocations = allocation_rows
    state.disabled_setup_type_filtered_candidates = disabled_setup_type_filtered_candidates
    state.paper_trading = _paper_trading_summary(
        config.execution.mode,
        executor.paper_ledger_path if config.execution.mode == "paper" else None,
        execution_rows,
    )
    optimization_dir = Path(config.state_file).parent / "optimization"
    signal_facts_path = optimization_dir / "signal_facts.jsonl"
    trade_outcomes_path = optimization_dir / "trade_outcomes.jsonl"
    daily_metrics_path = optimization_dir / "daily_metrics.json"
    health_report_path = optimization_dir / "health_report.json"
    recommendations_path = optimization_dir / "recommendations.json"
    promotion_decision_path = optimization_dir / "promotion_decision.json"
    collect_signal_facts(
        signal_facts_path=signal_facts_path,
        candidate_rows=candidate_rows,
        allocation_rows=allocation_rows,
        execution_rows=execution_rows,
        regime=regime_payload,
        mode=config.execution.mode,
        runtime_env=Path(config.state_file).parent.name,
    )
    collect_trade_outcomes(
        trade_outcomes_path=trade_outcomes_path,
        signal_facts_path=signal_facts_path,
        runtime_positions=state.positions,
        paper_ledger_path=executor.paper_ledger_path if config.execution.mode == "paper" else None,
    )
    metrics_payload = write_daily_metrics_and_health_report(
        trade_outcomes_path=trade_outcomes_path,
        signal_facts_path=signal_facts_path,
        daily_metrics_path=daily_metrics_path,
        health_report_path=health_report_path,
        runtime_positions=state.positions,
    )
    recommendations_payload = write_recommendations(
        daily_metrics_path=daily_metrics_path,
        health_report_path=health_report_path,
        recommendations_path=recommendations_path,
        previous_recommendations_path=recommendations_path,
        recorded_at_bj=state.updated_at_bj,
    )
    promotion_payload = write_promotion_decision(
        recommendations_path=recommendations_path,
        promotion_decision_path=promotion_decision_path,
        recorded_at_bj=state.updated_at_bj,
    )
    state.optimization_summary = {
        "daily_metrics_status": (metrics_payload.get("health_report") or {}).get("status"),
        "recommendation_count": recommendations_payload.get("recommendation_count", 0),
        "suppressed_count": len(recommendations_payload.get("suppressed") or []),
        "promotion_decision": promotion_payload.get("decision") or promotion_payload.get("status"),
        "promotion_summary": promotion_payload.get("summary"),
        "artifacts_dir": str(optimization_dir),
    }
    state.latest_lifecycle = lifecycle_updates
    state.lifecycle_summary = lifecycle_summary
    state.trend_candidates = [row for row in candidate_rows if str(row.get("engine", "")).lower() == "trend"]
    trend_review_notes = _trend_review_notes(
        market=market,
        major_universe=list(state.latest_universes.get("major_universe", [])),
        trend_candidates=state.trend_candidates,
        derivatives=derivatives,
        entry_profile=config.entry_profile,
    )
    state.trend_summary = build_trend_report(
        trend_candidates=state.trend_candidates,
        allocations=state.latest_allocations,
        major_universe=list(state.latest_universes.get("major_universe", [])),
    )
    if trend_review_notes:
        state.trend_summary["review_notes"] = trend_review_notes
    state.rotation_candidates = [row for row in candidate_rows if str(row.get("engine", "")).lower() == "rotation"]
    rotation_review_notes = _rotation_review_notes(
        market=market,
        rotation_universe=list(state.latest_universes.get("rotation_universe", [])),
        rotation_candidates=rotation_candidates,
        derivatives=derivatives,
        entry_profile=config.entry_profile,
    )
    state.rotation_summary = build_rotation_report(
        rotation_candidates=state.rotation_candidates,
        allocations=state.latest_allocations,
        executions=execution_rows,
        rotation_universe=list(state.latest_universes.get("rotation_universe", [])),
    )
    state.short_candidates = [row for row in candidate_rows if str(row.get("engine", "")).lower() == "short"]
    short_review_notes = _short_review_notes(
        market=market,
        short_universe=list(state.latest_universes.get("short_universe", [])),
        short_candidates=short_candidates,
        derivatives=derivatives,
    )
    state.short_summary = build_short_report(
        short_candidates=state.short_candidates,
        allocations=state.latest_allocations,
        short_universe=list(state.latest_universes.get("short_universe", [])),
    )
    state.partial_v2_coverage = True
    store.replace_management_suggestions(state, management)
    store.replace_management_action_previews(state, management_previews)
    store.save(state)

    regime_summary = build_regime_summary(
        regime=state.latest_regime,
        universes=state.latest_universes,
        candidates=state.latest_candidates,
        allocations=state.latest_allocations,
        executions=execution_rows,
        trend_report={**state.trend_summary, "review_notes": trend_review_notes}
        if trend_review_notes
        else state.trend_summary,
        rotation_report={**state.rotation_summary, "review_notes": rotation_review_notes}
        if rotation_review_notes
        else state.rotation_summary,
        short_report={**state.short_summary, "review_notes": short_review_notes} if short_review_notes else state.short_summary,
    )
    print(
        json.dumps(
            {
                "regime": regime_summary,
                "entry_profile": state.latest_entry_profile,
                "portfolio": {
                "tracked_positions": len(state.positions),
                "management_suggestions": management,
                "management_action_previews": management_previews,
                "lifecycle_updates": lifecycle_updates,
                "lifecycle_summary": lifecycle_summary,
                "paper_trading": state.paper_trading,
                "account_open_orders": len(account.open_orders),
            },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
