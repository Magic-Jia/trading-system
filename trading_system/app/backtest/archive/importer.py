from __future__ import annotations

import json
import shutil
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trading_system.app.universe.sector_map import sector_for_symbol

from ..dataset import load_historical_dataset
from .data_quality import build_raw_market_data_quality_report
from .raw_market import (
    ImportedRawMarketRecord,
    ImportedRawMarketSeries,
    load_phase1_raw_market_imports,
    load_phase1_raw_market_imports_from_manifest_paths,
    load_phase1_raw_market_manifest,
)

PHASE1_IMPORTER_SCOPE = "phase1_binance_futures"
PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA = "imported_market_context.v1"
PHASE1_IMPORTER_DERIVATIVES_SCHEMA = "imported_derivatives_snapshot.v1"
PHASE1_IMPORTER_ACCOUNT_SCHEMA = "imported_account_snapshot.v1"
PHASE1_IMPORTER_INSTRUMENT_SNAPSHOT_SCHEMA = "imported_instrument_snapshot.v1"
PHASE1_IMPORTER_BUNDLE_SCHEMA = "phase1_import_bundle.v1"
PHASE1_IMPORTER_ROOT_SCHEMA = "phase1_imported_dataset_root.v1"
PHASE1_IMPORTER_OHLCV_TIMEFRAME = "1h"
PHASE1_IMPORTER_ROOT_MANIFEST = "import_manifest.json"
PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES = ("1m", "5m", "15m", "30m")
PHASE1_IMPORTER_DEFAULT_EXECUTION_EVIDENCE_MAX_STALENESS = timedelta(minutes=5)
PHASE1_IMPORTER_DEFAULT_MARK_PRICE_MAX_AGE = timedelta(hours=1, minutes=1)
PHASE1_IMPORTER_DEFAULT_FUNDING_MAX_AGE = timedelta(hours=8, minutes=1)
PHASE1_IMPORTER_DEFAULT_OPEN_INTEREST_MAX_AGE = timedelta(hours=1, minutes=1)
_PHASE1_IMPORTER_INTRADAY_GAPS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
}
_PHASE1_IMPORTER_OHLCV_TIMEFRAME_ORDER = ("1h", *PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES)
_KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "USD")
_PHASE1_DEFAULT_QUANTITY_STEP = 0.001
_PHASE1_DEFAULT_PRICE_TICK = 0.1


@dataclass(frozen=True, slots=True)
class Phase1DatasetBundleMaterial:
    timestamp: datetime
    run_id: str
    metadata: dict[str, Any]
    market_context: dict[str, Any]
    derivatives_snapshot: dict[str, Any]
    account_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ImportedPhase1DatasetRoot:
    archive_root: Path
    dataset_root: Path
    bundle_dirs: tuple[Path, ...]
    snapshot_count: int
    symbols: tuple[str, ...]
    start_timestamp: datetime
    end_timestamp: datetime


@dataclass(frozen=True, slots=True)
class _OhlcvBar:
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float


@dataclass(frozen=True, slots=True)
class _OhlcvSeriesIndex:
    timestamps: tuple[datetime, ...]
    bars: tuple[_OhlcvBar, ...]
    timestamp_set: frozenset[datetime]
    contiguous_start_indexes: tuple[int, ...]

    @classmethod
    def from_records(cls, records: Sequence[ImportedRawMarketRecord], *, expected_gap: timedelta) -> "_OhlcvSeriesIndex":
        timestamps, bars = _ohlcv_bar_lookup(records)
        start_indexes: list[int] = []
        current_start = 0
        for index, bar in enumerate(bars):
            if index > 0 and bar.observed_at - bars[index - 1].observed_at != expected_gap:
                current_start = index
            start_indexes.append(current_start)
        return cls(
            timestamps=tuple(timestamps),
            bars=tuple(bars),
            timestamp_set=frozenset(timestamps),
            contiguous_start_indexes=tuple(start_indexes),
        )

    def contiguous_history_up_to(self, timestamp: datetime) -> list[_OhlcvBar]:
        end_index = bisect_right(self.timestamps, timestamp) - 1
        if end_index < 0:
            return []
        start_index = self.contiguous_start_indexes[end_index]
        return list(self.bars[start_index : end_index + 1])


def _aggregate_ohlcv_bucket(bucket: datetime, rows: Sequence[_OhlcvBar]) -> _OhlcvBar:
    first = rows[0]
    last = rows[-1]
    return _OhlcvBar(
        observed_at=bucket,
        open=first.open,
        high=max(row.high for row in rows),
        low=min(row.low for row in rows),
        close=last.close,
        base_volume=sum(row.base_volume for row in rows),
        quote_volume=sum(row.quote_volume for row in rows),
    )


def _resampled_history_by_hourly_timestamp(
    hourly_index: _OhlcvSeriesIndex,
    *,
    hours: int,
) -> dict[datetime, tuple[_OhlcvBar, ...]]:
    histories: dict[datetime, tuple[_OhlcvBar, ...]] = {}
    aggregated: list[_OhlcvBar] = []
    bucket_rows: list[_OhlcvBar] = []
    current_bucket: datetime | None = None
    previous_bar: _OhlcvBar | None = None

    for bar in hourly_index.bars:
        if previous_bar is None or bar.observed_at - previous_bar.observed_at != timedelta(hours=1):
            aggregated = []
            bucket_rows = []
            current_bucket = None

        bucket = _bucket_start(bar.observed_at, hours=hours)
        if current_bucket != bucket:
            current_bucket = bucket
            bucket_rows = [bar]
            aggregated.append(_aggregate_ohlcv_bucket(bucket, bucket_rows))
        else:
            bucket_rows.append(bar)
            aggregated[-1] = _aggregate_ohlcv_bucket(bucket, bucket_rows)

        histories[bar.observed_at] = tuple(aggregated)
        previous_bar = bar

    return histories


@dataclass(frozen=True, slots=True)
class _Phase1SymbolSeries:
    symbol: str
    ohlcv: ImportedRawMarketSeries
    funding: ImportedRawMarketSeries | None
    mark_price: ImportedRawMarketSeries | None
    open_interest: ImportedRawMarketSeries | None
    intraday_ohlcv: dict[str, ImportedRawMarketSeries]
    order_book: ImportedRawMarketSeries | None
    trades: ImportedRawMarketSeries | None
    symbol_metadata: dict[str, Any] | None


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bundle_fragment(value: datetime) -> str:
    return _utc_timestamp(value).replace(":", "-")


def _run_id(value: datetime) -> str:
    return f"phase1-import-{_bundle_fragment(value)}"


def _to_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _required_ohlcv_float(value: Any, *, field: str, observed_at: datetime) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ohlcv {field} must be numeric: {observed_at}") from exc
    if not parsed == parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError(f"ohlcv {field} must be finite: {observed_at}")
    return parsed


def _hourly_ohlcv_bar(record: ImportedRawMarketRecord) -> _OhlcvBar:
    payload = record.payload
    if isinstance(payload, Mapping):
        close = _required_ohlcv_float(payload.get("close"), field="close", observed_at=record.observed_at)
        open_value = _required_ohlcv_float(payload.get("open", close), field="open", observed_at=record.observed_at)
        high = _required_ohlcv_float(payload.get("high", max(open_value, close)), field="high", observed_at=record.observed_at)
        low = _required_ohlcv_float(payload.get("low", min(open_value, close)), field="low", observed_at=record.observed_at)
        base_volume = _required_ohlcv_float(payload.get("volume"), field="volume", observed_at=record.observed_at)
        if "quote_asset_volume" in payload:
            quote_volume = _required_ohlcv_float(
                payload.get("quote_asset_volume"), field="quote volume", observed_at=record.observed_at
            )
        else:
            quote_volume = close * base_volume
    elif isinstance(payload, (list, tuple)):
        if len(payload) < 6:
            raise ValueError(f"ohlcv array payload must match Binance kline layout: {record.observed_at}")
        close = _required_ohlcv_float(payload[4], field="close", observed_at=record.observed_at)
        open_value = _required_ohlcv_float(payload[1], field="open", observed_at=record.observed_at)
        high = _required_ohlcv_float(payload[2], field="high", observed_at=record.observed_at)
        low = _required_ohlcv_float(payload[3], field="low", observed_at=record.observed_at)
        base_volume = _required_ohlcv_float(payload[5], field="volume", observed_at=record.observed_at)
        if len(payload) > 7:
            quote_volume = _required_ohlcv_float(payload[7], field="quote volume", observed_at=record.observed_at)
        else:
            quote_volume = close * base_volume
    else:
        raise ValueError(f"ohlcv record payload must be a JSON object: {record.observed_at}")
    if close <= 0.0:
        raise ValueError(f"ohlcv close must be positive: {record.observed_at}")
    if open_value <= 0.0:
        raise ValueError(f"ohlcv open must be positive: {record.observed_at}")
    if high < max(open_value, close):
        raise ValueError(f"ohlcv high must cover open and close: {record.observed_at}")
    if low > min(open_value, close):
        raise ValueError(f"ohlcv low must cover open and close: {record.observed_at}")
    if low <= 0.0 or high <= 0.0:
        raise ValueError(f"ohlcv price bounds must be positive: {record.observed_at}")
    if base_volume < 0.0:
        raise ValueError(f"ohlcv volume must be non-negative: {record.observed_at}")
    if quote_volume < 0.0:
        raise ValueError(f"ohlcv quote volume must be non-negative: {record.observed_at}")
    return _OhlcvBar(
        observed_at=record.observed_at,
        open=open_value,
        high=high,
        low=low,
        close=close,
        base_volume=base_volume,
        quote_volume=quote_volume,
    )


@lru_cache(maxsize=200_000)
def _bucket_start(value: datetime, *, hours: int) -> datetime:
    normalized = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if hours == 24:
        return normalized.replace(hour=0)
    return normalized - timedelta(hours=normalized.hour % hours)


def _resample_bars(hourly_bars: Sequence[_OhlcvBar], *, hours: int) -> list[_OhlcvBar]:
    grouped: dict[datetime, list[_OhlcvBar]] = {}
    for bar in hourly_bars:
        grouped.setdefault(_bucket_start(bar.observed_at, hours=hours), []).append(bar)
    aggregated: list[_OhlcvBar] = []
    for bucket in sorted(grouped):
        rows = sorted(grouped[bucket], key=lambda row: row.observed_at)
        first = rows[0]
        last = rows[-1]
        aggregated.append(
            _OhlcvBar(
                observed_at=bucket,
                open=first.open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=last.close,
                base_volume=sum(row.base_volume for row in rows),
                quote_volume=sum(row.quote_volume for row in rows),
            )
        )
    return aggregated


def _ema(values: Sequence[float], *, period: int) -> float:
    if len(values) < period:
        raise ValueError(f"ema period requires at least {period} values")
    alpha = 2.0 / (period + 1.0)
    current = values[0]
    for value in values[1:]:
        current = (value * alpha) + (current * (1.0 - alpha))
    return current


def _rsi(values: Sequence[float], *, period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"rsi period requires more than {period} values")
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[:-1], values[1:], strict=False):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    seed_gains = gains[:period]
    seed_losses = losses[:period]
    avg_gain = sum(seed_gains) / period
    avg_loss = sum(seed_losses) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss <= 0.0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr_pct(bars: Sequence[_OhlcvBar], *, period: int = 14) -> float:
    if len(bars) < period:
        raise ValueError(f"atr period requires at least {period} bars")
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        if previous_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        true_ranges.append(max(true_range, 0.0))
        previous_close = bar.close
    current_close = bars[-1].close
    if current_close <= 0.0:
        return 0.0
    return (sum(true_ranges[-period:]) / period) / current_close


