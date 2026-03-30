from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from trading_system.app.runtime_paths import RuntimePaths
from trading_system.binance_client import FUTURES_BASE, SPOT_BASE, public_get

PAPER_ACCOUNT_SNAPSHOT_NAME = "account_snapshot.json"
PAPER_MARKET_CONTEXT_NAME = "market_context.json"
PAPER_DERIVATIVES_SNAPSHOT_NAME = "derivatives_snapshot.json"
PAPER_SYMBOLS_ENV = "TRADING_PAPER_SNAPSHOT_SYMBOLS"
PAPER_ACCOUNT_EQUITY_ENV = "TRADING_PAPER_ACCOUNT_EQUITY"

_DEFAULT_PAPER_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
_SYMBOL_METADATA: dict[str, dict[str, str]] = {
    "BTCUSDT": {"sector": "majors", "liquidity_tier": "top"},
    "ETHUSDT": {"sector": "majors", "liquidity_tier": "top"},
    "SOLUSDT": {"sector": "alt_l1", "liquidity_tier": "high"},
    "BNBUSDT": {"sector": "exchange", "liquidity_tier": "high"},
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _paper_symbols() -> tuple[str, ...]:
    raw_value = os.environ.get(PAPER_SYMBOLS_ENV, "")
    if not raw_value.strip():
        return _DEFAULT_PAPER_SYMBOLS

    symbols = tuple(dict.fromkeys(part.strip().upper() for part in raw_value.split(",") if part.strip()))
    if not symbols:
        raise RuntimeError(f"{PAPER_SYMBOLS_ENV} must contain at least one symbol")
    return symbols


def _paper_account_equity() -> float:
    raw_value = os.environ.get(PAPER_ACCOUNT_EQUITY_ENV, "100000")
    try:
        equity = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{PAPER_ACCOUNT_EQUITY_ENV} must be numeric, got: {raw_value}") from exc
    if equity <= 0:
        raise RuntimeError(f"{PAPER_ACCOUNT_EQUITY_ENV} must be greater than zero, got: {raw_value}")
    return equity


def _safe_div(numerator: float, denominator: float) -> float:
    if abs(denominator) <= 1e-12:
        return 0.0
    return numerator / denominator


def _to_float(value: Any, *, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"expected numeric {field}, got: {value!r}") from exc


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise RuntimeError(f"need at least {period} rows to compute EMA, got {len(values)}")
    multiplier = 2.0 / (period + 1)
    ema_value = sum(values[:period]) / period
    for value in values[period:]:
        ema_value = ((value - ema_value) * multiplier) + ema_value
    return ema_value


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise RuntimeError(f"need more than {period} closes to compute RSI, got {len(values)}")
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[:-1], values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period
    if avg_loss <= 1e-12:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr_pct(rows: list[list[Any]], period: int = 14) -> float:
    if len(rows) < period + 1:
        raise RuntimeError(f"need at least {period + 1} rows to compute ATR, got {len(rows)}")
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in rows[-period:]:
        high = _to_float(row[2], field="high")
        low = _to_float(row[3], field="low")
        close = _to_float(row[4], field="close")
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = close
    last_close = _to_float(rows[-1][4], field="close")
    return _safe_div(sum(true_ranges) / len(true_ranges), last_close)


def _pct_return(values: list[float], bars_back: int) -> float:
    if len(values) <= bars_back:
        raise RuntimeError(f"need more than {bars_back} closes to compute return, got {len(values)}")
    return _safe_div(values[-1] - values[-1 - bars_back], values[-1 - bars_back])


def _spot_klines(symbol: str, interval: str, *, limit: int = 60) -> list[list[Any]]:
    rows = public_get(SPOT_BASE, "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"spot klines returned no rows for {symbol} {interval}")
    return rows


def _futures_klines(symbol: str, interval: str, *, limit: int = 24) -> list[list[Any]]:
    rows = public_get(FUTURES_BASE, "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"futures klines returned no rows for {symbol} {interval}")
    return rows


def _timeframe_payload(rows: list[list[Any]], *, return_bars: int, volume_usdt_24h: float) -> dict[str, float]:
    closes = [_to_float(row[4], field="close") for row in rows]
    return {
        "close": round(closes[-1], 8),
        "ema_20": round(_ema(closes, 20), 8),
        "ema_50": round(_ema(closes, 50), 8),
        "rsi": round(_rsi(closes, 14), 4),
        "atr_pct": round(_atr_pct(rows, 14), 6),
        f"return_pct_{'7d' if return_bars == 7 else '3d' if return_bars == 18 else '24h'}": round(
            _pct_return(closes, return_bars), 6
        ),
        "volume_usdt_24h": round(volume_usdt_24h, 4),
    }


def _spot_ticker(symbol: str) -> dict[str, Any]:
    payload = public_get(SPOT_BASE, "/api/v3/ticker/24hr", {"symbol": symbol})
    if not isinstance(payload, dict):
        raise RuntimeError(f"spot ticker returned invalid payload for {symbol}")
    return payload


def _futures_ticker(symbol: str) -> dict[str, Any]:
    payload = public_get(FUTURES_BASE, "/fapi/v1/ticker/24hr", {"symbol": symbol})
    if not isinstance(payload, dict):
        raise RuntimeError(f"futures ticker returned invalid payload for {symbol}")
    return payload


def _futures_premium_index(symbol: str) -> dict[str, Any]:
    payload = public_get(FUTURES_BASE, "/fapi/v1/premiumIndex", {"symbol": symbol})
    if not isinstance(payload, dict):
        raise RuntimeError(f"premium index returned invalid payload for {symbol}")
    return payload


def _open_interest_payload(symbol: str) -> dict[str, Any]:
    payload = public_get(FUTURES_BASE, "/fapi/v1/openInterest", {"symbol": symbol})
    if not isinstance(payload, dict):
        raise RuntimeError(f"open interest returned invalid payload for {symbol}")
    return payload


def _open_interest_change_24h_pct(symbol: str) -> float:
    payload = public_get(FUTURES_BASE, "/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 24})
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"open interest history returned insufficient rows for {symbol}")
    first = payload[0]
    last = payload[-1]
    if not isinstance(first, dict) or not isinstance(last, dict):
        raise RuntimeError(f"open interest history returned invalid rows for {symbol}")
    first_value = _to_float(first.get("sumOpenInterestValue", first.get("sumOpenInterest")), field="sumOpenInterestValue")
    last_value = _to_float(last.get("sumOpenInterestValue", last.get("sumOpenInterest")), field="sumOpenInterestValue")
    return round(_safe_div(last_value - first_value, first_value), 6)


def _taker_buy_sell_ratio(symbol: str) -> float:
    rows = _futures_klines(symbol, "1h", limit=24)
    last_row = rows[-1]
    quote_volume = _to_float(last_row[7], field="quote_volume")
    taker_buy_quote_volume = _to_float(last_row[10], field="taker_buy_quote_volume")
    sell_quote_volume = max(quote_volume - taker_buy_quote_volume, 1e-12)
    return round(taker_buy_quote_volume / sell_quote_volume, 6)


def _market_context_payload(symbols: Iterable[str]) -> dict[str, Any]:
    as_of = _timestamp()
    payload: dict[str, Any] = {"as_of": as_of, "schema_version": "v2", "symbols": {}}
    for symbol in symbols:
        ticker = _spot_ticker(symbol)
        volume_usdt_24h = _to_float(ticker.get("quoteVolume"), field="quoteVolume")
        metadata = _SYMBOL_METADATA.get(symbol, {"sector": "other", "liquidity_tier": "medium"})
        daily_rows = _spot_klines(symbol, "1d", limit=60)
        h4_rows = _spot_klines(symbol, "4h", limit=60)
        h1_rows = _spot_klines(symbol, "1h", limit=60)
        payload["symbols"][symbol] = {
            "sector": metadata["sector"],
            "liquidity_tier": metadata["liquidity_tier"],
            "daily": _timeframe_payload(daily_rows, return_bars=7, volume_usdt_24h=volume_usdt_24h),
            "4h": _timeframe_payload(h4_rows, return_bars=18, volume_usdt_24h=volume_usdt_24h),
            "1h": _timeframe_payload(h1_rows, return_bars=24, volume_usdt_24h=volume_usdt_24h),
        }
    return payload


def _derivatives_snapshot_payload(symbols: Iterable[str]) -> dict[str, Any]:
    as_of = _timestamp()
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        premium_index = _futures_premium_index(symbol)
        futures_ticker = _futures_ticker(symbol)
        open_interest = _open_interest_payload(symbol)
        mark_price = _to_float(premium_index.get("markPrice"), field="markPrice")
        index_price = _to_float(
            premium_index.get("indexPrice", premium_index.get("estimatedSettlePrice", premium_index.get("markPrice"))),
            field="indexPrice",
        )
        funding_rate = _to_float(premium_index.get("lastFundingRate"), field="lastFundingRate")
        open_interest_contracts = _to_float(open_interest.get("openInterest"), field="openInterest")
        rows.append(
            {
                "symbol": symbol,
                "funding_rate": round(funding_rate, 8),
                "open_interest_usdt": round(open_interest_contracts * mark_price, 4),
                "open_interest_change_24h_pct": _open_interest_change_24h_pct(symbol),
                "mark_price_change_24h_pct": round(
                    _safe_div(_to_float(futures_ticker.get("priceChangePercent"), field="priceChangePercent"), 100.0),
                    6,
                ),
                "taker_buy_sell_ratio": _taker_buy_sell_ratio(symbol),
                "basis_bps": round(_safe_div(mark_price - index_price, index_price) * 10000.0, 4),
            }
        )
    return {"as_of": as_of, "schema_version": "v2", "rows": rows}


def _paper_account_snapshot_payload() -> dict[str, Any]:
    as_of = _timestamp()
    equity = round(_paper_account_equity(), 4)
    return {
        "as_of": as_of,
        "schema_version": "v2",
        "equity": equity,
        "available_balance": equity,
        "futures_wallet_balance": equity,
        "open_positions": [],
        "open_orders": [],
        "meta": {
            "account_type": "paper",
            "source": "paper_snapshot_bootstrap",
            "snapshot_source": "paper_snapshot_bootstrap",
            "generated_at": as_of,
        },
    }


def _refresh_snapshot_file(path: Path, *, label: str, builder: Callable[[], dict[str, Any]]) -> None:
    try:
        payload = builder()
    except Exception as exc:
        raise RuntimeError(f"failed to prepare paper {label}: {exc}") from exc
    _json_dump(path, payload)


def prepare_paper_runtime_inputs(paths: RuntimePaths) -> None:
    symbols = _paper_symbols()
    _refresh_snapshot_file(
        paths.bucket_dir / PAPER_ACCOUNT_SNAPSHOT_NAME,
        label=PAPER_ACCOUNT_SNAPSHOT_NAME,
        builder=_paper_account_snapshot_payload,
    )
    _refresh_snapshot_file(
        paths.bucket_dir / PAPER_MARKET_CONTEXT_NAME,
        label=PAPER_MARKET_CONTEXT_NAME,
        builder=lambda: _market_context_payload(symbols),
    )
    _refresh_snapshot_file(
        paths.bucket_dir / PAPER_DERIVATIVES_SNAPSHOT_NAME,
        label=PAPER_DERIVATIVES_SNAPSHOT_NAME,
        builder=lambda: _derivatives_snapshot_payload(symbols),
    )


__all__ = [
    "PAPER_ACCOUNT_SNAPSHOT_NAME",
    "PAPER_DERIVATIVES_SNAPSHOT_NAME",
    "PAPER_MARKET_CONTEXT_NAME",
    "prepare_paper_runtime_inputs",
]
