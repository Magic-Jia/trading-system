from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_system import paper_snapshots as paper_snapshots_module
from trading_system import run_cycle as run_cycle_module
from trading_system.app import main as main_module
from trading_system.app.risk.validator import ValidationResult
from trading_system.app.types import AllocationDecision


def test_run_cycle_prepares_runtime_bucket_calls_main_and_writes_latest_summary(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    expected_bucket = runtime_root / "paper" / "testnet"
    expected_state_file = expected_bucket / "runtime_state.json"
    captured: dict[str, Path | str] = {}

    def fake_main() -> None:
        captured["mode"] = os.environ["TRADING_EXECUTION_MODE"]
        captured["runtime_env"] = os.environ["TRADING_RUNTIME_ENV"]
        captured["state_file"] = Path(os.environ["TRADING_STATE_FILE"])
        captured["state_file"].write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [{"symbol": "BTCUSDT"}],
                    "latest_allocations": [{"symbol": "BTCUSDT", "status": "ACCEPTED"}],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="testnet")

    latest_path = expected_bucket / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))

    assert expected_bucket.is_dir()
    assert latest_path.exists()
    assert not (expected_bucket / "error.json").exists()
    assert captured == {
        "mode": "paper",
        "runtime_env": "testnet",
        "state_file": expected_state_file,
    }
    assert summary == latest
    assert latest["status"] == "ok"
    assert latest["mode"] == "paper"
    assert latest["runtime_env"] == "testnet"
    assert latest["bucket_dir"] == str(expected_bucket)
    assert latest["state_file"] == str(expected_state_file)
    assert latest["execution_mode"] == "paper"
    assert latest["candidate_count"] == 1
    assert latest["allocation_count"] == 1
    assert "finished_at" in latest


def test_run_cycle_defaults_paper_mode_to_paper_runtime_bucket(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    expected_bucket = runtime_root / "paper" / "paper"
    expected_state_file = expected_bucket / "runtime_state.json"
    captured: dict[str, Path | str] = {}

    def fake_main() -> None:
        captured["mode"] = os.environ["TRADING_EXECUTION_MODE"]
        captured["runtime_env"] = os.environ["TRADING_RUNTIME_ENV"]
        captured["state_file"] = Path(os.environ["TRADING_STATE_FILE"])
        captured["state_file"].write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [],
                    "latest_allocations": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)
    monkeypatch.delenv("TRADING_RUNTIME_ENV", raising=False)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root)

    assert captured == {
        "mode": "paper",
        "runtime_env": "paper",
        "state_file": expected_state_file,
    }
    assert summary["bucket_dir"] == str(expected_bucket)
    assert summary["state_file"] == str(expected_state_file)


