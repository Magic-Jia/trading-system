from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .raw_market import archive_raw_market_payload

BINANCE_FUTURES_PUBLIC_BASE_URL = "https://fapi.binance.com"
DEPTH_ENDPOINT = "/fapi/v1/depth"
AGG_TRADES_ENDPOINT = "/fapi/v1/aggTrades"
DEFAULT_DEPTH_LIMIT = 5
DEFAULT_AGG_TRADES_LIMIT = 1000


class BinanceExecutionHttpError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BinanceExecutionDownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceExecutionDownloadResult:
    symbol: str
    requested_start_time: str
    requested_end_time: str
    base_url: str
    dry_run: bool
    order_book_request_count: int
    trade_request_count: int
    order_book_record_count: int
    trade_record_count: int
    archive_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


FetchJson = Callable[[str, dict[str, object]], object]
Sleep = Callable[[float], None]
Now = Callable[[], str]


def _utc_datetime(value: str | int | float) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: str | int | float | datetime) -> str:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _utc_datetime(value).isoformat().replace("+00:00", "Z")


def _utc_millis(value: str) -> int:
    return int(_utc_datetime(value).timestamp() * 1000)


def _default_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _default_fetch_json(base_url: str) -> FetchJson:
    def fetch(endpoint: str, params: dict[str, object]) -> object:
        url = f"{base_url.rstrip('/')}{endpoint}?{urlencode(params)}"
        try:
            with urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise BinanceExecutionHttpError(
                f"Binance public endpoint returned HTTP {exc.code}: {endpoint}",
                status_code=exc.code,
            ) from exc
        except URLError as exc:
            raise BinanceExecutionHttpError(f"Binance public endpoint failed: {endpoint}") from exc

    return fetch


def _call_with_retries(
    fetch: FetchJson,
    endpoint: str,
    params: dict[str, object],
    *,
    max_retries: int,
    retry_sleep_seconds: float,
    sleep: Sleep,
) -> object:
    attempts = 0
    while True:
        try:
            return fetch(endpoint, params)
        except BinanceExecutionHttpError:
            if attempts >= max_retries:
                raise
            attempts += 1
            sleep(retry_sleep_seconds)