def _rolling_quote_volume(hourly_bars: Sequence[_OhlcvBar], *, period_hours: int = 24) -> float:
    if len(hourly_bars) < period_hours:
        raise ValueError(f"rolling quote volume requires at least {period_hours} hourly bars")
    return sum(bar.quote_volume for bar in hourly_bars[-period_hours:])


def _return_pct(bars: Sequence[_OhlcvBar], *, periods_back: int) -> float:
    if len(bars) <= periods_back:
        raise ValueError(f"return lookback requires at least {periods_back + 1} bars")
    current = bars[-1].close
    previous = bars[-(periods_back + 1)].close
    if previous <= 0.0:
        return 0.0
    return (current / previous) - 1.0


def _liquidity_tier(volume_usdt_24h: float) -> str:
    if volume_usdt_24h >= 2_000_000_000.0:
        return "top"
    if volume_usdt_24h >= 500_000_000.0:
        return "high"
    if volume_usdt_24h >= 100_000_000.0:
        return "medium"
    return "low"


def _base_asset(symbol: str) -> str:
    upper = str(symbol).upper().strip()
    for quote in _KNOWN_QUOTES:
        if upper.endswith(quote) and len(upper) > len(quote):
            return upper[: -len(quote)]
    return upper


def _listing_timestamp(series: ImportedRawMarketSeries) -> datetime:
    if not series.records:
        raise ValueError(f"ohlcv series must include at least one record: {series.series_key}")
    return min(record.observed_at for record in series.records)


def _resolved_symbol_metadata(
    *,
    symbol: str,
    series_items: Sequence[ImportedRawMarketSeries],
) -> dict[str, Any] | None:
    resolved: dict[str, Any] | None = None
    for series in series_items:
        symbol_metadata = series.symbol_metadata
        if symbol_metadata is None:
            continue
        if resolved is None:
            resolved = symbol_metadata
            continue
        if symbol_metadata != resolved:
            raise ValueError(f"raw-market symbol metadata mismatch across phase1 datasets for symbol {symbol}")
    return resolved


def _symbol_metadata_timestamp(
    *,
    symbol_metadata: Mapping[str, Any] | None,
    fallback_series: ImportedRawMarketSeries,
) -> datetime:
    if symbol_metadata is None:
        return _listing_timestamp(fallback_series)
    return _utc_datetime(str(symbol_metadata["listing_timestamp"]))


def _symbol_metadata_float(
    *,
    symbol_metadata: Mapping[str, Any] | None,
    field: str,
    default: float,
) -> float:
    if symbol_metadata is None:
        return default
    return _to_float(symbol_metadata.get(field), default=default)


def _has_complete_funding_series(
    funding_records: Sequence[ImportedRawMarketRecord],
    *,
    start: datetime,
    end: datetime,
) -> bool:
    if not funding_records:
        return False
    max_gap = timedelta(hours=8, minutes=1)
    timestamps = sorted(record.observed_at for record in funding_records if start <= record.observed_at <= end)
    if not timestamps:
        return False
    if timestamps[0] - start > max_gap:
        return False
    previous = timestamps[0]
    for current in timestamps[1:]:
        if current - previous > max_gap:
            return False
        previous = current
    return end - timestamps[-1] <= max_gap


def _series_index(imported_series: Iterable[ImportedRawMarketSeries]) -> dict[tuple[str, str, str | None], ImportedRawMarketSeries]:
    indexed: dict[tuple[str, str, str | None], ImportedRawMarketSeries] = {}
    for series in imported_series:
        key = (series.symbol, series.dataset, series.timeframe)
        indexed[key] = series
    return indexed


def _phase1_symbol_series(imported_series: Iterable[ImportedRawMarketSeries]) -> tuple[_Phase1SymbolSeries, ...]:
    indexed = _series_index(imported_series)
    symbols = sorted({symbol for symbol, _, _ in indexed})
    assembled: list[_Phase1SymbolSeries] = []
    for symbol in symbols:
        missing: list[str] = []
        ohlcv = indexed.get((symbol, "ohlcv", PHASE1_IMPORTER_OHLCV_TIMEFRAME))
        funding = indexed.get((symbol, "funding", None))
        mark_price = indexed.get((symbol, "mark-price", None))
        open_interest = indexed.get((symbol, "open-interest", None))
        if ohlcv is None:
            missing.append("ohlcv:1h")
        if missing:
            missing_suffix = ", ".join(missing)
            raise ValueError(f"missing required phase1 raw-market series for symbol {symbol}: {missing_suffix}")
        assert ohlcv is not None
        assembled.append(
            _Phase1SymbolSeries(
                symbol=symbol,
                ohlcv=ohlcv,
                funding=funding,
                mark_price=mark_price,
                open_interest=open_interest,
                intraday_ohlcv={
                    timeframe: series
                    for timeframe in PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES
                    if (series := indexed.get((symbol, "ohlcv", timeframe))) is not None
                },
                order_book=indexed.get((symbol, "order-book", None)),
                trades=indexed.get((symbol, "trades", None)),
                symbol_metadata=_resolved_symbol_metadata(
                    symbol=symbol,
                    series_items=(
                        ohlcv,
                        *tuple(series for series in (funding, mark_price, open_interest) if series is not None),
                        *(
                            indexed[(symbol, "ohlcv", timeframe)]
                            for timeframe in PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES
                            if (symbol, "ohlcv", timeframe) in indexed
                        ),
                    ),
                ),
            )
        )
    return tuple(assembled)


def _record_lookup(records: Sequence[ImportedRawMarketRecord]) -> tuple[list[datetime], list[ImportedRawMarketRecord]]:
    ordered = sorted(records, key=lambda record: record.observed_at)
    return [record.observed_at for record in ordered], ordered


def _ohlcv_bar_lookup(records: Sequence[ImportedRawMarketRecord]) -> tuple[list[datetime], list[_OhlcvBar]]:
    ordered = sorted((_hourly_ohlcv_bar(record) for record in records), key=lambda row: row.observed_at)
    return [bar.observed_at for bar in ordered], ordered


def _latest_record_at_or_before(
    timestamps: Sequence[datetime],
    records: Sequence[ImportedRawMarketRecord],
    target: datetime,
) -> ImportedRawMarketRecord | None:
    index = bisect_right(timestamps, target) - 1
    if index < 0:
        return None
    return records[index]


def _first_record_at_or_after(
    timestamps: Sequence[datetime],
    records: Sequence[ImportedRawMarketRecord],
    target: datetime,
) -> ImportedRawMarketRecord | None:
    index = bisect_right(timestamps, target - timedelta(microseconds=1))
    if index >= len(records):
        return None
    return records[index]


def _records_in_window(
    timestamps: Sequence[datetime],
    records: Sequence[ImportedRawMarketRecord],
    *,
    start: datetime,
    end: datetime,
) -> list[ImportedRawMarketRecord]:
    index = bisect_right(timestamps, start - timedelta(microseconds=1))
    selected: list[ImportedRawMarketRecord] = []
    for record in records[index:]:
        if record.observed_at > end:
            break
        selected.append(record)
    return selected


def _execution_coverage_template(*, available: bool, max_staleness: timedelta) -> dict[str, Any]:
    return {
        "available": available,
        "max_staleness_seconds": int(max_staleness.total_seconds()),
        "materialized": {"order_book": 0, "trades": 0},
        "missing": {"order_book": 0, "trades": 0},
        "stale": {"order_book": 0, "trades": 0},
        "ambiguous": {"order_book": 0, "trades": 0},
    }


def _increment_execution_coverage(coverage: dict[str, Any], bucket: str, evidence_type: str) -> None:
    current = coverage.setdefault(bucket, {}).setdefault(evidence_type, 0)
    coverage[bucket][evidence_type] = int(current) + 1


def _positive_execution_float(value: Any) -> float | None:
    parsed = _to_float(value, default=-1.0)
    return parsed if parsed > 0.0 else None


def _required_positive_execution_float(value: Any, *, field: str, observed_at: datetime) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric: {observed_at}") from exc
    if not parsed == parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError(f"{field} must be finite: {observed_at}")
    if parsed <= 0.0:
        raise ValueError(f"{field} must be positive: {observed_at}")
    return parsed


def _execution_symbol_matches(payload: Mapping[str, Any], symbol: str) -> bool:
    raw_symbol = payload.get("symbol")
    if raw_symbol is None:
        return True
    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        raise ValueError("execution symbol must be a string")
    if raw_symbol != raw_symbol.strip() or raw_symbol.upper() != raw_symbol:
        raise ValueError("execution symbol must be canonical")
    return raw_symbol == symbol.upper()


def _order_book_payload(record: ImportedRawMarketRecord, *, symbol: str) -> dict[str, Any] | None:
    payload = record.payload
    if not isinstance(payload, Mapping) or not _execution_symbol_matches(payload, symbol):
        return None
    bid = _required_positive_execution_float(payload.get("bid"), field="order book bid", observed_at=record.observed_at)
    ask = _required_positive_execution_float(payload.get("ask"), field="order book ask", observed_at=record.observed_at)
    if ask < bid:
        raise ValueError(f"order book ask must be greater than or equal to bid: {record.observed_at}")
    result: dict[str, Any] = {
        "timestamp": _utc_timestamp(record.observed_at),
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
    }
    if "bid_size" in payload:
        bid_size = _required_positive_execution_float(
            payload.get("bid_size"), field="order book bid_size", observed_at=record.observed_at
        )
        result["bid_size"] = bid_size
    elif "bidSize" in payload:
        bid_size = _required_positive_execution_float(
            payload.get("bidSize"), field="order book bidSize", observed_at=record.observed_at
        )
        result["bid_size"] = bid_size
    if "ask_size" in payload:
        ask_size = _required_positive_execution_float(
            payload.get("ask_size"), field="order book ask_size", observed_at=record.observed_at
        )
        result["ask_size"] = ask_size
    elif "askSize" in payload:
        ask_size = _required_positive_execution_float(
            payload.get("askSize"), field="order book askSize", observed_at=record.observed_at
        )
        result["ask_size"] = ask_size
    return result


def _trade_payload(record: ImportedRawMarketRecord, *, symbol: str) -> dict[str, Any] | None:
    payload = record.payload
    if not isinstance(payload, Mapping) or not _execution_symbol_matches(payload, symbol):
        return None
    price = _required_positive_execution_float(
        payload.get("price", payload.get("p")), field="trade price", observed_at=record.observed_at
    )
    quantity = _required_positive_execution_float(
        payload.get("quantity", payload.get("qty", payload.get("q"))),
        field="trade quantity",
        observed_at=record.observed_at,
    )
    result = {
        "timestamp": _utc_timestamp(record.observed_at),
        "symbol": symbol,
        "price": price,
        "quantity": quantity,
    }
    raw_side = payload.get("side")
    if raw_side is not None:
        if not isinstance(raw_side, str) or not raw_side.strip():
            raise ValueError(f"trade side must be a non-empty string: {record.observed_at}")
        if raw_side != raw_side.strip() or raw_side.lower() != raw_side:
            raise ValueError(f"trade side must be canonical: {record.observed_at}")
        if raw_side in {"buy", "sell"}:
            result["side"] = raw_side
        else:
            raise ValueError(f"trade side must be buy or sell: {record.observed_at}")
    elif "m" in payload:
        maker_flag = payload.get("m")
        if not isinstance(maker_flag, bool):
            raise ValueError(f"trade maker flag must be boolean: {record.observed_at}")
        result["side"] = "sell" if maker_flag else "buy"
    return result


