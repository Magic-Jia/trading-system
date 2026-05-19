from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.bootstrap_live_sim_generation_inputs import (
    _execution_calibration_rows,
    bootstrap_live_sim_generation_inputs,
    main,
)
from trading_system.scheduled_live_sim_generation import run_scheduled_generation


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _legacy_state() -> dict[str, object]:
    return {
        "execution_mode": "paper",
        "latest_candidates": [{"engine": "trend", "symbol": "BTCUSDT", "score": 0.91}],
        "latest_allocations": [
            {
                "engine": "trend",
                "symbol": "BTCUSDT",
                "status": "ACCEPTED",
                "execution": {"status": "FILLED", "intent_id": "paper-intent-001"},
            }
        ],
        "paper_trading": {
            "mode": "paper",
            "emitted_count": 4,
            "ledger_event_count": 4,
            "intents": [{"symbol": "BTCUSDT", "status": "FILLED", "intent_id": "paper-intent-001"}],
        },
    }


def _legacy_account() -> dict[str, object]:
    return {
        "as_of": "2026-05-16T10:00:00Z",
        "equity": 10000.0,
        "available_balance": 9000.0,
        "open_positions": [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": 0.5,
                "entry_price": 100.0,
                "mark_price": 101.0,
                "unrealized_pnl": 0.5,
                "notional": 50.5,
            }
        ],
        "open_orders": [],
        "meta": {"account_type": "paper"},
    }


def _legacy_exchange_account() -> dict[str, object]:
    account = _legacy_account()
    account["futures"] = {
        "total_margin_balance": 10000.0,
        "total_wallet_balance": 9500.0,
        "available_balance": 9000.0,
        "positions": [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "amt": 0.5,
                "entry": 100.0,
                "mark": 101.0,
                "upl": 0.5,
                "notional": 50.5,
                "roi_pct": 1.0,
                "leverage": 2,
                "liquidation_price": "0",
            }
        ]
    }
    return account


def _legacy_exchange_account_without_top_level_equity() -> dict[str, object]:
    account = _legacy_exchange_account()
    del account["equity"]
    return account


def _legacy_market() -> dict[str, object]:
    return {
        "as_of": "2026-05-16T10:00:00Z",
        "symbols": {
            "BTCUSDT": {
                "daily": {
                    "close": 101.0,
                    "volume_usdt_24h": 1000000.0,
                }
            }
        },
    }


def _legacy_derivatives() -> dict[str, object]:
    return {
        "as_of": "2026-05-16T10:00:00Z",
        "rows": [{"symbol": "BTCUSDT", "funding_rate": 0.0, "open_interest_usdt": 1000000.0}],
    }


def _legacy_trades() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, (symbol, maker_taker, status, filled_qty, cancel_reason) in enumerate(
        [
            ("BTCUSDT", "maker", "filled", 1.0, None),
            ("ETHUSDT", "taker", "filled", 1.0, None),
            ("SOLUSDT", "maker", "partially_filled", 0.5, None),
            ("BNBUSDT", "maker", "rejected", 0.0, "post_only_reject"),
        ]
    ):
        second = index * 10
        base_time = f"2026-05-16T10:00:{second:02d}Z"
        row = {
            "symbol": symbol,
            "side": "buy",
            "intended_limit_price": 100.0,
            "signal_at": base_time,
            "decision_at": f"2026-05-16T10:00:{second + 1:02d}Z",
            "submitted_at": f"2026-05-16T10:00:{second + 2:02d}Z",
            "exchange_ack_at": f"2026-05-16T10:00:{second + 3:02d}Z",
            "first_fill_at": f"2026-05-16T10:00:{second + 4:02d}Z" if filled_qty else None,
            "last_fill_at": f"2026-05-16T10:00:{second + 5:02d}Z" if filled_qty else None,
            "cancel_ack_at": f"2026-05-16T10:00:{second + 5:02d}Z" if cancel_reason else None,
            "requested_qty": 1.0,
            "filled_qty": filled_qty,
            "filled_notional": filled_qty * 100.0 if filled_qty else None,
            "status": status,
            "maker_taker": maker_taker,
            "slippage_bps": 2.0 if filled_qty else None,
            "adverse_selection_bps": 1.0 if filled_qty else None,
            "fees": 0.01 if filled_qty else 0.0,
            "funding": 0.0,
            "cancel_reason": cancel_reason,
        }
        rows.append(row)
    return rows