def _require_mapping(payload: object, *, endpoint: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise BinanceExecutionDownloadError(f"{endpoint} returned non-object payload")
    return payload


def _require_list(payload: object, *, endpoint: str) -> list[Any]:
    if not isinstance(payload, list):
        raise BinanceExecutionDownloadError(f"{endpoint} returned non-list payload")
    return payload


def _top_of_book_row(*, symbol: str, fetched_at: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    bids = payload.get("bids")
    asks = payload.get("asks")
    if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
        raise BinanceExecutionDownloadError("depth payload missing top bid/ask")
    best_bid = bids[0]
    best_ask = asks[0]
    if not isinstance(best_bid, (list, tuple)) or len(best_bid) < 2:
        raise BinanceExecutionDownloadError("depth payload top bid is malformed")
    if not isinstance(best_ask, (list, tuple)) or len(best_ask) < 2:
        raise BinanceExecutionDownloadError("depth payload top ask is malformed")
    row: dict[str, Any] = {
        "timestamp": fetched_at,
        "symbol": symbol,
        "bid": str(best_bid[0]),
        "ask": str(best_ask[0]),
        "bid_size": str(best_bid[1]),
        "ask_size": str(best_ask[1]),
        "evidence_time_semantics": "point_in_time_fetch",
    }
    if payload.get("lastUpdateId") is not None:
        row["lastUpdateId"] = payload["lastUpdateId"]
    return row


def _buyer_was_maker(payload: Mapping[str, Any]) -> bool:
    maker_flag = payload.get("m")
    if not isinstance(maker_flag, bool):
        raise BinanceExecutionDownloadError("aggTrades row maker flag must be boolean")
    return maker_flag


def _maker_side(payload: Mapping[str, Any]) -> str:
    return "sell" if _buyer_was_maker(payload) else "buy"


def _trade_id(payload: Mapping[str, Any]) -> int:
    trade_id = payload["a"]
    if isinstance(trade_id, bool) or not isinstance(trade_id, int):
        raise BinanceExecutionDownloadError("aggTrades row trade id must be integer")
    return trade_id


def _trade_time_ms(payload: Mapping[str, Any]) -> int:
    trade_time = payload["T"]
    if isinstance(trade_time, bool) or not isinstance(trade_time, int):
        raise BinanceExecutionDownloadError("aggTrades row trade timestamp must be integer")
    return trade_time


def _trade_string_field(payload: Mapping[str, Any], *, field: str, label: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise BinanceExecutionDownloadError(f"aggTrades row {label} must be a string")
    return value


def _trade_row(*, symbol: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    buyer_was_maker = _buyer_was_maker(payload)
    return {
        "timestamp": _trade_time_ms(payload),
        "symbol": symbol,
        "price": _trade_string_field(payload, field="p", label="price"),
        "quantity": _trade_string_field(payload, field="q", label="quantity"),
        "side": _maker_side(payload),
        "agg_trade_id": _trade_id(payload),
        "is_buyer_maker": buyer_was_maker,
        "evidence_time_semantics": "historical_agg_trade_time" if buyer_was_maker else "trade_execution_time",
    }


def _fetch_depth(
    *,
    symbol: str,
    depth_limit: int,
    fetch: FetchJson,
    max_retries: int,
    retry_sleep_seconds: float,
    sleep: Sleep,
    now: Now,
) -> tuple[dict[str, Any], str]:
    params = {"symbol": symbol, "limit": depth_limit}
    try:
        payload = _call_with_retries(
            fetch,
            DEPTH_ENDPOINT,
            params,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
            sleep=sleep,
        )
    except BinanceExecutionHttpError as exc:
        raise BinanceExecutionDownloadError(f"depth failed for {symbol}: {exc}") from exc
    fetched_at = _utc_iso(now())
    return _top_of_book_row(symbol=symbol, fetched_at=fetched_at, payload=_require_mapping(payload, endpoint=DEPTH_ENDPOINT)), fetched_at


def _fetch_agg_trades(
    *,
    symbol: str,
    start_time: str,
    end_time: str,
    agg_trades_limit: int,
    fetch: FetchJson,
    max_retries: int,
    retry_sleep_seconds: float,
    sleep: Sleep,
) -> tuple[list[dict[str, Any]], int]:
    start_ms = _utc_millis(start_time)
    end_ms = _utc_millis(end_time)
    params: dict[str, object] = {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": agg_trades_limit,
    }
    request_count = 0
    rows_by_id: dict[int, dict[str, Any]] = {}
    next_from_id: int | None = None

    while True:
        request_params = dict(params)
        if next_from_id is not None:
            request_params.pop("startTime", None)
            request_params.pop("endTime", None)
            request_params["fromId"] = next_from_id
        try:
            payload = _call_with_retries(
                fetch,
                AGG_TRADES_ENDPOINT,
                request_params,
                max_retries=max_retries,
                retry_sleep_seconds=retry_sleep_seconds,
                sleep=sleep,
            )
        except BinanceExecutionHttpError as exc:
            raise BinanceExecutionDownloadError(f"aggTrades failed for {symbol}: {exc}") from exc
        request_count += 1
        page = _require_list(payload, endpoint=AGG_TRADES_ENDPOINT)
        if not page:
            break
        mapped_page: list[dict[str, Any]] = []
        for item in page:
            if not isinstance(item, Mapping):
                raise BinanceExecutionDownloadError("aggTrades row is not an object")
            if _trade_time_ms(item) > end_ms:
                continue
            if _trade_time_ms(item) >= start_ms:
                row = _trade_row(symbol=symbol, payload=item)
                rows_by_id.setdefault(int(row["agg_trade_id"]), row)
                mapped_page.append(row)
        last_id = max(_trade_id(item) for item in page if isinstance(item, Mapping))
        page_times = [_trade_time_ms(item) for item in page if isinstance(item, Mapping)]
        next_from_id = last_id + 1
        if len(page) < agg_trades_limit:
            break
        if page_times and min(page_times) > end_ms:
            break
        if mapped_page and max(int(row["timestamp"]) for row in mapped_page) >= end_ms:
            break

    return [rows_by_id[item] for item in sorted(rows_by_id)], request_count


def _execution_metadata(
    *,
    endpoint: str,
    symbol: str,
    requested_start_time: str,
    requested_end_time: str,
    fetched_at: str,
    rows: int,
    evidence_time_semantics: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "endpoint": endpoint,
        "symbol": symbol,
        "requested_window": {
            "start_time": _utc_iso(requested_start_time),
            "end_time": _utc_iso(requested_end_time),
        },
        "fetched_at": _utc_iso(fetched_at),
        "rows": rows,
        "evidence_time_semantics": evidence_time_semantics,
    }
    if extra:
        metadata.update(dict(extra))
    return metadata


def download_binance_execution_evidence(
    *,
    archive_root: str | Path,
    symbol: str,
    start_time: str,
    end_time: str,
    include_order_book: bool = True,
    include_trades: bool = True,
    base_url: str = BINANCE_FUTURES_PUBLIC_BASE_URL,
    depth_limit: int = DEFAULT_DEPTH_LIMIT,
    agg_trades_limit: int = DEFAULT_AGG_TRADES_LIMIT,
    max_retries: int = 2,
    retry_sleep_seconds: float = 1.0,
    fetch_json: FetchJson | None = None,
    sleep: Sleep = time.sleep,
    now: Now = _default_now,
) -> BinanceExecutionDownloadResult:
    requested_start = _utc_iso(start_time)
    requested_end = _utc_iso(end_time)
    if _utc_millis(requested_end) <= _utc_millis(requested_start):
        raise ValueError("end_time must be after start_time")
    fetch = fetch_json or _default_fetch_json(base_url)
    order_book_row: dict[str, Any] | None = None
    order_book_fetched_at: str | None = None
    trade_rows: list[dict[str, Any]] = []
    trade_request_count = 0

    if include_order_book:
        order_book_row, order_book_fetched_at = _fetch_depth(
            symbol=symbol,
            depth_limit=depth_limit,
            fetch=fetch,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
            sleep=sleep,
            now=now,
        )
    if include_trades:
        trade_rows, trade_request_count = _fetch_agg_trades(
            symbol=symbol,
            start_time=requested_start,
            end_time=requested_end,
            agg_trades_limit=agg_trades_limit,
            fetch=fetch,
            max_retries=max_retries,
            retry_sleep_seconds=retry_sleep_seconds,
            sleep=sleep,
        )

    archive_paths: list[str] = []
    archive_path = Path(archive_root)
    if order_book_row is not None and order_book_fetched_at is not None:
        archived = archive_raw_market_payload(
            archive_root=archive_path,
            exchange="binance",
            market="futures",
            dataset="order_book",
            symbol=symbol,
            coverage_start=order_book_fetched_at,
            coverage_end=order_book_fetched_at,
            fetched_at=order_book_fetched_at,
            endpoint=DEPTH_ENDPOINT,
            payload={"rows": [order_book_row]},
            metadata=_execution_metadata(
                endpoint=DEPTH_ENDPOINT,
                symbol=symbol,
                requested_start_time=requested_start,
                requested_end_time=requested_end,
                fetched_at=order_book_fetched_at,
                rows=1,
                evidence_time_semantics="point_in_time_fetch_not_historical",
                extra={
                    "limitation": "Binance REST depth is a point-in-time fetch snapshot, not a historical order book snapshot.",
                },
            ),
        )
        archive_paths.extend([str(archived.data_path), str(archived.manifest_path)])
    if trade_rows:
        fetched_at = _utc_iso(now())
        archived = archive_raw_market_payload(
            archive_root=archive_path,
            exchange="binance",
            market="futures",
            dataset="trades",
            symbol=symbol,
            coverage_start=_utc_iso(trade_rows[0]["timestamp"]),
            coverage_end=_utc_iso(trade_rows[-1]["timestamp"]),
            fetched_at=fetched_at,
            endpoint=AGG_TRADES_ENDPOINT,
            payload={"rows": trade_rows},
            metadata=_execution_metadata(
                endpoint=AGG_TRADES_ENDPOINT,
                symbol=symbol,
                requested_start_time=requested_start,
                requested_end_time=requested_end,
                fetched_at=fetched_at,
                rows=len(trade_rows),
                evidence_time_semantics="historical_agg_trade_time",
                extra={
                    "maker_side_mapping": {
                        "m_false": "buyer_was_taker_side_buy",
                        "m_true": "buyer_was_maker_side_sell",
                    },
                },
            ),
        )
        archive_paths.extend([str(archived.data_path), str(archived.manifest_path)])

    return BinanceExecutionDownloadResult(
        symbol=symbol,
        requested_start_time=requested_start,
        requested_end_time=requested_end,
        base_url=base_url,
        dry_run=False,
        order_book_request_count=1 if include_order_book else 0,
        trade_request_count=trade_request_count,
        order_book_record_count=1 if order_book_row is not None else 0,
        trade_record_count=len(trade_rows),
        archive_paths=tuple(archive_paths),
    )


def _dry_run_payload(args: argparse.Namespace) -> dict[str, Any]:
    endpoints = []
    if args.include_order_book:
        endpoints.append(DEPTH_ENDPOINT)
    if args.include_trades:
        endpoints.append(AGG_TRADES_ENDPOINT)
    return {
        "dry_run": True,
        "symbol": args.symbol,
        "archive_root": args.archive_root,
        "requested_window": {
            "start_time": _utc_iso(args.start_time),
            "end_time": _utc_iso(args.end_time),
        },
        "base_url": args.base_url,
        "would_request": endpoints,
        "note": "Use --execute to call Binance public market-data endpoints and write archives.",
    }


def main(argv: Sequence[str] | None = None, *, fetch_json: FetchJson | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Binance Futures public execution evidence into raw-market archives")
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--base-url", default=BINANCE_FUTURES_PUBLIC_BASE_URL)
    parser.add_argument("--depth-limit", type=int, default=DEFAULT_DEPTH_LIMIT)
    parser.add_argument("--agg-trades-limit", type=int, default=DEFAULT_AGG_TRADES_LIMIT)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-sleep-seconds", type=float, default=1.0)
    parser.add_argument("--execute", action="store_true", help="Call public Binance endpoints and write archive files")
    parser.add_argument("--skip-order-book", action="store_true")
    parser.add_argument("--skip-trades", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.include_order_book = not args.skip_order_book
    args.include_trades = not args.skip_trades

    if not args.execute:
        print(json.dumps(_dry_run_payload(args), ensure_ascii=False, indent=2))
        return 0

    result = download_binance_execution_evidence(
        archive_root=args.archive_root,
        symbol=args.symbol,
        start_time=args.start_time,
        end_time=args.end_time,
        include_order_book=args.include_order_book,
        include_trades=args.include_trades,
        base_url=args.base_url,
        depth_limit=args.depth_limit,
        agg_trades_limit=args.agg_trades_limit,
        max_retries=args.max_retries,
        retry_sleep_seconds=args.retry_sleep_seconds,
        fetch_json=fetch_json,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
