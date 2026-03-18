from __future__ import annotations

from pathlib import Path


def test_testnet_credentials_prefer_canonical_env_file_over_fallback_env(
    monkeypatch,
    tmp_path: Path,
):
    env_file = tmp_path / "binance-testnet.env"
    env_file.write_text(
        "BINANCE_TESTNET_API_KEY=file-key\nBINANCE_TESTNET_API_SECRET=file-secret\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "fallback-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "fallback-secret")

    from trading_system import binance_client

    monkeypatch.setattr(binance_client, "TESTNET_CREDENTIALS_FILE", env_file, raising=False)

    assert binance_client._api_key() == "file-key"
    assert binance_client._api_secret() == "file-secret"


def test_live_credentials_keep_existing_env_resolution_when_not_in_testnet_mode(
    monkeypatch,
    tmp_path: Path,
):
    env_file = tmp_path / "binance-testnet.env"
    env_file.write_text(
        "BINANCE_TESTNET_API_KEY=file-key\nBINANCE_TESTNET_API_SECRET=file-secret\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("BINANCE_USE_TESTNET", raising=False)
    monkeypatch.setenv("BINANCE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "live-secret")

    from trading_system import binance_client

    monkeypatch.setattr(binance_client, "TESTNET_CREDENTIALS_FILE", env_file, raising=False)

    assert binance_client._api_key() == "live-key"
    assert binance_client._api_secret() == "live-secret"