def _execution_payload_for_symbol(
    *,
    item: _Phase1SymbolSeries,
    timestamp: datetime,
    evidence_lookups: Mapping[tuple[str, str], tuple[list[datetime], list[ImportedRawMarketRecord]]],
    max_staleness: timedelta,
    coverage: dict[str, Any],
) -> dict[str, Any] | None:
    execution: dict[str, Any] = {}
    window_end = timestamp + max_staleness

    if item.order_book is not None:
        order_book_times, order_book_records = evidence_lookups[(item.symbol, "order_book")]
        candidate = _first_record_at_or_after(order_book_times, order_book_records, timestamp)
        if candidate is None:
            _increment_execution_coverage(coverage, "missing", "order_book")
        elif candidate.observed_at > window_end:
            _increment_execution_coverage(coverage, "missing", "order_book")
            _increment_execution_coverage(coverage, "stale", "order_book")
        else:
            order_book = _order_book_payload(candidate, symbol=item.symbol)
            if order_book is None:
                _increment_execution_coverage(coverage, "ambiguous", "order_book")
                _increment_execution_coverage(coverage, "missing", "order_book")
            else:
                execution["order_book"] = order_book
                _increment_execution_coverage(coverage, "materialized", "order_book")

    if item.trades is not None:
        trade_times, trade_records = evidence_lookups[(item.symbol, "trades")]
        trade_rows = [
            trade
            for record in _records_in_window(trade_times, trade_records, start=timestamp, end=window_end)
            if (trade := _trade_payload(record, symbol=item.symbol)) is not None
        ]
        if trade_rows:
            execution["trades"] = trade_rows
            _increment_execution_coverage(coverage, "materialized", "trades")
        else:
            candidate = _first_record_at_or_after(trade_times, trade_records, timestamp)
            _increment_execution_coverage(coverage, "missing", "trades")
            if candidate is not None and candidate.observed_at > window_end:
                _increment_execution_coverage(coverage, "stale", "trades")

    return execution or None


def _hourly_history_up_to(series: ImportedRawMarketSeries, *, timestamp: datetime) -> list[_OhlcvBar]:
    return _ohlcv_history_up_to(series, timestamp=timestamp, expected_gap=timedelta(hours=1))


def _contiguous_ohlcv_history_up_to(
    timestamps: Sequence[datetime],
    bars: Sequence[_OhlcvBar],
    *,
    timestamp: datetime,
    expected_gap: timedelta,
) -> list[_OhlcvBar]:
    end_index = bisect_right(timestamps, timestamp) - 1
    if end_index < 0:
        return []
    contiguous = [bars[end_index]]
    for index in range(end_index - 1, -1, -1):
        current = contiguous[-1]
        previous = bars[index]
        if current.observed_at - previous.observed_at != expected_gap:
            break
        contiguous.append(previous)
    contiguous.reverse()
    return contiguous


def _ohlcv_history_up_to(
    series: ImportedRawMarketSeries,
    *,
    timestamp: datetime,
    expected_gap: timedelta,
) -> list[_OhlcvBar]:
    timestamps, bars = _ohlcv_bar_lookup(series.records)
    return _contiguous_ohlcv_history_up_to(
        timestamps,
        bars,
        timestamp=timestamp,
        expected_gap=expected_gap,
    )


def _timeframe_payload(hourly_bars: Sequence[_OhlcvBar], *, timeframe: str) -> dict[str, Any]:
    if timeframe == "1h":
        bars = list(hourly_bars)
        periods_back = 24
        return_label = "return_pct_24h"
    elif timeframe == "30m":
        bars = list(hourly_bars)
        periods_back = 16
        return_label = "return_pct_8h"
    elif timeframe == "15m":
        bars = list(hourly_bars)
        periods_back = 16
        return_label = "return_pct_4h"
    elif timeframe == "5m":
        bars = list(hourly_bars)
        periods_back = 12
        return_label = "return_pct_1h"
    elif timeframe == "1m":
        bars = list(hourly_bars)
        periods_back = 15
        return_label = "return_pct_15m"
    elif timeframe == "4h":
        bars = _resample_bars(hourly_bars, hours=4)
        periods_back = 18
        return_label = "return_pct_3d"
    elif timeframe == "daily":
        bars = _resample_bars(hourly_bars, hours=24)
        periods_back = 7
        return_label = "return_pct_7d"
    else:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    closes = [bar.close for bar in bars]
    current = bars[-1]
    volume_usdt_24h = _rolling_quote_volume(hourly_bars)
    return {
        "open": current.open,
        "high": current.high,
        "low": current.low,
        "close": current.close,
        "ema_20": _ema(closes, period=20),
        "ema_50": _ema(closes, period=50),
        "rsi": _rsi(closes, period=14),
        "atr_pct": _atr_pct(bars, period=14),
        "volume_usdt_24h": volume_usdt_24h,
        return_label: _return_pct(
            bars,
            periods_back=periods_back,
        ),
    }


def _intraday_timeframe_return_config(timeframe: str) -> tuple[int, str]:
    if timeframe == "1h":
        return 24, "return_pct_24h"
    if timeframe == "30m":
        return 16, "return_pct_8h"
    if timeframe == "15m":
        return 16, "return_pct_4h"
    if timeframe == "5m":
        return 12, "return_pct_1h"
    if timeframe == "1m":
        return 15, "return_pct_15m"
    raise ValueError(f"unsupported incremental timeframe: {timeframe}")


def _timeframe_payloads_by_timestamp(
    bar_index: _OhlcvSeriesIndex,
    *,
    timeframe: str,
) -> dict[datetime, dict[str, Any]]:
    periods_back, return_label = _intraday_timeframe_return_config(timeframe)
    payloads: dict[datetime, dict[str, Any]] = {}
    closes: list[float] = []
    quote_window: deque[float] = deque()
    quote_window_sum = 0.0
    true_range_window: deque[float] = deque()
    true_range_sum = 0.0
    ema20: float | None = None
    ema50: float | None = None
    previous_close: float | None = None
    rsi_seed_gains: list[float] = []
    rsi_seed_losses: list[float] = []
    rsi_avg_gain: float | None = None
    rsi_avg_loss: float | None = None
    rsi_period = 14

    def reset_state() -> None:
        nonlocal quote_window_sum, true_range_sum, ema20, ema50, previous_close, rsi_avg_gain, rsi_avg_loss
        closes.clear()
        quote_window.clear()
        quote_window_sum = 0.0
        true_range_window.clear()
        true_range_sum = 0.0
        ema20 = None
        ema50 = None
        previous_close = None
        rsi_seed_gains.clear()
        rsi_seed_losses.clear()
        rsi_avg_gain = None
        rsi_avg_loss = None

    for index, bar in enumerate(bar_index.bars):
        if bar_index.contiguous_start_indexes[index] == index:
            reset_state()

        close = bar.close
        closes.append(close)

        ema20 = close if ema20 is None else (close * (2.0 / 21.0)) + (ema20 * (1.0 - (2.0 / 21.0)))
        ema50 = close if ema50 is None else (close * (2.0 / 51.0)) + (ema50 * (1.0 - (2.0 / 51.0)))

        quote_window.append(bar.quote_volume)
        quote_window_sum += bar.quote_volume
        if len(quote_window) > 24:
            quote_window_sum -= quote_window.popleft()

        if previous_close is None:
            true_range = max(bar.high - bar.low, 0.0)
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
                0.0,
            )
            delta = close - previous_close
            gain = max(delta, 0.0)
            loss = abs(min(delta, 0.0))
            if len(rsi_seed_gains) < rsi_period:
                rsi_seed_gains.append(gain)
                rsi_seed_losses.append(loss)
                if len(rsi_seed_gains) == rsi_period:
                    rsi_avg_gain = sum(rsi_seed_gains) / rsi_period
                    rsi_avg_loss = sum(rsi_seed_losses) / rsi_period
            elif rsi_avg_gain is not None and rsi_avg_loss is not None:
                rsi_avg_gain = ((rsi_avg_gain * (rsi_period - 1)) + gain) / rsi_period
                rsi_avg_loss = ((rsi_avg_loss * (rsi_period - 1)) + loss) / rsi_period

        true_range_window.append(true_range)
        true_range_sum += true_range
        if len(true_range_window) > 14:
            true_range_sum -= true_range_window.popleft()

        if (
            len(closes) >= 50
            and len(closes) > periods_back
            and len(quote_window) == 24
            and len(true_range_window) == 14
            and rsi_avg_gain is not None
            and rsi_avg_loss is not None
            and ema20 is not None
            and ema50 is not None
        ):
            rsi = 100.0 if rsi_avg_loss <= 0.0 else 100.0 - (100.0 / (1.0 + (rsi_avg_gain / rsi_avg_loss)))
            previous_return_close = closes[-(periods_back + 1)]
            return_pct = 0.0 if previous_return_close <= 0.0 else (close / previous_return_close) - 1.0
            payloads[bar.observed_at] = {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": close,
                "ema_20": ema20,
                "ema_50": ema50,
                "rsi": rsi,
                "atr_pct": (true_range_sum / 14) / close if close > 0.0 else 0.0,
                "volume_usdt_24h": quote_window_sum,
                return_label: return_pct,
            }

        previous_close = close

    return payloads


def _derived_timeframe_payload(
    bars: Sequence[_OhlcvBar],
    *,
    source_hourly_bars: Sequence[_OhlcvBar],
    timeframe: str,
) -> dict[str, Any]:
    if timeframe == "4h":
        periods_back = 18
        return_label = "return_pct_3d"
    elif timeframe == "daily":
        periods_back = 7
        return_label = "return_pct_7d"
    else:
        raise ValueError(f"unsupported derived timeframe: {timeframe}")
    closes = [bar.close for bar in bars]
    current = bars[-1]
    volume_usdt_24h = _rolling_quote_volume(source_hourly_bars)
    return {
        "open": current.open,
        "high": current.high,
        "low": current.low,
        "close": current.close,
        "ema_20": _ema(closes, period=20),
        "ema_50": _ema(closes, period=50),
        "rsi": _rsi(closes, period=14),
        "atr_pct": _atr_pct(bars, period=14),
        "volume_usdt_24h": volume_usdt_24h,
        return_label: _return_pct(
            bars,
            periods_back=periods_back,
        ),
    }


def _next_ohlcv_bar_payload(
    timestamps: Sequence[datetime],
    bars: Sequence[_OhlcvBar],
    *,
    timestamp: datetime,
) -> dict[str, Any] | None:
    index = bisect_right(timestamps, timestamp)
    if index >= len(bars):
        return None
    next_bar = bars[index]
    return {
        "timestamp": _utc_timestamp(next_bar.observed_at),
        "open": next_bar.open,
        "high": next_bar.high,
        "low": next_bar.low,
        "close": next_bar.close,
        "volume": next_bar.base_volume,
        "quote_asset_volume": next_bar.quote_volume,
    }