def _legacy_recommendation_only_trades() -> list[dict[str, object]]:
    return [
        {
            "recorded_at": "2026-05-16T10:00:00Z",
            "symbol": "BTCUSDT",
            "action": "BUY",
            "recommendation": "open_paper_position",
            "confidence": 0.72,
        },
        {
            "recorded_at": "2026-05-16T10:00:30Z",
            "symbol": "ETHUSDT",
            "action": "HOLD",
            "recommendation": "keep_watch",
            "confidence": 0.61,
        },
    ]


def _execution_calibration_chain() -> list[dict[str, object]]:
    base = {
        "intent_id": "intent-1",
        "order_id": "order-1",
        "trade_id": "trade-1",
        "position_id": "pos-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "quantity": 0.1,
        "price": 100.0,
        "ref_price": 99.5,
        "maker_taker": "maker",
        "fee": 0.01,
        "funding": 0.0,
        "setup_type": "TREND",
    }
    return [
        {**base, "stage": "signal", "status": "accepted", "occurred_at": "2026-05-16T10:00:00Z"},
        {**base, "stage": "order_intent", "status": "created", "occurred_at": "2026-05-16T10:00:01Z"},
        {**base, "stage": "risk_check", "status": "passed", "occurred_at": "2026-05-16T10:00:02Z"},
        {**base, "stage": "submit", "status": "submitted", "occurred_at": "2026-05-16T10:00:03Z"},
        {**base, "stage": "exchange_ack", "status": "acknowledged", "occurred_at": "2026-05-16T10:00:04Z"},
        {**base, "stage": "fill", "status": "filled", "occurred_at": "2026-05-16T10:00:05Z"},
        {**base, "stage": "position_reconcile", "status": "reconciled", "occurred_at": "2026-05-16T10:00:06Z"},
    ]


