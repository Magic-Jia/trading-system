from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

SPOT_BASE = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
FUTURES_BASE = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")
TESTNET_CREDENTIALS_FILE = Path("/home/cn/.local/secrets/binance-testnet.env")


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _is_testnet_mode() -> bool:
    use_testnet = os.environ.get("BINANCE_USE_TESTNET", "")
    if use_testnet.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return "testnet.binance" in SPOT_BASE or "testnet.binance" in FUTURES_BASE


def _load_env_file(path: Path) -> dict[str, str]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if (
            len(cleaned_value) >= 2
            and cleaned_value[0] == cleaned_value[-1]
            and cleaned_value[0] in {'"', "'"}
        ):
            cleaned_value = cleaned_value[1:-1]
        if cleaned_key:
            values[cleaned_key] = cleaned_value
    return values


def _testnet_file_value(*names: str) -> str | None:
    values = _load_env_file(TESTNET_CREDENTIALS_FILE)
    for name in names:
        value = values.get(name)
        if value:
            return value
    return None


def _api_key() -> str | None:
    if _is_testnet_mode():
        return _testnet_file_value(
            "BINANCE_TESTNET_API_KEY",
            "BINANCE_API_KEY",
            "BINANCE_APIKEY",
        ) or _env_value(
            "BINANCE_TESTNET_API_KEY",
            "BINANCE_API_KEY",
            "BINANCE_APIKEY",
        )
    return _env_value("BINANCE_API_KEY", "BINANCE_APIKEY")


def _api_secret() -> str | None:
    if _is_testnet_mode():
        return _testnet_file_value(
            "BINANCE_TESTNET_API_SECRET",
            "BINANCE_API_SECRET",
            "BINANCE_SECRET",
        ) or _env_value(
            "BINANCE_TESTNET_API_SECRET",
            "BINANCE_API_SECRET",
            "BINANCE_SECRET",
        )
    return _env_value("BINANCE_API_SECRET", "BINANCE_SECRET")


def env_ready() -> bool:
    return bool(_api_key() and _api_secret())


def _request(url: str, headers: Dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def public_get(base: str, path: str, params: Dict[str, Any] | None = None) -> Any:
    params = params or {}
    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{base}{path}" + (f"?{qs}" if qs else "")
    return _request(url)


def signed_get(base: str, path: str, params: Dict[str, Any] | None = None) -> Any:
    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise RuntimeError("Missing Binance API credentials")
    params = params or {}
    qs = urllib.parse.urlencode(params, doseq=True)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{base}{path}?{qs}&signature={sig}"
    return _request(url, headers={"X-MBX-APIKEY": key})


def server_time() -> int:
    return int(public_get(SPOT_BASE, "/api/v3/time")["serverTime"])


def signed_params(recv_window: int = 5000) -> Dict[str, Any]:
    return {"timestamp": server_time(), "recvWindow": recv_window}
