from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ..connectors.binance import query_open_protective_orders
from ..config import AppConfig
from ..types import ManagementActionIntent, OrderIntent, RuntimeState
from .paper_executor import PaperExecutor
from .paper_ledger import PaperLedger
from .orders import OrderMode, build_management_preview, dry_run_fill, preview_result

BASE = Path(__file__).resolve().parents[2]
EXEC_LOG = BASE / "data" / "execution_log.jsonl"


class ExecutionError(RuntimeError):
    pass


class OrderExecutor:
    def __init__(
        self,
        config: AppConfig,
        mode: OrderMode | None = None,
        persist_state: Callable[[RuntimeState], None] | None = None,
    ):
        self.config = config
        self.mode = mode or config.execution.mode
        self.persist_state = persist_state
        self.execution_log_path = EXEC_LOG
        self.paper_ledger_path = config.data_dir / "paper_ledger.jsonl"
        self.paper_executor = PaperExecutor(PaperLedger(self.paper_ledger_path))
        if self.mode == "live" and not config.execution.allow_live_execution:
            raise ExecutionError("live execution is disabled unless TRADING_ALLOW_LIVE_EXECUTION is explicitly enabled")
        self.execution_log_path.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, order: OrderIntent, state: RuntimeState) -> dict[str, Any]:
        if self.mode == "live":
            raise ExecutionError("live 模式尚未启用；当前 MVP 仅支持 paper / dry-run")

        if self.mode == "paper":
            result = self.paper_executor.execute(order, state)
            if self.persist_state is not None:
                try:
                    self.persist_state(state)
                except Exception:
                    self.append_log(order, result)
                    raise
        else:
            result = dry_run_fill(order)
            order.status = "SENT"

        if self.mode != "dry-run":
            self.append_log(order, result)
        return result

    def preview_management_action(
        self,
        intent: ManagementActionIntent,
        open_orders: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.mode == "live":
            raise ExecutionError("management action 仅支持 paper / dry-run 预览，不执行 live 写入")
        open_protective_orders = query_open_protective_orders(intent.symbol, open_orders or [])
        preview = build_management_preview(intent, open_protective_orders)
        return preview_result(intent, preview, self.mode)

    def preview_management_actions(
        self,
        intents: list[ManagementActionIntent],
        open_orders: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return [self.preview_management_action(intent, open_orders) for intent in intents]

    def append_log(self, order: OrderIntent, result: dict[str, Any]) -> None:
        payload = {
            "order": asdict(order),
            "result": result,
        }
        with self.execution_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
