from __future__ import annotations

import email.message
import io
import urllib.error

import app.integrations.binance_adapter as adapter_module
from app.integrations.binance_adapter import BinanceConfig, BinanceTestnetAdapter


def test_binance_429_honors_retry_after_and_suppresses_followup_requests(monkeypatch):
    with adapter_module._RATE_LIMIT_LOCK:
        adapter_module._RATE_LIMIT_UNTIL = 0.0

    headers = email.message.Message()
    headers["Retry-After"] = "45"
    error = urllib.error.HTTPError(
        "https://testnet.binance.vision/api/v3/time",
        429,
        "Too Many Requests",
        headers,
        io.BytesIO(b'{"code":-1003,"msg":"Too many requests"}'),
    )
    calls = {"count": 0}

    def fail_with_429(*args, **kwargs):
        calls["count"] += 1
        raise error

    monkeypatch.setattr(adapter_module.urllib.request, "urlopen", fail_with_429)
    adapter = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="spot"))
    try:
        first = adapter._request("GET", "/api/v3/time")
        assert first["error"] == "binance_rate_limit_exceeded"
        assert first["status"] == 429
        assert first["retry_after_seconds"] >= 44

        second = adapter._request("GET", "/api/v3/time")
        assert second["error"] == "rate_limit_cooldown"
        assert second["retry_after_seconds"] > 0
        assert calls["count"] == 1
    finally:
        with adapter_module._RATE_LIMIT_LOCK:
            adapter_module._RATE_LIMIT_UNTIL = 0.0


def test_binance_418_uses_conservative_cooldown_when_retry_after_missing(monkeypatch):
    with adapter_module._RATE_LIMIT_LOCK:
        adapter_module._RATE_LIMIT_UNTIL = 0.0

    headers = email.message.Message()
    error = urllib.error.HTTPError(
        "https://testnet.binance.vision/api/v3/time",
        418,
        "IP Banned",
        headers,
        io.BytesIO(b'{"code":-1003,"msg":"IP banned"}'),
    )
    monkeypatch.setattr(adapter_module.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(error))
    adapter = BinanceTestnetAdapter(BinanceConfig(enabled=True, environment="testnet", market="spot"))
    try:
        result = adapter._request("GET", "/api/v3/time")
        assert result["error"] == "binance_rate_limit_exceeded"
        assert result["status"] == 418
        assert result["retry_after_seconds"] >= 59
    finally:
        with adapter_module._RATE_LIMIT_LOCK:
            adapter_module._RATE_LIMIT_UNTIL = 0.0
