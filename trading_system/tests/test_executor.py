from dataclasses import replace

from trading_system.app.config import DEFAULT_CONFIG
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import OrderIntent


def _sample_order() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-btc-long",
        signal_id="signal-btc-long",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
    )


def test_dry_run_execute_does_not_mutate_runtime_positions_or_active_orders(tmp_path):
    config = replace(DEFAULT_CONFIG, data_dir=tmp_path)
    state = RuntimeStateV2.empty()
    executor = OrderExecutor(config, mode="dry-run")
    order = _sample_order()

    result = executor.execute(order, state)

    assert result["mode"] == "dry-run"
    assert order.status == "SENT"
    assert state.positions == {}
    assert state.active_orders == {}
