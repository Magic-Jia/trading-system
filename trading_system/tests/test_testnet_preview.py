from trading_system.app.execution.testnet_preview import build_validated_order_preview
from trading_system.app.types import OrderIntent


def fake_order_intent() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-btc",
        signal_id="signal-btc",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
    )


def fake_exchange_metadata() -> dict[str, dict[str, float | list[str]]]:
    return {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100,
            "allowed_order_types": ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        }
    }


def test_validated_order_preview_checks_step_size_and_precision():
    intent = OrderIntent(
        intent_id="intent-btc",
        signal_id="signal-btc",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.00015,
        entry_price=65000.123,
        stop_loss=64000.456,
        take_profit=67000.789,
    )
    metadata = {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100,
            "allowed_order_types": ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        }
    }

    preview = build_validated_order_preview(
        intent,
        exchange_metadata=metadata,
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    assert preview["local_validation_passed"] is False
    assert any("step" in reason.lower() or "precision" in reason.lower() for reason in preview["reasons"])


def test_validated_order_preview_exposes_fixed_futures_payload_mapping():
    intent = OrderIntent(
        intent_id="intent-btc",
        signal_id="signal-btc",
        symbol="BTCUSDT",
        side="LONG",
        qty=0.01,
        entry_price=65000,
        stop_loss=64000,
        take_profit=67000,
    )
    metadata = {
        "BTCUSDT": {
            "quantity_step_size": 0.001,
            "price_tick_size": 0.1,
            "min_notional": 100,
            "allowed_order_types": ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
        }
    }

    preview = build_validated_order_preview(
        intent,
        exchange_metadata=metadata,
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    assert preview["payloads"]["entry"]["type"] == "LIMIT"
    assert preview["payloads"]["entry"]["timeInForce"] == "GTX"
    assert preview["payloads"]["entry"]["price"] == 65000
    assert preview["payloads"]["stop"]["type"] == "STOP_MARKET"
    assert preview["payloads"]["take_profit"]["type"] == "TAKE_PROFIT_MARKET"
    assert preview["payloads"]["stop"]["closePosition"] == "true"
    assert preview["payloads"]["take_profit"]["workingType"] == "MARK_PRICE"


def test_validated_order_preview_can_build_configured_taker_market_entry_payload():
    preview = build_validated_order_preview(
        intent=fake_order_intent(),
        exchange_metadata=fake_exchange_metadata(),
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
        entry_order_policy="taker_market",
    )

    assert preview["payloads"]["entry"] == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": 0.01,
        "newClientOrderId": "intent-btc",
    }
    assert preview["order_types"] == ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert preview["submission_prerequisites_passed"] is True


def test_validated_order_preview_includes_configured_maker_timeout_seconds():
    preview = build_validated_order_preview(
        intent=fake_order_intent(),
        exchange_metadata=fake_exchange_metadata(),
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
        entry_order_policy="maker_only",
        maker_entry_timeout_seconds=15,
    )

    assert preview["entry_order_policy"] == "maker_only"
    assert preview["maker_entry_timeout_seconds"] == 15


def test_build_validated_order_preview_marks_no_signal_fallback_source():
    preview = build_validated_order_preview(
        intent=fake_order_intent(),
        exchange_metadata=fake_exchange_metadata(),
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="no_signal_fallback",
    )

    assert preview["preview_source"] == "no_signal_fallback"
    assert preview["submission_enabled"] is False
    assert preview["would_submit"] is False
    assert preview["submission_prerequisites_passed"] is True
