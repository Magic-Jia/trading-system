from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .config import build_config, runtime_path_defaults_enabled
from .data_sources import load_derivatives_snapshot, load_market_context
from .execution.executor import OrderExecutor
from .execution.idempotency import already_processed, intent_id, mark_processed, replay_processed_execution
from .market_regime import classify_regime, summarize_derivatives_risk
from .market_regime.derivatives import symbol_derivatives_features
from .portfolio.allocator import allocate_candidates
from .portfolio.lifecycle import advance_lifecycle_positions, build_management_action_intents, evaluate_portfolio
from .portfolio.positions import apply_executed_intent, sync_positions_from_account
from .reporting.daily_report import build_lifecycle_report, build_rotation_report, build_short_report
from .reporting.regime_report import build_regime_summary
from .risk.stop_policy import build_stop_policy
from .signals.rotation_engine import generate_rotation_candidates
from .signals.short_engine import (
    _reject_crowded_short_squeeze_risk,
    _setup_type as _short_setup_type,
    _trend_broken,
    generate_short_candidates,
)
from .risk.validator import validate_candidate_for_allocation, validate_candidate_for_execution, validate_signal
from .signals.trend_engine import generate_trend_candidates
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
    return [
        PositionSnapshot(
            symbol=str(row["symbol"]),
            side=str(row.get("side", row.get("positionSide", "LONG"))),
            qty=abs(_float(row, "qty", "position_amt", "positionAmt", "amt")),
            entry_price=_float(row, "entry_price", "entryPrice", "entry"),
            mark_price=_float(row, "mark_price", "markPrice", "mark"),
            unrealized_pnl=_float(row, "unrealized_pnl", "upl", "unRealizedProfit"),
            notional=_float(row, "notional"),
            leverage=_float(row, "leverage") if row.get("leverage") is not None else None,
            strategy_tag=row.get("strategy_tag"),
        )
        for row in rows
        if abs(_float(row, "qty", "position_amt", "positionAmt", "amt")) > 0
    ]


def _load_v1_account_snapshot(raw: dict[str, Any]) -> AccountSnapshot:
    futures = raw["futures"]
    open_orders = futures.get("open_orders", futures.get("openOrders", raw.get("open_orders", raw.get("openOrders", []))))
    if not isinstance(open_orders, list):
        open_orders = []
    positions = _positions_from_rows(list(futures.get("positions", [])))
    return AccountSnapshot(
        equity=float(futures["total_wallet_balance"]),
        available_balance=float(futures.get("available_balance", futures["total_wallet_balance"])),
        futures_wallet_balance=float(futures["total_wallet_balance"]),
        open_positions=positions,
        open_orders=open_orders,
        meta={"source": "account_snapshot.json"},
    )


def _load_v2_account_snapshot(raw: dict[str, Any]) -> AccountSnapshot:
    open_positions = raw.get("open_positions", raw.get("positions", []))
    if not isinstance(open_positions, list):
        open_positions = []
    open_orders = raw.get("open_orders", raw.get("openOrders", []))
    if not isinstance(open_orders, list):
        open_orders = []

    equity = _float(raw, "equity", "total_wallet_balance")
    available_balance = _float(raw, "available_balance")
    if available_balance <= 0:
        available_balance = equity
    futures_wallet_balance = _float(raw, "futures_wallet_balance", "total_wallet_balance")
    if futures_wallet_balance <= 0:
        futures_wallet_balance = equity

    return AccountSnapshot(
        equity=equity,
        available_balance=available_balance,
        futures_wallet_balance=futures_wallet_balance,
        open_positions=_positions_from_rows(open_positions),
        open_orders=open_orders,
        meta=dict(raw.get("meta") or {}),
    )


