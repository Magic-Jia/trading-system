from __future__ import annotations

import json
import shutil
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trading_system.app.universe.sector_map import sector_for_symbol

from ..dataset import load_historical_dataset
from .raw_market import (
    ImportedRawMarketRecord,
    ImportedRawMarketSeries,
    load_phase1_raw_market_imports,
    load_phase1_raw_market_manifest,
)

PHASE1_IMPORTER_SCOPE = "phase1_binance_futures"
PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA = "imported_market_context.v1"
PHASE1_IMPORTER_DERIVATIVES_SCHEMA = "imported_derivatives_snapshot.v1"
PHASE1_IMPORTER_ACCOUNT_SCHEMA = "imported_account_snapshot.v1"
PHASE1_IMPORTER_BUNDLE_SCHEMA = "phase1_import_bundle.v1"
PHASE1_IMPORTER_ROOT_SCHEMA = "phase1_imported_dataset_root.v1"
PHASE1_IMPORTER_OHLCV_TIMEFRAME = "1h"
PHASE1_IMPORTER_ROOT_MANIFEST = "import_manifest.json"
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
class _Phase1SymbolSeries:
    symbol: str
    ohlcv: ImportedRawMarketSeries
    funding: ImportedRawMarketSeries
    open_interest: ImportedRawMarketSeries
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


def _hourly_ohlcv_bar(record: ImportedRawMarketRecord) -> _OhlcvBar:
    payload = record.payload
    if isinstance(payload, Mapping):
        close = _to_float(payload.get("close"))
        open_value = _to_float(payload.get("open"), default=close)
        high = _to_float(payload.get("high"), default=max(open_value, close))
        low = _to_float(payload.get("low"), default=min(open_value, close))
        base_volume = _to_float(payload.get("volume"))
        quote_volume = _to_float(payload.get("quote_asset_volume"), default=close * base_volume)
    elif isinstance(payload, (list, tuple)):
        if len(payload) < 6:
            raise ValueError(f"ohlcv array payload must match Binance kline layout: {record.observed_at}")
        close = _to_float(payload[4])
        open_value = _to_float(payload[1], default=close)
        high = _to_float(payload[2], default=max(open_value, close))
        low = _to_float(payload[3], default=min(open_value, close))
        base_volume = _to_float(payload[5])
        quote_volume = _to_float(payload[7], default=close * base_volume) if len(payload) > 7 else close * base_volume
    else:
        raise ValueError(f"ohlcv record payload must be a JSON object: {record.observed_at}")
    if close <= 0.0:
        raise ValueError(f"ohlcv close must be positive: {record.observed_at}")
    return _OhlcvBar(
        observed_at=record.observed_at,
        open=open_value or close,
        high=max(high, open_value, close),
        low=min(low, open_value, close),
        close=close,
        base_volume=base_volume,
        quote_volume=quote_volume,
    )


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
        open_interest = indexed.get((symbol, "open-interest", None))
        if ohlcv is None:
            missing.append("ohlcv:1h")
        if funding is None:
            missing.append("funding")
        if open_interest is None:
            missing.append("open-interest")
        if missing:
            missing_suffix = ", ".join(missing)
            raise ValueError(f"missing required phase1 raw-market series for symbol {symbol}: {missing_suffix}")
        assembled.append(
            _Phase1SymbolSeries(
                symbol=symbol,
                ohlcv=ohlcv,
                funding=funding,
                open_interest=open_interest,
                symbol_metadata=_resolved_symbol_metadata(
                    symbol=symbol,
                    series_items=(ohlcv, funding, open_interest),
                ),
            )
        )
    return tuple(assembled)


def _record_lookup(records: Sequence[ImportedRawMarketRecord]) -> tuple[list[datetime], list[ImportedRawMarketRecord]]:
    ordered = sorted(records, key=lambda record: record.observed_at)
    return [record.observed_at for record in ordered], ordered


def _latest_record_at_or_before(
    timestamps: Sequence[datetime],
    records: Sequence[ImportedRawMarketRecord],
    target: datetime,
) -> ImportedRawMarketRecord | None:
    index = bisect_right(timestamps, target) - 1
    if index < 0:
        return None
    return records[index]


