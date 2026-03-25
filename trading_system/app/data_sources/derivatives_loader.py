from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_DERIVATIVES_SNAPSHOT_FILE = Path(__file__).resolve().parents[2] / "data" / "derivatives_snapshot.json"
DERIVATIVES_SNAPSHOT_FILE_ENV = "TRADING_DERIVATIVES_SNAPSHOT_FILE"

_REQUIRED_ROOT_KEYS = ("as_of", "schema_version", "rows")
_REQUIRED_ROW_KEYS = (
    "symbol",
    "funding_rate",
    "open_interest_usdt",
    "open_interest_change_24h_pct",
    "mark_price_change_24h_pct",
    "taker_buy_sell_ratio",
    "basis_bps",
)


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    env_value = os.environ.get(DERIVATIVES_SNAPSHOT_FILE_ENV)
    if env_value:
        return Path(env_value)
    return DEFAULT_DERIVATIVES_SNAPSHOT_FILE


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], prefix: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"missing required keys in {prefix}: {', '.join(missing)}")


def load_derivatives_snapshot(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = _resolve_path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("derivatives snapshot must be a JSON object")

    _require_keys(raw, _REQUIRED_ROOT_KEYS, "derivatives snapshot root")
    rows = raw["rows"]
    if not isinstance(rows, list):
        raise ValueError("derivatives snapshot rows must be an array")

    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"derivatives snapshot rows[{idx}] must be an object")
        _require_keys(row, _REQUIRED_ROW_KEYS, f"derivatives snapshot rows[{idx}]")
        normalized.append(row)

    return normalized
