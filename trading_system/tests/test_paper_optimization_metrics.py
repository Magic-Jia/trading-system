from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths


def _metrics_module():
    try:
        return importlib.import_module("trading_system.app.paper_optimization.metrics")
    except ModuleNotFoundError as exc:
        pytest.fail(f"paper optimization metrics module is missing: {exc}")


def test_write_daily_metrics_uses_runtime_positions_for_current_open_state(tmp_path: Path) -> None:
    paths = build_runtime_paths("testnet", runtime_root=tmp_path / "runtime", runtime_env="prod")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text(json.dumps({"symbol": "BTCUSDT"}) + "\n", encoding="utf-8")
    paths.trade_outcomes_file.write_text(
        json.dumps({"symbol": "BTCUSDT", "outcome_status": "UNKNOWN", "unrealized_pnl": -999}) + "\n",
        encoding="utf-8",
    )

    module.write_daily_metrics_and_health_report(
        trade_outcomes_path=paths.trade_outcomes_file,
        signal_facts_path=paths.signal_facts_file,
        daily_metrics_path=paths.daily_metrics_file,
        health_report_path=paths.health_report_file,
        recorded_at_bj="2026-04-27T00:00:00+08:00",
        runtime_positions={
            "BTCUSDT": {"symbol": "BTCUSDT", "status": "OPEN", "qty": 0.1, "unrealized_pnl": -5.5},
            "ETHUSDT": {"symbol": "ETHUSDT", "status": "OPEN", "qty": 2, "unrealized_pnl": -3.5},
            "LINKUSDT": {"symbol": "LINKUSDT", "status": "CLOSED", "qty": 0, "unrealized_pnl": 0},
        },
    )

    daily_metrics = json.loads(paths.daily_metrics_file.read_text(encoding="utf-8"))
    assert daily_metrics["scope"] == "current_runtime_positions"
    assert daily_metrics["open_count"] == 2
    assert daily_metrics["unrealized_pnl_total"] == -9.0
    assert daily_metrics["current_positions"] == {
        "BTCUSDT": {"status": "OPEN", "qty": 0.1, "unrealized_pnl": -5.5},
        "ETHUSDT": {"status": "OPEN", "qty": 2.0, "unrealized_pnl": -3.5},
    }


def test_write_daily_metrics_deduplicates_latest_outcome_per_symbol_for_current_runtime_scope(tmp_path: Path) -> None:
    paths = build_runtime_paths("testnet", runtime_root=tmp_path / "runtime", runtime_env="prod")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text("{}\n{}\n", encoding="utf-8")
    rows = [
        {
            "symbol": "BTCUSDT",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "regime_label": "OLD",
            "execution_status": "FILLED",
            "outcome_status": "OPEN",
            "unrealized_pnl": -1000,
            "updated_at_bj": "2026-04-26T10:00:00+08:00",
        },
        {
            "symbol": "BTCUSDT",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "regime_label": "NEW",
            "execution_status": "FILLED",
            "outcome_status": "OPEN",
            "unrealized_pnl": -5,
            "updated_at_bj": "2026-04-26T23:00:00+08:00",
        },
        {
            "symbol": "ETHUSDT",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "regime_label": "NEW",
            "execution_status": "FILLED",
            "outcome_status": "POSITION_NOT_TRACKED",
            "unrealized_pnl": -99,
            "updated_at_bj": "2026-04-26T11:00:00+08:00",
        },
        {
            "symbol": "ETHUSDT",
            "engine": "trend",
            "setup_type": "BREAKOUT_CONTINUATION",
            "regime_label": "NEW",
            "execution_status": "FILLED",
            "outcome_status": "OPEN",
            "unrealized_pnl": -4,
            "updated_at_bj": "2026-04-26T23:01:00+08:00",
        },
    ]
    paths.trade_outcomes_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    module.write_daily_metrics_and_health_report(
        trade_outcomes_path=paths.trade_outcomes_file,
        signal_facts_path=paths.signal_facts_file,
        daily_metrics_path=paths.daily_metrics_file,
        health_report_path=paths.health_report_file,
        recorded_at_bj="2026-04-27T00:00:00+08:00",
    )

    daily_metrics = json.loads(paths.daily_metrics_file.read_text(encoding="utf-8"))
    assert daily_metrics["scope"] == "current_runtime_latest_by_symbol"
    assert daily_metrics["raw_trade_outcome_count"] == 4
    assert daily_metrics["trade_outcome_count"] == 2
    assert daily_metrics["open_count"] == 2
    assert daily_metrics["position_not_tracked_count"] == 0
    assert daily_metrics["unrealized_pnl_total"] == -9.0
    health_report = json.loads(paths.health_report_file.read_text(encoding="utf-8"))
    assert health_report["status"] == "ok"