def _hourly_history_up_to(series: ImportedRawMarketSeries, *, timestamp: datetime) -> list[_OhlcvBar]:
    history = [_hourly_ohlcv_bar(record) for record in series.records if record.observed_at <= timestamp]
    return sorted(history, key=lambda row: row.observed_at)


def _timeframe_payload(hourly_bars: Sequence[_OhlcvBar], *, timeframe: str) -> dict[str, float]:
    if timeframe == "1h":
        bars = list(hourly_bars)
        periods_back = 24
    elif timeframe == "4h":
        bars = _resample_bars(hourly_bars, hours=4)
        periods_back = 18
    elif timeframe == "daily":
        bars = _resample_bars(hourly_bars, hours=24)
        periods_back = 7
    else:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    closes = [bar.close for bar in bars]
    current = bars[-1]
    volume_usdt_24h = _rolling_quote_volume(hourly_bars)
    return {
        "close": current.close,
        "ema_20": _ema(closes, period=20),
        "ema_50": _ema(closes, period=50),
        "rsi": _rsi(closes, period=14),
        "atr_pct": _atr_pct(bars, period=14),
        "volume_usdt_24h": volume_usdt_24h,
        "return_pct_24h" if timeframe == "1h" else "return_pct_3d" if timeframe == "4h" else "return_pct_7d": _return_pct(
            bars,
            periods_back=periods_back,
        ),
    }


def _open_interest_units(record: ImportedRawMarketRecord) -> float:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return 0.0
    if "sumOpenInterestValue" in payload:
        return _to_float(payload.get("sumOpenInterestValue"))
    if "openInterestUsd" in payload:
        return _to_float(payload.get("openInterestUsd"))
    return _to_float(payload.get("sumOpenInterest"))


def _open_interest_is_quote_value(record: ImportedRawMarketRecord) -> bool:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return False
    return "sumOpenInterestValue" in payload or "openInterestUsd" in payload


def _funding_rate(record: ImportedRawMarketRecord) -> float:
    payload = record.payload
    if not isinstance(payload, Mapping):
        return 0.0
    return _to_float(payload.get("fundingRate"))


def _import_trace(symbol_series: Sequence[_Phase1SymbolSeries]) -> dict[str, Any]:
    series_keys: list[str] = []
    manifest_paths: list[str] = []
    for item in symbol_series:
        for series in (item.funding, item.ohlcv, item.open_interest):
            series_keys.append(series.series_key)
            manifest_paths.extend(str(imported_file.manifest_path) for imported_file in series.files)
    return {
        "scope": PHASE1_IMPORTER_SCOPE,
        "exchange": "binance",
        "market": "futures",
        "symbols": sorted(item.symbol for item in symbol_series),
        "series_keys": sorted(series_keys),
        "manifest_paths": sorted(manifest_paths),
    }


