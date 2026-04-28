from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from trading_system.binance_client import (
    cancel_futures_testnet_order,
    query_futures_testnet_order,
    submit_futures_testnet_conditional_algo_order,
    submit_futures_testnet_order,
)

from ..connectors.binance import query_open_protective_orders
from ..config import AppConfig
from ..notifications import send_feishu_text
from ..portfolio.positions import _has_explicit_target_management_state, apply_management_action_fill
from ..types import BJ, ManagementActionIntent, OrderIntent, RuntimeState
from .paper_executor import PaperExecutor
from .paper_ledger import PaperLedger
from .orders import EntryOrderPolicy, OrderMode, build_management_preview, dry_run_fill, preview_result

BASE = Path(__file__).resolve().parents[2]
EXEC_LOG = BASE / "data" / "execution_log.jsonl"
logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    pass


def _entry_payload_matches_policy(entry_payload: dict[str, Any], entry_order_policy: EntryOrderPolicy) -> bool:
    if entry_order_policy == "taker_market":
        return entry_payload.get("type") == "MARKET"
    if entry_order_policy == "maker_only":
        return (
            entry_payload.get("type") == "LIMIT"
            and entry_payload.get("timeInForce") == "GTX"
            and entry_payload.get("price") is not None
        )
    return False


def _entry_executed_qty(order_status: dict[str, Any]) -> float:
    return float(order_status.get("executedQty") or 0.0)


def _entry_status(order_status: dict[str, Any]) -> str:
    return str(order_status.get("status", "")).upper()


def _has_open_entry_remainder(order_status: dict[str, Any]) -> bool:
    return _entry_status(order_status) in {"NEW", "PARTIALLY_FILLED"}


def _has_filled_entry_quantity(order_status: dict[str, Any]) -> bool:
    return _entry_status(order_status) == "FILLED" or _entry_executed_qty(order_status) > 0.0


def _is_filled_entry_order_status(order_status: dict[str, Any]) -> bool:
    return _entry_status(order_status) == "FILLED"


