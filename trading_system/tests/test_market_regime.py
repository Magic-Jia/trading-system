import json
from pathlib import Path

import pytest

from trading_system.app.data_sources.derivatives_loader import load_derivatives_snapshot
from trading_system.app.data_sources.market_loader import load_market_context


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


def test_loaders_fail_fast_on_missing_required_keys(tmp_path: Path):
    bad_market = tmp_path / "bad_market_context.json"
    bad_derivatives = tmp_path / "bad_derivatives_snapshot.json"
    bad_market.write_text(json.dumps({"schema_version": "v2", "symbols": {}}), encoding="utf-8")
    bad_derivatives.write_text(json.dumps({"schema_version": "v2"}), encoding="utf-8")

    with pytest.raises(ValueError, match="as_of"):
        load_market_context(bad_market)

    with pytest.raises(ValueError, match="as_of"):
        load_derivatives_snapshot(bad_derivatives)
