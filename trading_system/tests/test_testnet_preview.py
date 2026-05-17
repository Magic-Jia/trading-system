import pytest

from trading_system.app.execution.exchange_constraints import (
    build_exchange_constraint_report,
    build_exchange_reject_event,
    build_venue_rulebook_report,
    reject_reason_from_exchange_code,
)
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
            "rulebook_version": "binance-futures-testnet-2026-05-17",
            "rulebook_generated_at": "2026-05-17T08:00:00Z",
            "rulebook_effective_at": "2026-05-17T07:30:00Z",
            "rulebook_source": "offline_fixture:binance_futures_testnet/BTCUSDT.json",
            "post_only_policy": "reject_would_cross",
            "reduce_only_policy": "require_position",
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


def test_validated_order_preview_exposes_exchange_reject_taxonomy_report():
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

    preview = build_validated_order_preview(
        intent,
        exchange_metadata={
            "BTCUSDT": {
                "quantity_step_size": 0.001,
                "price_tick_size": 0.1,
                "min_notional": 100,
                "allowed_order_types": ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
                "best_ask": 65000.0,
            }
        },
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    report = preview["exchange_reject_report"]
    assert report["schema_version"] == "exchange_reject_report.v1"
    assert report["venue"] == "binance_futures_testnet"
    assert report["symbol"] == "BTCUSDT"
    assert report["generated_at"] == "preview"
    assert report["validation_passed"] is False
    assert report["reason_codes"] == [
        "lot_size_violation",
        "tick_size_violation",
        "tick_size_violation",
        "tick_size_violation",
        "min_notional_violation",
        "post_only_would_cross",
    ]
    assert preview["execution_preview"]["unsupported"] == [
        {"reason_code": code, "detail": code} for code in report["reason_codes"]
    ]


def test_venue_rulebook_report_is_machine_readable_with_provenance():
    report = build_venue_rulebook_report(
        venue="binance_futures_testnet",
        symbol="BTCUSDT",
        rulebook_version="binance-futures-testnet-2026-05-17",
        generated_at="2026-05-17T08:00:00Z",
        effective_at="2026-05-17T07:30:00Z",
        source="offline_fixture:binance_futures_testnet/BTCUSDT.json",
        price_tick_size=0.1,
        quantity_step_size=0.001,
        min_notional=100,
        post_only_policy="reject_would_cross",
        reduce_only_policy="require_position",
        now="2026-05-17T08:10:00Z",
        max_age_seconds=3600,
    )

    assert report == {
        "schema_version": "venue_rulebook_report.v1",
        "venue": "binance_futures_testnet",
        "symbol": "BTCUSDT",
        "rulebook_version": "binance-futures-testnet-2026-05-17",
        "generated_at": "2026-05-17T08:00:00Z",
        "effective_at": "2026-05-17T07:30:00Z",
        "source": "offline_fixture:binance_futures_testnet/BTCUSDT.json",
        "constraints": {
            "price_tick_size": 0.1,
            "quantity_step_size": 0.001,
            "min_notional": 100.0,
            "post_only_policy": "reject_would_cross",
            "reduce_only_policy": "require_position",
        },
        "provenance": {
            "source": "offline_fixture:binance_futures_testnet/BTCUSDT.json",
            "rulebook_version": "binance-futures-testnet-2026-05-17",
        },
    }


def test_exchange_constraint_report_carries_rulebook_provenance_when_supplied():
    rulebook = build_venue_rulebook_report(
        venue="binance_futures_testnet",
        symbol="BTCUSDT",
        rulebook_version="binance-futures-testnet-2026-05-17",
        generated_at="2026-05-17T08:00:00Z",
        effective_at="2026-05-17T07:30:00Z",
        source="offline_fixture:binance_futures_testnet/BTCUSDT.json",
        price_tick_size=0.1,
        quantity_step_size=0.001,
        min_notional=100,
        post_only_policy="reject_would_cross",
        reduce_only_policy="require_position",
        now="2026-05-17T08:10:00Z",
        max_age_seconds=3600,
    )

    report = build_exchange_constraint_report(
        venue="binance_futures_testnet",
        symbol="BTCUSDT",
        generated_at="preview",
        order={"side": "BUY", "quantity": 0.01, "price": 65000.0, "post_only": True, "best_ask": 65000.0},
        constraints=rulebook["constraints"],
        rulebook=rulebook,
    )

    assert report["rulebook_version"] == "binance-futures-testnet-2026-05-17"
    assert report["rulebook_source"] == "offline_fixture:binance_futures_testnet/BTCUSDT.json"
    assert report["provenance"] == {
        "rulebook_version": "binance-futures-testnet-2026-05-17",
        "source": "offline_fixture:binance_futures_testnet/BTCUSDT.json",
    }
    assert report["reason_codes"] == ["post_only_would_cross"]


def test_validated_order_preview_propagates_rulebook_provenance_from_metadata():
    preview = build_validated_order_preview(
        intent=fake_order_intent(),
        exchange_metadata=fake_exchange_metadata(),
        allowlist=["BTCUSDT"],
        max_order_notional_usdt=1000,
        submission_enabled=False,
        preview_source="accepted_signal",
    )

    report = preview["exchange_reject_report"]
    assert report["rulebook_version"] == "binance-futures-testnet-2026-05-17"
    assert report["rulebook_source"] == "offline_fixture:binance_futures_testnet/BTCUSDT.json"


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"venue": ""}, "venue"),
        ({"symbol": ""}, "symbol"),
        ({"rulebook_version": ""}, "rulebook_version"),
        ({"source": ""}, "source"),
        ({"generated_at": "2026-05-17T08:00:00+00:00"}, "generated_at"),
        ({"generated_at": "2026-05-17T06:00:00Z"}, "stale generated_at"),
        ({"effective_at": "2026-05-17T08:20:00Z"}, "effective_at"),
        ({"price_tick_size": True}, "price_tick_size"),
        ({"quantity_step_size": float("inf")}, "quantity_step_size"),
        ({"min_notional": -1.0}, "min_notional"),
        ({"post_only_policy": "maybe"}, "post_only_policy"),
        ({"reduce_only_policy": "maybe"}, "reduce_only_policy"),
    ],
)
def test_venue_rulebook_report_fails_closed_on_malformed_rulebooks(patch, match):
    kwargs = {
        "venue": "binance_futures_testnet",
        "symbol": "BTCUSDT",
        "rulebook_version": "binance-futures-testnet-2026-05-17",
        "generated_at": "2026-05-17T08:00:00Z",
        "effective_at": "2026-05-17T07:30:00Z",
        "source": "offline_fixture:binance_futures_testnet/BTCUSDT.json",
        "price_tick_size": 0.1,
        "quantity_step_size": 0.001,
        "min_notional": 100,
        "post_only_policy": "reject_would_cross",
        "reduce_only_policy": "require_position",
        "now": "2026-05-17T08:10:00Z",
        "max_age_seconds": 3600,
    }
    kwargs.update(patch)

    with pytest.raises(ValueError, match=match):
        build_venue_rulebook_report(**kwargs)


