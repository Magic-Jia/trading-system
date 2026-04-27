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


def _testnet_preview_payloads(*, include_stop: bool = True, include_take_profit: bool = False) -> dict:
    payloads = {
        "entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"},
    }
    if include_stop:
        payloads["stop"] = {"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 58000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-sl"}
    if include_take_profit:
        payloads["take_profit"] = {"symbol": "BTCUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": 64000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-tp"}
    return payloads


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


def test_execution_config_enables_feishu_notifications_by_default(monkeypatch, tmp_path):
    build_testnet_config(tmp_path, monkeypatch)
    monkeypatch.delenv("TRADING_FEISHU_NOTIFICATIONS_ENABLED", raising=False)
    monkeypatch.setenv("TRADING_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("TRADING_FEISHU_APP_SECRET", "app-secret")
    monkeypatch.setenv("TRADING_FEISHU_RECEIVE_ID", "chat-id")

    config = build_config()

    assert config.execution.feishu_notifications_enabled is True
    assert not hasattr(config.execution, "feishu_webhook_url")


def test_execution_config_can_disable_feishu_notifications_with_env(monkeypatch, tmp_path):
    build_testnet_config(tmp_path, monkeypatch)
    monkeypatch.setenv("TRADING_FEISHU_NOTIFICATIONS_ENABLED", "0")
    config = build_config()

    assert config.execution.feishu_notifications_enabled is False
    assert not hasattr(config.execution, "feishu_webhook_url")


def test_execution_config_resolves_feishu_app_bot_fallback_env(monkeypatch, tmp_path):
    build_testnet_config(tmp_path, monkeypatch)
    monkeypatch.delenv("TRADING_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("TRADING_FEISHU_APP_SECRET", raising=False)
    monkeypatch.setenv("FEISHU_APP_ID", "fallback-app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "fallback-app-secret")
    monkeypatch.setenv("TRADING_FEISHU_RECEIVE_ID", "chat-id")

    config = build_config()

    assert config.execution.feishu_app_id == "fallback-app-id"
    assert config.execution.feishu_app_secret == "fallback-app-secret"
    assert config.execution.feishu_receive_id == "chat-id"
    assert config.execution.feishu_receive_id_type == "chat_id"
    assert config.execution.feishu_domain == "feishu"


def test_execution_config_prefers_trading_feishu_app_env_over_global_env(monkeypatch, tmp_path):
    build_testnet_config(tmp_path, monkeypatch)
    monkeypatch.setenv("FEISHU_APP_ID", "fallback-app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "fallback-app-secret")
    monkeypatch.setenv("FEISHU_DOMAIN", "global-domain.example")
    monkeypatch.setenv("TRADING_FEISHU_APP_ID", "trading-app-id")
    monkeypatch.setenv("TRADING_FEISHU_APP_SECRET", "trading-app-secret")
    monkeypatch.setenv("TRADING_FEISHU_RECEIVE_ID", "open-id")
    monkeypatch.setenv("TRADING_FEISHU_RECEIVE_ID_TYPE", "open_id")
    monkeypatch.setenv("TRADING_FEISHU_DOMAIN", "trading-domain.example")

    config = build_config()

    assert config.execution.feishu_app_id == "trading-app-id"
    assert config.execution.feishu_app_secret == "trading-app-secret"
    assert config.execution.feishu_receive_id == "open-id"
    assert config.execution.feishu_receive_id_type == "open_id"
    assert config.execution.feishu_domain == "trading-domain.example"


def test_order_executor_testnet_does_not_notify_when_feishu_disabled(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=False,
        ),
    )
    calls = []

    def fake_submit(payload):
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    def fake_notify(message: str) -> None:
        calls.append(message)

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    monkeypatch.setattr(
        executor_module,
        "submit_futures_testnet_conditional_algo_order",
        lambda payload: {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]},
    )
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": _testnet_preview_payloads(),
    }
    executor = OrderExecutor(config, feishu_notifier=fake_notify)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert result["result"] == "SUBMITTED"
    assert calls == []


def test_order_executor_testnet_success_notifies_when_feishu_enabled(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=True,
        ),
    )
    calls = []

    def fake_submit(payload):
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    def fake_notify(message: str) -> None:
        calls.append(message)

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    monkeypatch.setattr(
        executor_module,
        "submit_futures_testnet_conditional_algo_order",
        lambda payload: {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]},
    )
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": _testnet_preview_payloads(),
    }
    executor = OrderExecutor(config, feishu_notifier=fake_notify)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert result["result"] == "SUBMITTED"
    assert len(calls) == 1
    assert "SUBMITTED" in calls[0]
    assert "BTCUSDT" in calls[0]
    assert "intent-btc-long" in calls[0]


