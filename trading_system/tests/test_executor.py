from dataclasses import replace

import pytest

from trading_system.app.config import DEFAULT_CONFIG, build_config
from trading_system.app.execution.executor import OrderExecutor
from trading_system.app.storage.state_store import RuntimeStateV2
from trading_system.app.types import ManagementActionIntent, OrderIntent


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


def build_testnet_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_EXECUTION_MODE", "testnet")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "1")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://testnet.binancefuture.com")
    monkeypatch.setenv("TRADING_TESTNET_ALLOWED_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_TESTNET_MAX_ORDER_NOTIONAL_USDT", "1000")
    monkeypatch.setenv("TRADING_TESTNET_MAX_OPEN_POSITIONS", "2")
    monkeypatch.setenv("TRADING_TESTNET_ORDER_SUBMISSION_ENABLED", "0")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "secret")

    return replace(
        build_config(),
        data_dir=tmp_path,
        state_file=tmp_path / "runtime_state.json",
    )


def test_executor_defaults_to_configured_execution_mode(tmp_path):
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        execution=replace(DEFAULT_CONFIG.execution, mode="dry-run"),
    )

    executor = OrderExecutor(config)

    assert executor.mode == "dry-run"


def test_executor_rejects_live_mode_without_explicit_allow(tmp_path):
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        execution=replace(DEFAULT_CONFIG.execution, mode="live", allow_live_execution=False),
    )

    with pytest.raises(Exception, match="live execution is disabled"):
        OrderExecutor(config)


def test_executor_live_mode_requires_explicit_feature_enable_beyond_allow_flag(tmp_path):
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        execution=replace(DEFAULT_CONFIG.execution, mode="live", allow_live_execution=True),
    )
    state = RuntimeStateV2.empty()
    executor = OrderExecutor(config)

    with pytest.raises(Exception, match="live 模式尚未启用"):
        executor.execute(_sample_order(), state)


def test_live_management_preview_is_rejected_even_if_live_is_allowed(tmp_path):
    config = replace(
        DEFAULT_CONFIG,
        data_dir=tmp_path,
        execution=replace(DEFAULT_CONFIG.execution, mode="live", allow_live_execution=True),
    )
    executor = OrderExecutor(config)

    with pytest.raises(Exception, match="仅支持 paper / dry-run 预览"):
        executor.preview_management_action(
            ManagementActionIntent(
                intent_id="mgmt-btc-break-even",
                symbol="BTCUSDT",
                action="BREAK_EVEN",
                side="LONG",
                position_qty=0.01,
                stop_loss=60000.0,
            )
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


def test_dry_run_execute_does_not_append_execution_log(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)

    config = replace(DEFAULT_CONFIG, data_dir=tmp_path)
    state = RuntimeStateV2.empty()
    executor = OrderExecutor(config, mode="dry-run")

    executor.execute(_sample_order(), state)

    assert not exec_log.exists()


def test_order_executor_testnet_mode_returns_preview_without_submission(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    executor = OrderExecutor(config)

    result = executor.execute(_sample_order(), RuntimeStateV2.empty())

    assert result["mode"] == "testnet"
    assert result["would_submit"] is False
    assert result["submission_enabled"] is False


def test_order_executor_testnet_submission_uses_fake_submitter(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(config.execution, testnet_order_submission_enabled=True),
    )
    calls = []

    def fake_submit(payload):
        calls.append(payload)
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": {"entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"}},
    }
    executor = OrderExecutor(config)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert calls == [{"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"}]
    assert result["mode"] == "testnet"
    assert result["venue"] == "binance_futures_testnet"
    assert result["result"] == "SUBMITTED"
    assert result["exchange_response"]["orderId"] == 12345
    assert result["clientOrderId"] == "intent-btc-long"
    assert not (tmp_path / "paper_ledger.jsonl").exists()
