from trading_system.app import main as main_module
from trading_system.app.storage.state_store import RuntimeStateV2


def test_testnet_existing_position_blocks_new_entry_even_when_signal_fingerprint_changes():
    state = RuntimeStateV2.empty()
    state.positions["BTCUSDT"] = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.0256,
        "status": "OPEN",
        "intent_id": "intent-old-fingerprint",
        "tracked_from_intent": True,
    }

    blocked = main_module._testnet_existing_position_entry_block(state, "BTCUSDT")

    assert blocked == {
        "status": "SKIPPED",
        "reason": "testnet_existing_position_open",
        "existing_intent_id": "intent-old-fingerprint",
        "existing_qty": 0.0256,
    }


def test_testnet_existing_position_block_ignores_flat_or_closed_positions():
    state = RuntimeStateV2.empty()
    state.positions["BTCUSDT"] = {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.0, "status": "CLOSED"}

    assert main_module._testnet_existing_position_entry_block(state, "BTCUSDT") is None
