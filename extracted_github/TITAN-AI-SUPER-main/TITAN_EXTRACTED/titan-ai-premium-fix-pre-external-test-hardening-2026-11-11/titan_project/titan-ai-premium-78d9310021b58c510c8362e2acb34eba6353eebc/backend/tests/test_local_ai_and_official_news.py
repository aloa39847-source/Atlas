import json
from io import BytesIO

from app.integrations import local_ai
from app.data import public_news


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return json.dumps(self.payload).encode("utf-8")


def test_local_model_rejects_non_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("TITAN_LOCAL_AI_ENABLED", "true")
    monkeypatch.setenv("TITAN_LOCAL_AI_BASE_URL", "https://api.example.com/v1")
    result = local_ai.invoke_local_model([{"role": "user", "content": "test"}])
    assert result["ok"] is False
    assert result["error"] == "local_model_endpoint_must_be_loopback"


def test_local_model_uses_no_api_key_and_only_loopback(monkeypatch):
    monkeypatch.setenv("TITAN_LOCAL_AI_ENABLED", "true")
    monkeypatch.setenv("TITAN_LOCAL_AI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("TITAN_LOCAL_AI_MODEL", "qwen2.5:1.5b")
    calls = []
    class _FakeLocalOpener:
        def open(self, req, timeout=0):
            calls.append((req, timeout))
            return _Response({"choices": [{"message": {"content": "Read-only market commentary"}}]})
    monkeypatch.setattr(local_ai, "_LOCAL_OPENER", _FakeLocalOpener())
    result = local_ai.invoke_local_model([{"role": "user", "content": "analyze"}])
    assert result["ok"] is True
    assert result["provider"] == "local"
    assert result["api_key_required"] is False
    assert result["data_residency"] == "local_loopback_only"
    assert len(calls) == 1
    assert calls[0][0].full_url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in calls[0][0].header_items()


def test_local_model_status_reports_not_enabled_without_external_calls(monkeypatch):
    monkeypatch.delenv("TITAN_LOCAL_AI_ENABLED", raising=False)
    result = local_ai.local_model_status()
    assert result["enabled"] is False
    assert result["api_key_required"] is False


def test_official_news_feeds_are_allowlisted_and_tagged_primary():
    primary = [feed for feed in public_news.NEWS_FEEDS if feed.get("source_type") == "official_primary"]
    assert {feed["name"] for feed in primary} >= {
        "SEC Press Releases", "Federal Reserve Press Releases",
        "Bureau of Labor Statistics", "Ethereum Foundation",
    }
    assert all(feed["url"].startswith("https://") for feed in primary)
    assert all(feed["url"] in {item["url"] for item in public_news.NEWS_FEEDS} for feed in primary)


def test_news_fetch_cache_avoids_repeated_network_fetch(monkeypatch):
    key = tuple(feed.get("url", "") for feed in public_news.NEWS_FEEDS)
    with public_news._NEWS_CACHE_LOCK:
        public_news._NEWS_CACHE.pop(key, None)
    calls = {"count": 0}
    def fake_fetch(self, feed):
        calls["count"] += 1
        return [{"title": feed["name"], "url": feed["url"] + "?article=1",
                 "summary": "official update", "published_at": "2026-11-11T00:00:00+00:00",
                 "source": feed["name"], "source_type": feed.get("source_type", "secondary_media")}]
    monkeypatch.setattr(public_news.PublicRSSNewsProvider, "_fetch_feed", fake_fetch)
    first = public_news.PublicRSSNewsProvider().fetch(limit=3)
    first_count = calls["count"]
    second = public_news.PublicRSSNewsProvider().fetch(limit=2)
    assert first["ok"] is True
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(second["items"]) == 2
    assert calls["count"] == first_count
    with public_news._NEWS_CACHE_LOCK:
        public_news._NEWS_CACHE.pop(key, None)


def test_local_ai_redirect_handler_refuses_redirects():
    from app.integrations.local_ai import _NoRedirectHandler

    handler = _NoRedirectHandler()
    result = handler.redirect_request(
        None, None, 302, "Found", {}, "https://example.com/collect"
    )
    assert result is None


def test_local_model_status_is_not_ready_when_runtime_has_no_selected_model(monkeypatch):
    monkeypatch.setenv("TITAN_LOCAL_AI_ENABLED", "true")
    monkeypatch.setenv("TITAN_LOCAL_AI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("TITAN_LOCAL_AI_MODEL", "qwen2.5:1.5b")

    class _ModelsOpener:
        def open(self, req, timeout=0):
            assert req.full_url == "http://127.0.0.1:11434/v1/models"
            return _Response({"data": [{"id": "another-model"}]})

    monkeypatch.setattr(local_ai, "_LOCAL_OPENER", _ModelsOpener())
    result = local_ai.local_model_status()
    assert result["connected"] is True
    assert result["model_available"] is False
    assert result["ready"] is False
    assert result["status"] == "model_not_installed"
