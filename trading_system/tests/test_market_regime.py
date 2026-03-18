import json
from pathlib import Path

import pytest

from trading_system.app.data_sources.derivatives_loader import load_derivatives_snapshot
from trading_system.app.data_sources.market_loader import load_market_context
from trading_system.app.market_regime.breadth import compute_breadth_metrics
from trading_system.app.market_regime.classifier import classify_regime
from trading_system.app.market_regime.derivatives import summarize_derivatives_risk


def _high_vol_mixed_market_context() -> dict[str, object]:
    return {
        "as_of": "2026-03-15T00:00:00Z",
        "schema_version": "v2",
        "symbols": {
            "BTCUSDT": {
                "daily": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "atr_pct": 0.07},
                "4h": {"close": 101.0, "ema_20": 100.0, "ema_50": 99.0, "return_pct_3d": 0.03},
            },
            "ETHUSDT": {
                "daily": {"close": 98.0, "ema_20": 99.0, "ema_50": 100.0, "atr_pct": 0.07},
                "4h": {"close": 98.0, "ema_20": 99.0, "ema_50": 100.0, "return_pct_3d": -0.03},
            },
        },
    }


def _majors_derivatives_snapshot(
    *,
    funding_rate: float,
    open_interest_change_24h_pct: float,
    taker_buy_sell_ratio: float,
    basis_bps: float,
    mark_price_change_24h_pct: float,
) -> dict[str, object]:
    return {
        "as_of": "2026-03-15T00:00:00Z",
        "schema_version": "v2",
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": funding_rate,
                "open_interest_usdt": 10000000000,
                "open_interest_change_24h_pct": open_interest_change_24h_pct,
                "taker_buy_sell_ratio": taker_buy_sell_ratio,
                "basis_bps": basis_bps,
                "mark_price_change_24h_pct": mark_price_change_24h_pct,
            },
            {
                "symbol": "ETHUSDT",
                "funding_rate": funding_rate,
                "open_interest_usdt": 5000000000,
                "open_interest_change_24h_pct": open_interest_change_24h_pct,
                "taker_buy_sell_ratio": taker_buy_sell_ratio,
                "basis_bps": basis_bps,
                "mark_price_change_24h_pct": mark_price_change_24h_pct,
            },
        ],
    }


