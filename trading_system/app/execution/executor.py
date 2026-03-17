from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..types import OrderIntent, RuntimeState
from .idempotency import bind_active_order
from .orders import OrderMode, dry_run_fill, paper_fill

BASE = Path(__file__).resolve().parents[2]
EXEC_LOG = BASE / "data" / "execution_log.jsonl"


class ExecutionError(RuntimeError):
    pass


class OrderExecutor:
    def __init__(self, config: AppConfig, mode: OrderMode = "paper"):
        self.config = config
        self.mode = mode
        EXEC_LOG.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, order: OrderIntent, state: RuntimeState) -> dict[str, Any]:
        if self.mode == "live":
            raise ExecutionError("live 模式尚未启用；当前 MVP 仅支持 paper / dry-run")

        if self.mode == "paper":
            result = paper_fill(order)
            order.status = "FILLED"
        else:
            result = dry_run_fill(order)
            order.status = "SENT"

        bind_active_order(state, order)
        state.positions[order.symbol] = {
            "side": order.side,
            "qty": order.qty,
            "entry_price": order.entry_price,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "status": order.status,
            "intent_id": order.intent_id,
        }
        self.append_log(order, result)
        return result

    def append_log(self, order: OrderIntent, result: dict[str, Any]) -> None:
        payload = {
            "order": asdict(order),
            "result": result,
        }
        with EXEC_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
