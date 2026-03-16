from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict

SPOT_BASE = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
FUTURES_BASE = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")


def _api_key() -> str | None:
    return os.environ.get("BINANCE_API_KEY") or os.environ.get("BINANCE_APIKEY")


def _api_secret() -> str | None:
    return os.environ.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_SECRET")


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
