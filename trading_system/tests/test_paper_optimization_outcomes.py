from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths


def _outcomes_module():
    try:
        return importlib.import_module("trading_system.app.paper_optimization.outcomes")
    except ModuleNotFoundError as exc:
        pytest.fail(f"paper optimization outcomes module is missing: {exc}")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_collect_trade_outcomes_reconstructs_open_and_not_executed_rows(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()
    ledger_path = paths.paper_ledger_file
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "paper_fill",
                        "recorded_at_bj": "2026-04-23T12:00:00+08:00",
                        "intent_id": "intent-btc",
                        "signal_id": "signal-btc",
                        "symbol": "BTCUSDT",
                        "order": {
                            "intent_id": "intent-btc",
                            "signal_id": "signal-btc",
                            "symbol": "BTCUSDT",
                            "side": "LONG",
                            "qty": 0.02,
                            "entry_price": 64000.0,
                            "stop_loss": 62830.0,
                            "take_profit": 67200.0,
                            "status": "FILLED",
                            "meta": {"engine": "trend", "setup_type": "BREAKOUT_CONTINUATION"},
                        },
                        "result": {"status": "FILLED", "filled_qty": 0.02, "avg_price": 64010.0},
                        "position_update": {
                            "symbol": "BTCUSDT",
                            "side": "LONG",
                            "qty": 0.02,
                            "entry_price": 64000.0,
                            "mark_price": 64650.0,
                            "unrealized_pnl": 13.0,
                            "stop_loss": 62830.0,
                            "take_profit": 67200.0,
                            "status": "OPEN",
                            "intent_id": "intent-btc",
                            "signal_id": "signal-btc",
                            "opened_at_bj": "2026-04-23T12:00:00+08:00",
                            "updated_at_bj": "2026-04-23T12:05:00+08:00",
                        },
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = module.collect_trade_outcomes(
        trade_outcomes_path=paths.trade_outcomes_file,
        signal_facts=[
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
            },
            {
                "fact_type": "signal",
                "mode": "paper",
                "runtime_env": "research",
                "regime_label": "RISK_ON_TREND",
                "regime_confidence": 0.82,
                "symbol": "ETHUSDT",
                "side": "LONG",
                "engine": "rotation",
                "setup_type": "RS_PULLBACK",
                "score": 0.77,
                "stop_loss": 3100.0,
                "invalidation_source": "rotation_failed_followthrough",
                "validation_allowed": True,
                "allocation_status": "ACCEPTED",
                "allocation_rank": 2,
                "final_risk_budget": 0.008,
                "execution_status": "BLOCKED",
                "intent_id": None,
            },
        ],
        runtime_positions={
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.02,
                "entry_price": 64000.0,
                "mark_price": 64650.0,
                "unrealized_pnl": 13.0,
                "stop_loss": 62830.0,
                "take_profit": 67200.0,
                "status": "OPEN",
                "intent_id": "intent-btc",
                "signal_id": "signal-btc",
                "opened_at_bj": "2026-04-23T12:00:00+08:00",
                "updated_at_bj": "2026-04-23T12:05:00+08:00",
            }
        },
        paper_ledger_path=ledger_path,
    )

    assert paths.trade_outcomes_file == paths.optimization_dir / "trade_outcomes.jsonl"
    assert summary == {
        "trade_outcomes_path": str(paths.trade_outcomes_file),
        "appended_count": 2,
        "open_count": 1,
        "not_executed_count": 1,
        "position_not_tracked_count": 0,
    }
    assert _jsonl(paths.trade_outcomes_file) == [
        {
            "fact_type": "trade_outcome",
            "mode": "paper",
            "runtime_env": "research",
            "regime_label": "RISK_ON_TREND",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "intent_id": "intent-btc",
            "signal_id": "signal-btc",
            "allocation_status": "ACCEPTED",
            "execution_status": "FILLED",
            "outcome_status": "OPEN",
            "position_status": "OPEN",
            "score": 0.91,
            "final_risk_budget": 0.01,
            "filled_qty": 0.02,
            "open_qty": 0.02,
            "entry_price": 64000.0,
            "mark_price": 64650.0,
            "stop_loss": 62830.0,
            "take_profit": 67200.0,
            "unrealized_pnl": 13.0,
            "realized_pnl": None,
            "pnl_basis": "unrealized",
            "opened_at_bj": "2026-04-23T12:00:00+08:00",
            "updated_at_bj": "2026-04-23T12:05:00+08:00",
            "recorded_at_bj": "2026-04-23T12:00:00+08:00",
        },
        {
            "fact_type": "trade_outcome",
            "mode": "paper",
            "runtime_env": "research",
            "regime_label": "RISK_ON_TREND",
            "symbol": "ETHUSDT",
            "side": "LONG",
            "engine": "rotation",
            "setup_type": "RS_PULLBACK",
            "intent_id": None,
            "signal_id": None,
            "allocation_status": "ACCEPTED",
            "execution_status": "BLOCKED",
            "outcome_status": "NOT_EXECUTED",
            "position_status": None,
            "score": 0.77,
            "final_risk_budget": 0.008,
            "filled_qty": None,
            "open_qty": None,
            "entry_price": None,
            "mark_price": None,
            "stop_loss": 3100.0,
            "take_profit": None,
            "unrealized_pnl": None,
            "realized_pnl": None,
            "pnl_basis": None,
            "opened_at_bj": None,
            "updated_at_bj": None,
            "recorded_at_bj": None,
        },
    ]