def test_write_daily_metrics_and_health_report_summarizes_trade_outcomes(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text(
        "\n".join(
            [
                json.dumps({"fact_type": "signal", "symbol": "BTCUSDT"}),
                json.dumps({"fact_type": "signal", "symbol": "ETHUSDT"}),
                json.dumps({"fact_type": "signal", "symbol": "SOLUSDT"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths.trade_outcomes_file.write_text(
        "\n".join(
            [
                json.dumps(
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
                    }
                ),
                json.dumps(
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
                    }
                ),
                json.dumps(
                    {
                        "fact_type": "trade_outcome",
                        "mode": "paper",
                        "runtime_env": "research",
                        "regime_label": "RISK_OFF",
                        "symbol": "SOLUSDT",
                        "side": "LONG",
                        "engine": "trend",
                        "setup_type": "BREAKOUT_CONTINUATION",
                        "intent_id": "intent-sol",
                        "signal_id": None,
                        "allocation_status": "ACCEPTED",
                        "execution_status": "FILLED",
                        "outcome_status": "POSITION_NOT_TRACKED",
                        "position_status": None,
                        "score": 0.66,
                        "final_risk_budget": 0.004,
                        "filled_qty": None,
                        "open_qty": None,
                        "entry_price": None,
                        "mark_price": None,
                        "stop_loss": 118.0,
                        "take_profit": None,
                        "unrealized_pnl": None,
                        "realized_pnl": None,
                        "pnl_basis": None,
                        "opened_at_bj": None,
                        "updated_at_bj": None,
                        "recorded_at_bj": None,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = module.write_daily_metrics_and_health_report(
        trade_outcomes_path=paths.trade_outcomes_file,
        signal_facts_path=paths.signal_facts_file,
        daily_metrics_path=paths.daily_metrics_file,
        health_report_path=paths.health_report_file,
        recorded_at_bj="2026-04-23T18:50:00+08:00",
    )

    assert summary == {
        "daily_metrics_path": str(paths.daily_metrics_file),
        "health_report_path": str(paths.health_report_file),
        "trade_outcome_count": 3,
        "signal_fact_count": 3,
        "open_count": 1,
        "position_not_tracked_count": 1,
        "warning_count": 1,
    }

    daily_metrics = json.loads(paths.daily_metrics_file.read_text(encoding="utf-8"))
    assert daily_metrics == {
        "recorded_at_bj": "2026-04-23T18:50:00+08:00",
        "scope": "current_runtime_latest_by_symbol",
        "raw_trade_outcome_count": 3,
        "signal_fact_count": 3,
        "trade_outcome_count": 3,
        "execution_status_counts": {"BLOCKED": 1, "FILLED": 2},
        "outcome_status_counts": {"NOT_EXECUTED": 1, "OPEN": 1, "POSITION_NOT_TRACKED": 1},
        "open_count": 1,
        "not_executed_count": 1,
        "position_not_tracked_count": 1,
        "unrealized_pnl_total": 13.0,
        "current_positions": {},
        "by_engine": {
            "rotation": {
                "trade_outcome_count": 1,
                "open_count": 0,
                "position_not_tracked_count": 0,
                "unrealized_pnl_total": 0.0,
            },
            "trend": {
                "trade_outcome_count": 2,
                "open_count": 1,
                "position_not_tracked_count": 1,
                "unrealized_pnl_total": 13.0,
            },
        },
        "by_setup_type": {
            "BREAKOUT_CONTINUATION": {
                "trade_outcome_count": 2,
                "open_count": 1,
                "position_not_tracked_count": 1,
                "unrealized_pnl_total": 13.0,
            },
            "RS_PULLBACK": {
                "trade_outcome_count": 1,
                "open_count": 0,
                "position_not_tracked_count": 0,
                "unrealized_pnl_total": 0.0,
            },
        },
        "by_regime": {
            "RISK_OFF": {
                "trade_outcome_count": 1,
                "open_count": 0,
                "position_not_tracked_count": 1,
                "unrealized_pnl_total": 0.0,
            },
            "RISK_ON_TREND": {
                "trade_outcome_count": 2,
                "open_count": 1,
                "position_not_tracked_count": 0,
                "unrealized_pnl_total": 13.0,
            },
        },
    }

    health_report = json.loads(paths.health_report_file.read_text(encoding="utf-8"))
    assert health_report == {
        "recorded_at_bj": "2026-04-23T18:50:00+08:00",
        "scope": "current_runtime_latest_by_symbol",
        "raw_trade_outcome_count": 3,
        "status": "warn",
        "signal_fact_count": 3,
        "trade_outcome_count": 3,
        "warnings": [
            {
                "code": "position_not_tracked",
                "count": 1,
                "message": "1 filled outcomes do not currently map to an active runtime position",
            }
        ],
    }

def test_write_daily_metrics_rejects_malformed_jsonl_rows(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text('{"symbol": "BTCUSDT"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="trade_outcomes.jsonl line 2 must be valid JSON"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
        )

def test_write_daily_metrics_rejects_invalid_runtime_position_rows(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text('{"symbol": "BTCUSDT", "outcome_status": "OPEN"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="runtime position rows must be objects"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
            runtime_positions={"BTCUSDT": "not-an-object"},
        )

def test_write_daily_metrics_rejects_non_string_runtime_position_status(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text('{"symbol": "BTCUSDT", "outcome_status": "OPEN"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="runtime_position.status must be a string"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
            runtime_positions={"BTCUSDT": {"symbol": "BTCUSDT", "status": 123, "qty": 1}},
        )

def test_write_daily_metrics_rejects_boolean_runtime_position_qty(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text('{"symbol": "BTCUSDT", "outcome_status": "OPEN"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="runtime_position.qty must be numeric"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
            runtime_positions={"BTCUSDT": {"symbol": "BTCUSDT", "status": "OPEN", "qty": True}},
        )

def test_write_daily_metrics_rejects_non_string_trade_outcome_symbol(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text(
        json.dumps({"symbol": 123, "outcome_status": "OPEN", "unrealized_pnl": 1.0}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trade_outcome.symbol must be a string"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
        )

def test_write_daily_metrics_rejects_non_string_group_breakdown_fields(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "engine": 123,
                "setup_type": "BREAKOUT_CONTINUATION",
                "regime_label": "RISK_ON",
                "outcome_status": "OPEN",
                "unrealized_pnl": 1.0,
            }
        ) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trade_outcome.engine must be a string"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
        )

def test_write_daily_metrics_rejects_non_string_outcome_status_fields(tmp_path: Path) -> None:
    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="research")
    module = _metrics_module()
    paths.signal_facts_file.parent.mkdir(parents=True, exist_ok=True)
    paths.signal_facts_file.write_text('{"symbol": "BTCUSDT"}\n', encoding="utf-8")
    paths.trade_outcomes_file.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "regime_label": "RISK_ON",
                "execution_status": 123,
                "outcome_status": "OPEN",
                "unrealized_pnl": 1.0,
            }
        ) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trade_outcome.execution_status must be a string"):
        module.write_daily_metrics_and_health_report(
            trade_outcomes_path=paths.trade_outcomes_file,
            signal_facts_path=paths.signal_facts_file,
            daily_metrics_path=paths.daily_metrics_file,
            health_report_path=paths.health_report_file,
            recorded_at_bj="2026-04-27T00:00:00+08:00",
        )