def _load_account_snapshot_payload(raw: dict[str, Any]) -> AccountSnapshot:
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
        return dict(candidate)
    if is_dataclass(candidate):
        return asdict(candidate)
    return {
        "engine": str(getattr(candidate, "engine", "")),
        "setup_type": str(getattr(candidate, "setup_type", "")),
        "symbol": str(getattr(candidate, "symbol", "")),
        "side": str(getattr(candidate, "side", "LONG")),
        "score": _float({"score": getattr(candidate, "score", 0.0)}, "score"),
        "stop_loss": _float({"stop_loss": getattr(candidate, "stop_loss", 0.0)}, "stop_loss"),
        "invalidation_source": str(getattr(candidate, "invalidation_source", "") or ""),
        "sector": getattr(candidate, "sector", None),
        "timeframe_meta": dict(getattr(candidate, "timeframe_meta", {}) or {}),
        "liquidity_meta": dict(getattr(candidate, "liquidity_meta", {}) or {}),
    }


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
    return (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", "")), str(row.get("engine", "")))


def _short_candidate_symbol(candidate: Any) -> str:
    if isinstance(candidate, Mapping):
        return str(candidate.get("symbol", "")).upper().strip()
    return str(getattr(candidate, "symbol", "")).upper().strip()


def _short_review_notes(
    *,
    market: Mapping[str, Any],
    short_universe: list[Mapping[str, Any]],
    short_candidates: list[Any],
    derivatives: Mapping[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    candidate_symbols = {_short_candidate_symbol(candidate) for candidate in short_candidates}
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


def _candidate_with_stop_taxonomy(candidate: Any, market: Mapping[str, Any], regime: Any | None = None) -> dict[str, Any]:
    row = _candidate_row(candidate)
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
    return row


def _candidate_signal(candidate: Mapping[str, Any], market: Mapping[str, Any], regime: Any | None = None) -> TradeSignal:
    candidate_row = _candidate_with_stop_taxonomy(candidate, market, regime)
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
        take_profit = entry_price * (1.04 if side == "LONG" else 0.96)
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
        take_profit=round(take_profit, 8),
        source="strategy",
        timeframe="4h",
        tags=["v2", str(candidate_row.get("engine", "trend"))],
        meta=signal_meta,
    )


def _order_qty(account: AccountSnapshot, signal: TradeSignal, allocation: Mapping[str, Any]) -> float:
    final_risk_budget = float(allocation.get("final_risk_budget", 0.0) or 0.0)
    risk_per_unit = abs(signal.entry_price - signal.stop_loss)
    if final_risk_budget <= 0 or risk_per_unit <= 0:
        return 0.0
    risk_budget_usdt = float(account.equity) * final_risk_budget
    qty = risk_budget_usdt / risk_per_unit
    return round(max(qty, 0.0), 8)


def _with_state_file_override(config: Any) -> Any:
    env_state_file = os.environ.get(STATE_FILE_ENV)
    if not env_state_file:
        return config
    if hasattr(config, "state_file"):
        return replace(config, state_file=Path(env_state_file))
    return config


def _allocation_summary(decision: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    if is_dataclass(decision):
        payload = asdict(decision)
    else:
        payload = dict(decision)
    decision_meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
    payload["symbol"] = str(candidate.get("symbol", ""))
    payload["side"] = str(candidate.get("side", "LONG"))
    payload["setup_type"] = str(candidate.get("setup_type", ""))
    payload["score"] = float(candidate.get("score", 0.0) or 0.0)
    payload["timeframe_meta"] = dict(candidate.get("timeframe_meta") or {})
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
            payload[key] = value
    compression_reasons: list[str] = []
    if float(decision_meta.get("regime_hazard_multiplier", 1.0) or 1.0) < 1.0:
        compression_reasons.append("regime_hazard")
    if float(decision_meta.get("late_stage_heat_multiplier", 1.0) or 1.0) < 1.0:
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


def main() -> None:
    config = _with_state_file_override(load_config())
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
    regime = classify_regime(market_rows, derivatives)
    universes = build_universes(market, derivatives=derivatives)

    trend_candidates = generate_trend_candidates(
        market,
        derivatives=derivatives,
        include_high_liquidity_strong_names=False,
    )
    rotation_candidates = generate_rotation_candidates(
        market,
        rotation_universe=universes.rotation_universe,
        derivatives=derivatives,
        regime=regime,
    )
    short_candidates = generate_short_candidates(
        market,
        short_universe=universes.short_universe,
        derivatives=derivatives,
        regime=regime,
    )
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

    execution_rows: list[dict[str, Any]] = []
    for allocation in allocation_rows:
        if allocation.get("status") not in {"ACCEPTED", "DOWNSIZED"}:
            continue
        if str(allocation.get("engine", "")).lower() == "short":
            allocation["execution"] = {"status": "SKIPPED", "reason": "short_execution_not_enabled"}
            continue
        try:
            signal = _candidate_signal(allocation, market, regime=regime)
        except ValueError as exc:
            allocation["execution"] = {"status": "BLOCKED", "reason": str(exc)}
            continue
        signal_validation, _signal_context = validate_signal(signal, account, config.risk)
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
                "invalidation_source": signal.meta.get("invalidation_source"),
                "invalidation_reason": signal.meta.get("invalidation_reason"),
                "stop_family": signal.meta.get("stop_family"),
                "stop_reference": signal.meta.get("stop_reference"),
                "stop_policy_source": signal.meta.get("stop_policy_source"),
                "taxonomy_stop_loss": signal.stop_loss,
            },
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
    management_previews = executor.preview_management_actions(management_intents, account.open_orders)
    lifecycle_updates = advance_lifecycle_positions(state, config.lifecycle)
    lifecycle_summary = build_lifecycle_report(
        lifecycle_updates=lifecycle_updates,
        management_suggestions=management,
    )

    state.latest_regime = regime_payload
    state.latest_universes = _universes_payload(universes)
    state.latest_candidates = candidate_rows
    state.latest_allocations = allocation_rows
    state.paper_trading = _paper_trading_summary(
        config.execution.mode,
        executor.paper_ledger_path if config.execution.mode == "paper" else None,
        execution_rows,
    )
    state.latest_lifecycle = lifecycle_updates
    state.lifecycle_summary = lifecycle_summary
    state.rotation_candidates = [row for row in candidate_rows if str(row.get("engine", "")).lower() == "rotation"]
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
        rotation_report=state.rotation_summary,
        short_report={**state.short_summary, "review_notes": short_review_notes} if short_review_notes else state.short_summary,
    )
    print(
        json.dumps(
            {
                "regime": regime_summary,
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