def test_collect_trade_outcomes_marks_missing_position_after_fill(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    summary = module.collect_trade_outcomes(
        trade_outcomes_path=paths.trade_outcomes_file,
        signal_facts=[
            {
                "fact_type": "signal",
                "mode": "paper",
                "runtime_env": "research",
                "regime_label": "RISK_OFF",
                "regime_confidence": 0.65,
                "symbol": "SOLUSDT",
                "side": "LONG",
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "score": 0.66,
                "stop_loss": 118.0,
                "invalidation_source": "structure_break",
                "validation_allowed": True,
                "allocation_status": "ACCEPTED",
                "allocation_rank": 1,
                "final_risk_budget": 0.004,
                "execution_status": "FILLED",
                "intent_id": "intent-sol",
            }
        ],
        runtime_positions={},
        paper_ledger_path=None,
    )

    assert summary == {
        "trade_outcomes_path": str(paths.trade_outcomes_file),
        "appended_count": 1,
        "open_count": 0,
        "not_executed_count": 0,
        "position_not_tracked_count": 1,
    }
    assert _jsonl(paths.trade_outcomes_file)[0]["outcome_status"] == "POSITION_NOT_TRACKED"

def test_collect_trade_outcomes_rejects_invalid_present_filled_qty(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()
    ledger_path = paths.paper_ledger_file
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "intent_id": "intent-btc",
                "order": {"qty": 0.02},
                "result": {"filled_qty": "bad"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="result.filled_qty must be numeric"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts=[
                {
                    "fact_type": "signal",
                    "mode": "paper",
                    "runtime_env": "research",
                    "regime_label": "RISK_ON_TREND",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "score": 0.91,
                    "stop_loss": 62830.0,
                    "allocation_status": "ACCEPTED",
                    "final_risk_budget": 0.01,
                    "execution_status": "FILLED",
                    "intent_id": "intent-btc",
                }
            ],
            runtime_positions={},
            paper_ledger_path=ledger_path,
        )

def test_collect_trade_outcomes_rejects_malformed_signal_fact_jsonl(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="signal_facts.jsonl:1 must be valid JSON"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            runtime_positions={},
            signal_facts_path=paths.signal_facts_file,
            paper_ledger_path=None,
        )

def test_collect_trade_outcomes_rejects_non_object_signal_fact_rows(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    with pytest.raises(ValueError, match="signal_facts rows must be objects"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            runtime_positions={},
            signal_facts=["not-an-object"],
            paper_ledger_path=None,
        )

def test_collect_trade_outcomes_rejects_non_object_runtime_positions(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    with pytest.raises(ValueError, match="runtime_positions.BTCUSDT must be an object"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts=[],
            runtime_positions={"BTCUSDT": "not-an-object"},
            paper_ledger_path=None,
        )

def test_collect_trade_outcomes_rejects_non_string_execution_status(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    with pytest.raises(ValueError, match="fact.execution_status must be a string"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts=[
                {
                    "fact_type": "signal",
                    "mode": "paper",
                    "runtime_env": "research",
                    "regime_label": "RISK_ON_TREND",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "score": 0.91,
                    "stop_loss": 62830.0,
                    "allocation_status": "ACCEPTED",
                    "final_risk_budget": 0.01,
                    "execution_status": 123,
                    "intent_id": "intent-btc",
                }
            ],
            runtime_positions={},
            paper_ledger_path=None,
        )

def test_collect_trade_outcomes_rejects_non_string_fact_intent_id(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    with pytest.raises(ValueError, match="fact.intent_id must be a string"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts=[
                {
                    "fact_type": "signal",
                    "mode": "paper",
                    "runtime_env": "research",
                    "regime_label": "RISK_ON_TREND",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "score": 0.91,
                    "stop_loss": 62830.0,
                    "allocation_status": "ACCEPTED",
                    "final_risk_budget": 0.01,
                    "execution_status": "FILLED",
                    "intent_id": 123,
                }
            ],
            runtime_positions={},
            paper_ledger_path=None,
        )

def test_collect_trade_outcomes_rejects_non_string_position_signal_id(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _outcomes_module()

    with pytest.raises(ValueError, match="position.signal_id must be a string"):
        module.collect_trade_outcomes(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts=[
                {
                    "fact_type": "signal",
                    "mode": "paper",
                    "runtime_env": "research",
                    "regime_label": "RISK_ON_TREND",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "engine": "trend",
                    "setup_type": "BREAKOUT_CONTINUATION",
                    "score": 0.91,
                    "stop_loss": 62830.0,
                    "allocation_status": "ACCEPTED",
                    "final_risk_budget": 0.01,
                    "execution_status": "FILLED",
                    "intent_id": "intent-btc",
                }
            ],
            runtime_positions={"BTCUSDT": {"symbol": "BTCUSDT", "qty": 0.02, "signal_id": 123}},
            paper_ledger_path=None,
        )
