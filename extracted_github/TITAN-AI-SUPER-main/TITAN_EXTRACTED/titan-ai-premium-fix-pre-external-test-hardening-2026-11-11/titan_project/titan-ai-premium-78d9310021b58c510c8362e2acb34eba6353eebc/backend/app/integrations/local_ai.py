"""Optional no-API-key local LLM adapter (Ollama / llama.cpp OpenAI-compatible server).

The adapter only connects to loopback addresses. It never falls back to a hosted
provider, never transmits prompts to a remote host, and only returns read-only
advisory text. Model weights/runtime installation remain an explicit operator
step because model size and device support vary.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent a loopback inference endpoint from redirecting prompts off-device."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())


def _settings() -> tuple[bool, str, str]:
    enabled = os.getenv("TITAN_LOCAL_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    base_url = os.getenv("TITAN_LOCAL_AI_BASE_URL", "http://127.0.0.1:11434/v1").strip().rstrip("/")
    model = os.getenv("TITAN_LOCAL_AI_MODEL", "qwen2.5:1.5b").strip()
    return enabled, base_url, model


def _loopback_url(base_url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(base_url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"} and parsed.port is not None
    except (TypeError, ValueError):
        return False


def local_model_status(timeout: int = 2) -> dict[str, Any]:
    enabled, base_url, model = _settings()
    if not enabled:
        return {"enabled": False, "configured": False, "connected": False,
                "provider": "local", "api_key_required": False,
                "note": "Set TITAN_LOCAL_AI_ENABLED=true and install a local inference runtime/model."}
    if not _loopback_url(base_url):
        return {"enabled": True, "configured": False, "connected": False,
                "provider": "local", "api_key_required": False,
                "error": "local_model_endpoint_must_be_loopback"}
    # OpenAI-compatible local runtimes (Ollama and llama.cpp) expose /v1/models.
    req = urllib.request.Request(base_url + "/models", headers={"Accept": "application/json"}, method="GET")
    try:
        with _LOCAL_OPENER.open(req, timeout=timeout) as response:
            data = json.loads(response.read(500_001).decode("utf-8"))
        models = data.get("data", []) if isinstance(data, dict) else []
        if not models and isinstance(data, dict):
            models = data.get("models", [])
        names = [str(item.get("id") or item.get("name") or item.get("model") or "")
                 for item in models if isinstance(item, dict)]
        model_available = model in names
        return {"enabled": True, "configured": True, "connected": True,
                "provider": "local_openai_compatible", "model": model, "api_key_required": False,
                "installed_models": names, "model_available": model_available,
                "ready": model_available,
                "status": "ready" if model_available else "model_not_installed"}
    except Exception as exc:
        return {"enabled": True, "configured": True, "connected": False,
                "provider": "local_openai_compatible", "model": model, "api_key_required": False,
                "model_available": False, "ready": False, "status": "runtime_unreachable",
                "error": str(exc)[:240]}


def invoke_local_model(messages: list[dict[str, str]], timeout: int = 45) -> dict[str, Any]:
    enabled, base_url, model = _settings()
    if not enabled:
        return {"ok": False, "provider": "local", "model": model, "error": "local_model_disabled"}
    if not _loopback_url(base_url):
        return {"ok": False, "provider": "local", "model": model, "error": "local_model_endpoint_must_be_loopback"}
    if not model or not isinstance(messages, list) or not messages:
        return {"ok": False, "provider": "local", "model": model, "error": "local_model_configuration_invalid"}
    payload = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with _LOCAL_OPENER.open(req, timeout=timeout) as response:
            data = json.loads(response.read(1_000_001).decode("utf-8"))
        choices = data.get("choices") or []
        text = (choices[0].get("message") or {}).get("content", "") if choices else ""
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "provider": "local", "model": model, "error": "local_model_empty_response"}
        return {"ok": True, "provider": "local", "model": model, "text": text.strip()[:8000],
                "latency_ms": round((time.time() - started) * 1000, 2),
                "usage": data.get("usage") or {}, "api_key_required": False,
                "data_residency": "local_loopback_only", "action_authority": "none"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "provider": "local", "model": model,
                "error": f"local_model_http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "provider": "local", "model": model, "error": str(exc)[:300]}
