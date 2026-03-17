from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_CONFIG
from .execution.executor import OrderExecutor
from .execution.idempotency import already_processed, intent_id, mark_processed
from .portfolio.lifecycle import build_management_action_intents, evaluate_portfolio
from .portfolio.positions import apply_executed_intent, sync_positions_from_account
from .risk.validator import validate_signal
from .storage.state_store import build_state_store
from .types import AccountSnapshot, OrderIntent, PositionSnapshot, TradeSignal

BASE = Path(__file__).resolve().parents[1]
ENTRY_TEMPLATES = BASE / "data" / "entry_templates.json"
ACCOUNT_SNAPSHOT = BASE / "data" / "account_snapshot.json"


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


def load_account_snapshot() -> AccountSnapshot:
    raw = json.loads(ACCOUNT_SNAPSHOT.read_text())
    futures = raw["futures"]
    open_orders = futures.get("open_orders", futures.get("openOrders", raw.get("open_orders", raw.get("openOrders", []))))
    if not isinstance(open_orders, list):
        open_orders = []
    positions = [
        PositionSnapshot(
            symbol=row["symbol"],
            side=row.get("side", row.get("positionSide", "LONG")),
            qty=abs(_float(row, "position_amt", "positionAmt", "amt")),
            entry_price=_float(row, "entry_price", "entryPrice", "entry"),
            mark_price=_float(row, "mark_price", "markPrice", "mark"),
            unrealized_pnl=_float(row, "upl", "unRealizedProfit"),
            notional=_float(row, "notional"),
            leverage=_float(row, "leverage") if row.get("leverage") is not None else None,
        )
        for row in futures.get("positions", [])
        if abs(_float(row, "position_amt", "positionAmt", "amt")) > 0
    ]
    return AccountSnapshot(
        equity=float(futures["total_wallet_balance"]),
        available_balance=float(futures.get("available_balance", futures["total_wallet_balance"])),
        futures_wallet_balance=float(futures["total_wallet_balance"]),
        open_positions=positions,
        open_orders=open_orders,
        meta={"source": "account_snapshot.json"},
    )


def load_signals() -> list[TradeSignal]:
    if not ENTRY_TEMPLATES.exists():
        return []
    rows = json.loads(ENTRY_TEMPLATES.read_text())
    signals: list[TradeSignal] = []
    for idx, row in enumerate(rows, start=1):
        entry_zone = row.get("entry_zone") or []
        if len(entry_zone) != 2:
            continue
        entry_price = sum(entry_zone) / 2
        signals.append(
            TradeSignal(
                signal_id=f"entry-template-{idx}-{row['symbol']}",
                symbol=row["symbol"],
                side=row.get("side", "LONG"),
                entry_price=entry_price,
                stop_loss=float(row["stop_loss"]),
                take_profit=float(row.get("take_profit_1")) if row.get("take_profit_1") is not None else None,
                source="manual",
                timeframe=row.get("timeframe", "4h"),
                tags=row.get("tags", []),
                meta={"from": "entry_templates.json", **row},
            )
        )
    return signals


def main() -> None:
    config = DEFAULT_CONFIG
    store = build_state_store(config)
    state = store.load()
    account = load_account_snapshot()
    signals = load_signals()
    executor = OrderExecutor(config, mode="paper")
    sync_positions_from_account(state, account)

    report: list[dict] = []
    for signal in signals:
        if store.circuit_breaker_active(state):
            report.append({"symbol": signal.symbol, "status": "BLOCKED", "reason": "circuit_breaker_active"})
            continue
        if store.in_cooldown(state, signal.symbol):
            report.append({"symbol": signal.symbol, "status": "SKIPPED", "reason": "cooldown_active"})
            continue
        if already_processed(state, signal):
            report.append({"symbol": signal.symbol, "status": "SKIPPED", "reason": "already_processed"})
            continue

        validation, detail = validate_signal(signal, account, config.risk)
        sizing = detail.get("sizing")
        if not validation.allowed or sizing is None:
            report.append({
                "symbol": signal.symbol,
                "status": "BLOCKED",
                "reasons": validation.reasons,
                "metrics": validation.metrics,
            })
            continue

        order = OrderIntent(
            intent_id=intent_id(signal),
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            qty=sizing.qty,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status="PENDING",
            meta={
                "risk_budget_usdt": sizing.risk_budget_usdt,
                "planned_loss_usdt": sizing.planned_loss_usdt,
                "planned_notional_usdt": sizing.planned_notional_usdt,
            },
        )
        execution = executor.execute(order, state)
        apply_executed_intent(state, order)
        mark_processed(state, signal)
        store.record_signal(state, signal.symbol, state.last_signal_ids[signal.symbol], config.risk.cooldown_minutes)
        report.append(
            {
                "symbol": signal.symbol,
                "status": order.status,
                "intent_id": order.intent_id,
                "qty": order.qty,
                "reasons": validation.reasons,
                "metrics": validation.metrics,
                "order_meta": order.meta,
                "execution": execution,
            }
        )

    management = evaluate_portfolio(state)
    management_intents = build_management_action_intents(state, management)
    management_previews = executor.preview_management_actions(management_intents, account.open_orders)
    store.replace_management_suggestions(state, management)
    store.replace_management_action_previews(state, management_previews)
    store.save(state)
    print(
        json.dumps(
            {
                "signals": report,
                "portfolio": {
                    "tracked_positions": len(state.positions),
                    "management_suggestions": management,
                    "management_action_previews": management_previews,
                    "account_open_orders": len(account.open_orders),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
