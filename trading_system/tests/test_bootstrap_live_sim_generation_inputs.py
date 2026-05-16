from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_system.app.runtime_paths import build_runtime_paths
from trading_system.bootstrap_live_sim_generation_inputs import bootstrap_live_sim_generation_inputs, main
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


def _write_legacy_artifacts(root: Path) -> None:
    _write_json(root / "runtime_state.json", _legacy_state())
    _write_json(root / "account_snapshot.json", _legacy_account())
    _write_json(root / "market_context.json", _legacy_market())
    _write_json(root / "derivatives_snapshot.json", _legacy_derivatives())
    _write_jsonl(root / "paper_trades.jsonl", _legacy_trades())


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
