from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
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


def _request(
    url: str,
    headers: Dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> Any:
    req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        parsed = urllib.parse.urlsplit(url)
        safe_endpoint = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        detail = body.strip() or exc.reason
        raise RuntimeError(f"Binance HTTP {exc.code} {exc.reason} at {safe_endpoint}: {detail}") from exc


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


def signed_post(base: str, path: str, params: Dict[str, Any] | None = None) -> Any:
    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise RuntimeError("Missing Binance API credentials")
    params = params or {}
    qs = urllib.parse.urlencode(params, doseq=True)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    body = f"{qs}&signature={sig}".encode()
    return _request(
        f"{base}{path}",
        headers={"X-MBX-APIKEY": key, "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
        method="POST",
    )


def signed_delete(base: str, path: str, params: Dict[str, Any] | None = None) -> Any:
    key = _api_key()
    secret = _api_secret()
    if not key or not secret:
        raise RuntimeError("Missing Binance API credentials")
    params = params or {}
    qs = urllib.parse.urlencode(params, doseq=True)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{base}{path}?{qs}&signature={sig}"
    return _request(url, headers={"X-MBX-APIKEY": key}, method="DELETE")


def server_time() -> int:
    return int(public_get(SPOT_BASE, "/api/v3/time")["serverTime"])


def signed_params(recv_window: int = 5000) -> Dict[str, Any]:
    return {"timestamp": server_time(), "recvWindow": recv_window}


def fetch_futures_testnet_local_time_ms() -> int:
    return int(time.time() * 1000)


def _futures_testnet_signed_params(
    *,
    timestamp_ms: int | None = None,
    recv_window: int = 5000,
) -> Dict[str, Any]:
    return {
        "timestamp": fetch_futures_testnet_local_time_ms() if timestamp_ms is None else int(timestamp_ms),
        "recvWindow": recv_window,
    }


def submit_futures_testnet_order(
    payload: dict[str, Any],
    *,
    timestamp_ms: int | None = None,
    recv_window: int = 5000,
) -> dict[str, Any]:
    if not _is_testnet_mode() or "testnet.binancefuture.com" not in FUTURES_BASE:
        raise RuntimeError("futures testnet order submission requires Binance Futures testnet endpoint")
    params = dict(payload)
    params.update(_futures_testnet_signed_params(timestamp_ms=timestamp_ms, recv_window=recv_window))
    response = signed_post(FUTURES_BASE, "/fapi/v1/order", params)
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected futures testnet order response payload")
    return response


def query_futures_testnet_order(
    *,
    symbol: str,
    orig_client_order_id: str,
    timestamp_ms: int | None = None,
    recv_window: int = 5000,
) -> dict[str, Any]:
    if not _is_testnet_mode() or "testnet.binancefuture.com" not in FUTURES_BASE:
        raise RuntimeError("futures testnet order query requires Binance Futures testnet endpoint")
    params = {
        "symbol": symbol,
        "origClientOrderId": orig_client_order_id,
    }
    params.update(_futures_testnet_signed_params(timestamp_ms=timestamp_ms, recv_window=recv_window))
    response = signed_get(FUTURES_BASE, "/fapi/v1/order", params)
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected futures testnet order query response payload")
    return response


def cancel_futures_testnet_order(
    *,
    symbol: str,
    orig_client_order_id: str,
    timestamp_ms: int | None = None,
    recv_window: int = 5000,
) -> dict[str, Any]:
    if not _is_testnet_mode() or "testnet.binancefuture.com" not in FUTURES_BASE:
        raise RuntimeError("futures testnet order cancellation requires Binance Futures testnet endpoint")
    params = {
        "symbol": symbol,
        "origClientOrderId": orig_client_order_id,
    }
    params.update(_futures_testnet_signed_params(timestamp_ms=timestamp_ms, recv_window=recv_window))
    response = signed_delete(FUTURES_BASE, "/fapi/v1/order", params)
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected futures testnet order cancellation response payload")
    return response


def submit_futures_testnet_conditional_algo_order(
    payload: dict[str, Any],
    *,
    timestamp_ms: int | None = None,
    recv_window: int = 5000,
) -> dict[str, Any]:
    if not _is_testnet_mode() or "testnet.binancefuture.com" not in FUTURES_BASE:
        raise RuntimeError("futures testnet algo order submission requires Binance Futures testnet endpoint")
    params = dict(payload)
    params.update(_futures_testnet_signed_params(timestamp_ms=timestamp_ms, recv_window=recv_window))
    response = signed_post(FUTURES_BASE, "/fapi/v1/algoOrder", params)
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected futures testnet algo order response payload")
    return response
