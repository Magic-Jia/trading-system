from __future__ import annotations

from pathlib import Path
import urllib.error

import pytest

from trading_system import binance_client


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
    monkeypatch.setenv("BINANCE_BASE_URL", "https://api.binance.com")
    monkeypatch.setenv("BINANCE_FAPI_URL", "https://fapi.binance.com")
    monkeypatch.setenv("BINANCE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "live-secret")

    monkeypatch.setattr(binance_client, "SPOT_BASE", "https://api.binance.com", raising=False)
    monkeypatch.setattr(binance_client, "FUTURES_BASE", "https://fapi.binance.com", raising=False)
    monkeypatch.setattr(binance_client, "TESTNET_CREDENTIALS_FILE", env_file, raising=False)

    assert binance_client._api_key() == "live-key"
    assert binance_client._api_secret() == "live-secret"


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, body: bytes):
        super().__init__("https://testnet.binancefuture.com/fapi/v1/algoOrder", 400, "Bad Request", {}, None)
        self._body = body

    def read(self, *args, **kwargs):
        return self._body


def test_request_includes_binance_error_body_without_sensitive_url(monkeypatch):
    def fake_urlopen(_req, timeout):
        raise _FakeHTTPError(b'{"code":-1102,"msg":"mandatory parameter was not sent"}')

    monkeypatch.setattr(binance_client.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as excinfo:
        binance_client._request(
            "https://testnet.binancefuture.com/fapi/v1/algoOrder?signature=secret-signature",
            headers={"X-MBX-APIKEY": "secret-key"},
        )

    message = str(excinfo.value)
    assert "HTTP 400 Bad Request" in message
    assert "-1102" in message
    assert "mandatory parameter was not sent" in message
    assert "secret-signature" not in message
    assert "secret-key" not in message