def test_run_cycle_auto_prepares_missing_paper_snapshots_in_bucket_before_main(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    bucket_dir = runtime_root / "paper" / "paper"
    state_file = bucket_dir / "runtime_state.json"
    captured: dict[str, object] = {}

    def fake_prepare(paths) -> None:
        captured["prepared_bucket"] = paths.bucket_dir
        paths.bucket_dir.mkdir(parents=True, exist_ok=True)
        (paths.bucket_dir / "account_snapshot.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "equity": 100000.0,
                    "available_balance": 100000.0,
                    "futures_wallet_balance": 100000.0,
                    "open_positions": [],
                    "open_orders": [],
                    "meta": {
                        "account_type": "paper",
                        "source": "paper_snapshot_bootstrap",
                    },
                }
            ),
            encoding="utf-8",
        )
        (paths.bucket_dir / "market_context.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "symbols": {
                        "BTCUSDT": {
                            "sector": "majors",
                            "liquidity_tier": "top",
                            "daily": {
                                "close": 64000.0,
                                "ema_20": 63500.0,
                                "ema_50": 62000.0,
                                "rsi": 60.0,
                                "atr_pct": 0.02,
                                "return_pct_7d": 0.03,
                                "volume_usdt_24h": 1000000000,
                            },
                            "4h": {
                                "close": 64000.0,
                                "ema_20": 63800.0,
                                "ema_50": 63000.0,
                                "rsi": 58.0,
                                "atr_pct": 0.01,
                                "return_pct_3d": 0.02,
                                "volume_usdt_24h": 1000000000,
                            },
                            "1h": {
                                "close": 64000.0,
                                "ema_20": 63900.0,
                                "ema_50": 63700.0,
                                "rsi": 56.0,
                                "atr_pct": 0.005,
                                "return_pct_24h": 0.01,
                                "volume_usdt_24h": 1000000000,
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (paths.bucket_dir / "derivatives_snapshot.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": 0.0001,
                            "open_interest_usdt": 1000000.0,
                            "open_interest_change_24h_pct": 0.02,
                            "mark_price_change_24h_pct": 0.01,
                            "taker_buy_sell_ratio": 1.05,
                            "basis_bps": 10.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def fake_main() -> None:
        captured["mode"] = os.environ["TRADING_EXECUTION_MODE"]
        captured["runtime_env"] = os.environ["TRADING_RUNTIME_ENV"]
        captured["state_file"] = Path(os.environ["TRADING_STATE_FILE"])
        captured["account_file"] = Path(os.environ["TRADING_ACCOUNT_SNAPSHOT_FILE"])
        captured["market_file"] = Path(os.environ["TRADING_MARKET_CONTEXT_FILE"])
        captured["derivatives_file"] = Path(os.environ["TRADING_DERIVATIVES_SNAPSHOT_FILE"])
        assert captured["account_file"] == bucket_dir / "account_snapshot.json"
        assert captured["market_file"] == bucket_dir / "market_context.json"
        assert captured["derivatives_file"] == bucket_dir / "derivatives_snapshot.json"
        assert Path(captured["account_file"]).exists()
        assert Path(captured["market_file"]).exists()
        assert Path(captured["derivatives_file"]).exists()
        captured["state_file"].write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [],
                    "latest_allocations": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(run_cycle_module, "prepare_paper_runtime_inputs", fake_prepare, raising=False)
    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="paper")

    assert captured["prepared_bucket"] == bucket_dir
    assert captured["mode"] == "paper"
    assert captured["runtime_env"] == "paper"
    assert captured["state_file"] == state_file
    assert summary["status"] == "ok"
    assert summary["bucket_dir"] == str(bucket_dir)
    assert (bucket_dir / "account_snapshot.json").exists()
    assert (bucket_dir / "market_context.json").exists()
    assert (bucket_dir / "derivatives_snapshot.json").exists()


def test_run_cycle_smoke_auto_prepares_paper_bucket_inputs_with_builtin_generators(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    bucket_dir = runtime_root / "paper" / "paper"
    state_file = bucket_dir / "runtime_state.json"
    captured: dict[str, object] = {}

    def _kline_rows(limit: int, *, start_close: float, step: float, quote_volume: float = 1000000.0):
        rows = []
        for index in range(limit):
            close = start_close + (index * step)
            rows.append(
                [
                    1710000000000 + (index * 3600000),
                    f"{close - 2.0:.8f}",
                    f"{close + 3.0:.8f}",
                    f"{close - 3.0:.8f}",
                    f"{close:.8f}",
                    "100.0",
                    1710003600000 + (index * 3600000),
                    f"{quote_volume:.8f}",
                    1000,
                    "50.0",
                    f"{quote_volume * 0.56:.8f}",
                    "0.0",
                ]
            )
        return rows

    def fake_public_get(base: str, path: str, params: dict[str, object] | None = None):
        params = params or {}
        symbol = str(params.get("symbol", "BTCUSDT"))
        if base == paper_snapshots_module.SPOT_BASE and path == "/api/v3/ticker/24hr":
            return {"symbol": symbol, "quoteVolume": "987654321.0"}
        if base == paper_snapshots_module.SPOT_BASE and path == "/api/v3/klines":
            interval = str(params["interval"])
            if interval == "1d":
                return _kline_rows(int(params["limit"]), start_close=60000.0, step=120.0)
            if interval == "4h":
                return _kline_rows(int(params["limit"]), start_close=62000.0, step=25.0)
            if interval == "1h":
                return _kline_rows(int(params["limit"]), start_close=63500.0, step=5.0)
        if base == paper_snapshots_module.FUTURES_BASE and path == "/fapi/v1/premiumIndex":
            return {"symbol": symbol, "markPrice": "64120.0", "indexPrice": "64080.0", "lastFundingRate": "0.0001"}
        if base == paper_snapshots_module.FUTURES_BASE and path == "/fapi/v1/ticker/24hr":
            return {"symbol": symbol, "priceChangePercent": "1.7"}
        if base == paper_snapshots_module.FUTURES_BASE and path == "/fapi/v1/openInterest":
            return {"symbol": symbol, "openInterest": "1000.0"}
        if base == paper_snapshots_module.FUTURES_BASE and path == "/futures/data/openInterestHist":
            return [
                {"sumOpenInterestValue": "1000000.0"},
                {"sumOpenInterestValue": "1025000.0"},
            ]
        if base == paper_snapshots_module.FUTURES_BASE and path == "/fapi/v1/klines":
            return _kline_rows(int(params["limit"]), start_close=64000.0, step=8.0, quote_volume=2000000.0)
        raise AssertionError(f"unexpected public_get call: base={base} path={path} params={params}")

    def fake_main() -> None:
        account_path = Path(os.environ["TRADING_ACCOUNT_SNAPSHOT_FILE"])
        market_path = Path(os.environ["TRADING_MARKET_CONTEXT_FILE"])
        derivatives_path = Path(os.environ["TRADING_DERIVATIVES_SNAPSHOT_FILE"])
        account = json.loads(account_path.read_text(encoding="utf-8"))
        market = json.loads(market_path.read_text(encoding="utf-8"))
        derivatives = json.loads(derivatives_path.read_text(encoding="utf-8"))
        captured["account_path"] = account_path
        captured["market_path"] = market_path
        captured["derivatives_path"] = derivatives_path
        captured["account_meta"] = account["meta"]
        captured["market_symbols"] = sorted(market["symbols"])
        captured["derivatives_symbols"] = [row["symbol"] for row in derivatives["rows"]]
        Path(os.environ["TRADING_STATE_FILE"]).write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [],
                    "latest_allocations": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(paper_snapshots_module, "public_get", fake_public_get)
    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)
    monkeypatch.setenv("TRADING_PAPER_SNAPSHOT_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("TRADING_PAPER_ACCOUNT_EQUITY", "25000")
    monkeypatch.setattr(main_module, "ACCOUNT_SNAPSHOT", tmp_path / "should-not-be-used" / "account_snapshot.json")
    monkeypatch.setattr(main_module, "MARKET_CONTEXT", tmp_path / "should-not-be-used" / "market_context.json")
    monkeypatch.setattr(main_module, "DERIVATIVES_SNAPSHOT", tmp_path / "should-not-be-used" / "derivatives_snapshot.json")

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="paper")

    assert summary["status"] == "ok"
    assert captured["account_path"] == bucket_dir / "account_snapshot.json"
    assert captured["market_path"] == bucket_dir / "market_context.json"
    assert captured["derivatives_path"] == bucket_dir / "derivatives_snapshot.json"
    assert captured["market_symbols"] == ["BTCUSDT"]
    assert captured["derivatives_symbols"] == ["BTCUSDT"]
    assert captured["account_meta"] == {
        "account_type": "paper",
        "source": "paper_snapshot_bootstrap",
        "snapshot_source": "paper_snapshot_bootstrap",
        "generated_at": json.loads((bucket_dir / "account_snapshot.json").read_text(encoding="utf-8"))["meta"][
            "generated_at"
        ],
    }
    assert json.loads((bucket_dir / "account_snapshot.json").read_text(encoding="utf-8"))["equity"] == 25000.0
    assert state_file.exists()
    assert not (tmp_path / "should-not-be-used").exists()


def test_run_cycle_fail_fast_when_paper_snapshot_generation_is_blocked(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    bucket_dir = runtime_root / "paper" / "paper"

    def fake_public_get(base: str, path: str, params: dict[str, object] | None = None):
        if path == "/api/v3/ticker/24hr":
            raise RuntimeError("network unavailable")
        raise AssertionError(f"unexpected public_get call during fail-fast test: {path}")

    monkeypatch.setattr(paper_snapshots_module, "public_get", fake_public_get)
    monkeypatch.setattr(
        run_cycle_module,
        "run_main",
        lambda: (_ for _ in ()).throw(AssertionError("run_main should not be called when snapshot generation fails")),
    )
    monkeypatch.setenv("TRADING_PAPER_SNAPSHOT_SYMBOLS", "BTCUSDT")

    with pytest.raises(RuntimeError, match="failed to prepare paper market_context.json: network unavailable"):
        run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="paper")

    latest = json.loads((bucket_dir / "latest.json").read_text(encoding="utf-8"))

    assert latest["status"] == "error"
    assert latest["error_type"] == "RuntimeError"
    assert "failed to prepare paper market_context.json: network unavailable" in latest["error_message"]
    assert (bucket_dir / "account_snapshot.json").exists()
    assert not (bucket_dir / "market_context.json").exists()
    assert not (bucket_dir / "derivatives_snapshot.json").exists()


def test_run_cycle_ignores_explicit_snapshot_env_overrides_and_pins_paper_inputs_to_bucket(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    bucket_dir = runtime_root / "paper" / "testnet"
    external_dir = tmp_path / "external-live-like"
    external_dir.mkdir()
    (external_dir / "account_snapshot.json").write_text('{"bad": true}', encoding="utf-8")
    (external_dir / "market_context.json").write_text('{"bad": true}', encoding="utf-8")
    (external_dir / "derivatives_snapshot.json").write_text('{"bad": true}', encoding="utf-8")
    captured: dict[str, Path] = {}

    def fake_prepare(paths) -> None:
        paths.bucket_dir.mkdir(parents=True, exist_ok=True)
        (paths.bucket_dir / "account_snapshot.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "equity": 100000.0,
                    "available_balance": 100000.0,
                    "futures_wallet_balance": 100000.0,
                    "open_positions": [],
                    "open_orders": [],
                    "meta": {"account_type": "paper", "source": "paper_snapshot_bootstrap"},
                }
            ),
            encoding="utf-8",
        )
        (paths.bucket_dir / "market_context.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "symbols": {
                        "BTCUSDT": {
                            "sector": "majors",
                            "liquidity_tier": "top",
                            "daily": {
                                "close": 64000.0,
                                "ema_20": 63500.0,
                                "ema_50": 62000.0,
                                "rsi": 60.0,
                                "atr_pct": 0.02,
                                "return_pct_7d": 0.03,
                                "volume_usdt_24h": 1000000000,
                            },
                            "4h": {
                                "close": 64000.0,
                                "ema_20": 63800.0,
                                "ema_50": 63000.0,
                                "rsi": 58.0,
                                "atr_pct": 0.01,
                                "return_pct_3d": 0.02,
                                "volume_usdt_24h": 1000000000,
                            },
                            "1h": {
                                "close": 64000.0,
                                "ema_20": 63900.0,
                                "ema_50": 63700.0,
                                "rsi": 56.0,
                                "atr_pct": 0.005,
                                "return_pct_24h": 0.01,
                                "volume_usdt_24h": 1000000000,
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (paths.bucket_dir / "derivatives_snapshot.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-03-30T00:00:00Z",
                    "schema_version": "v2",
                    "rows": [
                        {
                            "symbol": "BTCUSDT",
                            "funding_rate": 0.0001,
                            "open_interest_usdt": 1000000.0,
                            "open_interest_change_24h_pct": 0.02,
                            "mark_price_change_24h_pct": 0.01,
                            "taker_buy_sell_ratio": 1.05,
                            "basis_bps": 10.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def fake_main() -> None:
        captured["account_path"] = Path(os.environ["TRADING_ACCOUNT_SNAPSHOT_FILE"])
        captured["market_path"] = Path(os.environ["TRADING_MARKET_CONTEXT_FILE"])
        captured["derivatives_path"] = Path(os.environ["TRADING_DERIVATIVES_SNAPSHOT_FILE"])
        Path(os.environ["TRADING_STATE_FILE"]).write_text(
            json.dumps(
                {
                    "execution_mode": "paper",
                    "latest_candidates": [],
                    "latest_allocations": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv("TRADING_ACCOUNT_SNAPSHOT_FILE", str(external_dir / "account_snapshot.json"))
    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(external_dir / "market_context.json"))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(external_dir / "derivatives_snapshot.json"))
    monkeypatch.setattr(run_cycle_module, "prepare_paper_runtime_inputs", fake_prepare)
    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="testnet")

    assert summary["status"] == "ok"
    assert captured["account_path"] == bucket_dir / "account_snapshot.json"
    assert captured["market_path"] == bucket_dir / "market_context.json"
    assert captured["derivatives_path"] == bucket_dir / "derivatives_snapshot.json"


def test_run_cycle_dry_run_still_falls_back_to_default_snapshot_data_when_runtime_bucket_is_empty(
    monkeypatch, tmp_path, load_fixture
):
    base_dir = tmp_path / "smoke"
    bucket_dir = base_dir / "data" / "runtime" / "dry-run" / "paper"
    state_file = bucket_dir / "runtime_state.json"
    fallback_dir = tmp_path / "fallback"
    account_path = fallback_dir / "account_snapshot.json"
    market_path = fallback_dir / "market_context.json"
    deriv_path = fallback_dir / "derivatives_snapshot.json"
    fallback_dir.mkdir(parents=True)
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        ),
        encoding="utf-8",
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")), encoding="utf-8")
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")), encoding="utf-8")

    base_dir.mkdir()
    assert list(base_dir.iterdir()) == []

    monkeypatch.setattr(main_module, "ACCOUNT_SNAPSHOT", account_path)
    monkeypatch.setattr(main_module, "MARKET_CONTEXT", market_path)
    monkeypatch.setattr(main_module, "DERIVATIVES_SNAPSHOT", deriv_path)
    monkeypatch.setenv("TRADING_BASE_DIR", str(base_dir))
    monkeypatch.setenv("TRADING_RUNTIME_ENV", "paper")
    monkeypatch.delenv("TRADING_ACCOUNT_SNAPSHOT_FILE", raising=False)
    monkeypatch.delenv("TRADING_MARKET_CONTEXT_FILE", raising=False)
    monkeypatch.delenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", raising=False)
    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    summary = run_cycle_module.run_cycle("dry-run")
    latest = json.loads((bucket_dir / "latest.json").read_text(encoding="utf-8"))

    assert summary == latest
    assert summary["status"] == "ok"
    assert summary["mode"] == "dry-run"
    assert summary["bucket_dir"] == str(bucket_dir)
    assert summary["state_file"] == str(state_file)
    assert summary["state_written"] is True
    assert not (bucket_dir / "account_snapshot.json").exists()
    assert not (bucket_dir / "market_context.json").exists()
    assert not (bucket_dir / "derivatives_snapshot.json").exists()


def test_run_cycle_writes_error_summary_and_latest_on_failure(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    expected_bucket = runtime_root / "paper" / "prod"
    expected_state_file = expected_bucket / "runtime_state.json"

    def fake_main() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(run_cycle_module, "run_main", fake_main)

    with pytest.raises(RuntimeError, match="boom"):
        run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="prod")

    error_path = expected_bucket / "error.json"
    latest_path = expected_bucket / "latest.json"
    error_summary = json.loads(error_path.read_text(encoding="utf-8"))
    latest_summary = json.loads(latest_path.read_text(encoding="utf-8"))

    assert expected_bucket.is_dir()
    assert error_summary == latest_summary
    assert error_summary["status"] == "error"
    assert error_summary["mode"] == "paper"
    assert error_summary["runtime_env"] == "prod"
    assert error_summary["state_file"] == str(expected_state_file)
    assert error_summary["error_type"] == "RuntimeError"
    assert error_summary["error_message"] == "boom"
    assert "finished_at" in error_summary


def test_run_cycle_preserves_paper_ledger_and_reports_replay_summary_when_state_is_missing(
    monkeypatch, tmp_path, load_fixture
):
    runtime_root = tmp_path / "runtime"
    bucket_dir = runtime_root / "paper" / "testnet"
    state_file = bucket_dir / "runtime_state.json"
    ledger_path = bucket_dir / "paper_ledger.jsonl"
    account_path = bucket_dir / "account_snapshot.json"
    market_path = bucket_dir / "market_context.json"
    deriv_path = bucket_dir / "derivatives_snapshot.json"
    bucket_dir.mkdir(parents=True)
    account_path.write_text(
        json.dumps(
            {
                "equity": 125000.0,
                "available_balance": 96000.0,
                "futures_wallet_balance": 118500.0,
                "open_positions": [],
                "open_orders": [],
            }
        ),
        encoding="utf-8",
    )
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")), encoding="utf-8")
    deriv_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")), encoding="utf-8")

    monkeypatch.setattr(main_module.OrderExecutor, "append_log", lambda self, order, result: None)
    monkeypatch.setattr(
        main_module,
        "validate_candidate_for_allocation",
        lambda candidate, account: ValidationResult(True, "INFO", reasons=[], metrics={}),
    )
    monkeypatch.setattr(
        main_module,
        "validate_signal",
        lambda signal, account, config: (ValidationResult(True, "INFO", reasons=[], metrics={}), {"sizing": None}),
    )
    monkeypatch.setattr(
        main_module,
        "generate_trend_candidates",
        lambda *args, **kwargs: [
            {
                "engine": "trend",
                "setup_type": "BREAKOUT_CONTINUATION",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "score": 0.91,
                "stop_loss": 62830.0,
                "invalidation_source": "trend_structure_loss_below_4h_ema50",
            }
        ],
    )
    monkeypatch.setattr(main_module, "generate_rotation_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(main_module, "generate_short_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        main_module,
        "allocate_candidates",
        lambda **kwargs: [AllocationDecision(status="ACCEPTED", engine="trend", final_risk_budget=0.01, rank=1)],
    )

    run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="testnet")

    ledger_lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ledger_event = ledger_lines[0]
    state_file.unlink()

    def fail_execute(self, order, state):
        raise AssertionError("expected paper ledger replay before execute")

    monkeypatch.setattr(main_module.OrderExecutor, "execute", fail_execute)

    summary = run_cycle_module.run_cycle("paper", runtime_root=runtime_root, runtime_env="testnet")
    latest = json.loads((bucket_dir / "latest.json").read_text(encoding="utf-8"))

    assert summary == latest
    assert ledger_path.exists()
    assert [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()] == ledger_lines
    assert summary["paper_trading"]["mode"] == "paper"
    assert summary["paper_trading"]["ledger_path"] == str(ledger_path)
    assert summary["paper_trading"]["ledger_event_count"] == 1
    assert summary["paper_trading"]["emitted_count"] == 0
    assert summary["paper_trading"]["replayed_count"] == 1
    assert summary["paper_trading"]["intents"][0]["intent_id"] == ledger_event["intent_id"]
    assert summary["paper_trading"]["intents"][0]["replay_source"] == "paper_ledger"