def test_v2_fixture_loader_is_cwd_safe(load_fixture, monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    account = load_fixture("account_snapshot_v2.json")
    assert account["as_of"] == "2026-03-15T00:00:00Z"
    assert account["equity"] == 125000.0


def test_v2_market_regime_fixtures_follow_expected_contract(load_fixture, fixture_dir: Path):
    account = load_fixture("account_snapshot_v2.json")
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")

    assert (fixture_dir / "FIXTURE_PROVENANCE.md").exists()

    assert account["meta"]["account_type"] == "paper"
    assert len(account["open_positions"]) == 3
    assert account["open_positions"][0]["symbol"] == "BTCUSDT"

    assert market["schema_version"] == "v2"
    assert set(["BTCUSDT", "ETHUSDT", "SOLUSDT"]).issubset(market["symbols"])
    assert market["symbols"]["BTCUSDT"]["daily"]["volume_usdt_24h"] == 19800000000

    assert derivatives["schema_version"] == "v2"
    assert derivatives["rows"][0]["symbol"] == "BTCUSDT"
    assert derivatives["rows"][0]["basis_bps"] == 22
    assert derivatives["rows"][0]["mark_price_change_24h_pct"] == 0.017


def test_load_market_context_reads_single_runtime_contract(tmp_path: Path, load_fixture):
    market_path = tmp_path / "market_context.json"
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")), encoding="utf-8")

    rows = load_market_context(market_path)

    assert rows
    assert all("symbol" in row for row in rows)


def test_load_derivatives_snapshot_reads_majors_only_snapshot(tmp_path: Path, load_fixture):
    derivatives_path = tmp_path / "derivatives_snapshot.json"
    derivatives_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")), encoding="utf-8")

    rows = load_derivatives_snapshot(derivatives_path)

    assert rows
    assert all("symbol" in row for row in rows)


def test_market_and_derivatives_loaders_support_env_override(
    tmp_path: Path, load_fixture, monkeypatch: pytest.MonkeyPatch
):
    market_path = tmp_path / "market_context_from_env.json"
    derivatives_path = tmp_path / "derivatives_snapshot_from_env.json"
    market_path.write_text(json.dumps(load_fixture("market_context_v2.json")), encoding="utf-8")
    derivatives_path.write_text(json.dumps(load_fixture("derivatives_snapshot_v2.json")), encoding="utf-8")

    monkeypatch.setenv("TRADING_MARKET_CONTEXT_FILE", str(market_path))
    monkeypatch.setenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", str(derivatives_path))

    market_rows = load_market_context()
    derivatives_rows = load_derivatives_snapshot()

    assert {row["symbol"] for row in market_rows}.issuperset({"BTCUSDT", "ETHUSDT"})
    assert {row["symbol"] for row in derivatives_rows}.issuperset({"BTCUSDT", "ETHUSDT"})


def test_market_and_derivatives_loaders_use_default_runtime_files(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRADING_MARKET_CONTEXT_FILE", raising=False)
    monkeypatch.delenv("TRADING_DERIVATIVES_SNAPSHOT_FILE", raising=False)

    market_rows = load_market_context()
    derivatives_rows = load_derivatives_snapshot()

    assert {row["symbol"] for row in market_rows}.issuperset({"BTCUSDT", "ETHUSDT"})
    assert {row["symbol"] for row in derivatives_rows} == {"BTCUSDT", "ETHUSDT"}


def test_loaders_fail_fast_on_missing_required_keys(tmp_path: Path):
    bad_market = tmp_path / "bad_market_context.json"
    bad_derivatives = tmp_path / "bad_derivatives_snapshot.json"
    bad_market.write_text(json.dumps({"schema_version": "v2", "symbols": {}}), encoding="utf-8")
    bad_derivatives.write_text(json.dumps({"schema_version": "v2"}), encoding="utf-8")

    with pytest.raises(ValueError, match="as_of"):
        load_market_context(bad_market)

    with pytest.raises(ValueError, match="as_of"):
        load_derivatives_snapshot(bad_derivatives)


def test_derivatives_loader_requires_price_change_field(tmp_path: Path):
    missing_price_change = {
        "as_of": "2026-03-15T00:00:00Z",
        "schema_version": "v2",
        "rows": [
            {
                "symbol": "BTCUSDT",
                "funding_rate": 0.0,
                "open_interest_usdt": 1.0,
                "open_interest_change_24h_pct": 0.0,
                "taker_buy_sell_ratio": 1.0,
                "basis_bps": 0.0,
            }
        ],
    }
    derivatives_path = tmp_path / "bad_derivatives_snapshot.json"
    derivatives_path.write_text(json.dumps(missing_price_change), encoding="utf-8")

    with pytest.raises(ValueError, match="mark_price_change_24h_pct"):
        load_derivatives_snapshot(derivatives_path)


def test_compute_breadth_metrics_counts_positive_participation(load_fixture):
    market = load_fixture("market_context_v2.json")

    metrics = compute_breadth_metrics(market)

    assert metrics["pct_above_4h_ema20"] == 1.0
    assert metrics["pct_4h_ema20_above_ema50"] == 1.0
    assert metrics["positive_momentum_share"] == 1.0


def test_summarize_derivatives_risk_detects_crowding(load_fixture):
    derivatives = load_fixture("derivatives_snapshot_v2.json")

    summary = summarize_derivatives_risk(derivatives)

    assert summary["crowding_bias"] == "crowded_long"
    assert summary["funding_heat"] in {"cool", "warm", "hot"}


def test_summarize_derivatives_risk_price_oi_interaction_uses_price_change():
    derivatives = _majors_derivatives_snapshot(
        funding_rate=0.00012,
        open_interest_change_24h_pct=0.04,
        taker_buy_sell_ratio=1.06,
        basis_bps=20,
        mark_price_change_24h_pct=-0.02,
    )

    summary = summarize_derivatives_risk(derivatives)

    assert summary["price_oi_interaction"] == "short_build"


def test_classify_regime_returns_bucket_targets(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")

    regime = classify_regime(market, derivatives)

    assert regime.label
    assert regime.bucket_targets
    assert regime.risk_multiplier > 0
    assert regime.execution_policy in {"normal", "downsize", "suppress"}


def test_classify_regime_fixture_maps_to_risk_on_trend(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")

    regime = classify_regime(market, derivatives)

    assert regime.label == "RISK_ON_TREND"


def test_low_confidence_regime_reduces_aggression(load_fixture):
    market = load_fixture("market_context_v2.json")
    derivatives = load_fixture("derivatives_snapshot_v2.json")

    base = classify_regime(market, derivatives)
    low_conf = classify_regime(market, derivatives, force_low_confidence=True)

    assert low_conf.confidence < base.confidence
    assert low_conf.risk_multiplier < base.risk_multiplier
    assert sum(low_conf.bucket_targets.values()) < sum(base.bucket_targets.values())
    assert low_conf.execution_policy == "suppress"


def test_classify_regime_crowded_long_dampens_confidence_and_aggression():
    market = _high_vol_mixed_market_context()
    balanced = _majors_derivatives_snapshot(
        funding_rate=0.0,
        open_interest_change_24h_pct=0.0,
        taker_buy_sell_ratio=1.0,
        basis_bps=0.0,
        mark_price_change_24h_pct=0.0,
    )
    crowded_long = _majors_derivatives_snapshot(
        funding_rate=0.00015,
        open_interest_change_24h_pct=0.04,
        taker_buy_sell_ratio=1.08,
        basis_bps=20.0,
        mark_price_change_24h_pct=0.02,
    )

    base = classify_regime(market, balanced)
    crowded = classify_regime(market, crowded_long)

    assert base.label == "HIGH_VOL_DEFENSIVE"
    assert crowded.label == "HIGH_VOL_DEFENSIVE"
    assert crowded.confidence < base.confidence
    assert crowded.risk_multiplier < base.risk_multiplier
    assert sum(crowded.bucket_targets.values()) < sum(base.bucket_targets.values())
    assert crowded.execution_policy in {"downsize", "suppress"}