def test_exchange_constraint_report_fails_closed_on_mismatched_rulebook_identity():
    rulebook = build_venue_rulebook_report(
        venue="binance_futures_testnet",
        symbol="ETHUSDT",
        rulebook_version="binance-futures-testnet-2026-05-17",
        generated_at="2026-05-17T08:00:00Z",
        effective_at="2026-05-17T07:30:00Z",
        source="offline_fixture:binance_futures_testnet/ETHUSDT.json",
        price_tick_size=0.01,
        quantity_step_size=0.001,
        min_notional=100,
        post_only_policy="reject_would_cross",
        reduce_only_policy="require_position",
        now="2026-05-17T08:10:00Z",
        max_age_seconds=3600,
    )

    with pytest.raises(ValueError, match="rulebook symbol"):
        build_exchange_constraint_report(
            venue="binance_futures_testnet",
            symbol="BTCUSDT",
            generated_at="preview",
            order={"side": "BUY", "quantity": 0.01, "price": 65000.0},
            constraints=rulebook["constraints"],
            rulebook=rulebook,
        )


@pytest.mark.parametrize(
    ("metadata_patch", "match"),
    [
        ({"quantity_step_size": True}, "quantity_step_size"),
        ({"price_tick_size": float("inf")}, "price_tick_size"),
        ({"min_notional": -1.0}, "min_notional"),
    ],
)
def test_validated_order_preview_fails_closed_on_malformed_exchange_constraints(metadata_patch, match):
    metadata = fake_exchange_metadata()
    metadata["BTCUSDT"].update(metadata_patch)

    with pytest.raises(ValueError, match=match):
        build_validated_order_preview(
            fake_order_intent(),
            exchange_metadata=metadata,
            allowlist=["BTCUSDT"],
            max_order_notional_usdt=1000,
            submission_enabled=False,
            preview_source="accepted_signal",
        )