class OrderExecutor:
    def __init__(
        self,
        config: AppConfig,
        mode: OrderMode | None = None,
        persist_state: Callable[[RuntimeState], None] | None = None,
        feishu_notifier: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.mode = mode or config.execution.mode
        self.persist_state = persist_state
        self.feishu_notifier = feishu_notifier or (
            lambda message: send_feishu_text(
                message,
                app_id=self.config.execution.feishu_app_id,
                app_secret=self.config.execution.feishu_app_secret,
                receive_id=self.config.execution.feishu_receive_id,
                receive_id_type=self.config.execution.feishu_receive_id_type,
                domain=self.config.execution.feishu_domain,
            )
        )
        self.execution_log_path = EXEC_LOG
        self.paper_ledger_path = config.state_file.parent / "paper_ledger.jsonl"
        self.paper_executor = PaperExecutor(PaperLedger(self.paper_ledger_path))
        if self.mode == "live" and not config.execution.allow_live_execution:
            raise ExecutionError("live execution is disabled unless TRADING_ALLOW_LIVE_EXECUTION is explicitly enabled")
        self.execution_log_path.parent.mkdir(parents=True, exist_ok=True)

    def execute(self, order: OrderIntent, state: RuntimeState) -> dict[str, Any]:
        if self.mode == "live":
            raise ExecutionError("live 模式尚未启用；当前 MVP 仅支持 paper / dry-run")

        if self.mode == "testnet":
            preview = order.meta.get("validated_order_preview")
            if not isinstance(preview, dict):
                preview = {}

            submission_enabled = bool(self.config.execution.testnet_order_submission_enabled)
            submission_prerequisites_passed = bool(preview.get("submission_prerequisites_passed", False))
            would_submit = submission_enabled and submission_prerequisites_passed
            result = {
                "mode": "testnet",
                "ts_bj": datetime.now(BJ).isoformat(),
                "intent": asdict(order),
                "validated_order_preview": preview,
                "submission_enabled": submission_enabled,
                "would_submit": would_submit,
                "submission_prerequisites_passed": submission_prerequisites_passed,
                "result": "PREVIEW_ONLY",
            }
            if would_submit:
                payloads = preview.get("payloads") if isinstance(preview, dict) else {}
                entry_payload = payloads.get("entry") if isinstance(payloads, dict) else None
                stop_payload = payloads.get("stop") if isinstance(payloads, dict) else None
                take_profit_payload = payloads.get("take_profit") if isinstance(payloads, dict) else None
                if not isinstance(entry_payload, dict):
                    raise ExecutionError("testnet submission requires a validated entry payload")
                if not _entry_payload_matches_policy(entry_payload, self.config.execution.entry_order_policy):
                    raise ExecutionError("testnet submission entry payload incompatible with configured entry order policy")
                if not isinstance(stop_payload, dict):
                    raise ExecutionError("testnet submission requires a protective stop before entry submission")
                try:
                    exchange_response = submit_futures_testnet_order(entry_payload)
                except Exception as exc:
                    self._notify_testnet_event(
                        order,
                        status="FAILED",
                        detail=f"error={type(exc).__name__}: {exc}",
                    )
                    raise

                entry_order_status = None
                entry_cancel_response = None
                if self.config.execution.entry_order_policy == "maker_only":
                    entry_timeout_seconds = int(self.config.execution.maker_entry_timeout_seconds)
                    entry_order_status = exchange_response if _is_filled_entry_order_status(exchange_response) else None
                    if entry_order_status is None:
                        time.sleep(entry_timeout_seconds)
                        entry_order_status = query_futures_testnet_order(
                            symbol=str(entry_payload["symbol"]),
                            orig_client_order_id=str(entry_payload["newClientOrderId"]),
                        )
                    if _has_open_entry_remainder(entry_order_status):
                        entry_cancel_response = cancel_futures_testnet_order(
                            symbol=str(entry_payload["symbol"]),
                            orig_client_order_id=str(entry_payload["newClientOrderId"]),
                        )
                    else:
                        entry_cancel_response = None
                    if not _has_filled_entry_quantity(entry_order_status):
                        order.status = "CANCELLED"
                        result.update(
                            {
                                "venue": "binance_futures_testnet",
                                "entry_order": entry_payload,
                                "clientOrderId": entry_payload.get("newClientOrderId"),
                                "exchange_response": exchange_response,
                                "entry_timeout_seconds": entry_timeout_seconds,
                                "entry_order_status": entry_order_status,
                                "entry_cancel_response": entry_cancel_response,
                                "result": "ENTRY_TIMEOUT_CANCELLED",
                            }
                        )
                        self._notify_testnet_event(
                            order,
                            status="ENTRY_TIMEOUT_CANCELLED",
                            detail=f"clientOrderId={entry_payload.get('newClientOrderId')} timeout_seconds={entry_timeout_seconds}",
                        )
                        self.append_log(order, result)
                        return result

                stop_algo_order = None
                stop_algo_response = None
                stop_algo_error = None
                take_profit_algo_order = None
                take_profit_algo_response = None
                take_profit_algo_error = None
                if isinstance(stop_payload, dict):
                    client_algo_id = stop_payload.get("newClientOrderId") or f"{entry_payload.get('newClientOrderId')}-sl"
                    stop_algo_order = {
                        "symbol": stop_payload["symbol"],
                        "side": stop_payload["side"],
                        "type": stop_payload["type"],
                        "algoType": "CONDITIONAL",
                        "triggerPrice": stop_payload["stopPrice"],
                        "closePosition": stop_payload.get("closePosition", "true"),
                        "workingType": stop_payload.get("workingType", "MARK_PRICE"),
                        "clientAlgoId": client_algo_id,
                    }
                    try:
                        stop_algo_response = submit_futures_testnet_conditional_algo_order(stop_algo_order)
                    except Exception as exc:
                        stop_algo_error = f"{type(exc).__name__}: {exc}"
                        logger.exception("testnet protective stop submission failed after entry submission")
                if isinstance(take_profit_payload, dict):
                    client_algo_id = take_profit_payload.get("newClientOrderId") or f"{entry_payload.get('newClientOrderId')}-tp"
                    take_profit_algo_order = {
                        "symbol": take_profit_payload["symbol"],
                        "side": take_profit_payload["side"],
                        "type": take_profit_payload["type"],
                        "algoType": "CONDITIONAL",
                        "triggerPrice": take_profit_payload["stopPrice"],
                        "closePosition": take_profit_payload.get("closePosition", "true"),
                        "workingType": take_profit_payload.get("workingType", "MARK_PRICE"),
                        "clientAlgoId": client_algo_id,
                    }
                    try:
                        take_profit_algo_response = submit_futures_testnet_conditional_algo_order(take_profit_algo_order)
                    except Exception as exc:
                        take_profit_algo_error = f"{type(exc).__name__}: {exc}"
                        logger.exception("testnet take profit submission failed after entry submission")
                order.status = "SENT"
                protective_order_error = stop_algo_error or take_profit_algo_error
                result.update(
                    {
                        "venue": "binance_futures_testnet",
                        "entry_order": entry_payload,
                        "clientOrderId": entry_payload.get("newClientOrderId"),
                        "exchange_response": exchange_response,
                        "entry_order_status": entry_order_status,
                        "entry_cancel_response": entry_cancel_response,
                        "stop_algo_order": stop_algo_order,
                        "stop_algo_response": stop_algo_response,
                        "stop_algo_error": stop_algo_error,
                        "take_profit_algo_order": take_profit_algo_order,
                        "take_profit_algo_response": take_profit_algo_response,
                        "take_profit_algo_error": take_profit_algo_error,
                        "requires_protective_stop_repair": bool(stop_algo_error),
                        "requires_take_profit_repair": bool(take_profit_algo_error),
                        "result": "SUBMITTED_PROTECTION_FAILED" if protective_order_error else "SUBMITTED",
                    }
                )
                self._notify_testnet_event(
                    order,
                    status="SUBMITTED",
                    detail=f"clientOrderId={entry_payload.get('newClientOrderId')}",
                )
            self.append_log(order, result)
            return result

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
            if (
                intent.action == "BREAK_EVEN"
                and not position.get("tracked_from_intent")
                and not _has_explicit_target_management_state(position)
            ):
                return {
                    "intent": asdict(intent),
                    "result": {
                        "status": "FILLED",
                        "mode": "paper",
                        "updated_stop_loss": round(float(stop_loss), 8),
                        "writeback_skipped": True,
                    },
                    "position": position,
                }
            position["stop_loss"] = round(float(stop_loss), 8)
            state.positions[intent.symbol] = position
            return {
                "intent": asdict(intent),
                "result": {
                    "status": "FILLED",
                    "mode": "paper",
                    "updated_stop_loss": position["stop_loss"],
                    "writeback_skipped": False,
                },
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

    def _notify_testnet_event(self, order: OrderIntent, *, status: str, detail: str) -> None:
        if self.mode != "testnet" or not self.config.execution.feishu_notifications_enabled:
            return
        message = " | ".join(
            [
                f"Trading testnet {status}",
                f"symbol={order.symbol}",
                f"side={order.side}",
                f"intent_id={order.intent_id}",
                detail,
            ]
        )
        try:
            self.feishu_notifier(message)
        except Exception as exc:
            logger.warning("Feishu notification failed: %s", exc)
