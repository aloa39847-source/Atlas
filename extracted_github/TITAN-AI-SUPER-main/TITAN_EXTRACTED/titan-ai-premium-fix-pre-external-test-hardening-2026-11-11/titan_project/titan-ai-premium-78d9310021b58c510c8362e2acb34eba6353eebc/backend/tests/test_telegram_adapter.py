import json
from io import BytesIO

from app.integrations.telegram_adapter import TelegramAdapter, TelegramConfig


def _adapter(monkeypatch, *, enabled=True):
    monkeypatch.setenv("TITAN_TEST_TELEGRAM_TOKEN", "123456:secret-token")
    monkeypatch.setenv("TITAN_TEST_TELEGRAM_CHATS", "12345,-99")
    return TelegramAdapter(TelegramConfig(
        enabled=enabled,
        bot_token_ref="env:TITAN_TEST_TELEGRAM_TOKEN",
        allowed_chat_ids_ref="env:TITAN_TEST_TELEGRAM_CHATS",
    ))


def test_telegram_status_and_command_parser_enforce_chat_allowlist(monkeypatch):
    adapter = _adapter(monkeypatch)
    status = adapter.status()
    assert status["configured"] is True
    assert status["authorized_chat_count"] == 2
    assert "secret-token" not in str(status)

    safe = adapter.parse_command("12345", "/status")
    assert safe["authorized"] is True
    assert safe["known"] is True
    assert safe["executed"] is False

    sensitive = adapter.parse_command("12345", "/startbot")
    assert sensitive["sensitive"] is True
    assert sensitive["requires_confirmation"] is True
    assert sensitive["executed"] is False

    unauthorized = adapter.parse_command("77777", "/status")
    assert unauthorized["authorized"] is False
    assert unauthorized["reason"] == "unauthorized_chat"


def test_telegram_send_message_only_posts_to_allowlisted_chat(monkeypatch):
    adapter = _adapter(monkeypatch)
    calls = []

    class Response(BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(json.dumps({"ok": True, "result": {"message_id": 7}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    denied = adapter.send_message("77777", "hello")
    assert denied["error"] == "unauthorized_chat"
    assert calls == []

    sent = adapter.send_message("12345", "TITAN status")
    assert sent == {"ok": True, "message_id": 7}
    assert len(calls) == 1
    assert "secret-token" not in str(sent)


def test_telegram_poll_filters_unauthorized_updates_and_never_executes_commands(monkeypatch):
    adapter = _adapter(monkeypatch)
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 12345}, "text": "/report"}},
        {"update_id": 11, "message": {"chat": {"id": 77777}, "text": "/startbot"}},
        {"update_id": 12, "message": {"chat": {"id": 12345}, "text": "/live"}},
    ]

    class Response(BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Response(json.dumps({"ok": True, "result": updates}).encode()),
    )
    result = adapter.get_updates(offset=9, timeout=0, limit=10)
    assert result["ok"] is True
    assert len(result["updates"]) == 2
    assert result["ignored_unauthorized_updates"] == 1
    assert result["updates"][1]["requires_confirmation"] is True
    assert all(item["executed"] is False for item in result["updates"])
    assert result["next_offset"] == 13


def test_telegram_transport_failure_does_not_leak_token(monkeypatch):
    adapter = _adapter(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("failed")))
    result = adapter.send_message("12345", "hello")
    assert result == {"ok": False, "error": "telegram_transport_failed"}
    assert "secret-token" not in str(result)
