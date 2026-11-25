from __future__ import annotations

import json
import urllib.request

from app.integrations import ai_providers


def test_provider_transport_disables_environment_proxies_and_redirects():
    # build_opener omits the default environment-aware ProxyHandler when
    # an empty ProxyHandler({}) is supplied, leaving no proxy handler at all.
    proxy_handlers = [
        handler for handler in ai_providers._PROVIDER_OPENER.handlers
        if isinstance(handler, urllib.request.ProxyHandler)
    ]
    redirect_handlers = [
        handler for handler in ai_providers._PROVIDER_OPENER.handlers
        if isinstance(handler, ai_providers._NoRedirectHandler)
    ]
    assert proxy_handlers == []
    assert redirect_handlers

    request = urllib.request.Request("https://api.example.com/v1/chat/completions")
    assert redirect_handlers[0].redirect_request(
        request, None, 302, "Found", {"Location": "https://attacker.example/collect"},
        "https://attacker.example/collect",
    ) is None


def test_provider_json_requests_use_the_hardened_opener_not_urlopen(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok":true}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse()

    monkeypatch.setattr(ai_providers, "_PROVIDER_OPENER", FakeOpener())
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ambient urlopen must not be used")),
    )

    result = ai_providers._http_json(
        "POST", "https://api.openai.com/v1/chat/completions",
        {"Authorization": "Bearer test-only-key"}, {"model": "test"}, timeout=7,
    )
    assert result == {"ok": True}
    assert captured == {
        "url": "https://api.openai.com/v1/chat/completions",
        "timeout": 7,
        "authorization": "Bearer test-only-key",
    }


def test_remote_provider_endpoints_require_https_but_local_models_can_use_http(monkeypatch):
    import pytest

    called = []

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"ok":true}'

    class FakeOpener:
        def open(self, request, timeout):
            called.append(request.full_url)
            return FakeResponse()

    monkeypatch.setattr(ai_providers, "_PROVIDER_OPENER", FakeOpener())
    headers = {"Content-Type": "application/json"}

    with pytest.raises(RuntimeError, match="provider_endpoint_requires_https_or_local_http"):
        ai_providers._http_json("POST", "http://api.example.com/v1/chat/completions", headers, {})
    with pytest.raises(RuntimeError, match="provider_endpoint_requires_https_or_local_http"):
        ai_providers._http_json("POST", "http://[2606:4700::1111]/v1/chat/completions", headers, {})
    with pytest.raises(RuntimeError, match="invalid_provider_endpoint"):
        ai_providers._http_json("POST", "https://user:pass@api.example.com/v1/chat/completions", headers, {})
    assert called == []

    result = ai_providers._http_json(
        "POST", "http://127.0.0.1:11434/v1/chat/completions", headers, {},
    )
    assert result == {"ok": True}
    assert called == ["http://127.0.0.1:11434/v1/chat/completions"]
