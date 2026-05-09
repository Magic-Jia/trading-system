from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths


def _collector_module():
    try:
        return importlib.import_module("trading_system.app.paper_optimization.collector")
    except ModuleNotFoundError as exc:
        pytest.fail(f"paper optimization collector is missing: {exc}")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_collect_signal_facts_appends_minimal_candidate_allocation_execution_fact(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    summary = collector.collect_signal_facts(
        signal_facts_path=paths.signal_facts_file,
        candidate_rows=[
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
                "validation": {"allowed": True, "reasons": [], "metrics": {"risk": 0.01}},
            }
        ],
        allocation_rows=[
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "status": "ACCEPTED",
                "rank": 1,
                "final_risk_budget": 0.01,
                "execution": {"status": "FILLED", "intent_id": "intent-btc"},
            }
        ],
        execution_rows=[
            {
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "intent_id": "intent-btc",
                "qty": 0.02,
            }
        ],
        regime={"label": "RISK_ON_TREND", "confidence": 0.82},
        mode="paper",
        runtime_env="research",
    )

    assert paths.optimization_dir == tmp_path / "runtime" / "paper" / "research" / "optimization"
    assert paths.signal_facts_file == paths.optimization_dir / "signal_facts.jsonl"
    assert summary == {"signal_facts_path": str(paths.signal_facts_file), "appended_count": 1}
    assert _jsonl(paths.signal_facts_file) == [
        {
            "fact_type": "signal",
            "mode": "paper",
            "runtime_env": "research",
            "regime_label": "RISK_ON_TREND",
            "regime_confidence": 0.82,
            "symbol": "BTCUSDT",
            "side": "LONG",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "score": 0.91,
            "stop_loss": 62830.0,
            "invalidation_source": "trend_structure_loss_below_4h_ema50",
            "validation_allowed": True,
            "allocation_status": "ACCEPTED",
            "allocation_rank": 1,
            "final_risk_budget": 0.01,
            "execution_status": "FILLED",
            "intent_id": "intent-btc",
        }
    ]

def test_collect_signal_facts_rejects_non_boolean_validation_allowed(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    with pytest.raises(ValueError, match="validation.allowed must be boolean"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[
                {
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "score": 0.91,
                    "validation": {"allowed": "false"},
                }
            ],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

def test_collect_signal_facts_rejects_invalid_candidate_numeric_fields(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    with pytest.raises(ValueError, match="candidate.score must be numeric"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[
                {
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "score": True,
                    "validation": {"allowed": True},
                }
            ],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

    with pytest.raises(ValueError, match="candidate.score must be numeric"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[
                {
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "score": "0.91",
                    "validation": {"allowed": True},
                }
            ],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

    with pytest.raises(ValueError, match="allocation.rank must be numeric"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[{"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION", "symbol": "BTCUSDT", "side": "LONG"}],
            allocation_rows=[{"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION", "symbol": "BTCUSDT", "rank": "1"}],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

def test_collect_signal_facts_rejects_non_object_candidate_rows(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    with pytest.raises(ValueError, match="candidate rows must be objects"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=["not-an-object"],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

def test_collect_signal_facts_rejects_non_string_intent_ids(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    with pytest.raises(ValueError, match="allocation.execution.intent_id must be a string"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[
                {
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "score": 0.91,
                    "validation": {"allowed": True},
                }
            ],
            allocation_rows=[
                {
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "symbol": "BTCUSDT",
                    "execution": {"intent_id": 123, "status": "FILLED"},
                }
            ],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )


def test_collect_signal_facts_rejects_non_string_identity_fields(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    collector = _collector_module()

    with pytest.raises(ValueError, match="candidate.symbol must be a string"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[{"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION", "symbol": 123, "side": "LONG"}],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

    with pytest.raises(ValueError, match="candidate.engine must be a string"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[{"engine": 123, "setup_type": "BREAKOUT_CONTINUATION", "symbol": "BTCUSDT", "side": "LONG"}],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

    with pytest.raises(ValueError, match="regime.label must be a string"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[{"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION", "symbol": "BTCUSDT", "side": "LONG"}],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": 123, "confidence": 0.82},
            mode="paper",
            runtime_env="research",
        )

    with pytest.raises(ValueError, match="mode must be a string"):
        collector.collect_signal_facts(
            signal_facts_path=paths.signal_facts_file,
            candidate_rows=[{"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION", "symbol": "BTCUSDT", "side": "LONG"}],
            allocation_rows=[],
            execution_rows=[],
            regime={"label": "RISK_ON_TREND", "confidence": 0.82},
            mode=123,
            runtime_env="research",
        )
