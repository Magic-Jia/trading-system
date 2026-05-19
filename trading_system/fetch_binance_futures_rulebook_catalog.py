from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from trading_system.app.execution.exchange_constraints import build_venue_rulebook_catalog, build_venue_rulebook_report
from trading_system.app.reporting.market_coverage import write_venue_rulebook_catalog_freshness_report

BINANCE_FUTURES_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
VENUE = "binance_futures"
PRODUCT_TYPE = "usdt_perpetual"
CATALOG_FILENAME = "venue_rulebook_catalog.json"


def _canonical_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _filters_by_type(symbol_payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    filters = symbol_payload.get("filters")
    if not isinstance(filters, list):
        raise ValueError(f"{symbol_payload.get('symbol', '<unknown>')} filters must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for entry in filters:
        if not isinstance(entry, Mapping):
            raise ValueError("exchangeInfo filters must contain objects")
        filter_type = entry.get("filterType")
        if isinstance(filter_type, str) and filter_type:
            result[filter_type] = entry
    return result


def _required_filter(filters: Mapping[str, Mapping[str, Any]], filter_type: str, symbol: str) -> Mapping[str, Any]:
    value = filters.get(filter_type)
    if value is None:
        raise ValueError(f"{symbol} missing {filter_type} filter")
    return value


def _required_decimal_number(payload: Mapping[str, Any], field: str, *, context: str) -> float:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} must be a non-empty decimal string")
    text = value.strip()
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{context}.{field} must be a finite decimal string") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{context}.{field} must be a finite decimal string")
    return float(decimal_value)


def _symbol_payloads(exchange_info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("exchangeInfo.symbols must be a list")
    result: list[Mapping[str, Any]] = []
    for entry in symbols:
        if not isinstance(entry, Mapping):
            raise ValueError("exchangeInfo.symbols must contain objects")
        result.append(entry)
    return result


def build_rulebook_catalog_from_exchange_info(
    exchange_info: Mapping[str, Any],
    *,
    generated_at: str,
    symbols: Iterable[str] = DEFAULT_SYMBOLS,
    source: str = BINANCE_FUTURES_EXCHANGE_INFO_URL,
    max_age_seconds: int = 7 * 24 * 60 * 60,
) -> dict[str, Any]:
    requested = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
    reports = []
    for symbol_payload in _symbol_payloads(exchange_info):
        symbol = symbol_payload.get("symbol")
        if not isinstance(symbol, str) or symbol not in requested:
            continue
        if symbol_payload.get("status") != "TRADING":
            continue
        if symbol_payload.get("contractType") != "PERPETUAL":
            continue
        filters = _filters_by_type(symbol_payload)
        price_filter = _required_filter(filters, "PRICE_FILTER", symbol)
        lot_size = _required_filter(filters, "LOT_SIZE", symbol)
        min_notional_filter = _required_filter(filters, "MIN_NOTIONAL", symbol)
        rulebook_version = f"binance-futures-{symbol}-{generated_at[:10]}"
        reports.append(
            build_venue_rulebook_report(
                venue=VENUE,
                symbol=symbol,
                product_type=PRODUCT_TYPE,
                rulebook_version=rulebook_version,
                generated_at=generated_at,
                effective_at=generated_at,
                source=source,
                price_tick_size=_required_decimal_number(price_filter, "tickSize", context=f"{symbol}.PRICE_FILTER"),
                quantity_step_size=_required_decimal_number(lot_size, "stepSize", context=f"{symbol}.LOT_SIZE"),
                min_notional=_required_decimal_number(min_notional_filter, "notional", context=f"{symbol}.MIN_NOTIONAL"),
                post_only_policy="reject_would_cross",
                reduce_only_policy="allow",
                now=generated_at,
                max_age_seconds=max_age_seconds,
            )
        )
    return build_venue_rulebook_catalog(
        reports,
        generated_at=generated_at,
        effective_at=generated_at,
        required_symbols=[(VENUE, symbol, PRODUCT_TYPE) for symbol in sorted(requested)],
        max_age_seconds=max_age_seconds,
    )


def fetch_exchange_info(url: str = BINANCE_FUTURES_EXCHANGE_INFO_URL, *, timeout_seconds: int = 10) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "trading-system-rulebook-catalog/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("exchangeInfo response must be a JSON object")
    return payload


def write_binance_futures_rulebook_catalog(
    output_dir: str | Path,
    *,
    generated_at: str | None = None,
    symbols: Iterable[str] = DEFAULT_SYMBOLS,
    exchange_info: Mapping[str, Any] | None = None,
    fetch_url: str = BINANCE_FUTURES_EXCHANGE_INFO_URL,
) -> dict[str, Any]:
    evaluated_at = generated_at or _canonical_now()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    payload = dict(exchange_info) if exchange_info is not None else fetch_exchange_info(fetch_url)
    catalog = build_rulebook_catalog_from_exchange_info(
        payload,
        generated_at=evaluated_at,
        symbols=symbols,
        source=fetch_url,
    )
    (output_path / CATALOG_FILENAME).write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    freshness = write_venue_rulebook_catalog_freshness_report(output_path, generated_at=evaluated_at)
    return {"catalog": catalog, "freshness": freshness, "catalog_file": str(output_path / CATALOG_FILENAME)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch public Binance Futures exchangeInfo into a venue rulebook catalog.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--generated-at")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols to include")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    result = write_binance_futures_rulebook_catalog(args.output_dir, generated_at=args.generated_at, symbols=symbols)
    print(json.dumps({"catalog_file": result["catalog_file"], "freshness_status": result["freshness"].get("status")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