def test_order_executor_testnet_success_still_returns_submitted_when_notification_fails(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=True,
        ),
    )

    def fake_submit(payload):
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    def fake_notify(_message: str) -> None:
        raise RuntimeError("feishu down")

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    monkeypatch.setattr(
        executor_module,
        "submit_futures_testnet_conditional_algo_order",
        lambda payload: {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]},
    )
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": _testnet_preview_payloads(),
    }
    executor = OrderExecutor(config, feishu_notifier=fake_notify)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert result["result"] == "SUBMITTED"
    assert result["exchange_response"]["orderId"] == 12345



def test_order_executor_testnet_submits_entry_and_stop_algo_order(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=False,
        ),
    )
    submitted_entry_payloads = []
    submitted_stop_payloads = []

    def fake_submit_entry(payload):
        submitted_entry_payloads.append(payload)
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    def fake_submit_stop(payload):
        submitted_stop_payloads.append(payload)
        return {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]}

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit_entry)
    monkeypatch.setattr(executor_module, "submit_futures_testnet_conditional_algo_order", fake_submit_stop)
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": {
            "entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"},
            "stop": {"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 58000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-sl"},
        },
    }
    executor = OrderExecutor(config)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert result["result"] == "SUBMITTED"
    assert submitted_entry_payloads == [order.meta["validated_order_preview"]["payloads"]["entry"]]
    assert submitted_stop_payloads == [
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "triggerPrice": 58000.0,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "clientAlgoId": "intent-btc-long-sl",
        }
    ]
    assert result["stop_algo_order"] == submitted_stop_payloads[0]
    assert result["stop_algo_response"] == {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": "intent-btc-long-sl"}



def test_order_executor_testnet_stop_failure_returns_partial_success_and_logs(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=False,
        ),
    )

    def fake_submit_entry(payload):
        return {"orderId": 12345, "status": "FILLED", "clientOrderId": payload["newClientOrderId"]}

    def fake_submit_stop(_payload):
        raise RuntimeError("binance stop rejected: -1102 mandatory parameter missing")

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit_entry)
    monkeypatch.setattr(executor_module, "submit_futures_testnet_conditional_algo_order", fake_submit_stop)
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": {
            "entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"},
            "stop": {"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 58000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-sl"},
        },
    }
    executor = OrderExecutor(config)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert order.status == "SENT"
    assert result["result"] == "SUBMITTED_PROTECTION_FAILED"
    assert result["exchange_response"]["orderId"] == 12345
    assert result["stop_algo_order"]["clientAlgoId"] == "intent-btc-long-sl"
    assert "binance stop rejected" in result["stop_algo_error"]
    assert exec_log.exists()
    log_payload = exec_log.read_text(encoding="utf-8")
    assert "SUBMITTED_PROTECTION_FAILED" in log_payload
    assert "intent-btc-long" in log_payload



def test_order_executor_testnet_requires_protective_stop_before_entry_submission(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=False,
        ),
    )
    submitted_entry_payloads = []

    def fake_submit_entry(payload):
        submitted_entry_payloads.append(payload)
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit_entry)
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": {
            "entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"},
            "stop": None,
            "take_profit": {"symbol": "BTCUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": 64000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-tp"},
        },
    }
    executor = OrderExecutor(config)

    with pytest.raises(Exception, match="protective stop"):
        executor.execute(order, RuntimeStateV2.empty())

    assert submitted_entry_payloads == []