def _required_context_float(value: Any, *, field: str, observed_at: datetime) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric: {observed_at}") from exc
    if not parsed == parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError(f"{field} must be finite: {observed_at}")
    return parsed


def _open_interest_units(record: ImportedRawMarketRecord) -> float:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return 0.0
    if "sumOpenInterestValue" in payload:
        value = _required_context_float(
            payload.get("sumOpenInterestValue"), field="open interest", observed_at=record.observed_at
        )
    elif "openInterestUsd" in payload:
        value = _required_context_float(
            payload.get("openInterestUsd"), field="open interest", observed_at=record.observed_at
        )
    else:
        value = _required_context_float(
            payload.get("sumOpenInterest"), field="open interest", observed_at=record.observed_at
        )
    if value < 0.0:
        raise ValueError(f"open interest must be non-negative: {record.observed_at}")
    return value


def _open_interest_is_quote_value(record: ImportedRawMarketRecord) -> bool:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return False
    return "sumOpenInterestValue" in payload or "openInterestUsd" in payload


def _funding_rate(record: ImportedRawMarketRecord) -> float:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return 0.0
    return _required_context_float(payload.get("fundingRate"), field="funding rate", observed_at=record.observed_at)


def _mark_price(record: ImportedRawMarketRecord) -> float:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return 0.0
    value = _required_context_float(
        payload.get("markPrice", payload.get("mark_price")), field="mark price", observed_at=record.observed_at
    )
    if value <= 0.0:
        raise ValueError(f"mark price must be positive: {record.observed_at}")
    return value


def _context_coverage_template(
    *,
    available: bool,
    mark_price_max_age: timedelta,
    funding_max_age: timedelta,
    open_interest_max_age: timedelta,
) -> dict[str, Any]:
    return {
        "available": available,
        "max_age_seconds": {
            "mark_price": int(mark_price_max_age.total_seconds()),
            "funding": int(funding_max_age.total_seconds()),
            "open_interest": int(open_interest_max_age.total_seconds()),
        },
        "materialized": {"mark_price": 0, "funding": 0, "open_interest": 0},
        "missing": {"mark_price": 0, "funding": 0, "open_interest": 0},
        "stale": {"mark_price": 0, "funding": 0, "open_interest": 0},
    }


def _increment_context_coverage(coverage: dict[str, Any], bucket: str, evidence_type: str) -> None:
    current = coverage.setdefault(bucket, {}).setdefault(evidence_type, 0)
    coverage[bucket][evidence_type] = int(current) + 1


def _latest_fresh_record_at_or_before(
    timestamps: Sequence[datetime],
    records: Sequence[ImportedRawMarketRecord],
    target: datetime,
    *,
    max_age: timedelta,
    coverage: dict[str, Any],
    evidence_type: str,
) -> tuple[ImportedRawMarketRecord | None, str, int | None]:
    record = _latest_record_at_or_before(timestamps, records, target)
    if record is None:
        _increment_context_coverage(coverage, "missing", evidence_type)
        return None, "missing", None
    age_seconds = int((target - record.observed_at).total_seconds())
    if age_seconds < 0 or target - record.observed_at > max_age:
        _increment_context_coverage(coverage, "stale", evidence_type)
        return None, "stale", age_seconds
    _increment_context_coverage(coverage, "materialized", evidence_type)
    return record, "materialized", age_seconds


def _ordered_timeframes(values: Iterable[str]) -> list[str]:
    present = set(_require_canonical_string_items(tuple(values), field="ohlcv_timeframes.value"))
    ordered = [timeframe for timeframe in _PHASE1_IMPORTER_OHLCV_TIMEFRAME_ORDER if timeframe in present]
    ordered.extend(sorted(present.difference(ordered)))
    return ordered


