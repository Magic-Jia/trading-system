from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MARKET_CONTEXT_FILE = Path(__file__).resolve().parents[2] / "data" / "market_context.json"
MARKET_CONTEXT_FILE_ENV = "TRADING_MARKET_CONTEXT_FILE"

_REQUIRED_ROOT_KEYS = ("as_of", "schema_version", "symbols")
_REQUIRED_SYMBOL_KEYS = ("sector", "liquidity_tier", "daily", "4h", "1h")
_REQUIRED_TF_KEYS = ("close", "ema_20", "ema_50", "rsi", "atr_pct", "volume_usdt_24h")
_TIMEFRAME_RETURNS = {
    "daily": "return_pct_7d",
    "4h": "return_pct_3d",
    "1h": "return_pct_24h",
}


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    env_value = os.environ.get(MARKET_CONTEXT_FILE_ENV)
    if env_value:
        return Path(env_value)
    return DEFAULT_MARKET_CONTEXT_FILE


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], prefix: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"missing required keys in {prefix}: {', '.join(missing)}")


def load_market_context(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = _resolve_path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("market context must be a JSON object")

    _require_keys(raw, _REQUIRED_ROOT_KEYS, "market context root")
    symbols = raw["symbols"]
    if not isinstance(symbols, dict):
        raise ValueError("market context symbols must be an object")

    rows: list[dict[str, Any]] = []
    for symbol in sorted(symbols):
        payload = symbols[symbol]
        if not isinstance(payload, dict):
            raise ValueError(f"market context symbol payload must be an object: {symbol}")
        _require_keys(payload, _REQUIRED_SYMBOL_KEYS, f"market context symbols.{symbol}")

        for timeframe in ("daily", "4h", "1h"):
            tf_payload = payload[timeframe]
            if not isinstance(tf_payload, dict):
                raise ValueError(f"market context symbols.{symbol}.{timeframe} must be an object")
            _require_keys(tf_payload, _REQUIRED_TF_KEYS, f"market context symbols.{symbol}.{timeframe}")
            return_key = _TIMEFRAME_RETURNS[timeframe]
            if return_key not in tf_payload:
                raise ValueError(
                    f"missing required keys in market context symbols.{symbol}.{timeframe}: {return_key}"
                )

        row = {"symbol": symbol, **payload}
        rows.append(row)

    return rows
