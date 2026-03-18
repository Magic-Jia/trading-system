from pathlib import Path


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