def _ohlcv_timeframe_coverage(
    symbol_series: Sequence[_Phase1SymbolSeries],
    *,
    materialized_timeframes: Iterable[str],
    not_materialized: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    available = {"1h"}
    for item in symbol_series:
        available.update(item.intraday_ohlcv.keys())
    materialized = set(materialized_timeframes)
    missing_optional = [
        timeframe
        for timeframe in PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES
        if timeframe not in available
    ]
    return {
        "available": _ordered_timeframes(available),
        "materialized": _ordered_timeframes(materialized),
        "missing_optional": list(missing_optional),
        "not_materialized": dict(sorted((not_materialized or {}).items())),
    }


def _import_trace(
    symbol_series: Sequence[_Phase1SymbolSeries],
    *,
    materialized_timeframes: Iterable[str] | None = None,
    not_materialized: Mapping[str, str] | None = None,
    execution_coverage: Mapping[str, Any] | None = None,
    futures_context_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    series_keys: list[str] = []
    manifest_paths: list[str] = []
    for item in symbol_series:
        optional_execution_series = tuple(
            series
            for series in (item.order_book, item.trades)
            if series is not None
        )
        for series in (
            item.ohlcv,
            *tuple(series for series in (item.funding, item.mark_price, item.open_interest) if series is not None),
            *item.intraday_ohlcv.values(),
            *optional_execution_series,
        ):
            series_keys.append(series.series_key)
            manifest_paths.extend(str(imported_file.manifest_path) for imported_file in series.files)
    return {
        "scope": PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": sorted(item.symbol for item in symbol_series),
        "series_keys": sorted(series_keys),
        "manifest_paths": sorted(manifest_paths),
        "ohlcv_timeframes": _ohlcv_timeframe_coverage(
            symbol_series,
            materialized_timeframes=materialized_timeframes or ("1h",),
            not_materialized=not_materialized,
        ),
        "execution_evidence": dict(
            execution_coverage
            if execution_coverage is not None
            else _execution_coverage_template(available=False, max_staleness=PHASE1_IMPORTER_DEFAULT_EXECUTION_EVIDENCE_MAX_STALENESS)
        ),
        "futures_context": dict(
            futures_context_coverage
            if futures_context_coverage is not None
            else _context_coverage_template(
                available=False,
                mark_price_max_age=PHASE1_IMPORTER_DEFAULT_MARK_PRICE_MAX_AGE,
                funding_max_age=PHASE1_IMPORTER_DEFAULT_FUNDING_MAX_AGE,
                open_interest_max_age=PHASE1_IMPORTER_DEFAULT_OPEN_INTEREST_MAX_AGE,
            )
        ),
    }


def _require_canonical_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a string")
    if value != value.strip():
        raise ValueError(f"{field} must be canonical")
    return value


def _require_canonical_string_items(values: Any, *, field: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a list")
    parsed: list[str] = []
    for index, value in enumerate(values):
        parsed.append(_require_canonical_string(value, field=f"{field}[{index}]"))
    return tuple(parsed)


def _merged_import_trace(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scope: str | None = None
    exchange: str | None = None
    market: str | None = None
    symbols: set[str] = set()
    series_keys: set[str] = set()
    manifest_paths: set[str] = set()

    for trace in traces:
        normalized = dict(trace)
        current_scope = _require_canonical_string(normalized.get("scope", ""), field="import_trace.scope")
        current_exchange = _require_canonical_string(normalized.get("exchange", ""), field="import_trace.exchange")
        current_market = _require_canonical_string(normalized.get("market", ""), field="import_trace.market")
        if scope is None:
            scope = current_scope
            exchange = current_exchange
            market = current_market
        elif (current_scope, current_exchange, current_market) != (scope, exchange, market):
            raise ValueError(
                "phase1 importer source trace scope/exchange/market must stay aligned across bundles: "
                f"expected {(scope, exchange, market)}, loaded {(current_scope, current_exchange, current_market)}"
            )
        symbols.update(_require_canonical_string_items(normalized.get("symbols"), field="import_trace.symbols"))
        series_keys.update(_require_canonical_string_items(normalized.get("series_keys"), field="import_trace.series_keys"))
        manifest_paths.update(_require_canonical_string_items(normalized.get("manifest_paths"), field="import_trace.manifest_paths"))

    if scope is None or exchange is None or market is None:
        return {}
    return {
        "scope": scope,
        "exchange": exchange,
        "market": market,
        "symbols": sorted(symbols),
        "series_keys": sorted(series_keys),
        "manifest_paths": sorted(manifest_paths),
    }


def _merged_ohlcv_timeframe_coverage(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    available: set[str] = set()
    materialized: set[str] = set()
    not_materialized: dict[str, str] = {}
    for trace in traces:
        coverage = trace.get("ohlcv_timeframes")
        if not isinstance(coverage, Mapping):
            continue
        available.update(_require_canonical_string_items(coverage.get("available"), field="ohlcv_timeframes.available"))
        materialized.update(_require_canonical_string_items(coverage.get("materialized"), field="ohlcv_timeframes.materialized"))
        raw_not_materialized = coverage.get("not_materialized") or {}
        if isinstance(raw_not_materialized, Mapping):
            for timeframe, reason in raw_not_materialized.items():
                timeframe_key = _require_canonical_string(timeframe, field="ohlcv_timeframes.not_materialized key")
                not_materialized[timeframe_key] = _require_canonical_string(
                    reason, field=f"ohlcv_timeframes.not_materialized.{timeframe_key}"
                )
    missing_optional = [
        timeframe
        for timeframe in PHASE1_IMPORTER_OPTIONAL_INTRADAY_OHLCV_TIMEFRAMES
        if timeframe not in available
    ]
    return {
        "available": _ordered_timeframes(available),
        "materialized": _ordered_timeframes(materialized),
        "missing_optional": list(missing_optional),
        "not_materialized": dict(sorted(not_materialized.items())),
    }


def _require_bool_field(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _require_non_negative_int_field(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _merged_execution_evidence_coverage(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    merged = _execution_coverage_template(
        available=False,
        max_staleness=PHASE1_IMPORTER_DEFAULT_EXECUTION_EVIDENCE_MAX_STALENESS,
    )
    max_staleness_values: set[int] = set()
    for trace in traces:
        coverage = trace.get("execution_evidence")
        if not isinstance(coverage, Mapping):
            continue
        merged["available"] = bool(merged["available"]) or _require_bool_field(
            coverage.get("available", False), field="execution_evidence.available"
        )
        max_staleness_values.add(
            _require_non_negative_int_field(
                coverage.get("max_staleness_seconds", 0), field="execution_evidence.max_staleness_seconds"
            )
        )
        for bucket in ("materialized", "missing", "stale", "ambiguous"):
            raw_counts = coverage.get(bucket) or {}
            if not isinstance(raw_counts, Mapping):
                continue
            for evidence_type in ("order_book", "trades"):
                increment = _require_non_negative_int_field(
                    raw_counts.get(evidence_type, 0),
                    field=f"execution_evidence.{bucket}.{evidence_type}",
                )
                merged[bucket][evidence_type] = int(merged[bucket].get(evidence_type, 0)) + increment
    if len(max_staleness_values) == 1:
        merged["max_staleness_seconds"] = next(iter(max_staleness_values))
    return merged


def _merged_futures_context_coverage(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    merged = _context_coverage_template(
        available=False,
        mark_price_max_age=PHASE1_IMPORTER_DEFAULT_MARK_PRICE_MAX_AGE,
        funding_max_age=PHASE1_IMPORTER_DEFAULT_FUNDING_MAX_AGE,
        open_interest_max_age=PHASE1_IMPORTER_DEFAULT_OPEN_INTEREST_MAX_AGE,
    )
    max_age_values: dict[str, set[int]] = {"mark_price": set(), "funding": set(), "open_interest": set()}
    for trace in traces:
        coverage = trace.get("futures_context")
        if not isinstance(coverage, Mapping):
            continue
        merged["available"] = bool(merged["available"]) or _require_bool_field(
            coverage.get("available", False), field="futures_context.available"
        )
        raw_max_age = coverage.get("max_age_seconds") or {}
        if isinstance(raw_max_age, Mapping):
            for evidence_type in max_age_values:
                max_age_values[evidence_type].add(
                    _require_non_negative_int_field(
                        raw_max_age.get(evidence_type, 0), field=f"futures_context.max_age_seconds.{evidence_type}"
                    )
                )
        for bucket in ("materialized", "missing", "stale"):
            raw_counts = coverage.get(bucket) or {}
            if not isinstance(raw_counts, Mapping):
                continue
            for evidence_type in ("mark_price", "funding", "open_interest"):
                increment = _require_non_negative_int_field(
                    raw_counts.get(evidence_type, 0),
                    field=f"futures_context.{bucket}.{evidence_type}",
                )
                merged[bucket][evidence_type] = int(merged[bucket].get(evidence_type, 0)) + increment
    for evidence_type, values in max_age_values.items():
        if len(values) == 1:
            merged["max_age_seconds"][evidence_type] = next(iter(values))
    return merged


def build_phase1_dataset_bundle_materials(
    imported_series: Iterable[ImportedRawMarketSeries],
    *,
    start_timestamp: datetime | None = None,
    end_timestamp: datetime | None = None,
    execution_evidence_max_staleness: timedelta = PHASE1_IMPORTER_DEFAULT_EXECUTION_EVIDENCE_MAX_STALENESS,
    mark_price_max_age: timedelta = PHASE1_IMPORTER_DEFAULT_MARK_PRICE_MAX_AGE,
    funding_max_age: timedelta = PHASE1_IMPORTER_DEFAULT_FUNDING_MAX_AGE,
    open_interest_max_age: timedelta = PHASE1_IMPORTER_DEFAULT_OPEN_INTEREST_MAX_AGE,
) -> tuple[Phase1DatasetBundleMaterial, ...]:
    symbol_series = _phase1_symbol_series(imported_series)
    if not symbol_series:
        return ()

    hourly_ohlcv_indexes = {
        item.symbol: _OhlcvSeriesIndex.from_records(item.ohlcv.records, expected_gap=timedelta(hours=1))
        for item in symbol_series
    }
    ohlcv_timestamp_sets = {symbol: index.timestamp_set for symbol, index in hourly_ohlcv_indexes.items()}
    all_timestamps = sorted(
        timestamp
        for timestamp in {
            timestamp
            for timestamps in ohlcv_timestamp_sets.values()
            for timestamp in timestamps
        }
        if (start_timestamp is None or timestamp >= start_timestamp)
        and (end_timestamp is None or timestamp < end_timestamp)
    )
    materials: list[Phase1DatasetBundleMaterial] = []

    funding_lookups = {
        item.symbol: _record_lookup(item.funding.records)
        for item in symbol_series
        if item.funding is not None
    }
    mark_price_lookups = {
        item.symbol: _record_lookup(item.mark_price.records)
        for item in symbol_series
        if item.mark_price is not None
    }
    open_interest_lookups = {
        item.symbol: _record_lookup(item.open_interest.records)
        for item in symbol_series
        if item.open_interest is not None
    }
    derived_ohlcv_histories = {
        item.symbol: {
            4: _resampled_history_by_hourly_timestamp(hourly_ohlcv_indexes[item.symbol], hours=4),
            24: _resampled_history_by_hourly_timestamp(hourly_ohlcv_indexes[item.symbol], hours=24),
        }
        for item in symbol_series
    }
    hourly_payloads = {
        item.symbol: _timeframe_payloads_by_timestamp(hourly_ohlcv_indexes[item.symbol], timeframe="1h")
        for item in symbol_series
    }
    intraday_ohlcv_indexes = {
        (item.symbol, timeframe): _OhlcvSeriesIndex.from_records(
            series.records,
            expected_gap=_PHASE1_IMPORTER_INTRADAY_GAPS[timeframe],
        )
        for item in symbol_series
        for timeframe, series in item.intraday_ohlcv.items()
    }
    intraday_payloads = {
        (item.symbol, timeframe): _timeframe_payloads_by_timestamp(index, timeframe=timeframe)
        for item in symbol_series
        for timeframe in item.intraday_ohlcv
        for index in (intraday_ohlcv_indexes[(item.symbol, timeframe)],)
    }
    execution_evidence_available = any(item.order_book is not None or item.trades is not None for item in symbol_series)
    execution_evidence_lookups: dict[tuple[str, str], tuple[list[datetime], list[ImportedRawMarketRecord]]] = {}
    for item in symbol_series:
        if item.order_book is not None:
            execution_evidence_lookups[(item.symbol, "order_book")] = _record_lookup(item.order_book.records)
        if item.trades is not None:
            execution_evidence_lookups[(item.symbol, "trades")] = _record_lookup(item.trades.records)

    for timestamp in all_timestamps:
        market_symbols: dict[str, Any] = {}
        derivatives_rows: list[dict[str, Any]] = []
        instrument_rows: list[dict[str, Any]] = []
        eligible_symbol_series: list[_Phase1SymbolSeries] = []
        materialized_ohlcv_timeframes = {"1h"}
        not_materialized_ohlcv_timeframes: dict[str, str] = {}
        execution_coverage = _execution_coverage_template(
            available=execution_evidence_available,
            max_staleness=execution_evidence_max_staleness,
        )
        context_coverage = _context_coverage_template(
            available=any(item.funding is not None or item.mark_price is not None or item.open_interest is not None for item in symbol_series),
            mark_price_max_age=mark_price_max_age,
            funding_max_age=funding_max_age,
            open_interest_max_age=open_interest_max_age,
        )

        for item in symbol_series:
            if timestamp not in ohlcv_timestamp_sets[item.symbol]:
                continue
            hourly_index = hourly_ohlcv_indexes[item.symbol]
            hourly_bars = hourly_index.contiguous_history_up_to(timestamp)
            if len(hourly_bars) < 24:
                continue
            daily_bars = list(derived_ohlcv_histories[item.symbol][24].get(timestamp, ()))
            four_hour_bars = list(derived_ohlcv_histories[item.symbol][4].get(timestamp, ()))
            hourly_payload = hourly_payloads[item.symbol].get(timestamp)
            if len(daily_bars) < 50 or len(four_hour_bars) < 50 or hourly_payload is None:
                continue

            latest_close = hourly_bars[-1].close
            volume_usdt_24h = _rolling_quote_volume(hourly_bars)
            liquidity_tier = _liquidity_tier(volume_usdt_24h)
            futures_context: dict[str, Any] = {}
            derivatives_row: dict[str, Any] = {
                "symbol": item.symbol,
                "open_interest_change_24h_pct": 0.0,
            }

            mark_record: ImportedRawMarketRecord | None = None
            mark_age: int | None = None
            mark_status = "missing"
            if item.mark_price is None:
                _increment_context_coverage(context_coverage, "missing", "mark_price")
            else:
                mark_times, mark_records = mark_price_lookups[item.symbol]
                mark_record, mark_status, mark_age = _latest_fresh_record_at_or_before(
                    mark_times,
                    mark_records,
                    timestamp,
                    max_age=mark_price_max_age,
                    coverage=context_coverage,
                    evidence_type="mark_price",
                )
            futures_context["mark_price_status"] = mark_status
            if mark_record is not None:
                mark_value = _mark_price(mark_record)
                if mark_value > 0.0:
                    futures_context.update(
                        {
                            "mark_price": mark_value,
                            "mark_price_timestamp": _utc_timestamp(mark_record.observed_at),
                            "mark_price_age_seconds": mark_age,
                        }
                    )
                    derivatives_row.update(
                        {
                            "mark_price": mark_value,
                            "mark_price_timestamp": _utc_timestamp(mark_record.observed_at),
                            "mark_price_age_seconds": mark_age,
                        }
                    )

            funding_record: ImportedRawMarketRecord | None = None
            funding_age: int | None = None
            funding_status = "missing"
            if item.funding is None:
                _increment_context_coverage(context_coverage, "missing", "funding")
            else:
                funding_times, funding_records = funding_lookups[item.symbol]
                funding_record, funding_status, funding_age = _latest_fresh_record_at_or_before(
                    funding_times,
                    funding_records,
                    timestamp,
                    max_age=funding_max_age,
                    coverage=context_coverage,
                    evidence_type="funding",
                )
            futures_context["funding_status"] = funding_status
            if funding_record is not None:
                funding_value = _funding_rate(funding_record)
                futures_context.update(
                    {
                        "funding_rate": funding_value,
                        "funding_timestamp": _utc_timestamp(funding_record.observed_at),
                        "funding_age_seconds": funding_age,
                    }
                )
                derivatives_row.update(
                    {
                        "funding_rate": funding_value,
                        "funding_timestamp": _utc_timestamp(funding_record.observed_at),
                        "funding_age_seconds": funding_age,
                    }
                )

            current_open_interest: ImportedRawMarketRecord | None = None
            oi_age: int | None = None
            oi_status = "missing"
            previous_open_interest: ImportedRawMarketRecord | None = None
            if item.open_interest is None:
                _increment_context_coverage(context_coverage, "missing", "open_interest")
            else:
                open_interest_times, open_interest_records = open_interest_lookups[item.symbol]
                current_open_interest, oi_status, oi_age = _latest_fresh_record_at_or_before(
                    open_interest_times,
                    open_interest_records,
                    timestamp,
                    max_age=open_interest_max_age,
                    coverage=context_coverage,
                    evidence_type="open_interest",
                )
                previous_open_interest = _latest_record_at_or_before(
                    open_interest_times,
                    open_interest_records,
                    timestamp - timedelta(hours=24),
                )
            futures_context["open_interest_status"] = oi_status
            if current_open_interest is not None:
                current_open_interest_units = _open_interest_units(current_open_interest)
                open_interest_usdt = (
                    current_open_interest_units
                    if _open_interest_is_quote_value(current_open_interest)
                    else current_open_interest_units * latest_close
                )
                futures_context.update(
                    {
                        "open_interest_usdt": open_interest_usdt,
                        "open_interest_timestamp": _utc_timestamp(current_open_interest.observed_at),
                        "open_interest_age_seconds": oi_age,
                    }
                )
                derivatives_row.update(
                    {
                        "open_interest_usdt": open_interest_usdt,
                        "open_interest_timestamp": _utc_timestamp(current_open_interest.observed_at),
                        "open_interest_age_seconds": oi_age,
                    }
                )
                if previous_open_interest is not None:
                    previous_open_interest_units = _open_interest_units(previous_open_interest)
                    if previous_open_interest_units > 0.0:
                        derivatives_row["open_interest_change_24h_pct"] = (
                            current_open_interest_units / previous_open_interest_units
                        ) - 1.0
            timeframe_payloads: dict[str, Any] = {
                "daily": _derived_timeframe_payload(daily_bars, source_hourly_bars=hourly_bars, timeframe="daily"),
                "4h": _derived_timeframe_payload(four_hour_bars, source_hourly_bars=hourly_bars, timeframe="4h"),
                "1h": hourly_payload,
            }
            for timeframe, series in item.intraday_ohlcv.items():
                intraday_index = intraday_ohlcv_indexes[(item.symbol, timeframe)]
                intraday_bars = intraday_index.contiguous_history_up_to(timestamp)
                timeframe_payload = intraday_payloads[(item.symbol, timeframe)].get(timestamp)
                if len(intraday_bars) >= 50 and timeframe_payload is not None:
                    timeframe_payload = dict(timeframe_payload)
                    next_bar = _next_ohlcv_bar_payload(
                        intraday_index.timestamps,
                        intraday_index.bars,
                        timestamp=timestamp,
                    )
                    if next_bar is not None:
                        timeframe_payload["next_bar"] = next_bar
                    timeframe_payloads[timeframe] = timeframe_payload
                    materialized_ohlcv_timeframes.add(timeframe)
                else:
                    not_materialized_ohlcv_timeframes[timeframe] = "missing_contiguous_bars"
            symbol_payload = {
                "sector": sector_for_symbol(item.symbol),
                "liquidity_tier": liquidity_tier,
                **timeframe_payloads,
                "futures_context": futures_context,
            }
            execution_payload = _execution_payload_for_symbol(
                item=item,
                timestamp=timestamp,
                evidence_lookups=execution_evidence_lookups,
                max_staleness=execution_evidence_max_staleness,
                coverage=execution_coverage,
            )
            if execution_payload is not None:
                symbol_payload["execution"] = execution_payload
            market_symbols[item.symbol] = symbol_payload
            derivatives_row.update(
                {
                    "mark_price_change_24h_pct": _return_pct(hourly_bars, periods_back=24),
                    "taker_buy_sell_ratio": 1.0,
                    "basis_bps": 0.0,
                }
            )
            derivatives_rows.append(derivatives_row)
            instrument_rows.append(
                {
                    "symbol": item.symbol,
                    "market_type": "futures",
                    "base_asset": _base_asset(item.symbol),
                    "listing_timestamp": _utc_timestamp(
                        _symbol_metadata_timestamp(
                            symbol_metadata=item.symbol_metadata,
                            fallback_series=item.ohlcv,
                        )
                    ),
                    "quote_volume_usdt_24h": volume_usdt_24h,
                    "liquidity_tier": liquidity_tier,
                    "quantity_step": _symbol_metadata_float(
                        symbol_metadata=item.symbol_metadata,
                        field="quantity_step",
                        default=_PHASE1_DEFAULT_QUANTITY_STEP,
                    ),
                    "price_tick": _symbol_metadata_float(
                        symbol_metadata=item.symbol_metadata,
                        field="price_tick",
                        default=_PHASE1_DEFAULT_PRICE_TICK,
                    ),
                    "has_complete_funding": _has_complete_funding_series(
                        item.funding.records if item.funding is not None else (),
                        start=hourly_bars[0].observed_at,
                        end=timestamp,
                    ),
                }
            )
            eligible_symbol_series.append(item)

        if not eligible_symbol_series:
            continue

        timestamp_iso = _utc_timestamp(timestamp)
        run_id = _run_id(timestamp)
        metadata = {
            "timestamp": timestamp_iso,
            "run_id": run_id,
            "schema_version": PHASE1_IMPORTER_BUNDLE_SCHEMA,
            "source": _import_trace(
                eligible_symbol_series,
                materialized_timeframes=materialized_ohlcv_timeframes,
                not_materialized=not_materialized_ohlcv_timeframes,
                execution_coverage=execution_coverage,
                futures_context_coverage=context_coverage,
            ),
        }
        materials.append(
            Phase1DatasetBundleMaterial(
                timestamp=timestamp,
                run_id=run_id,
                metadata=metadata,
                market_context={
                    "as_of": timestamp_iso,
                    "schema_version": PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA,
                    "symbols": market_symbols,
                    "instrument_rows": sorted(instrument_rows, key=lambda row: str(row["symbol"])),
                },
                derivatives_snapshot={
                    "as_of": timestamp_iso,
                    "schema_version": PHASE1_IMPORTER_DERIVATIVES_SCHEMA,
                    "rows": sorted(derivatives_rows, key=lambda row: str(row["symbol"])),
                },
                account_snapshot={
                    "as_of": timestamp_iso,
                    "schema_version": PHASE1_IMPORTER_ACCOUNT_SCHEMA,
                    "equity": 100_000.0,
                    "available_balance": 100_000.0,
                    "positions": [],
                    "meta": {
                        "account_type": "imported_baseline",
                        "source": PHASE1_IMPORTER_SCOPE,
                    },
                },
            )
        )

    return tuple(materials)


def _instrument_snapshot_payload(*, as_of: str, instrument_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "schema_version": PHASE1_IMPORTER_INSTRUMENT_SNAPSHOT_SCHEMA,
        "rows": [dict(row) for row in instrument_rows],
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"phase1 importer JSON file must contain an object: {path}")
    return payload


def _phase1_dataset_root_manifest_path(dataset_root: str | Path) -> Path:
    return Path(dataset_root) / PHASE1_IMPORTER_ROOT_MANIFEST


def _phase1_dataset_root_manifest(
    *,
    archive_root: Path,
    dataset_root: Path,
    symbols: Sequence[str],
    materials: Sequence[Phase1DatasetBundleMaterial],
    bundle_dirs: Sequence[Path],
) -> dict[str, Any]:
    bundle_timestamps = [_utc_timestamp(material.timestamp) for material in materials]
    data_quality_report = build_raw_market_data_quality_report(
        archive_root,
        expected_intervals={
            "ohlcv:1h": timedelta(hours=1),
            "ohlcv:30m": timedelta(minutes=30),
            "ohlcv:15m": timedelta(minutes=15),
            "ohlcv:5m": timedelta(minutes=5),
            "ohlcv:1m": timedelta(minutes=1),
            "order-book": timedelta(seconds=1),
            "trades": timedelta(milliseconds=1),
        },
    )
    return {
        "schema_version": PHASE1_IMPORTER_ROOT_SCHEMA,
        "scope": PHASE1_IMPORTER_SCOPE,
        "archive_root": str(archive_root),
        "dataset_root": str(dataset_root),
        "snapshot_count": len(bundle_dirs),
        "symbols": list(symbols),
        "start_timestamp": bundle_timestamps[0],
        "end_timestamp": bundle_timestamps[-1],
        "bundle_dirs": [str(bundle_dir) for bundle_dir in bundle_dirs],
        "bundle_timestamps": bundle_timestamps,
        "source": _merged_import_trace(material.metadata.get("source") or {} for material in materials),
        "data_quality_report": data_quality_report,
        "coverage": {
            "ohlcv_timeframes": _merged_ohlcv_timeframe_coverage(
                material.metadata.get("source") or {} for material in materials
            ),
            "execution_evidence": _merged_execution_evidence_coverage(
                material.metadata.get("source") or {} for material in materials
            ),
            "futures_context": _merged_futures_context_coverage(
                material.metadata.get("source") or {} for material in materials
            ),
        },
    }


def _phase1_dataset_root_summary_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_count": _phase1_root_manifest_nonnegative_int(payload, "snapshot_count", manifest_path=Path("<phase1 dataset root summary>")),
        "symbols": list(_canonical_string_sequence(payload.get("symbols") or [], field="symbols")),
        "archive_root": _phase1_root_manifest_canonical_string(payload, "archive_root", manifest_path=Path("<phase1 dataset root summary>"))
        if payload.get("archive_root") is not None
        else None,
        "bundle_dirs": list(_canonical_string_sequence(payload.get("bundle_dirs") or [], field="bundle_dirs")),
        "bundle_timestamps": list(_canonical_string_sequence(payload.get("bundle_timestamps") or [], field="bundle_timestamps")),
        "start_timestamp": _phase1_root_manifest_canonical_string(
            payload, "start_timestamp", manifest_path=Path("<phase1 dataset root summary>")
        )
        if payload.get("start_timestamp") is not None
        else None,
        "end_timestamp": _phase1_root_manifest_canonical_string(payload, "end_timestamp", manifest_path=Path("<phase1 dataset root summary>"))
        if payload.get("end_timestamp") is not None
        else None,
        "source": _json_object_field(payload.get("source") or {}, context="phase1 dataset root summary source"),
    }


def _resolved_phase1_imported_dataset_root_path(dataset_path: Path, value: str | Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    base_dir = _phase1_imported_dataset_root_relative_base_dir(dataset_path)
    if base_dir is None:
        return path
    return base_dir / path


def _json_object_field(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must contain a JSON object")
    return dict(value)


def _materialized_dataset_row_source(rows: Sequence[Any]) -> dict[str, Any]:
    return _merged_import_trace(
        _json_object_field(row.meta.get("source") or {}, context="materialized dataset bundle metadata source")
        for row in rows
    )


def _archive_root_from_manifest_paths(manifest_paths: Sequence[str]) -> Path | None:
    if not manifest_paths:
        return None

    archive_root: Path | None = None
    for value in manifest_paths:
        manifest_path = Path(str(value))
        raw_market_root = manifest_path.parent
        while raw_market_root.name != "raw-market" and raw_market_root != raw_market_root.parent:
            raw_market_root = raw_market_root.parent
        if raw_market_root.name != "raw-market":
            raise ValueError(
                "materialized dataset root source manifest_paths must stay under raw-market: "
                f"{manifest_path}"
            )

        current_archive_root = raw_market_root.parent
        if archive_root is None:
            archive_root = current_archive_root
            continue
        if current_archive_root != archive_root:
            raise ValueError(
                "materialized dataset root source manifest_paths did not share one archive_root: "
                f"expected {archive_root}, loaded {current_archive_root}"
            )

    return archive_root


def _validated_source_trace_against_manifests(source: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    normalized_source = _json_object_field(source, context=context)
    manifest_paths = sorted(str(value) for value in normalized_source.get("manifest_paths") or ())
    if not manifest_paths:
        raise ValueError(f"{context} manifest_paths must not be empty")

    referenced_files = tuple(load_phase1_raw_market_manifest(manifest_path) for manifest_path in manifest_paths)
    referenced_source = {
        "scope": PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": sorted({str(imported_file.manifest.get("symbol") or "") for imported_file in referenced_files}),
        "series_keys": sorted({imported_file.series_key for imported_file in referenced_files}),
        "manifest_paths": sorted(str(imported_file.manifest_path) for imported_file in referenced_files),
    }
    if normalized_source != referenced_source:
        raise ValueError(
            "materialized dataset root source trace did not match referenced raw-market manifests: "
            f"expected {normalized_source}, loaded {referenced_source}"
        )

    return normalized_source


def _validate_bundle_payloads(bundle_dir: Path, *, expected_timestamp: datetime) -> None:
    expected_bundle_name = f"{_bundle_fragment(expected_timestamp)}__{_run_id(expected_timestamp)}"
    if bundle_dir.name != expected_bundle_name:
        raise ValueError(
            "materialized dataset bundle directory name did not round-trip: "
            f"expected {expected_bundle_name}, loaded {bundle_dir.name}"
        )

    metadata = _read_json_object(bundle_dir / "metadata.json")
    loaded_schema_version = str(metadata.get("schema_version") or "")
    if loaded_schema_version != PHASE1_IMPORTER_BUNDLE_SCHEMA:
        raise ValueError(
            "materialized dataset bundle metadata schema_version is out of phase1 importer scope: "
            f"expected {PHASE1_IMPORTER_BUNDLE_SCHEMA}, loaded {loaded_schema_version}"
        )
    expected_run_id = _run_id(expected_timestamp)
    loaded_run_id = str(metadata.get("run_id") or "")
    if loaded_run_id != expected_run_id:
        raise ValueError(
            "materialized dataset bundle metadata run_id did not round-trip: "
            f"expected {expected_run_id}, loaded {loaded_run_id}"
        )

    expected_as_of = _utc_timestamp(expected_timestamp)
    loaded_payloads: dict[str, dict[str, Any]] = {}
    for file_name, expected_schema in (
        ("market_context.json", PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA),
        ("derivatives_snapshot.json", PHASE1_IMPORTER_DERIVATIVES_SCHEMA),
        ("account_snapshot.json", PHASE1_IMPORTER_ACCOUNT_SCHEMA),
        ("instrument_snapshot.json", PHASE1_IMPORTER_INSTRUMENT_SNAPSHOT_SCHEMA),
    ):
        payload = _read_json_object(bundle_dir / file_name)
        loaded_payloads[file_name] = payload
        loaded_schema = str(payload.get("schema_version") or "")
        if loaded_schema != expected_schema:
            raise ValueError(
                "materialized dataset bundle payload schema_version is out of phase1 importer scope: "
                f"expected {expected_schema}, loaded {loaded_schema}"
            )
        loaded_as_of = str(payload.get("as_of") or "")
        if loaded_as_of != expected_as_of:
            raise ValueError(
                "materialized dataset bundle payload as_of did not round-trip: "
                f"expected {expected_as_of}, loaded {loaded_as_of}"
            )

    market_context_rows = loaded_payloads["market_context.json"].get("instrument_rows")
    if market_context_rows is not None:
        if not isinstance(market_context_rows, list):
            raise ValueError(
                f"materialized dataset bundle market_context instrument_rows must be a JSON array: {bundle_dir / 'market_context.json'}"
            )
        instrument_snapshot_rows = loaded_payloads["instrument_snapshot.json"].get("rows")
        if not isinstance(instrument_snapshot_rows, list):
            raise ValueError(
                f"materialized dataset bundle instrument_snapshot rows must be a JSON array: {bundle_dir / 'instrument_snapshot.json'}"
            )
        normalized_market_context_rows = sorted(
            (_json_object_field(row, context="materialized dataset bundle market_context instrument_rows item") for row in market_context_rows),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        normalized_instrument_snapshot_rows = sorted(
            (_json_object_field(row, context="materialized dataset bundle instrument_snapshot rows item") for row in instrument_snapshot_rows),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        if normalized_market_context_rows != normalized_instrument_snapshot_rows:
            raise ValueError(
                "materialized dataset bundle instrument rows drifted between market_context.json and instrument_snapshot.json: "
                f"{bundle_dir}"
            )


def write_phase1_dataset_root_manifest(
    archive_root: str | Path,
    dataset_root: str | Path,
    *,
    symbols: Sequence[str],
    materials: Sequence[Phase1DatasetBundleMaterial],
    bundle_dirs: Sequence[Path],
) -> Path:
    manifest_path = _phase1_dataset_root_manifest_path(dataset_root)
    _write_json(
        manifest_path,
        _phase1_dataset_root_manifest(
            archive_root=Path(archive_root),
            dataset_root=Path(dataset_root),
            symbols=symbols,
            materials=materials,
            bundle_dirs=bundle_dirs,
        ),
    )
    return manifest_path


def write_phase1_dataset_bundle(material: Phase1DatasetBundleMaterial, dataset_root: str | Path) -> Path:
    root = Path(dataset_root)
    bundle_dir = root / f"{_bundle_fragment(material.timestamp)}__{material.run_id}"
    bundle_dir.mkdir(parents=True, exist_ok=False)
    _write_json(bundle_dir / "metadata.json", material.metadata)
    _write_json(bundle_dir / "market_context.json", material.market_context)
    _write_json(bundle_dir / "derivatives_snapshot.json", material.derivatives_snapshot)
    _write_json(bundle_dir / "account_snapshot.json", material.account_snapshot)
    _write_json(
        bundle_dir / "instrument_snapshot.json",
        _instrument_snapshot_payload(
            as_of=str(material.market_context["as_of"]),
            instrument_rows=tuple(dict(row) for row in material.market_context.get("instrument_rows") or ()),
        ),
    )
    return bundle_dir


def inspect_phase1_imported_dataset_root(dataset_root: str | Path) -> dict[str, dict[str, Any] | None]:
    dataset_path = Path(dataset_root)
    rows = load_historical_dataset(dataset_path)
    loaded_source = _normalized_phase1_source_trace(
        dataset_path,
        _materialized_dataset_row_source(rows),
        context="materialized dataset bundle metadata source",
    )
    loaded_archive_root = _archive_root_from_manifest_paths(loaded_source.get("manifest_paths") or ())
    row_summary = {
        "snapshot_count": len(rows),
        "symbols": sorted(
            {
                str(symbol)
                for row in rows
                for symbol in dict(row.market.get("symbols") or {}).keys()
            }
        ),
        "archive_root": str(loaded_archive_root) if loaded_archive_root is not None else None,
        "bundle_dirs": [str(row.source_path) for row in rows],
        "bundle_timestamps": [_utc_timestamp(row.timestamp) for row in rows],
        "start_timestamp": _utc_timestamp(rows[0].timestamp) if rows else None,
        "end_timestamp": _utc_timestamp(rows[-1].timestamp) if rows else None,
        "source": loaded_source,
    }

    manifest_path = _phase1_dataset_root_manifest_path(dataset_path)
    manifest_summary = _phase1_dataset_root_summary_fields(_read_json_object(manifest_path)) if manifest_path.exists() else None
    if manifest_summary is not None:
        if manifest_summary.get("archive_root"):
            manifest_summary["archive_root"] = str(
                _resolved_phase1_imported_dataset_root_path(dataset_path, manifest_summary["archive_root"])
            )
        manifest_summary["bundle_dirs"] = [
            str(_resolved_phase1_imported_dataset_root_path(dataset_path, value))
            for value in manifest_summary.get("bundle_dirs") or ()
        ]
        manifest_summary["source"] = _normalized_phase1_source_trace(
            dataset_path,
            manifest_summary.get("source") or {},
            context="phase1 dataset root summary source",
        )
    return {
        "manifest": manifest_summary,
        "rows": row_summary,
    }


def _canonical_string_sequence(values: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    parsed: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{field} entries must be canonical strings")
        parsed.append(value)
    return tuple(parsed)


def _phase1_root_manifest_canonical_string(manifest: Mapping[str, Any], field: str, *, manifest_path: Path) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"materialized dataset root manifest {field} must be a canonical string: {manifest_path}")
    return value


def _phase1_root_manifest_nonnegative_int(manifest: Mapping[str, Any], field: str, *, manifest_path: Path) -> int:
    value = manifest.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"materialized dataset root manifest {field} must be a non-negative integer: {manifest_path}")
    return value


def _phase1_root_manifest_canonical_strings(manifest: Mapping[str, Any], field: str, *, manifest_path: Path) -> tuple[str, ...]:
    try:
        return _canonical_string_sequence(manifest.get(field), field=f"materialized dataset root manifest {field}")
    except ValueError as exc:
        raise ValueError(f"{exc}: {manifest_path}") from exc


def validate_phase1_imported_dataset_root(
    dataset_root: str | Path,
    *,
    expected_bundle_dirs: Sequence[Path] | None = None,
    expected_timestamps: Sequence[datetime] | None = None,
) -> list[Any]:
    dataset_path = Path(dataset_root)
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset root does not exist: {dataset_path}")
    if not dataset_path.is_dir():
        raise NotADirectoryError(f"dataset root is not a directory: {dataset_path}")

    manifest_path = _phase1_dataset_root_manifest_path(dataset_path)
    root_manifest = _read_json_object(manifest_path) if manifest_path.exists() else None
    explicit_bundle_dirs = tuple(Path(bundle_dir) for bundle_dir in expected_bundle_dirs) if expected_bundle_dirs is not None else None
    explicit_timestamps = tuple(expected_timestamps) if expected_timestamps is not None else None
    if root_manifest is not None:
        schema_version = _phase1_root_manifest_canonical_string(root_manifest, "schema_version", manifest_path=manifest_path)
        if schema_version != PHASE1_IMPORTER_ROOT_SCHEMA:
            raise ValueError(f"unsupported phase1 dataset root manifest schema: {manifest_path}")
        manifest_dataset_root = _resolved_phase1_imported_dataset_root_path(
            dataset_path,
            _phase1_root_manifest_canonical_string(root_manifest, "dataset_root", manifest_path=manifest_path),
        )
        if manifest_dataset_root.resolve() != dataset_path.resolve():
            raise ValueError(
                "materialized dataset root manifest dataset_root mismatch: "
                f"expected {dataset_path}, loaded {manifest_dataset_root}"
            )
    if explicit_bundle_dirs is None and explicit_timestamps is None and root_manifest is None:
        raise ValueError("expected bundle directories/timestamps or root manifest are required for validation")

    explicit_row_count: int | None = None
    if explicit_bundle_dirs is not None:
        explicit_row_count = len(explicit_bundle_dirs)
    if explicit_timestamps is not None:
        if explicit_row_count is None:
            explicit_row_count = len(explicit_timestamps)
        elif len(explicit_timestamps) != explicit_row_count:
            raise ValueError(
                "materialized dataset root explicit expectations must share one row count: "
                f"bundle_dirs={explicit_row_count}, timestamps={len(explicit_timestamps)}"
            )

    rows = load_historical_dataset(dataset_path)
    if explicit_row_count is not None and len(rows) != explicit_row_count:
        raise ValueError(
            "materialized dataset root failed validation: "
            f"expected {explicit_row_count} rows, loaded {len(rows)}"
        )

    loaded_bundle_dirs = tuple(row.source_path for row in rows)
    if explicit_bundle_dirs is not None and loaded_bundle_dirs != explicit_bundle_dirs:
        raise ValueError(
            "materialized dataset root bundle directories did not round-trip: "
            f"expected {explicit_bundle_dirs}, loaded {loaded_bundle_dirs}"
        )

    loaded_timestamps = tuple(row.timestamp for row in rows)
    if explicit_timestamps is not None and loaded_timestamps != explicit_timestamps:
        raise ValueError(
            "materialized dataset root timestamps did not round-trip: "
            f"expected {explicit_timestamps}, loaded {loaded_timestamps}"
        )
    payload_timestamps = explicit_timestamps if explicit_timestamps is not None else loaded_timestamps
    for bundle_dir, timestamp in zip(loaded_bundle_dirs, payload_timestamps, strict=False):
        _validate_bundle_payloads(bundle_dir, expected_timestamp=timestamp)

    if root_manifest is not None:
        loaded_source = _validated_source_trace_against_manifests(
            _normalized_phase1_source_trace(
                dataset_path,
                _materialized_dataset_row_source(rows),
                context="materialized dataset bundle metadata source",
            ),
            context="materialized dataset bundle metadata source",
        )
        manifest_scope = _phase1_root_manifest_canonical_string(root_manifest, "scope", manifest_path=manifest_path)
        if manifest_scope != PHASE1_IMPORTER_SCOPE:
            raise ValueError(
                "materialized dataset root manifest scope is out of phase1 importer scope: "
                f"expected {PHASE1_IMPORTER_SCOPE}, loaded {manifest_scope}"
            )
        manifest_snapshot_count = _phase1_root_manifest_nonnegative_int(
            root_manifest,
            "snapshot_count",
            manifest_path=manifest_path,
        )
        if manifest_snapshot_count != len(rows):
            raise ValueError(
                "materialized dataset root manifest snapshot_count did not round-trip: "
                f"expected {manifest_snapshot_count}, loaded {len(rows)}"
            )
        manifest_symbols = _phase1_root_manifest_canonical_strings(
            root_manifest,
            "symbols",
            manifest_path=manifest_path,
        )
        loaded_symbols = tuple(
            sorted(
                {
                    str(symbol)
                    for row in rows
                    for symbol in dict(row.market.get("symbols") or {}).keys()
                }
            )
        )
        if manifest_symbols != loaded_symbols:
            raise ValueError(
                "materialized dataset root manifest symbols did not round-trip: "
                f"expected {manifest_symbols}, loaded {loaded_symbols}"
            )
        manifest_archive_root = _resolved_phase1_imported_dataset_root_path(
            dataset_path,
            _phase1_root_manifest_canonical_string(root_manifest, "archive_root", manifest_path=manifest_path),
        )
        loaded_archive_root = _archive_root_from_manifest_paths(loaded_source.get("manifest_paths") or ())
        if loaded_archive_root is not None and manifest_archive_root.resolve() != loaded_archive_root.resolve():
            raise ValueError(
                "materialized dataset root manifest archive_root did not round-trip: "
                f"expected {manifest_archive_root}, loaded {loaded_archive_root}"
            )
        manifest_source = _normalized_phase1_source_trace(
            dataset_path,
            root_manifest.get("source") or {},
            context="materialized dataset root manifest source",
        )
        if manifest_source != loaded_source:
            raise ValueError(
                "materialized dataset root manifest source did not round-trip: "
                f"expected {manifest_source}, loaded {loaded_source}"
            )
        manifest_bundle_dirs = tuple(
            _resolved_phase1_imported_dataset_root_path(dataset_path, value)
            for value in _phase1_root_manifest_canonical_strings(
                root_manifest,
                "bundle_dirs",
                manifest_path=manifest_path,
            )
        )
        if tuple(path.resolve() for path in loaded_bundle_dirs) != tuple(path.resolve() for path in manifest_bundle_dirs):
            raise ValueError(
                "materialized dataset root manifest bundle_dirs did not round-trip: "
                f"expected {manifest_bundle_dirs}, loaded {loaded_bundle_dirs}"
            )
        manifest_timestamps = tuple(
            _utc_datetime(value)
            for value in _phase1_root_manifest_canonical_strings(
                root_manifest,
                "bundle_timestamps",
                manifest_path=manifest_path,
            )
        )
        if loaded_timestamps != manifest_timestamps:
            raise ValueError(
                "materialized dataset root manifest bundle_timestamps did not round-trip: "
                f"expected {manifest_timestamps}, loaded {loaded_timestamps}"
            )
        start_timestamp = _phase1_root_manifest_canonical_string(
            root_manifest,
            "start_timestamp",
            manifest_path=manifest_path,
        )
        if start_timestamp != _utc_timestamp(rows[0].timestamp):
            raise ValueError(
                "materialized dataset root manifest start_timestamp did not round-trip: "
                f"expected {_utc_timestamp(rows[0].timestamp)}, loaded {start_timestamp}"
            )
        end_timestamp = _phase1_root_manifest_canonical_string(
            root_manifest,
            "end_timestamp",
            manifest_path=manifest_path,
        )
        if end_timestamp != _utc_timestamp(rows[-1].timestamp):
            raise ValueError(
                "materialized dataset root manifest end_timestamp did not round-trip: "
                f"expected {_utc_timestamp(rows[-1].timestamp)}, loaded {end_timestamp}"
            )

    return rows


def _phase1_imported_dataset_root_relative_base_dir(dataset_path: Path) -> Path | None:
    manifest_path = _phase1_dataset_root_manifest_path(dataset_path)
    if not manifest_path.exists():
        return None
    manifest = _read_json_object(manifest_path)
    dataset_root_value = _phase1_root_manifest_canonical_string(
        manifest,
        "dataset_root",
        manifest_path=manifest_path,
    )
    recorded_dataset_root = Path(dataset_root_value)
    if recorded_dataset_root.is_absolute():
        return None

    resolved_dataset_path = dataset_path.resolve()
    recorded_parts = recorded_dataset_root.parts
    if len(recorded_parts) > len(resolved_dataset_path.parts):
        return None
    if tuple(resolved_dataset_path.parts[-len(recorded_parts) :]) != tuple(recorded_parts):
        return None

    base_dir = resolved_dataset_path
    for _ in recorded_parts:
        base_dir = base_dir.parent
    return base_dir


def _resolved_source_manifest_paths(dataset_path: Path, manifest_paths: Sequence[str]) -> tuple[str, ...]:
    base_dir = _phase1_imported_dataset_root_relative_base_dir(dataset_path)
    resolved_paths: list[str] = []
    for value in manifest_paths:
        path = Path(value)
        if path.is_absolute():
            resolved_paths.append(str(path))
            continue
        if base_dir is None:
            raise ValueError(
                "relative source manifest_paths require a resolvable dataset_root base dir: "
                f"{dataset_path} -> {path}"
            )
        resolved_paths.append(str(base_dir / path))
    return tuple(sorted(set(resolved_paths)))


def _phase1_imported_dataset_root_manifest_paths(dataset_path: Path, rows: Sequence[Any]) -> tuple[str, ...]:
    loaded_source = _materialized_dataset_row_source(rows)
    manifest_paths = tuple(sorted(str(value) for value in loaded_source.get("manifest_paths") or ()))
    if not manifest_paths:
        raise ValueError("phase1 imported dataset root does not declare source manifest_paths")
    return _resolved_source_manifest_paths(dataset_path, manifest_paths)


def _normalized_phase1_source_trace(dataset_path: Path, source: Mapping[str, Any], *, context: str) -> dict[str, Any]:
    normalized = _json_object_field(source, context=context)
    field = f"{context} manifest_paths"
    manifest_paths = tuple(sorted(_canonical_string_sequence(normalized.get("manifest_paths"), field=field)))
    normalized["manifest_paths"] = list(_resolved_source_manifest_paths(dataset_path, manifest_paths))
    return normalized


def supplement_phase1_imported_dataset_root_instrument_snapshots(
    dataset_root: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    dataset_path = Path(dataset_root)
    rows = load_historical_dataset(dataset_path)
    if not rows:
        return ()

    manifest_paths = _phase1_imported_dataset_root_manifest_paths(dataset_path, rows)
    imported_series = load_phase1_raw_market_imports_from_manifest_paths(manifest_paths)
    if not imported_series:
        raise FileNotFoundError(
            "phase1 imported dataset root source manifest_paths did not resolve any raw-market imports: "
            f"{dataset_path}"
        )
    materials = build_phase1_dataset_bundle_materials(imported_series)
    materials_by_timestamp = {material.timestamp: material for material in materials}

    written_paths: list[Path] = []
    for row in rows:
        material = materials_by_timestamp.get(row.timestamp)
        if material is None:
            raise ValueError(
                "phase1 imported dataset root timestamp is missing from archive-derived materials: "
                f"{_utc_timestamp(row.timestamp)}"
            )
        expected_source = _normalized_phase1_source_trace(
            dataset_path,
            material.metadata.get("source") or {},
            context="archive-derived phase1 bundle metadata source",
        )
        loaded_source = _normalized_phase1_source_trace(
            dataset_path,
            row.meta.get("source") or {},
            context="loaded phase1 bundle metadata source",
        )
        if loaded_source != expected_source:
            raise ValueError(
                "phase1 imported dataset root source trace did not match archive-derived materials: "
                f"expected {expected_source}, loaded {loaded_source}"
            )
        bundle_dir = row.source_path
        if bundle_dir is None:
            raise ValueError(f"phase1 imported dataset row is missing source_path: {row.run_id}")
        instrument_snapshot_path = bundle_dir / "instrument_snapshot.json"
        if instrument_snapshot_path.exists() and not overwrite:
            continue
        _write_json(
            instrument_snapshot_path,
            _instrument_snapshot_payload(
                as_of=str(material.market_context["as_of"]),
                instrument_rows=tuple(dict(item) for item in material.market_context.get("instrument_rows") or ()),
            ),
        )
        written_paths.append(instrument_snapshot_path)

    reloaded_rows = load_historical_dataset(dataset_path)
    if any(not row.instrument_rows for row in reloaded_rows):
        raise ValueError(f"phase1 imported dataset root still has bundles without instrument rows after supplement: {dataset_path}")
    return tuple(written_paths)


def import_phase1_archive_dataset_root(
    archive_root: str | Path,
    dataset_root: str | Path,
) -> ImportedPhase1DatasetRoot:
    archive_path = Path(archive_root)
    dataset_path = Path(dataset_root)
    imported_series = load_phase1_raw_market_imports(archive_path)
    if not imported_series:
        raise FileNotFoundError(f"no phase1 raw-market imports found under: {archive_path}")

    materials = build_phase1_dataset_bundle_materials(imported_series)
    if not materials:
        raise ValueError("phase1 raw-market imports did not yield any eligible dataset bundles")
    symbols = tuple(
        sorted(
            {
                str(symbol)
                for material in materials
                for symbol in dict(material.market_context.get("symbols") or {}).keys()
            }
        )
    )

    dataset_preexisted = dataset_path.exists()
    if dataset_path.exists():
        if not dataset_path.is_dir():
            raise NotADirectoryError(f"dataset root is not a directory: {dataset_path}")
        if any(dataset_path.iterdir()):
            raise FileExistsError(f"dataset root must be empty before materialization: {dataset_path}")
    else:
        dataset_path.mkdir(parents=True, exist_ok=True)

    try:
        bundle_dirs = tuple(write_phase1_dataset_bundle(material, dataset_path) for material in materials)
        write_phase1_dataset_root_manifest(
            archive_path,
            dataset_path,
            symbols=symbols,
            materials=materials,
            bundle_dirs=bundle_dirs,
        )
        rows = validate_phase1_imported_dataset_root(
            dataset_path,
            expected_bundle_dirs=bundle_dirs,
            expected_timestamps=tuple(material.timestamp for material in materials),
        )
    except Exception:
        if dataset_path.exists():
            shutil.rmtree(dataset_path)
            if dataset_preexisted:
                dataset_path.mkdir(parents=True, exist_ok=True)
        raise

    return ImportedPhase1DatasetRoot(
        archive_root=archive_path,
        dataset_root=dataset_path,
        bundle_dirs=bundle_dirs,
        snapshot_count=len(rows),
        symbols=symbols,
        start_timestamp=rows[0].timestamp,
        end_timestamp=rows[-1].timestamp,
    )


__all__ = [
    "ImportedPhase1DatasetRoot",
    "Phase1DatasetBundleMaterial",
    "build_phase1_dataset_bundle_materials",
    "import_phase1_archive_dataset_root",
    "inspect_phase1_imported_dataset_root",
    "supplement_phase1_imported_dataset_root_instrument_snapshots",
    "validate_phase1_imported_dataset_root",
    "write_phase1_dataset_bundle",
    "write_phase1_dataset_root_manifest",
]