def _execution_ledger_event() -> dict[str, object]:
    return {
        "event_type": "paper_fill",
        "recorded_at": "2026-05-16T10:00:05Z",
        "intent_id": "intent-1",
        "symbol": "BTCUSDT",
        "order": {"intent_id": "intent-1", "symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0},
        "result": {"order_id": "order-1", "trade_id": "trade-1", "status": "FILLED", "qty": 0.1, "price": 100.0},
        "position_update": {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0},
    }


def _mixed_stale_and_fresh_execution_calibration_chain() -> list[dict[str, object]]:
    stale = _execution_calibration_chain()
    fresh = []
    for row in _execution_calibration_chain():
        row = dict(row)
        row["intent_id"] = "intent-2"
        row["order_id"] = "order-2"
        row["trade_id"] = "trade-2"
        row["position_id"] = "pos-2"
        second = int(str(row["occurred_at"])[17:19])
        row["occurred_at"] = f"2026-05-16T11:00:{second:02d}Z"
        fresh.append(row)
    return [*stale, *fresh]


def _four_execution_calibration_chains() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(4):
        for row in _execution_calibration_chain():
            row = dict(row)
            row["intent_id"] = f"intent-{index + 1}"
            row["order_id"] = f"order-{index + 1}"
            row["trade_id"] = f"trade-{index + 1}"
            row["position_id"] = f"pos-{index + 1}"
            minute = 56 + index
            second = int(str(row["occurred_at"])[17:19])
            row["occurred_at"] = f"2026-05-16T09:{minute:02d}:{second:02d}Z"
            rows.append(row)
    return rows


def _fresh_execution_ledger_events() -> list[dict[str, object]]:
    first = _execution_ledger_event()
    second = dict(first)
    second["intent_id"] = "intent-2"
    second["recorded_at"] = "2026-05-16T11:00:05Z"
    second["order"] = {"intent_id": "intent-2", "symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0}
    second["result"] = {"order_id": "order-2", "trade_id": "trade-2", "status": "FILLED", "qty": 0.1, "price": 100.0}
    return [first, second]


def _four_execution_ledger_events() -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index in range(4):
        event = dict(_execution_ledger_event())
        intent_id = f"intent-{index + 1}"
        minute = 56 + index
        event["intent_id"] = intent_id
        event["recorded_at"] = f"2026-05-16T09:{minute:02d}:05Z"
        event["order"] = {"intent_id": intent_id, "symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0}
        event["result"] = {
            "order_id": f"order-{index + 1}",
            "trade_id": f"trade-{index + 1}",
            "status": "FILLED",
            "qty": 0.1,
            "price": 100.0,
        }
        event["position_update"] = {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.1, "entry_price": 100.0}
        events.append(event)
    return events


def _write_legacy_artifacts(root: Path) -> None:
    _write_json(root / "runtime_state.json", _legacy_state())
    _write_json(root / "account_snapshot.json", _legacy_account())
    _write_json(root / "market_context.json", _legacy_market())
    _write_json(root / "derivatives_snapshot.json", _legacy_derivatives())
    _write_jsonl(root / "paper_trades.jsonl", _legacy_trades())


def test_bootstrap_uses_existing_independent_source_mid_for_manifest_prices(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    _write_json(
        paths.optimization_dir / "local_independent_source_snapshot.json",
        {
            "schema_version": "local_independent_source_snapshot.v1",
            "source_id": "coinbase_exchange_public_ticker",
            "generated_at": "2026-05-16T10:00:30Z",
            "observations": [
                {
                    "symbol": "BTCUSDT",
                    "source_symbol": "BTC-USDT",
                    "mid_price": 101.25,
                    "observed_at": "2026-05-16T10:00:30Z",
                }
            ],
        },
    )

    bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    manifest = json.loads((paths.optimization_dir / "paper_live_sim_evidence_manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text(encoding="utf-8"))
    payloads_by_stage = {stage["stage"]: stage["payload"] for stage in manifest["stages"]}

    assert payloads_by_stage["order_intent"]["limit_price"] == 101.25
    assert payloads_by_stage["risk_check"]["notional"] == 101.25
    assert payloads_by_stage["fill"]["fill_price"] == 101.25
    assert metadata["reference_price"] == {"source": "local_independent_source_snapshot", "symbol": "BTCUSDT", "price": 101.25}


def test_bootstrap_writes_required_inputs_from_legacy_local_artifacts(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    expected_files = {
        "paper_live_sim_evidence_manifest.json",
        "passive_order_calibration_records.jsonl",
        "tca_assumptions.json",
        "paper_live_shadow_drift_contract.json",
        "runtime_safety_gate.json",
    }
    assert result["status"] == "ok"
    assert expected_files <= set(result["generated_artifacts"])
    for filename in expected_files:
        assert (paths.optimization_dir / filename).exists()
    assert json.loads(paths.account_snapshot_file.read_text()) == _legacy_account()
    assert json.loads(paths.market_context_file.read_text()) == _legacy_market()
    assert json.loads(paths.derivatives_snapshot_file.read_text()) == _legacy_derivatives()


def test_bootstrap_accepts_same_bucket_as_source_and_destination(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    paths.bucket_dir.mkdir(parents=True)
    _write_json(paths.bucket_dir / "runtime_state.json", _legacy_state())
    _write_json(paths.bucket_dir / "account_snapshot.json", _legacy_account())
    _write_json(paths.bucket_dir / "market_context.json", _legacy_market())
    _write_json(paths.bucket_dir / "derivatives_snapshot.json", _legacy_derivatives())
    _write_jsonl(paths.bucket_dir / "execution_log.jsonl", _execution_calibration_chain())
    _write_jsonl(paths.bucket_dir / "paper_ledger.jsonl", [_execution_ledger_event()])

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=paths.bucket_dir,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    assert result["status"] == "ok"
    assert (paths.optimization_dir / "paper_live_sim_evidence_manifest.json").exists()


def test_bootstrap_filters_stale_lifecycle_calibration_rows_when_fresh_rows_remain(tmp_path: Path) -> None:
    source_root = tmp_path / "bucket-source"
    runtime_root = tmp_path / "runtime"
    _write_json(source_root / "runtime_state.json", _legacy_state())
    _write_json(source_root / "account_snapshot.json", {**_legacy_account(), "as_of": "2026-05-16T11:00:00Z"})
    _write_json(source_root / "market_context.json", {**_legacy_market(), "as_of": "2026-05-16T11:00:00Z"})
    _write_json(source_root / "derivatives_snapshot.json", {**_legacy_derivatives(), "as_of": "2026-05-16T11:00:00Z"})
    _write_jsonl(source_root / "execution_log.jsonl", _mixed_stale_and_fresh_execution_calibration_chain())
    _write_jsonl(source_root / "paper_ledger.jsonl", _fresh_execution_ledger_events())

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=source_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T11:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    records = [json.loads(line) for line in (paths.optimization_dir / "passive_order_calibration_records.jsonl").read_text().splitlines()]
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text())
    assert result["status"] == "ok"
    assert len(records) == 1
    assert records[0]["signal_at"] == "2026-05-16T11:00:00Z"
    assert metadata["calibration_records"] == {
        "available": True,
        "record_count": 1,
        "source": "execution_lifecycle",
        "dropped_stale_record_count": 1,
    }
    assert "stale_calibration_records_dropped" in metadata["quality_reasons"]


def test_bootstrap_preserves_existing_full_bucket_native_calibration_file_when_fresh_filter_is_smaller(tmp_path: Path) -> None:
    source_root = tmp_path / "bucket-source"
    runtime_root = tmp_path / "runtime"
    _write_json(source_root / "runtime_state.json", _legacy_state())
    _write_json(source_root / "account_snapshot.json", {**_legacy_account(), "as_of": "2026-05-16T10:00:30Z"})
    _write_json(source_root / "market_context.json", {**_legacy_market(), "as_of": "2026-05-16T10:00:30Z"})
    _write_json(source_root / "derivatives_snapshot.json", {**_legacy_derivatives(), "as_of": "2026-05-16T10:00:30Z"})
    _write_jsonl(source_root / "execution_log.jsonl", _four_execution_calibration_chains())
    _write_jsonl(source_root / "paper_ledger.jsonl", _four_execution_ledger_events())

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    existing_records = _execution_calibration_rows(source_root)
    assert len(existing_records) == 4
    _write_jsonl(paths.optimization_dir / "passive_order_calibration_records.jsonl", existing_records)

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=source_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    records = [json.loads(line) for line in (paths.optimization_dir / "passive_order_calibration_records.jsonl").read_text().splitlines()]
    scheduled = run_scheduled_generation(
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=600,
        min_tca_samples=4,
        max_p95_slippage_bps=5.0,
    )
    tca = json.loads((paths.optimization_dir / "tca_calibration_report.json").read_text())

    assert result["status"] == "ok"
    assert len(records) == 4
    assert scheduled["status"] == "ok"
    assert tca["sample_count"] == 4


def test_bootstrap_accepts_bucket_native_runtime_boolean_health_fields(tmp_path: Path) -> None:
    source_root = tmp_path / "bucket-source"
    runtime_root = tmp_path / "runtime"
    state = _legacy_state()
    state["latest_universes"] = {"major_universe": [{"symbol": "BTCUSDT", "listing_age_ok": True}]}
    _write_json(source_root / "runtime_state.json", state)
    _write_json(source_root / "account_snapshot.json", _legacy_account())
    _write_json(source_root / "market_context.json", _legacy_market())
    _write_json(source_root / "derivatives_snapshot.json", _legacy_derivatives())
    _write_jsonl(source_root / "execution_log.jsonl", _execution_calibration_chain())
    _write_jsonl(source_root / "paper_ledger.jsonl", [_execution_ledger_event()])

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=source_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    assert result["status"] == "ok"


def test_bootstrap_accepts_bucket_native_execution_logs_without_paper_trades(tmp_path: Path) -> None:
    source_root = tmp_path / "bucket-source"
    runtime_root = tmp_path / "runtime"
    _write_json(source_root / "runtime_state.json", _legacy_state())
    _write_json(source_root / "account_snapshot.json", _legacy_account())
    _write_json(source_root / "market_context.json", _legacy_market())
    _write_json(source_root / "derivatives_snapshot.json", _legacy_derivatives())
    _write_jsonl(source_root / "execution_log.jsonl", _execution_calibration_chain())
    _write_jsonl(source_root / "paper_ledger.jsonl", [_execution_ledger_event()])

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=source_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    records = [json.loads(line) for line in (paths.optimization_dir / "passive_order_calibration_records.jsonl").read_text().splitlines()]
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text())
    manifest = json.loads((paths.optimization_dir / "paper_live_sim_evidence_manifest.json").read_text())
    assert result["status"] == "ok"
    assert len(records) == 1
    assert records[0]["symbol"] == "BTCUSDT"
    assert records[0]["terminal_status"] == "filled"
    assert metadata["calibration_records"] == {
        "available": True,
        "record_count": 1,
        "source": "execution_lifecycle",
    }
    assert manifest["generated_at"] == "2026-05-16T10:01:00Z"


def test_bootstrap_outputs_allow_scheduled_generation_to_run(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)

    bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    result = run_scheduled_generation(
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
        min_tca_samples=4,
        max_p95_slippage_bps=5.0,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    gate = json.loads((paths.optimization_dir / "daily_quality_gate_report.json").read_text())
    assert result["status"] == "ok"
    assert gate["decision"] == "pass_for_continued_paper"


def test_bootstrap_legacy_recommendation_only_paper_trades_emit_missing_calibration_hold(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    _write_jsonl(legacy_root / "paper_trades.jsonl", _legacy_recommendation_only_trades())

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    marker = json.loads((paths.optimization_dir / "calibration_records_unavailable.json").read_text())
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text())
    calibration_records = (paths.optimization_dir / "passive_order_calibration_records.jsonl").read_text()
    assert result["status"] == "ok"
    assert marker["reason"] == "calibration_records_unavailable"
    assert marker["source_record_count"] == 2
    assert metadata["calibration_records"]["available"] is False
    assert metadata["calibration_records"]["reason"] == "calibration_records_unavailable"
    assert calibration_records == ""

    scheduled = run_scheduled_generation(
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
        min_tca_samples=4,
        max_p95_slippage_bps=5.0,
    )

    gate = json.loads((paths.optimization_dir / "daily_quality_gate_report.json").read_text())
    assert scheduled["status"] == "ok"
    assert scheduled["daily_quality_gate_decision"] == "hold_for_review"
    assert "tca_calibration_report" not in scheduled["generated_artifacts"]
    assert not (paths.optimization_dir / "tca_calibration_report.json").exists()
    assert gate["decision"] == "hold_for_review"
    assert gate["reasons"] == ["calibration_records_unavailable", "insufficient_sample_size"]
    assert gate["inputs"]["tca"]["sample_size"] == 0
    assert gate["inputs"]["tca"]["availability_reason"] == "calibration_records_unavailable"


def test_bootstrap_prefers_execution_lifecycle_calibration_over_recommendation_only_paper_trades(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    _write_jsonl(legacy_root / "paper_trades.jsonl", _legacy_recommendation_only_trades())
    _write_jsonl(legacy_root / "execution_log.jsonl", _execution_calibration_chain())
    _write_jsonl(legacy_root / "paper_ledger.jsonl", [_execution_ledger_event()])
    stale_unavailable = runtime_root / "paper" / "paper" / "optimization" / "calibration_records_unavailable.json"
    _write_json(stale_unavailable, {"reason": "calibration_records_unavailable"})

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text())
    records = (paths.optimization_dir / "passive_order_calibration_records.jsonl").read_text(encoding="utf-8")
    assert result["status"] == "ok"
    assert metadata["calibration_records"]["available"] is True
    assert metadata["calibration_records"]["source"] == "execution_lifecycle"
    assert "BTCUSDT" in records
    assert not (paths.optimization_dir / "calibration_records_unavailable.json").exists()


def test_bootstrap_fails_closed_for_malformed_calibration_like_paper_trade(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    malformed = _legacy_trades()[0]
    del malformed["signal_at"]
    _write_jsonl(legacy_root / "paper_trades.jsonl", [malformed])

    with pytest.raises(ValueError, match="calibration record missing signal_at"):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_records_missing_account_snapshot_as_of_without_fabricating_source_time(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_account()
    del account["as_of"]
    _write_json(legacy_root / "account_snapshot.json", account)

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text())
    runtime_account = json.loads(paths.account_snapshot_file.read_text())
    assert result["status"] == "ok"
    assert result["generated_at"] == "2026-05-16T10:01:00Z"
    assert "as_of" not in runtime_account
    assert metadata["generated_at"] == "2026-05-16T10:01:00Z"
    assert metadata["source_timestamp_quality"] == {
        "account_snapshot.json": {
            "as_of_present": False,
            "freshness_met": False,
            "reason": "account_snapshot_as_of_missing",
        },
        "market_context.json": {
            "as_of": "2026-05-16T10:00:00Z",
            "as_of_present": True,
            "freshness_met": True,
        },
        "derivatives_snapshot.json": {
            "as_of": "2026-05-16T10:00:00Z",
            "as_of_present": True,
            "freshness_met": True,
        },
    }
    assert metadata["quality_reasons"] == ["account_snapshot_as_of_missing"]


def test_missing_account_snapshot_as_of_outputs_scheduled_hold_reason(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_account()
    del account["as_of"]
    _write_json(legacy_root / "account_snapshot.json", account)

    bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    result = run_scheduled_generation(
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
        min_tca_samples=4,
        max_p95_slippage_bps=5.0,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    gate = json.loads((paths.optimization_dir / "daily_quality_gate_report.json").read_text())
    assert result["status"] == "ok"
    assert result["daily_quality_gate_decision"] == "hold_for_review"
    assert gate["decision"] == "hold_for_review"
    assert gate["reasons"] == ["data_freshness_violation"]
    assert gate["inputs"]["freshness"]["items"]["account_snapshot"]["fresh"] is False
    assert gate["inputs"]["freshness"]["items"]["account_snapshot"]["reason"] == "account_snapshot_as_of_missing"
    assert gate["checks"]["data_freshness_met"] is False


@pytest.mark.parametrize("raw_as_of", ["2026-05-16T10:00:00+00:00", "2026-05-16T10:00:00", True, 123])
def test_bootstrap_rejects_malformed_account_snapshot_as_of_when_present(tmp_path: Path, raw_as_of: object) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_account()
    account["as_of"] = raw_as_of
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(ValueError, match=r"account_snapshot\.json\.as_of must be a canonical UTC timestamp"):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_accepts_legacy_exchange_decimal_string_liquidation_price(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account()
    _write_json(legacy_root / "account_snapshot.json", account)

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    metadata_path = paths.optimization_dir / "bootstrap_input_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["generated_artifacts"]["bootstrap_input_metadata.json"] == str(metadata_path)
    assert json.loads(paths.account_snapshot_file.read_text()) == account
    assert metadata["accepted_decimal_string_fields"] == [
        {
            "field_path": "account_snapshot.json.futures.positions[0].liquidation_price",
            "source_type": "str",
            "decimal_value": "0",
            "normalized_type": "decimal",
        }
    ]


def test_bootstrap_derives_missing_top_level_equity_from_futures_total_margin_balance(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    runtime_root = tmp_path / "runtime"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account_without_top_level_equity()
    _write_json(legacy_root / "account_snapshot.json", account)

    result = bootstrap_live_sim_generation_inputs(
        legacy_root=legacy_root,
        mode="paper",
        runtime_root=runtime_root,
        runtime_env="paper",
        generated_at="2026-05-16T10:01:00Z",
        max_evidence_age_seconds=120,
    )

    paths = build_runtime_paths("paper", runtime_root=runtime_root, runtime_env="paper")
    runtime_account = json.loads(paths.account_snapshot_file.read_text(encoding="utf-8"))
    metadata = json.loads((paths.optimization_dir / "bootstrap_input_metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((paths.optimization_dir / "paper_live_sim_evidence_manifest.json").read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert runtime_account["equity"] == 10000.0
    assert runtime_account["meta"]["equity_provenance"] == "account_equity_derived_from_futures_total_margin_balance"
    assert runtime_account["meta"]["equity_source_field"] == "futures.total_margin_balance"
    assert metadata["account_equity"] == {
        "field": "equity",
        "source_field": "futures.total_margin_balance",
        "reason": "account_equity_derived_from_futures_total_margin_balance",
        "derived": True,
    }
    paper_snapshot = next(stage for stage in manifest["stages"] if stage["stage"] == "paper_snapshot")
    assert paper_snapshot["payload"]["equity"] == 10000.0


@pytest.mark.parametrize(
    ("raw_value", "expected_message"),
    [
        (True, "account_snapshot.json.futures.total_margin_balance must be numeric, not boolean"),
        ("10000.0", "account_snapshot.json.futures.total_margin_balance must be numeric"),
        (float("nan"), "account_snapshot.json.futures.total_margin_balance must be finite"),
        (0.0, "account_snapshot.json.futures.total_margin_balance must be greater than zero"),
        (-1.0, "account_snapshot.json.futures.total_margin_balance must be greater than zero"),
    ],
)
def test_bootstrap_rejects_malformed_derived_account_equity_source(
    tmp_path: Path, raw_value: object, expected_message: str
) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account_without_top_level_equity()
    futures = account["futures"]
    assert isinstance(futures, dict)
    futures["total_margin_balance"] = raw_value
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(ValueError, match=expected_message):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_rejects_missing_derived_account_equity_source(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account_without_top_level_equity()
    futures = account["futures"]
    assert isinstance(futures, dict)
    del futures["total_margin_balance"]
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(ValueError, match=r"account_snapshot\.json\.equity must be numeric"):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


@pytest.mark.parametrize(
    "raw_value",
    ["", "   ", "nan", "NaN", "inf", "-Infinity", "1,2", "1 2", "abc", "+1", "01", "1.", ".1", "1e3"],
)
def test_bootstrap_rejects_malformed_legacy_exchange_decimal_string_liquidation_price(
    tmp_path: Path, raw_value: str
) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account()
    futures = account["futures"]
    assert isinstance(futures, dict)
    positions = futures["positions"]
    assert isinstance(positions, list)
    position = positions[0]
    assert isinstance(position, dict)
    position["liquidation_price"] = raw_value
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(
        ValueError, match=r"account_snapshot\.json\.futures\.positions\[0\]\.liquidation_price must be a canonical decimal string"
    ):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_rejects_bool_legacy_exchange_decimal_string_liquidation_price(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_exchange_account()
    futures = account["futures"]
    assert isinstance(futures, dict)
    positions = futures["positions"]
    assert isinstance(positions, list)
    position = positions[0]
    assert isinstance(position, dict)
    position["liquidation_price"] = False
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(
        ValueError, match=r"account_snapshot\.json\.futures\.positions\[0\]\.liquidation_price must be numeric, not boolean"
    ):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_fails_closed_for_bool_numeric_legacy_input(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    _write_legacy_artifacts(legacy_root)
    account = _legacy_account()
    account["equity"] = True
    _write_json(legacy_root / "account_snapshot.json", account)

    with pytest.raises(ValueError, match="account_snapshot.json.equity must be numeric, not boolean"):
        bootstrap_live_sim_generation_inputs(
            legacy_root=legacy_root,
            mode="paper",
            runtime_root=tmp_path / "runtime",
            runtime_env="paper",
            generated_at="2026-05-16T10:01:00Z",
            max_evidence_age_seconds=120,
        )


def test_bootstrap_cli_fails_closed_without_real_exchange_side_effects(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    _write_json(legacy_root / "runtime_state.json", _legacy_state())

    exit_code = main(
        [
            "--legacy-root",
            str(legacy_root),
            "--mode",
            "paper",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--runtime-env",
            "paper",
            "--generated-at",
            "2026-05-16T10:01:00Z",
        ]
    )

    paths = build_runtime_paths("paper", runtime_root=tmp_path / "runtime", runtime_env="paper")
    failure = json.loads((paths.optimization_dir / "bootstrap_live_sim_generation_inputs_error.json").read_text())
    assert exit_code == 1
    assert failure["status"] == "fail_closed"
    assert failure["error_type"] == "FileNotFoundError"
    assert "account_snapshot.json" in failure["error_message"]
