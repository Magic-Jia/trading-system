from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trading_system.app.universe.sector_map import sector_for_symbol

from ..dataset import load_historical_dataset
from .raw_market import ImportedRawMarketRecord, ImportedRawMarketSeries, load_phase1_raw_market_imports

PHASE1_IMPORTER_SCOPE = "phase1_binance_futures"
PHASE1_IMPORTER_MARKET_CONTEXT_SCHEMA = "imported_market_context.v1"
PHASE1_IMPORTER_DERIVATIVES_SCHEMA = "imported_derivatives_snapshot.v1"
PHASE1_IMPORTER_ACCOUNT_SCHEMA = "imported_account_snapshot.v1"
PHASE1_IMPORTER_BUNDLE_SCHEMA = "phase1_import_bundle.v1"
PHASE1_IMPORTER_OHLCV_TIMEFRAME = "1h"


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


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    if not isinstance(payload, Mapping):
        raise ValueError(f"ohlcv record payload must be a JSON object: {record.observed_at}")
    close = _to_float(payload.get("close"))
    open_value = _to_float(payload.get("open"), default=close)
    high = _to_float(payload.get("high"), default=max(open_value, close))
    low = _to_float(payload.get("low"), default=min(open_value, close))
    base_volume = _to_float(payload.get("volume"))
    quote_volume = _to_float(payload.get("quote_asset_volume"), default=close * base_volume)
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


def build_phase1_dataset_bundle_materials(
    imported_series: Iterable[ImportedRawMarketSeries],
) -> tuple[Phase1DatasetBundleMaterial, ...]:
    symbol_series = _phase1_symbol_series(imported_series)
    if not symbol_series:
        return ()

    timestamp_sets = [
        {record.observed_at for record in item.ohlcv.records}
        for item in symbol_series
    ]
    common_timestamps = sorted(set.intersection(*timestamp_sets))
    materials: list[Phase1DatasetBundleMaterial] = []
    trace = _import_trace(symbol_series)

    funding_lookups = {
        item.symbol: _record_lookup(item.funding.records)
        for item in symbol_series
    }
    open_interest_lookups = {
        item.symbol: _record_lookup(item.open_interest.records)
        for item in symbol_series
    }

    for timestamp in common_timestamps:
        market_symbols: dict[str, Any] = {}
        derivatives_rows: list[dict[str, Any]] = []
        eligible = True

        for item in symbol_series:
            hourly_bars = _hourly_history_up_to(item.ohlcv, timestamp=timestamp)
            if len(hourly_bars) < 24:
                eligible = False
                break
            daily_bars = _resample_bars(hourly_bars, hours=24)
            four_hour_bars = _resample_bars(hourly_bars, hours=4)
            if len(daily_bars) < 50 or len(four_hour_bars) < 50 or len(hourly_bars) < 50:
                eligible = False
                break

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
                eligible = False
                break

            latest_close = hourly_bars[-1].close
            current_open_interest_units = _open_interest_units(current_open_interest)
            previous_open_interest_units = _open_interest_units(previous_open_interest)
            if previous_open_interest_units <= 0.0:
                eligible = False
                break

            volume_usdt_24h = _rolling_quote_volume(hourly_bars)
            market_symbols[item.symbol] = {
                "sector": sector_for_symbol(item.symbol),
                "liquidity_tier": _liquidity_tier(volume_usdt_24h),
                "daily": _timeframe_payload(hourly_bars, timeframe="daily"),
                "4h": _timeframe_payload(hourly_bars, timeframe="4h"),
                "1h": _timeframe_payload(hourly_bars, timeframe="1h"),
            }
            derivatives_rows.append(
                {
                    "symbol": item.symbol,
                    "funding_rate": _funding_rate(current_funding),
                    "open_interest_usdt": current_open_interest_units * latest_close,
                    "open_interest_change_24h_pct": (current_open_interest_units / previous_open_interest_units) - 1.0,
                    "mark_price_change_24h_pct": _return_pct(hourly_bars, periods_back=24),
                    "taker_buy_sell_ratio": 1.0,
                    "basis_bps": 0.0,
                }
            )

        if not eligible:
            continue

        timestamp_iso = _utc_timestamp(timestamp)
        run_id = _run_id(timestamp)
        metadata = {
            "timestamp": timestamp_iso,
            "run_id": run_id,
            "schema_version": PHASE1_IMPORTER_BUNDLE_SCHEMA,
            "source": dict(trace),
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


def write_phase1_dataset_bundle(material: Phase1DatasetBundleMaterial, dataset_root: str | Path) -> Path:
    root = Path(dataset_root)
    bundle_dir = root / f"{_bundle_fragment(material.timestamp)}__{material.run_id}"
    bundle_dir.mkdir(parents=True, exist_ok=False)
    _write_json(bundle_dir / "metadata.json", material.metadata)
    _write_json(bundle_dir / "market_context.json", material.market_context)
    _write_json(bundle_dir / "derivatives_snapshot.json", material.derivatives_snapshot)
    _write_json(bundle_dir / "account_snapshot.json", material.account_snapshot)
    return bundle_dir


def validate_phase1_imported_dataset_root(
    dataset_root: str | Path,
    *,
    expected_bundle_dirs: Sequence[Path],
    expected_timestamps: Sequence[datetime],
) -> list[Any]:
    dataset_path = Path(dataset_root)
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset root does not exist: {dataset_path}")
    if not dataset_path.is_dir():
        raise NotADirectoryError(f"dataset root is not a directory: {dataset_path}")

    rows = load_historical_dataset(dataset_path)
    if len(rows) != len(expected_bundle_dirs):
        raise ValueError(
            "materialized dataset root failed validation: "
            f"expected {len(expected_bundle_dirs)} rows, loaded {len(rows)}"
        )

    loaded_bundle_dirs = tuple(row.source_path for row in rows)
    expected_bundle_tuple = tuple(Path(bundle_dir) for bundle_dir in expected_bundle_dirs)
    if loaded_bundle_dirs != expected_bundle_tuple:
        raise ValueError(
            "materialized dataset root bundle directories did not round-trip: "
            f"expected {expected_bundle_tuple}, loaded {loaded_bundle_dirs}"
        )

    loaded_timestamps = tuple(row.timestamp for row in rows)
    expected_timestamp_tuple = tuple(expected_timestamps)
    if loaded_timestamps != expected_timestamp_tuple:
        raise ValueError(
            "materialized dataset root timestamps did not round-trip: "
            f"expected {expected_timestamp_tuple}, loaded {loaded_timestamps}"
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

    symbols = tuple(sorted(item.symbol for item in _phase1_symbol_series(imported_series)))
    materials = build_phase1_dataset_bundle_materials(imported_series)
    if not materials:
        raise ValueError("phase1 raw-market imports did not yield any eligible dataset bundles")

    if dataset_path.exists():
        if not dataset_path.is_dir():
            raise NotADirectoryError(f"dataset root is not a directory: {dataset_path}")
        if any(dataset_path.iterdir()):
            raise FileExistsError(f"dataset root must be empty before materialization: {dataset_path}")
    else:
        dataset_path.mkdir(parents=True, exist_ok=True)

    bundle_dirs = tuple(write_phase1_dataset_bundle(material, dataset_path) for material in materials)
    rows = validate_phase1_imported_dataset_root(
        dataset_path,
        expected_bundle_dirs=bundle_dirs,
        expected_timestamps=tuple(material.timestamp for material in materials),
    )

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
    "validate_phase1_imported_dataset_root",
    "write_phase1_dataset_bundle",
]
