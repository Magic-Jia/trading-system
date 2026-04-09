from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ..connectors.binance import query_open_protective_orders
from ..config import AppConfig
from ..portfolio.positions import apply_management_action_fill
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
        self.paper_ledger_path = config.state_file.parent / "paper_ledger.jsonl"
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

    def execute_management_action(self, intent: ManagementActionIntent, state: RuntimeState) -> dict[str, Any]:
        if self.mode != "paper":
            return {
                "intent": asdict(intent),
                "result": {"status": "UNSUPPORTED", "reason": "paper_mode_only"},
            }

        if intent.action in {"BREAK_EVEN", "ADD_PROTECTIVE_STOP"}:
            stop_loss = intent.stop_loss
            if stop_loss is None:
                return {
                    "intent": asdict(intent),
                    "result": {"status": "UNSUPPORTED", "reason": "missing_stop_loss"},
                }
            position = dict(state.positions.get(intent.symbol, {}))
            if not position:
                return {
                    "intent": asdict(intent),
                    "result": {"status": "UNSUPPORTED", "reason": "position_not_found"},
                }
            position["stop_loss"] = round(float(stop_loss), 8)
            state.positions[intent.symbol] = position
            return {
                "intent": asdict(intent),
                "result": {"status": "FILLED", "mode": "paper", "updated_stop_loss": position["stop_loss"]},
                "position": position,
            }

        qty = float(intent.qty or 0.0)
        if intent.action not in {"PARTIAL_TAKE_PROFIT", "DE_RISK", "EXIT"}:
            return {
                "intent": asdict(intent),
                "result": {"status": "UNSUPPORTED", "reason": "unsupported_management_action"},
            }
        if qty <= 0:
            return {
                "intent": asdict(intent),
                "result": {"status": "UNSUPPORTED", "reason": "missing_reduce_qty"},
            }

        updated_position = apply_management_action_fill(state, intent)
        return {
            "intent": asdict(intent),
            "result": {"status": "FILLED", "mode": "paper", "filled_qty": round(qty, 8)},
            "position": updated_position,
        }

    def execute_management_actions(
        self,
        intents: list[ManagementActionIntent],
        state: RuntimeState,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for intent in intents:
            row = self.execute_management_action(intent, state)
            status = str((row.get("result") or {}).get("status") or "")
            if status != "FILLED":
                break
            rows.append(row)

            stage = str((intent.meta or {}).get("target_stage") or "")
            if intent.action == "PARTIAL_TAKE_PROFIT" and stage in {"first", "second"}:
                position = dict(state.positions.get(intent.symbol, {}))
                if str(position.get(f"{stage}_target_status") or "pending") == "pending":
                    break
        return rows

    def append_log(self, order: OrderIntent, result: dict[str, Any]) -> None:
        payload = {
            "order": asdict(order),
            "result": result,
        }
        with self.execution_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
