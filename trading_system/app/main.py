from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .config import build_config
from .data_sources import load_derivatives_snapshot, load_market_context
from .execution.executor import OrderExecutor
from .execution.idempotency import already_processed, intent_id, mark_processed
from .market_regime import classify_regime
from .portfolio.allocator import allocate_candidates
from .portfolio.lifecycle import advance_lifecycle_positions, build_management_action_intents, evaluate_portfolio
from .portfolio.positions import apply_executed_intent, sync_positions_from_account
from .reporting.regime_report import build_regime_summary
from .risk.validator import validate_candidate_for_allocation
from .signals.trend_engine import generate_trend_candidates
from .storage.state_store import build_state_store
from .universe.builder import UniverseBuildResult, build_universes
from .types import AccountSnapshot, OrderIntent, PositionSnapshot, TradeSignal

BASE = Path(__file__).resolve().parents[1]
ACCOUNT_SNAPSHOT = BASE / "data" / "account_snapshot.json"
ACCOUNT_SNAPSHOT_FILE_ENV = "TRADING_ACCOUNT_SNAPSHOT_FILE"
STATE_FILE_ENV = "TRADING_STATE_FILE"


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


def _resolve_account_snapshot_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_value = os.environ.get(ACCOUNT_SNAPSHOT_FILE_ENV)
    if env_value:
        return Path(env_value)
    return ACCOUNT_SNAPSHOT


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


def load_account_snapshot(path: str | Path | None = None) -> AccountSnapshot:
    raw = json.loads(_resolve_account_snapshot_path(path).read_text())
    if "futures" in raw:
        return _load_v1_account_snapshot(raw)
    return _load_v2_account_snapshot(raw)


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
        "sector": getattr(candidate, "sector", None),
        "timeframe_meta": dict(getattr(candidate, "timeframe_meta", {}) or {}),
        "liquidity_meta": dict(getattr(candidate, "liquidity_meta", {}) or {}),
    }


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
    return (-float(row.get("score", 0.0) or 0.0), str(row.get("symbol", "")), str(row.get("engine", "")))


def _candidate_signal(candidate: Mapping[str, Any], market: Mapping[str, Any]) -> TradeSignal:
    symbol = str(candidate.get("symbol", "")).upper()
    side = str(candidate.get("side", "LONG")).upper()
    payload = dict(market.get("symbols", {}).get(symbol, {}))
    daily = dict(payload.get("daily", {}))
    entry_price = _float(daily, "close")
    if entry_price <= 0:
        entry_price = 1.0
    stop_loss = entry_price * (0.98 if side == "LONG" else 1.02)
    take_profit = entry_price * (1.04 if side == "LONG" else 0.96)
    setup_type = str(candidate.get("setup_type", "trend")).lower()
    signal_id = f"v2-{candidate.get('engine', 'trend')}-{setup_type}-{symbol}".lower()
    return TradeSignal(
        signal_id=signal_id,
        symbol=symbol,
        side=side,
        entry_price=round(entry_price, 8),
        stop_loss=round(stop_loss, 8),
        take_profit=round(take_profit, 8),
        source="strategy",
        timeframe="4h",
        tags=["v2", str(candidate.get("engine", "trend"))],
        meta={"setup_type": candidate.get("setup_type"), "score": candidate.get("score")},
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
    payload["symbol"] = str(candidate.get("symbol", ""))
    payload["side"] = str(candidate.get("side", "LONG"))
    payload["setup_type"] = str(candidate.get("setup_type", ""))
    payload["score"] = float(candidate.get("score", 0.0) or 0.0)
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


def main() -> None:
    config = _with_state_file_override(load_config())
    store = build_state_store(config)
    state = store.load()
    account = load_account_snapshot()
    market_rows = load_market_context()
    market = _market_payload(market_rows)
    derivatives = load_derivatives_snapshot()
    regime = classify_regime(market_rows, derivatives)
    universes = build_universes(market)

    trend_candidates = generate_trend_candidates(market)
    candidate_rows: list[dict[str, Any]] = []
    validated_rows: list[dict[str, Any]] = []
    for candidate in trend_candidates:
        row = _candidate_row(candidate)
        validation = validate_candidate_for_allocation(row, account)
        row["validation"] = {"allowed": validation.allowed, "reasons": list(validation.reasons), "metrics": validation.metrics}
        row["baseline_risk_proxy"] = round(max(float(row.get("score", 0.0) or 0.0) * 0.001, 0.0), 6)
        candidate_rows.append(row)
        if validation.allowed:
            validated_rows.append(row)

    ranked_candidates = sorted(validated_rows, key=_candidate_sort_key)
    decisions = allocate_candidates(account=account, candidates=validated_rows, regime=regime, config=config)
    allocation_rows = [_allocation_summary(decision, candidate) for decision, candidate in zip(decisions, ranked_candidates)]

    executor = OrderExecutor(config, mode="paper")
    sync_positions_from_account(state, account)

    execution_rows: list[dict[str, Any]] = []
    for allocation in allocation_rows:
        if allocation.get("status") not in {"ACCEPTED", "DOWNSIZED"}:
            continue
        signal = _candidate_signal(allocation, market)
        if store.circuit_breaker_active(state):
            allocation["execution"] = {"status": "BLOCKED", "reason": "circuit_breaker_active"}
            continue
        if store.in_cooldown(state, signal.symbol):
            allocation["execution"] = {"status": "SKIPPED", "reason": "cooldown_active"}
            continue
        if already_processed(state, signal):
            allocation["execution"] = {"status": "SKIPPED", "reason": "already_processed"}
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
            },
        )
        execution = executor.execute(order, state)
        apply_executed_intent(state, order)
        fingerprint = mark_processed(state, signal)
        store.record_signal(state, signal.symbol, fingerprint, config.risk.cooldown_minutes)
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

    management = evaluate_portfolio(state)
    management_intents = build_management_action_intents(state, management)
    management_previews = executor.preview_management_actions(management_intents, account.open_orders)
    lifecycle_updates = advance_lifecycle_positions(state, config.lifecycle)

    state.latest_regime = asdict(regime)
    state.latest_universes = _universes_payload(universes)
    state.latest_candidates = candidate_rows
    state.latest_allocations = allocation_rows
    state.latest_lifecycle = lifecycle_updates
    state.rotation_candidates = []
    state.short_candidates = []
    state.partial_v2_coverage = True
    store.replace_management_suggestions(state, management)
    store.replace_management_action_previews(state, management_previews)
    store.save(state)

    regime_summary = build_regime_summary(
        regime=regime,
        universes=state.latest_universes,
        candidates=state.latest_candidates,
        allocations=state.latest_allocations,
        executions=execution_rows,
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
                    "account_open_orders": len(account.open_orders),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
