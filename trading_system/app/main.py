from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_CONFIG
from .execution.executor import OrderExecutor
from .execution.idempotency import already_processed, intent_id, mark_processed
from .risk.validator import validate_signal
from .storage.state_store import build_state_store
from .types import AccountSnapshot, OrderIntent, PositionSnapshot, TradeSignal

BASE = Path(__file__).resolve().parents[1]
ENTRY_TEMPLATES = BASE / "data" / "entry_templates.json"
ACCOUNT_SNAPSHOT = BASE / "data" / "account_snapshot.json"


def load_account_snapshot() -> AccountSnapshot:
    raw = json.loads(ACCOUNT_SNAPSHOT.read_text())
    futures = raw["futures"]
    positions = [
        PositionSnapshot(
            symbol=row["symbol"],
            side=row["side"],
            qty=abs(float(row.get("position_amt", 0.0))),
            entry_price=float(row.get("entry_price", 0.0)),
            mark_price=float(row.get("mark_price", 0.0)),
            unrealized_pnl=float(row.get("upl", 0.0)),
            notional=float(row.get("notional", 0.0)),
            leverage=float(row.get("leverage", 0.0)) if row.get("leverage") is not None else None,
        )
        for row in futures.get("positions", [])
    ]
    return AccountSnapshot(
        equity=float(futures["total_wallet_balance"]),
        available_balance=float(futures.get("available_balance", futures["total_wallet_balance"])),
        futures_wallet_balance=float(futures["total_wallet_balance"]),
        open_positions=positions,
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

    store.save(state)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