def _merged_import_trace(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    scope: str | None = None
    exchange: str | None = None
    market: str | None = None
    symbols: set[str] = set()
    series_keys: set[str] = set()
    manifest_paths: set[str] = set()

    for trace in traces:
        normalized = dict(trace)
        current_scope = str(normalized.get("scope") or "")
        current_exchange = str(normalized.get("exchange") or "")
        current_market = str(normalized.get("market") or "")
        if scope is None:
            scope = current_scope
            exchange = current_exchange
            market = current_market
        elif (current_scope, current_exchange, current_market) != (scope, exchange, market):
            raise ValueError(
                "phase1 importer source trace scope/exchange/market must stay aligned across bundles: "
                f"expected {(scope, exchange, market)}, loaded {(current_scope, current_exchange, current_market)}"
            )
        symbols.update(str(value) for value in normalized.get("symbols") or ())
        series_keys.update(str(value) for value in normalized.get("series_keys") or ())
        manifest_paths.update(str(value) for value in normalized.get("manifest_paths") or ())

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


def build_phase1_dataset_bundle_materials(
    imported_series: Iterable[ImportedRawMarketSeries],
) -> tuple[Phase1DatasetBundleMaterial, ...]:
    symbol_series = _phase1_symbol_series(imported_series)
    if not symbol_series:
        return ()

    ohlcv_timestamp_sets = {
        item.symbol: {record.observed_at for record in item.ohlcv.records}
        for item in symbol_series
    }
    all_timestamps = sorted(
        {
            timestamp
            for timestamps in ohlcv_timestamp_sets.values()
            for timestamp in timestamps
        }
    )
    materials: list[Phase1DatasetBundleMaterial] = []

    funding_lookups = {
        item.symbol: _record_lookup(item.funding.records)
        for item in symbol_series
    }
    open_interest_lookups = {
        item.symbol: _record_lookup(item.open_interest.records)
        for item in symbol_series
    }

    for timestamp in all_timestamps:
        market_symbols: dict[str, Any] = {}
        derivatives_rows: list[dict[str, Any]] = []
        instrument_rows: list[dict[str, Any]] = []
        eligible_symbol_series: list[_Phase1SymbolSeries] = []

        for item in symbol_series:
            if timestamp not in ohlcv_timestamp_sets[item.symbol]:
                continue
            hourly_bars = _hourly_history_up_to(item.ohlcv, timestamp=timestamp)
            if len(hourly_bars) < 24:
                continue
            daily_bars = _resample_bars(hourly_bars, hours=24)
            four_hour_bars = _resample_bars(hourly_bars, hours=4)
            if len(daily_bars) < 50 or len(four_hour_bars) < 50 or len(hourly_bars) < 50:
                continue

            funding_times, funding_records = funding_lookups[item.symbol]
            current_funding = _latest_record_at_or_before(funding_times, funding_records, timestamp)
            open_interest_times, open_interest_records = open_interest_lookups[item.symbol]
            current_open_interest = _latest_record_at_or_before(open_interest_times, open_interest_records, timestamp)
            previous_open_interest = _latest_record_at_or_before(
                open_interest_times,
                open_interest_records,
                timestamp - timedelta(hours=24),
            )
            if current_funding is None or current_open_interest is None or previous_open_interest is None:
                continue

            latest_close = hourly_bars[-1].close
            current_open_interest_units = _open_interest_units(current_open_interest)
            previous_open_interest_units = _open_interest_units(previous_open_interest)
            if previous_open_interest_units <= 0.0:
                continue

            volume_usdt_24h = _rolling_quote_volume(hourly_bars)
            liquidity_tier = _liquidity_tier(volume_usdt_24h)
            market_symbols[item.symbol] = {
                "sector": sector_for_symbol(item.symbol),
                "liquidity_tier": liquidity_tier,
                "daily": _timeframe_payload(hourly_bars, timeframe="daily"),
                "4h": _timeframe_payload(hourly_bars, timeframe="4h"),
                "1h": _timeframe_payload(hourly_bars, timeframe="1h"),
            }
            derivatives_rows.append(
                {
                    "symbol": item.symbol,
                    "funding_rate": _funding_rate(current_funding),
                    "open_interest_usdt": (
                        current_open_interest_units
                        if _open_interest_is_quote_value(current_open_interest)
                        else current_open_interest_units * latest_close
                    ),
                    "open_interest_change_24h_pct": (current_open_interest_units / previous_open_interest_units) - 1.0,
                    "mark_price_change_24h_pct": _return_pct(hourly_bars, periods_back=24),
                    "taker_buy_sell_ratio": 1.0,
                    "basis_bps": 0.0,
                }
            )
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
                        item.funding.records,
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
            "source": _import_trace(eligible_symbol_series),
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
    }


def _phase1_dataset_root_summary_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_count": int(payload.get("snapshot_count") or 0),
        "symbols": [str(value) for value in payload.get("symbols") or ()],
        "archive_root": str(payload.get("archive_root") or "") or None,
        "bundle_dirs": [str(value) for value in payload.get("bundle_dirs") or ()],
        "bundle_timestamps": [str(value) for value in payload.get("bundle_timestamps") or ()],
        "start_timestamp": str(payload.get("start_timestamp") or "") or None,
        "end_timestamp": str(payload.get("end_timestamp") or "") or None,
        "source": _json_object_field(payload.get("source") or {}, context="phase1 dataset root summary source"),
    }


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
    for file_name, expected_schema in (
        ("market_context.json", PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA),
        ("derivatives_snapshot.json", PHASE1_IMPORTER_DERIVATIVES_SCHEMA),
        ("account_snapshot.json", PHASE1_IMPORTER_ACCOUNT_SCHEMA),
    ):
        payload = _read_json_object(bundle_dir / file_name)
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
    return bundle_dir


