from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .types import DatasetSnapshotRow, InstrumentSnapshotRow, SampleWindow

_REQUIRED_BUNDLE_FILES = ("metadata.json", "market_context.json", "derivatives_snapshot.json")
_BASELINE_ACCOUNT_FILENAME = "baseline_account_snapshot.json"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _instrument_rows(payload: dict) -> tuple[InstrumentSnapshotRow, ...]:
    raw_rows = payload.get("instrument_rows")
    if raw_rows is None:
        return ()
    if not isinstance(raw_rows, list):
        raise ValueError("dataset bundle has invalid instrument rows payload")
    return tuple(
        InstrumentSnapshotRow(
            symbol=str(row["symbol"]),
            market_type=str(row["market_type"]),  # type: ignore[arg-type]
            base_asset=str(row["base_asset"]),
            listing_timestamp=_parse_timestamp(str(row["listing_timestamp"])),
            quote_volume_usdt_24h=float(row["quote_volume_usdt_24h"]),
            liquidity_tier=str(row["liquidity_tier"]),
            quantity_step=float(row["quantity_step"]),
            price_tick=float(row["price_tick"]),
            has_complete_funding=bool(row["has_complete_funding"]),
        )
        for row in raw_rows
    )


def _bundle_dirs(dataset_root: Path) -> list[Path]:
    return sorted(path for path in dataset_root.iterdir() if path.is_dir())


def _baseline_account(dataset_root: Path) -> dict | None:
    path = dataset_root / _BASELINE_ACCOUNT_FILENAME
    if not path.exists():
        return None
    return _load_json(path)


def _row_from_bundle(bundle_path: Path, *, fallback_account: dict | None) -> DatasetSnapshotRow:
    for filename in _REQUIRED_BUNDLE_FILES:
        file_path = bundle_path / filename
        if not file_path.exists():
            raise FileNotFoundError(f"dataset bundle missing required file: {file_path}")

    metadata = _load_json(bundle_path / "metadata.json")
    market = _load_json(bundle_path / "market_context.json")
    derivatives_payload = _load_json(bundle_path / "derivatives_snapshot.json")
    derivatives = derivatives_payload.get("rows", derivatives_payload)
    if not isinstance(derivatives, list):
        raise ValueError(f"dataset bundle has invalid derivatives rows: {bundle_path / 'derivatives_snapshot.json'}")

    account_path = bundle_path / "account_snapshot.json"
    account = _load_json(account_path) if account_path.exists() else fallback_account
    if account is None:
        raise FileNotFoundError(
            f"dataset bundle missing account snapshot and no baseline provided: {bundle_path / 'account_snapshot.json'}"
        )

    forward_returns = dict(metadata.get("forward_returns") or {})
    forward_drawdowns = dict(metadata.get("forward_drawdowns") or {})
    meta = {
        key: value
        for key, value in metadata.items()
        if key not in {"timestamp", "run_id", "forward_returns", "forward_drawdowns"}
    }
    return DatasetSnapshotRow(
        timestamp=_parse_timestamp(str(metadata["timestamp"])),
        run_id=str(metadata["run_id"]),
        market=market,
        derivatives=[dict(row) for row in derivatives],
        instrument_rows=_instrument_rows(market),
        account=dict(account),
        forward_returns={str(key): float(value) for key, value in forward_returns.items()},
        forward_drawdowns={str(key): float(value) for key, value in forward_drawdowns.items()},
        meta=meta,
        source_path=bundle_path,
    )


def load_historical_dataset(dataset_root: str | Path) -> list[DatasetSnapshotRow]:
    root = Path(dataset_root)
    fallback_account = _baseline_account(root)
    rows = [_row_from_bundle(bundle_path, fallback_account=fallback_account) for bundle_path in _bundle_dirs(root)]
    return sorted(rows, key=lambda row: (row.timestamp, row.run_id))


def _window_rows(rows: Iterable[DatasetSnapshotRow], window: SampleWindow) -> list[DatasetSnapshotRow]:
    return [
        row
        for row in rows
        if window.start <= row.timestamp <= window.end
    ]


def split_rows_by_windows(
    rows: list[DatasetSnapshotRow], windows: tuple[SampleWindow, ...] | list[SampleWindow]
) -> dict[str, list[DatasetSnapshotRow]]:
    return {
        window.name: sorted(_window_rows(rows, window), key=lambda row: (row.timestamp, row.run_id))
        for window in windows
    }
