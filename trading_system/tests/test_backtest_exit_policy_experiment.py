from __future__ import annotations

import importlib

import pytest

from trading_system.app.backtest.types import ExitPolicyParams


def _policy() -> ExitPolicyParams:
    return ExitPolicyParams(name="after_cost_breakeven_stop", after_cost_buffer_bps=2.0)


def _valid_trade() -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "market_type": "spot",
        "base_asset": "BTC",
        "side": "long",
        "status": "accepted",
        "entry_timestamp": "2026-03-10T00:00:00Z",
        "exit_timestamp": "2026-03-10T00:10:00Z",
        "entry_price": 100.0,
        "exit_price": 99.0,
        "qty": 10.0,
        "position_notional": 1_000.0,
        "fee_paid": 0.5,
        "slippage_paid": 0.5,
        "funding_paid": 0.0,
        "trade_prints": [
            {"timestamp": "2026-03-10T00:01:00Z", "price": 100.11},
            {"timestamp": "2026-03-10T00:02:00Z", "price": 100.13},
            {"timestamp": "2026-03-10T00:03:00Z", "price": 100.25},
        ],
    }


@pytest.mark.parametrize("field", ("symbol", "side", "status", "market_type", "base_asset"))
@pytest.mark.parametrize("invalid_value", (123, "", "   "))
def test_exit_policy_experiment_rejects_present_invalid_trade_string_fields(field: str, invalid_value: object) -> None:
    module = importlib.import_module("trading_system.app.backtest.exit_policy_experiment")
    trade = _valid_trade()
    trade[field] = invalid_value

    with pytest.raises(ValueError, match=rf"trades\[1\]\.{field}"):
        module.build_exit_policy_experiment(trades=[trade], policy=_policy())


def test_exit_policy_experiment_records_evidence_backed_trigger_and_diagnostic_pnl() -> None:
    module = importlib.import_module("trading_system.app.backtest.exit_policy_experiment")

    artifact = module.build_exit_policy_experiment(
        trades=[
            {
                "symbol": "BTCUSDT",
                "market_type": "spot",
                "base_asset": "BTC",
                "side": "long",
                "status": "accepted",
                "entry_timestamp": "2026-03-10T00:00:00Z",
                "exit_timestamp": "2026-03-10T00:10:00Z",
                "entry_price": 100.0,
                "exit_price": 99.0,
                "qty": 10.0,
                "position_notional": 1_000.0,
                "fee_paid": 0.5,
                "slippage_paid": 0.5,
                "funding_paid": 0.0,
                "trade_prints": [
                    {"timestamp": "2026-03-10T00:01:00Z", "price": 100.11},
                    {"timestamp": "2026-03-10T00:02:00Z", "price": 100.13},
                    {"timestamp": "2026-03-10T00:03:00Z", "price": 100.25},
                ],
            }
        ],
        policy=_policy(),
    )

    assert artifact["metadata"]["artifact_type"] == "opt_in_offline_diagnostic"
    assert artifact["metadata"]["changes_baseline_ledger"] is False
    assert artifact["metadata"]["policy"] == {
        "name": "after_cost_breakeven_stop",
        "after_cost_buffer_bps": 2.0,
        "activation_minute": 0,
        "giveback_fraction": None,
        "giveback_min_bps": None,
        "no_breakeven_time_stop_minute": None,
    }
    assert artifact["summary"] == {
        "total_trades": 1,
        "evaluated_count": 1,
        "triggered_count": 1,
        "not_triggered_count": 0,
        "no_evidence_count": 0,
        "skipped_count": 0,
    }

    row = artifact["evaluation_rows"][0]
    assert row["symbol"] == "BTCUSDT"
    assert row["trade_print_path"] == "trade_prints"
    assert row["evaluation_status"] == "triggered"
    assert row["evaluation_reason"] == "after_cost_breakeven_stop"
    assert row["diagnostic_exit_timestamp"] == "2026-03-10T00:02:00+00:00"
    assert row["diagnostic_exit_price"] == pytest.approx(100.13)
    assert row["diagnostic_exit_price_source"] == "trade_print"
    assert row["diagnostic_fill_quality"] == "evidence_backed"
    assert row["diagnostic_policy_gross_pnl"] == pytest.approx(1.3)
    assert row["diagnostic_policy_net_pnl"] == pytest.approx(0.3)


def test_exit_policy_experiment_marks_missing_trade_print_path_as_no_evidence_without_fallback() -> None:
    module = importlib.import_module("trading_system.app.backtest.exit_policy_experiment")

    artifact = module.build_exit_policy_experiment(
        trades=[
            {
                "symbol": "ETHUSDT",
                "market_type": "futures",
                "base_asset": "ETH",
                "side": "short",
                "status": "accepted",
                "entry_timestamp": "2026-03-10T00:00:00Z",
                "exit_timestamp": "2026-03-10T00:10:00Z",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "qty": 2.0,
                "position_notional": 200.0,
                "fee_paid": 0.2,
                "slippage_paid": 0.3,
                "funding_paid": 0.1,
                "simulated_exit_price": 98.0,
                "reference_close": 97.5,
                "path_high": 105.0,
                "path_low": 95.0,
            }
        ],
        policy=_policy(),
    )

    assert artifact["summary"] == {
        "total_trades": 1,
        "evaluated_count": 1,
        "triggered_count": 0,
        "not_triggered_count": 0,
        "no_evidence_count": 1,
        "skipped_count": 0,
    }

    row = artifact["evaluation_rows"][0]
    assert row["evaluation_status"] == "no_evidence"
    assert row["evaluation_reason"] == "missing_trade_print_path"
    assert row["trade_print_path"] is None
    assert row["diagnostic_exit_timestamp"] is None
    assert row["diagnostic_exit_price"] is None
    assert row["diagnostic_exit_price_source"] == "none"
    assert row["diagnostic_fill_quality"] == "no_evidence"
    assert row["diagnostic_policy_gross_pnl"] is None
    assert row["diagnostic_policy_net_pnl"] is None