def inspect_phase1_imported_dataset_root(dataset_root: str | Path) -> dict[str, dict[str, Any] | None]:
    dataset_path = Path(dataset_root)
    rows = load_historical_dataset(dataset_path)
    loaded_source = _materialized_dataset_row_source(rows)
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
    return {
        "manifest": manifest_summary,
        "rows": row_summary,
    }


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
        schema_version = str(root_manifest.get("schema_version") or "")
        if schema_version != PHASE1_IMPORTER_ROOT_SCHEMA:
            raise ValueError(f"unsupported phase1 dataset root manifest schema: {manifest_path}")
        manifest_dataset_root = Path(str(root_manifest.get("dataset_root") or ""))
        if manifest_dataset_root != dataset_path:
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
            _materialized_dataset_row_source(rows),
            context="materialized dataset bundle metadata source",
        )
        manifest_scope = str(root_manifest.get("scope") or "")
        if manifest_scope != PHASE1_IMPORTER_SCOPE:
            raise ValueError(
                "materialized dataset root manifest scope is out of phase1 importer scope: "
                f"expected {PHASE1_IMPORTER_SCOPE}, loaded {manifest_scope}"
            )
        manifest_snapshot_count = root_manifest.get("snapshot_count")
        if manifest_snapshot_count != len(rows):
            raise ValueError(
                "materialized dataset root manifest snapshot_count did not round-trip: "
                f"expected {manifest_snapshot_count}, loaded {len(rows)}"
            )
        manifest_symbols = tuple(str(value) for value in root_manifest.get("symbols") or ())
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
        manifest_archive_root = Path(str(root_manifest.get("archive_root") or ""))
        loaded_archive_root = _archive_root_from_manifest_paths(loaded_source.get("manifest_paths") or ())
        if loaded_archive_root is not None and manifest_archive_root != loaded_archive_root:
            raise ValueError(
                "materialized dataset root manifest archive_root did not round-trip: "
                f"expected {manifest_archive_root}, loaded {loaded_archive_root}"
            )
        manifest_source = _json_object_field(root_manifest.get("source") or {}, context="materialized dataset root manifest source")
        if manifest_source != loaded_source:
            raise ValueError(
                "materialized dataset root manifest source did not round-trip: "
                f"expected {manifest_source}, loaded {loaded_source}"
            )
        manifest_bundle_dirs = tuple(Path(str(value)) for value in root_manifest.get("bundle_dirs") or ())
        if loaded_bundle_dirs != manifest_bundle_dirs:
            raise ValueError(
                "materialized dataset root manifest bundle_dirs did not round-trip: "
                f"expected {manifest_bundle_dirs}, loaded {loaded_bundle_dirs}"
            )
        manifest_timestamps = tuple(_utc_datetime(str(value)) for value in root_manifest.get("bundle_timestamps") or ())
        if loaded_timestamps != manifest_timestamps:
            raise ValueError(
                "materialized dataset root manifest bundle_timestamps did not round-trip: "
                f"expected {manifest_timestamps}, loaded {loaded_timestamps}"
            )
        if root_manifest.get("start_timestamp") != _utc_timestamp(rows[0].timestamp):
            raise ValueError(
                "materialized dataset root manifest start_timestamp did not round-trip: "
                f"expected {_utc_timestamp(rows[0].timestamp)}, loaded {root_manifest.get('start_timestamp')}"
            )
        if root_manifest.get("end_timestamp") != _utc_timestamp(rows[-1].timestamp):
            raise ValueError(
                "materialized dataset root manifest end_timestamp did not round-trip: "
                f"expected {_utc_timestamp(rows[-1].timestamp)}, loaded {root_manifest.get('end_timestamp')}"
            )

    return rows


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
    "validate_phase1_imported_dataset_root",
    "write_phase1_dataset_bundle",
    "write_phase1_dataset_root_manifest",
]
