from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def load_fixture():
    return _load_fixture


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def account_snapshot_v2(load_fixture: Any) -> dict[str, Any]:
    return load_fixture("account_snapshot_v2.json")


@pytest.fixture
def market_context_v2(load_fixture: Any) -> dict[str, Any]:
    return load_fixture("market_context_v2.json")


@pytest.fixture
def derivatives_snapshot_v2(load_fixture: Any) -> dict[str, Any]:
    return load_fixture("derivatives_snapshot_v2.json")


@pytest.fixture
def sample_trend_candidates() -> list[dict[str, Any]]:
    return [
        {
            "engine": "trend",
            "setup_type": "breakout",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "score": 0.88,
            "sector": "majors",
            "timeframe_meta": {"entry_tf": "4h", "confirm_tf": "1h"},
            "liquidity_meta": {"adv_usdt": 18000000000, "spread_bps": 1.2},
        },
        {
            "engine": "trend",
            "setup_type": "pullback",
            "symbol": "ETHUSDT",
            "side": "LONG",
            "score": 0.83,
            "sector": "majors",
            "timeframe_meta": {"entry_tf": "4h", "confirm_tf": "1h"},
            "liquidity_meta": {"adv_usdt": 11000000000, "spread_bps": 1.8},
        },
        {
            "engine": "trend",
            "setup_type": "breakout",
            "symbol": "SOLUSDT",
            "side": "LONG",
            "score": 0.79,
            "sector": "alt_l1",
            "timeframe_meta": {"entry_tf": "4h", "confirm_tf": "1h"},
            "liquidity_meta": {"adv_usdt": 3600000000, "spread_bps": 2.7},
        },
    ]


@pytest.fixture
def sample_rotation_candidates() -> list[dict[str, Any]]:
    return [
        {
            "engine": "rotation",
            "setup_type": "strength_rotation",
            "symbol": "LINKUSDT",
            "side": "LONG",
            "score": 0.74,
            "sector": "oracle",
            "timeframe_meta": {"entry_tf": "1h", "confirm_tf": "4h"},
            "liquidity_meta": {"adv_usdt": 980000000, "spread_bps": 4.9},
        },
        {
            "engine": "rotation",
            "setup_type": "strength_rotation",
            "symbol": "ADAUSDT",
            "side": "LONG",
            "score": 0.69,
            "sector": "alt_l1",
            "timeframe_meta": {"entry_tf": "1h", "confirm_tf": "4h"},
            "liquidity_meta": {"adv_usdt": 870000000, "spread_bps": 5.4},
        },
    ]
