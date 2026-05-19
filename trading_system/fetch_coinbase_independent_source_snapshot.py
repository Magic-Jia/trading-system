from __future__ import annotations

import argparse
import json
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

_CANONICAL_UTC_TIMESTAMP_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
DEFAULT_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT",
    "ETHUSDT": "ETH-USDT",
    "SOLUSDT": "SOL-USDT",
    "XRPUSDT": "XRP-USDT",
}


def _generated_at(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _canonical_utc(value, "generated_at")
    return value


def _canonical_utc(value: Any, field: str) -> str:
    if type(value) is not str or _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    return value


def _decimal_number(value: Any, field: str, *, required: bool = True) -> float | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be positive finite")
    return float(parsed)


def _fetch_ticker(product_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    url = f"https://api.exchange.coinbase.com/products/{product_id}/ticker"
    req = urllib.request.Request(url, headers={"User-Agent": "trading-system-independent-source/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310 public read-only endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{product_id} ticker payload must be an object")
    return payload


def build_independent_source_snapshot(
    ticker_payloads: Mapping[str, Mapping[str, Any]],
    *,
    symbol_map: Mapping[str, str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    snapshot_generated_at = _generated_at(generated_at)
    observations: list[dict[str, Any]] = []
    for symbol in sorted(symbol_map):
        source_symbol = symbol_map[symbol]
        payload = ticker_payloads.get(source_symbol)
        if not isinstance(payload, Mapping):
            raise ValueError(f"missing ticker payload for {source_symbol}")
        last_price = _decimal_number(payload.get("price"), f"{source_symbol}.price")
        bid_price = _decimal_number(payload.get("bid"), f"{source_symbol}.bid")
        ask_price = _decimal_number(payload.get("ask"), f"{source_symbol}.ask")
        volume = _decimal_number(payload.get("volume"), f"{source_symbol}.volume", required=False)
        observed_at = _canonical_utc(payload.get("time"), f"{source_symbol}.time")
        assert last_price is not None and bid_price is not None and ask_price is not None
        observations.append(
            {
                "symbol": symbol,
                "source_symbol": source_symbol,
                "mid_price": round((bid_price + ask_price) / 2.0, 12),
                "bid_price": bid_price,
                "ask_price": ask_price,
                "last_price": last_price,
                "volume": volume,
                "observed_at": observed_at,
            }
        )
    return {
        "schema_version": "local_independent_source_snapshot.v1",
        "source_id": "coinbase_exchange_public_ticker",
        "generated_at": snapshot_generated_at,
        "observations": observations,
        "side_effect_boundary": {
            "real_orders": "forbidden",
            "testnet_orders": "forbidden",
            "credential_use": "forbidden",
            "account_endpoints": "forbidden",
            "signed_endpoints": "forbidden",
        },
        "provenance": {
            "source": "coinbase_exchange_public_ticker",
            "endpoint_class": "public_market_data",
            "credential_use": "none",
        },
    }


def fetch_independent_source_snapshot(
    *,
    symbols: list[str],
    generated_at: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    symbol_map = {symbol: DEFAULT_SYMBOL_MAP[symbol] for symbol in symbols}
    payloads = {source_symbol: _fetch_ticker(source_symbol, timeout_seconds=timeout_seconds) for source_symbol in symbol_map.values()}
    return build_independent_source_snapshot(payloads, symbol_map=symbol_map, generated_at=generated_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch public Coinbase ticker snapshot as an independent market source")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT", help="Comma-separated symbols; supported: " + ",".join(sorted(DEFAULT_SYMBOL_MAP)))
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    unknown = sorted(set(symbols) - set(DEFAULT_SYMBOL_MAP))
    if unknown:
        raise ValueError("unsupported symbols: " + ", ".join(unknown))
    payload = fetch_independent_source_snapshot(symbols=symbols, generated_at=args.generated_at, timeout_seconds=args.timeout_seconds)
    output_path = Path(args.output_dir) / "local_independent_source_snapshot.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "observation_count": len(payload["observations"])}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
