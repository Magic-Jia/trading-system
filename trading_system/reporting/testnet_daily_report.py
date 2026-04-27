from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_system.app.types import BJ


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _account_positions(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = account.get("positions") or account.get("open_positions") or []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            qty = _float(row.get("qty") if row.get("qty") is not None else row.get("positionAmt"))
            if sym and abs(qty) > 0:
                item = dict(row)
                item["qty"] = qty
                out[sym] = item
    return out


def _runtime_positions(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    positions = state.get("positions") or {}
    if not isinstance(positions, dict):
        return {}
    return {str(k).upper(): dict(v) for k, v in positions.items() if isinstance(v, dict)}


def _runtime_account_mismatches(runtime: dict[str, dict[str, Any]], account: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in sorted(set(runtime) | set(account)):
        r = runtime.get(symbol) or {}
        a = account.get(symbol) or {}
        runtime_status = str(r.get("status") or "").upper()
        runtime_qty = _float(r.get("qty"))
        account_qty = _float(a.get("qty"))
        active_runtime = runtime_status not in {"", "CLOSED", "FAILED", "CANCELLED", "SKIPPED"} and abs(runtime_qty) > 0
        active_account = abs(account_qty) > 0
        if active_runtime != active_account or (active_runtime and abs(runtime_qty - account_qty) > 1e-9):
            rows.append(
                {
                    "symbol": symbol,
                    "runtime_status": runtime_status,
                    "runtime_qty": runtime_qty,
                    "account_qty": account_qty,
                }
            )
    return rows


def _protective_order_summary(protective_orders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"has_stop": False, "has_take_profit": False, "orders": []})
    for order in protective_orders:
        symbol = str(order.get("symbol") or "").upper()
        if not symbol:
            continue
        typ = str(order.get("type") or order.get("origType") or "").upper()
        item = {
            "type": typ,
            "triggerPrice": order.get("triggerPrice") or order.get("stopPrice"),
            "closePosition": order.get("closePosition"),
            "reduceOnly": order.get("reduceOnly"),
        }
        summary[symbol]["orders"].append(item)
        if typ == "STOP_MARKET":
            summary[symbol]["has_stop"] = True
        if typ == "TAKE_PROFIT_MARKET":
            summary[symbol]["has_take_profit"] = True
    return dict(summary)


def build_report_payload(
    *,
    bucket: Path,
    report_date: str,
    protective_orders: list[dict[str, Any]] | None = None,
    protective_orders_source: str | None = None,
) -> dict[str, Any]:
    latest = _read_json(bucket / "latest.json", {})
    state = _read_json(bucket / "runtime_state.json", {})
    account = _read_json(bucket / "account_snapshot.json", {})
    optimization_dir = bucket / "optimization"
    daily_metrics = _read_json(optimization_dir / "daily_metrics.json", {})
    health_report = _read_json(optimization_dir / "health_report.json", {})
    recommendations = _read_json(optimization_dir / "recommendations.json", {})
    promotion = _read_json(optimization_dir / "promotion_decision.json", {})

    runtime = _runtime_positions(state if isinstance(state, dict) else {})
    account_pos = _account_positions(account if isinstance(account, dict) else {})
    orders = list(protective_orders or [])
    source = protective_orders_source or ("unconfirmed" if not orders else "provided")
    return {
        "report_date": report_date,
        "generated_at_bj": datetime.now(BJ).isoformat(),
        "bucket": str(bucket),
        "latest": latest if isinstance(latest, dict) else {},
        "runtime_positions": runtime,
        "account_positions": account_pos,
        "runtime_account_mismatches": _runtime_account_mismatches(runtime, account_pos),
        "active_orders": state.get("active_orders", {}) if isinstance(state, dict) else {},
        "protective_orders_source": source,
        "protective_orders": orders,
        "protective_order_summary": _protective_order_summary(orders),
        "daily_metrics": daily_metrics if isinstance(daily_metrics, dict) else {},
        "health_report": health_report if isinstance(health_report, dict) else {},
        "recommendations": recommendations if isinstance(recommendations, dict) else {},
        "promotion_decision": promotion if isinstance(promotion, dict) else {},
        "optimization_summary": state.get("optimization_summary", {}) if isinstance(state, dict) else {},
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 交易日报 {payload['report_date']}（北京时间 / testnet）",
        f"- 生成时间：{payload.get('generated_at_bj')}",
        f"- Runtime bucket：{payload.get('bucket')}",
    ]
    latest = payload.get("latest") or {}
    lines.append(f"- 最新运行：status={latest.get('status')}，mode={latest.get('mode')}，runtime_env={latest.get('runtime_env')}，error={latest.get('error')}")
    lines.append("")
    lines.append("## 当前持仓一致性")
    mismatches = payload.get("runtime_account_mismatches") or []
    if mismatches:
        lines.append("- ⚠️ runtime 与交易所快照不一致：")
        for row in mismatches:
            lines.append(f"  - {row['symbol']}: runtime_status={row['runtime_status']} runtime_qty={row['runtime_qty']} account_qty={row['account_qty']}")
    else:
        lines.append("- ✅ runtime 与 account_snapshot 当前持仓一致")
    lines.append("")
    lines.append("## 真实保护单（Binance Futures testnet openAlgoOrders）")
    source = payload.get("protective_orders_source")
    lines.append(f"- source：{source}")
    if source == "unconfirmed":
        lines.append("- ⚠️ 真实保护单未确认：本次日报没有成功读取 openAlgoOrders，不能仅凭 runtime stop/take_profit 字段判断交易所保护单存在。")
    summary = payload.get("protective_order_summary") or {}
    for symbol in sorted(summary):
        item = summary[symbol]
        lines.append(f"- {symbol}: SL={'yes' if item.get('has_stop') else 'no'}，TP={'yes' if item.get('has_take_profit') else 'no'}")
        for order in item.get("orders") or []:
            lines.append(f"  - {order.get('type')} trigger={order.get('triggerPrice')} closePosition={order.get('closePosition')} reduceOnly={order.get('reduceOnly')}")
    lines.append("")
    lines.append("## 优化 artifacts")
    metrics = payload.get("daily_metrics") or {}
    health = payload.get("health_report") or {}
    rec = payload.get("recommendations") or {}
    promo = payload.get("promotion_decision") or {}
    lines.append(f"- metrics_scope：{metrics.get('scope', 'legacy_or_missing')}")
    lines.append(f"- health：{health.get('status')} warnings={len(health.get('warnings') or [])}")
    lines.append(f"- recommendation_count：{rec.get('recommendation_count')}")
    lines.append(f"- promotion_decision：{promo.get('decision') or promo.get('status')}")
    return "\n".join(lines) + "\n"
