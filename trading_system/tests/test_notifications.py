import json

from trading_system.app.notifications import send_feishu_text


class FakeResponse:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _decode_request(request):
    return json.loads(request.data.decode("utf-8"))


def test_send_feishu_text_app_bot_fetches_token_then_sends_message(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        return FakeResponse({"code": 0, "data": {"message_id": "msg-id"}})

    monkeypatch.setattr("trading_system.app.notifications.urllib.request.urlopen", fake_urlopen)

    send_feishu_text(
        "hello app",
        app_id="app-id",
        app_secret="app-secret",
        receive_id="chat-id",
        receive_id_type="chat_id",
        domain="feishu",
    )

    assert len(requests) == 2
    token_request, token_timeout = requests[0]
    assert token_request.full_url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    assert token_request.get_method() == "POST"
    assert token_timeout == 10
    assert _decode_request(token_request) == {"app_id": "app-id", "app_secret": "app-secret"}

    message_request, message_timeout = requests[1]
    assert message_request.full_url == "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    assert message_request.get_method() == "POST"
    assert message_timeout == 10
    assert message_request.headers["Authorization"] == "Bearer tenant-token"
    assert _decode_request(message_request) == {
        "receive_id": "chat-id",
        "msg_type": "text",
        "content": json.dumps({"text": "hello app"}, ensure_ascii=False),
    }


def test_send_feishu_text_app_bot_missing_config_is_noop(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse({"code": 0})

    monkeypatch.setattr("trading_system.app.notifications.urllib.request.urlopen", fake_urlopen)

    send_feishu_text(
        "hello missing",
        app_id="app-id",
        app_secret="app-secret",
        receive_id=None,
        receive_id_type="chat_id",
    )

    assert calls == []