@pytest.mark.parametrize(
    ("raw_code", "reason_code"),
    [
        ("POST_ONLY_WOULD_CROSS", "post_only_would_cross"),
        ("REDUCE_ONLY_INVALID", "reduce_only_invalid"),
        ("INSUFFICIENT_MARGIN", "insufficient_margin"),
        ("INSUFFICIENT_BALANCE", "insufficient_balance"),
        ("RATE_LIMIT", "rate_limit"),
        ("EXCHANGE_OUTAGE", "exchange_outage"),
        ("DUPLICATE_CLIENT_ORDER_ID", "duplicate_client_order_id"),
    ],
)
def test_exchange_reject_codes_normalize_to_stable_reason_taxonomy(raw_code, reason_code):
    assert reject_reason_from_exchange_code(raw_code) == reason_code


def test_exchange_reject_code_taxonomy_fails_closed_on_unknown_code():
    with pytest.raises(ValueError, match="unknown exchange reject code"):
        reject_reason_from_exchange_code("SOMETHING_NEW")


def test_exchange_reject_event_contract_fails_closed_on_missing_identity_and_stale_timestamp():
    with pytest.raises(ValueError, match="venue"):
        build_exchange_reject_event(
            venue="",
            symbol="BTCUSDT",
            client_order_id="intent-btc",
            raw_code="RATE_LIMIT",
            generated_at="2026-05-17T10:00:00Z",
            max_age_seconds=60,
            now="2026-05-17T10:00:10Z",
        )

    with pytest.raises(ValueError, match="stale generated_at"):
        build_exchange_reject_event(
            venue="binance_futures_testnet",
            symbol="BTCUSDT",
            client_order_id="intent-btc",
            raw_code="RATE_LIMIT",
            generated_at="2026-05-17T10:00:00Z",
            max_age_seconds=60,
            now="2026-05-17T10:02:00Z",
        )


def test_validated_order_preview_fails_closed_on_missing_symbol():
    order = fake_order_intent()
    order.symbol = ""

    with pytest.raises(ValueError, match="symbol"):
        build_validated_order_preview(
            order,
            exchange_metadata=fake_exchange_metadata(),
            allowlist=["BTCUSDT"],
            max_order_notional_usdt=1000,
            submission_enabled=False,
            preview_source="accepted_signal",
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("qty", -0.01),
        ("entry_price", float("nan")),
    ],
)
def test_validated_order_preview_fails_closed_on_invalid_order_numeric_values(field_name, value):
    order = fake_order_intent()
    setattr(order, field_name, value)

    with pytest.raises(ValueError, match=field_name):
        build_validated_order_preview(
            order,
            exchange_metadata=fake_exchange_metadata(),
            allowlist=["BTCUSDT"],
            max_order_notional_usdt=1000,
            submission_enabled=False,
            preview_source="accepted_signal",
        )
