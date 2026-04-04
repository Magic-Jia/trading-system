from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlencode
from urllib.request import urlopen

from .raw_market import PHASE1_RAW_MARKET_DATASET_ALIASES, archive_raw_market_payload

PHASE1_BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
PHASE1_OPEN_INTEREST_PERIOD = "1h"
_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


@dataclass(frozen=True, slots=True)
class Phase1BinanceEndpoint:
    dataset: str
    path: str
    max_limit: int


@dataclass(frozen=True, slots=True)
class Phase1RawMarketFetchResult:
    dataset: str
    symbol: str
    timeframe: str | None
    coverage_start: str
    coverage_end: str
    request_count: int
    archived_count: int
    skipped_count: int
    record_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PHASE1_BINANCE_FUTURES_ENDPOINTS: dict[str, Phase1BinanceEndpoint] = {
    "ohlcv": Phase1BinanceEndpoint(dataset="ohlcv", path="/fapi/v1/klines", max_limit=1500),
    "funding": Phase1BinanceEndpoint(dataset="funding", path="/fapi/v1/fundingRate", max_limit=1000),
    "open-interest": Phase1BinanceEndpoint(dataset="open-interest", path="/futures/data/openInterestHist", max_limit=500),
}


def _canonical_dataset(dataset: str) -> str:
    normalized = dataset.strip().lower()
    canonical = PHASE1_RAW_MARKET_DATASET_ALIASES.get(normalized)
    if canonical is None:
        supported = ", ".join(sorted(set(PHASE1_RAW_MARKET_DATASET_ALIASES.values())))
        raise ValueError(f"dataset must be one of: {supported}")
    return canonical


def _utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_millis(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _millis_to_utc_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _row_timestamp_ms(*, dataset: str, row: object) -> int:
    if dataset == "ohlcv":
        if isinstance(row, (list, tuple)) and row:
            return int(row[0])
        if isinstance(row, dict) and row.get("open_time") is not None:
            return int(row["open_time"])
        if isinstance(row, dict) and row.get("openTime") is not None:
            return int(row["openTime"])
        raise ValueError("ohlcv row missing open time")
    if not isinstance(row, dict):
        raise ValueError(f"{dataset} rows must be JSON objects")
    if dataset == "funding":
        return int(row["fundingTime"])
    return int(row["timestamp"])


def _next_cursor_ms(*, dataset: str, last_timestamp_ms: int, timeframe: str | None) -> int:
    if dataset == "ohlcv":
        if timeframe is None:
            raise ValueError("ohlcv timeframe is required")
        step = _TIMEFRAME_MS.get(timeframe)
        if step is None:
            raise ValueError(f"unsupported ohlcv timeframe: {timeframe}")
        return last_timestamp_ms + step
    return last_timestamp_ms + 1


def _payload_for_archive(*, dataset: str, symbol: str, timeframe: str | None, rows: list[object]) -> Any:
    if dataset == "ohlcv":
        return {
            "symbol": symbol,
            "interval": timeframe,
            "rows": rows,
        }
    return rows


def _params_for_request(*, dataset: str, symbol: str, timeframe: str | None, start_ms: int, end_ms: int, limit: int) -> dict[str, object]:
    params: dict[str, object] = {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    if dataset == "ohlcv":
        params["interval"] = timeframe
    elif dataset == "open-interest":
        params["period"] = PHASE1_OPEN_INTEREST_PERIOD
    return params


def _default_fetch_json(endpoint: str, params: dict[str, object]) -> list[object]:
    url = f"{PHASE1_BINANCE_FUTURES_BASE_URL}{endpoint}?{urlencode(params)}"
    with urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"expected list payload from Binance endpoint {endpoint}")
    return payload


def fetch_phase1_raw_market_coverage(
    *,
    archive_root: str | Path,
    dataset: str,
    symbol: str,
    coverage_start: str,
    coverage_end: str,
    timeframe: str | None = None,
    fetch_json: Callable[[str, dict[str, object]], list[object]] | None = None,
) -> Phase1RawMarketFetchResult:
    canonical_dataset = _canonical_dataset(dataset)
    endpoint = PHASE1_BINANCE_FUTURES_ENDPOINTS[canonical_dataset]
    fetch = fetch_json or _default_fetch_json
    archive_path = Path(archive_root)
    start_ms = _utc_millis(coverage_start)
    end_ms = _utc_millis(coverage_end)
    cursor_ms = start_ms
    request_count = 0
    archived_count = 0
    skipped_count = 0
    record_count = 0

    while cursor_ms < end_ms:
        params = _params_for_request(
            dataset=canonical_dataset,
            symbol=symbol,
            timeframe=timeframe,
            start_ms=cursor_ms,
            end_ms=end_ms,
            limit=endpoint.max_limit,
        )
        rows = list(fetch(endpoint.path, params))
        request_count += 1
        if not rows:
            break
        first_timestamp_ms = _row_timestamp_ms(dataset=canonical_dataset, row=rows[0])
        last_timestamp_ms = _row_timestamp_ms(dataset=canonical_dataset, row=rows[-1])
        try:
            archive_raw_market_payload(
                archive_root=archive_path,
                exchange="binance",
                market="futures",
                dataset=canonical_dataset,
                symbol=symbol,
                timeframe=timeframe,
                coverage_start=_millis_to_utc_iso(first_timestamp_ms),
                coverage_end=_millis_to_utc_iso(last_timestamp_ms),
                fetched_at=datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                endpoint=endpoint.path,
                payload=_payload_for_archive(
                    dataset=canonical_dataset,
                    symbol=symbol,
                    timeframe=timeframe,
                    rows=rows,
                ),
            )
            archived_count += 1
        except FileExistsError:
            skipped_count += 1
        record_count += len(rows)
        next_cursor = _next_cursor_ms(dataset=canonical_dataset, last_timestamp_ms=last_timestamp_ms, timeframe=timeframe)
        if next_cursor <= cursor_ms:
            raise ValueError("raw-market fetch cursor failed to advance")
        cursor_ms = next_cursor

    return Phase1RawMarketFetchResult(
        dataset=canonical_dataset,
        symbol=symbol,
        timeframe=timeframe,
        coverage_start=_utc_iso(coverage_start),
        coverage_end=_utc_iso(coverage_end),
        request_count=request_count,
        archived_count=archived_count,
        skipped_count=skipped_count,
        record_count=record_count,
    )


def main(argv: Sequence[str] | None = None, *, fetch_json: Callable[[str, dict[str, object]], list[object]] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch Binance futures raw-market history into canonical archive storage")
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--coverage-start", required=True)
    parser.add_argument("--coverage-end", required=True)
    parser.add_argument("--timeframe")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results = [
        fetch_phase1_raw_market_coverage(
            archive_root=args.archive_root,
            dataset=args.dataset,
            symbol=symbol,
            timeframe=args.timeframe,
            coverage_start=args.coverage_start,
            coverage_end=args.coverage_end,
            fetch_json=fetch_json,
        )
        for symbol in args.symbol
    ]
    print(json.dumps([item.as_dict() for item in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