def test_order_executor_testnet_submits_entry_stop_and_take_profit_algo_orders(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=False,
        ),
    )
    submitted_entry_payloads = []
    submitted_algo_payloads = []

    def fake_submit_entry(payload):
        submitted_entry_payloads.append(payload)
        return {"orderId": 12345, "status": "NEW", "clientOrderId": payload["newClientOrderId"]}

    def fake_submit_algo(payload):
        submitted_algo_payloads.append(payload)
        return {"algoId": len(submitted_algo_payloads), "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]}

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit_entry)
    monkeypatch.setattr(executor_module, "submit_futures_testnet_conditional_algo_order", fake_submit_algo)
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": {
            "entry": {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01, "newClientOrderId": "intent-btc-long"},
            "stop": {"symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "stopPrice": 58000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-sl"},
            "take_profit": {"symbol": "BTCUSDT", "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": 64000.0, "closePosition": "true", "workingType": "MARK_PRICE", "newClientOrderId": "intent-btc-long-tp"},
        },
    }
    executor = OrderExecutor(config)

    result = executor.execute(order, RuntimeStateV2.empty())

    assert result["result"] == "SUBMITTED"
    assert submitted_entry_payloads == [order.meta["validated_order_preview"]["payloads"]["entry"]]
    assert submitted_algo_payloads == [
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "triggerPrice": 58000.0,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "clientAlgoId": "intent-btc-long-sl",
        },
        {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "type": "TAKE_PROFIT_MARKET",
            "algoType": "CONDITIONAL",
            "triggerPrice": 64000.0,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "clientAlgoId": "intent-btc-long-tp",
        },
    ]
    assert result["stop_algo_response"]["clientAlgoId"] == "intent-btc-long-sl"
    assert result["take_profit_algo_response"]["clientAlgoId"] == "intent-btc-long-tp"


def test_order_executor_testnet_exception_notifies_failure_when_feishu_enabled(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=True,
        ),
    )
    calls = []

    def fake_submit(payload):
        raise RuntimeError("exchange down")

    def fake_notify(message: str) -> None:
        calls.append(message)

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    monkeypatch.setattr(
        executor_module,
        "submit_futures_testnet_conditional_algo_order",
        lambda payload: {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]},
    )
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": _testnet_preview_payloads(),
    }
    executor = OrderExecutor(config, feishu_notifier=fake_notify)

    with pytest.raises(RuntimeError, match="exchange down"):
        executor.execute(order, RuntimeStateV2.empty())

    assert len(calls) == 1
    assert "FAILED" in calls[0]
    assert "BTCUSDT" in calls[0]
    assert "exchange down" in calls[0]


def test_order_executor_testnet_exception_preserves_submit_error_when_notification_fails(monkeypatch, tmp_path):
    from trading_system.app.execution import executor as executor_module

    exec_log = tmp_path / "execution_log.jsonl"
    monkeypatch.setattr(executor_module, "EXEC_LOG", exec_log)
    config = build_testnet_config(tmp_path, monkeypatch)
    config = replace(
        config,
        execution=replace(
            config.execution,
            testnet_order_submission_enabled=True,
            feishu_notifications_enabled=True,
        ),
    )

    def fake_submit(payload):
        raise RuntimeError("exchange down")

    def fake_notify(_message: str) -> None:
        raise RuntimeError("feishu down")

    monkeypatch.setattr(executor_module, "submit_futures_testnet_order", fake_submit)
    monkeypatch.setattr(
        executor_module,
        "submit_futures_testnet_conditional_algo_order",
        lambda payload: {"algoId": 67890, "algoStatus": "NEW", "clientAlgoId": payload["clientAlgoId"]},
    )
    order = _sample_order()
    order.meta["validated_order_preview"] = {
        "submission_prerequisites_passed": True,
        "payloads": _testnet_preview_payloads(),
    }
    executor = OrderExecutor(config, feishu_notifier=fake_notify)

    with pytest.raises(RuntimeError, match="exchange down"):
        executor.execute(order, RuntimeStateV2.empty())
