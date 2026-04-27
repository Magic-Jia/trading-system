from __future__ import annotations

import json
import urllib.parse
import urllib.request


def _feishu_api_base(domain: str | None) -> str:
    value = (domain or "feishu").strip().rstrip("/")
    if not value or value == "feishu":
        return "https://open.feishu.cn"
    if value.startswith(("http://", "https://")):
        return value
    return f"https://open.{value}"


def _post_json(url: str, payload: dict, *, headers: dict[str, str] | None = None) -> bytes:
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def _tenant_access_token(*, app_id: str, app_secret: str, domain: str | None) -> str:
    base = _feishu_api_base(domain)
    raw = _post_json(
        f"{base}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    data = json.loads(raw.decode("utf-8") or "{}")
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError("Feishu tenant_access_token response missing token")
    return str(token)


def send_feishu_text(
    message: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    receive_id: str | None = None,
    receive_id_type: str = "chat_id",
    domain: str | None = "feishu",
) -> None:
    """Send a simple Feishu application-bot text message.

    Missing optional app-bot configuration is treated as disabled so
    notifications never block trading execution when incomplete.
    """
    if not (app_id and app_secret and receive_id):
        return

    base = _feishu_api_base(domain)
    token = _tenant_access_token(app_id=app_id, app_secret=app_secret, domain=domain)
    query = urllib.parse.urlencode({"receive_id_type": receive_id_type or "chat_id"})
    _post_json(
        f"{base}/open-apis/im/v1/messages?{query}",
        {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": message}, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
