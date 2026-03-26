from trading_system.app.execution.paper_executor import PaperExecutor
from trading_system.app.execution.paper_ledger import PaperLedger
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


def test_paper_executor_translates_order_intent_into_fill_and_ledger_event(tmp_path):
    ledger = PaperLedger(tmp_path / "paper_ledger.jsonl")
    executor = PaperExecutor(ledger=ledger)
    state = RuntimeStateV2.empty()

    result = executor.execute(_sample_order(), state)

    assert result["mode"] == "paper"
    assert result["result"] == "FILLED"
    assert result["ledger_event"]["event_type"] == "paper_fill"
    assert result["ledger_event"]["intent_id"] == "intent-btc-long"
    assert ledger.path.exists()


def test_paper_executor_records_position_updates_for_filled_order(tmp_path):
    executor = PaperExecutor(ledger=PaperLedger(tmp_path / "paper_ledger.jsonl"))
    state = RuntimeStateV2.empty()
    order = _sample_order()

    result = executor.execute(order, state)

    assert order.status == "FILLED"
    assert state.active_orders[order.intent_id]["status"] == "FILLED"
    assert state.positions[order.symbol] == result["position_update"]
    assert result["position_update"]["status"] == "OPEN"
    assert result["position_update"]["last_synced_from"] == "executed_intent"


def test_paper_ledger_stores_replayable_result_by_intent_id(tmp_path):
    ledger = PaperLedger(tmp_path / "paper_ledger.jsonl")
    executor = PaperExecutor(ledger=ledger)
    state = RuntimeStateV2.empty()
    order = _sample_order()

    executor.execute(order, state)

    replay = ledger.load_replay_result(order.intent_id)

    assert replay == {
        "status": "FILLED",
        "intent_id": order.intent_id,
    }
