from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "local_l2_order_book_snapshot.v1"
SOURCE_ID = "binance_usdm_futures_public_depth"
DEFAULT_BASE_URL = "https://fapi.binance.com"
DEFAULT_OUTPUT_NAME = "local_l2_order_book_snapshot.json"


def _canonical_generated_at(value: str | None) -> str:
    generated_at = value or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if type(generated_at) is not str or not generated_at.endswith("Z"):
        raise ValueError("generated_at must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(generated_at[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("generated_at must be a canonical UTC timestamp") from exc
    if parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") != generated_at:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    return generated_at


def _decimal_number(value: Any, field_path: str) -> float:
    if type(value) is not str or value.strip() != value or not value:
        raise ValueError(f"{field_path} must be a canonical decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field_path} must be a canonical decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field_path} must be a positive decimal")
    return float(parsed)


def _integer(value: Any, field_path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_path} must be an integer")
    return int(value)


def _levels(payload: Mapping[str, Any], side: str, symbol: str) -> list[tuple[float, float]]:
    raw_levels = payload.get(side)
    if not isinstance(raw_levels, list) or not raw_levels:
        raise ValueError(f"{symbol}.{side} must contain at least one level")
    levels: list[tuple[float, float]] = []
    previous_price: float | None = None
    for index, raw_level in enumerate(raw_levels):
        if not isinstance(raw_level, list) or len(raw_level) < 2:
            raise ValueError(f"{symbol}.{side}[{index}] must contain price and quantity")
        price = _decimal_number(raw_level[0], f"{symbol}.{side}[{index}].price")
        quantity = _decimal_number(raw_level[1], f"{symbol}.{side}[{index}].quantity")
        if previous_price is not None:
            if side == "bids" and price >= previous_price:
                raise ValueError(f"{symbol}.bids must be strictly descending by price")
            if side == "asks" and price <= previous_price:
                raise ValueError(f"{symbol}.asks must be strictly ascending by price")
        previous_price = price
        levels.append((price, quantity))
    return levels


def _book(symbol: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    last_update_id = _integer(payload.get("lastUpdateId"), f"{symbol}.lastUpdateId")
    bids = _levels(payload, "bids", symbol)
    asks = _levels(payload, "asks", symbol)
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    if best_ask <= best_bid:
        raise ValueError(f"{symbol}.best_ask must be greater than best_bid")
    mid_price = (best_bid + best_ask) / 2.0
    bid_depth_notional = sum(price * quantity for price, quantity in bids)
    ask_depth_notional = sum(price * quantity for price, quantity in asks)
    book: dict[str, Any] = {
        "symbol": symbol,
        "last_update_id": last_update_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid_price,
        "spread_bps": ((best_ask - best_bid) / mid_price) * 10000.0,
        "bid_depth_notional": bid_depth_notional,
        "ask_depth_notional": ask_depth_notional,
        "levels": {"bids": len(bids), "asks": len(asks)},
    }
    event_time = payload.get("E")
    transaction_time = payload.get("T")
    if event_time is not None:
        book["event_time_ms"] = _integer(event_time, f"{symbol}.E")
    if transaction_time is not None:
        book["transaction_time_ms"] = _integer(transaction_time, f"{symbol}.T")
    return book


def build_l2_depth_snapshot(
    payloads_by_symbol: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str | None,
    source_url: str,
) -> dict[str, Any]:
    if not isinstance(payloads_by_symbol, Mapping) or not payloads_by_symbol:
        raise ValueError("payloads_by_symbol must be a non-empty mapping")
    books = []
    for symbol in sorted(payloads_by_symbol):
        if type(symbol) is not str or not symbol.isalnum() or symbol.upper() != symbol:
            raise ValueError("symbols must be uppercase exchange symbols")
        payload = payloads_by_symbol[symbol]
        if not isinstance(payload, Mapping):
            raise ValueError(f"{symbol} payload must be an object")
        books.append(_book(symbol, payload))
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "source_url": source_url,
        "generated_at": _canonical_generated_at(generated_at),
        "depth_count": len(books),
        "symbols": [book["symbol"] for book in books],
        "books": books,
        "provenance": {
            "decision_policy": "fail_closed",
            "side_effect_boundary": {
                "real_orders": "forbidden",
                "testnet_orders": "forbidden",
                "account_endpoints": "forbidden",
                "credential_use": "forbidden",
                "public_market_data": "allowed",
            },
        },
    }


def fetch_depth(symbol: str, *, base_url: str, limit: int, timeout_seconds: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({"symbol": symbol, "limit": str(limit)})
    url = f"{base_url.rstrip('/')}/fapi/v1/depth?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "trading-system-l2-depth-evidence/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status != 200:
            raise RuntimeError(f"Binance futures depth returned HTTP {response.status} for {symbol}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Binance futures depth payload for {symbol} must be an object")
    return dict(payload)


def write_l2_depth_snapshot(
    output_dir: str | Path,
    *,
    symbols: list[str],
    generated_at: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    limit: int = 5,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    if limit not in {5, 10, 20, 50, 100, 500, 1000}:
        raise ValueError("limit must be one of Binance futures depth limits")
    payloads = {symbol: fetch_depth(symbol, base_url=base_url, limit=limit, timeout_seconds=timeout_seconds) for symbol in symbols}
    snapshot = build_l2_depth_snapshot(payloads, generated_at=generated_at, source_url=f"{base_url.rstrip('/')}/fapi/v1/depth")
    output_path = Path(output_dir) / DEFAULT_OUTPUT_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch public Binance USD-M futures L2 depth snapshot evidence.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT")
    parser.add_argument("--generated-at")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    snapshot = write_l2_depth_snapshot(
        args.output_dir,
        symbols=symbols,
        generated_at=args.generated_at,
        base_url=args.base_url,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({"output": str(Path(args.output_dir) / DEFAULT_OUTPUT_NAME), "depth_count": snapshot["depth_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
