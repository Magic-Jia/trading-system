from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trading_system import binance_client as bc
from trading_system.app.types import BJ
from trading_system.reporting.testnet_daily_report import build_report_payload, render_markdown


def fetch_testnet_protective_orders() -> tuple[list[dict[str, Any]], str]:
    if os.environ.get("TRADING_EXECUTION_MODE") != "testnet":
        return [], "unconfirmed:not_testnet_mode"
    if os.environ.get("BINANCE_USE_TESTNET") != "1":
        return [], "unconfirmed:BINANCE_USE_TESTNET_not_1"
    if "testnet.binancefuture.com" not in bc.FUTURES_BASE:
        return [], "unconfirmed:not_futures_testnet_endpoint"
    if not bc.env_ready():
        return [], "unconfirmed:missing_credentials"
    try:
        rows = bc.signed_get(bc.FUTURES_BASE, "/fapi/v1/openAlgoOrders", bc._futures_testnet_signed_params())
    except Exception as exc:  # noqa: BLE001 - report should degrade explicitly
        return [], f"unconfirmed:{type(exc).__name__}"
    if not isinstance(rows, list):
        return [], "unconfirmed:unexpected_payload"
    protective = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("type") or row.get("origType") or "").upper() in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    ]
    return protective, "binance_testnet_openAlgoOrders"


def default_report_date() -> str:
    return (datetime.now(BJ).date() - timedelta(days=1)).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Binance Futures testnet daily trading report")
    parser.add_argument("--bucket", default="/home/cn/.openclaw/agents/trade/workspace/trading_system/data/runtime/testnet/prod")
    parser.add_argument("--report-date", default=default_report_date())
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    protective, source = fetch_testnet_protective_orders()
    payload = build_report_payload(
        bucket=Path(args.bucket),
        report_date=args.report_date,
        protective_orders=protective,
        protective_orders_source=source,
    )
    text = render_markdown(payload)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    print("REPORT_JSON", json.dumps({"output": args.output, "source": source, "protective_count": len(protective)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
