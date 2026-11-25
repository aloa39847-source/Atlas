from types import SimpleNamespace

from app.integrations import ai_providers
from app.integrations import local_ai


def test_decision_advisory_is_inactive_without_openai_or_local_fallback(monkeypatch):
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False})
    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "buy"}})
    assert result == {
        "status": "not_configured", "action_authority": "none",
        "decision_modified": False, "advisors": [], "advice_count": 0,
        "primary_provider": None, "fallback_used": False,
    }


def test_openai_is_the_primary_evaluator_and_other_cloud_providers_are_not_used(monkeypatch):
    openai_primary = SimpleNamespace(enabled=True, provider="openai", model="gpt-model", tasks=["news"])
    openai_backup = SimpleNamespace(enabled=True, provider="openai", model="backup-model", tasks=["analysis"])
    other_cloud = SimpleNamespace(enabled=True, provider="anthropic", model="other-model", tasks=["analysis"])
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [openai_primary, openai_backup, other_cloud])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": True, "connected": True, "model_available": True})
    local_calls = []
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: (
        local_calls.append(messages) or {"ok": True, "text": "Local fallback"}
    ))
    cloud_calls = []
    monkeypatch.setattr(ai_providers, "invoke_provider", lambda cfg, messages, task=None: (
        cloud_calls.append((cfg.provider, cfg.model, task))
        or ai_providers.ProviderResult(True, cfg.provider, cfg.model, text="OpenAI primary review")
    ))
    monkeypatch.setattr(ai_providers, "record_usage", lambda *args: None)

    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "wait"}})

    assert cloud_calls == [("openai", "backup-model", "analysis")]
    assert local_calls == []
    assert result["primary_provider"] == "openai"
    assert result["fallback_used"] is False
    assert result["secondary_cloud_providers_ignored"] == 1
    assert result["advisors"][0]["role"] == "primary"
    assert result["advisors"][0]["status"] == "completed"
    assert result["action_authority"] == "none"


def test_local_model_is_used_only_when_openai_primary_is_unavailable(monkeypatch):
    openai = SimpleNamespace(enabled=True, tasks=["analysis"], provider="openai", model="gpt-primary")
    other_cloud = SimpleNamespace(enabled=True, tasks=["analysis"], provider="anthropic", model="unused")
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [openai, other_cloud])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": True, "connected": True, "model_available": True})
    local_calls = []
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: (
        local_calls.append(messages)
        or {"ok": True, "provider": "local", "model": "local-model",
            "text": "Review: setup aligns; monitor liquidity.", "latency_ms": 12}
    ))
    cloud_calls = []
    usage = []

    def fake_invoke(cfg, messages, task=None):
        cloud_calls.append((cfg.provider, task, messages))
        return ai_providers.ProviderResult(False, cfg.provider, cfg.model, error="temporary_failure")

    monkeypatch.setattr(ai_providers, "invoke_provider", fake_invoke)
    monkeypatch.setattr(ai_providers, "record_usage",
                        lambda db, cfg, task, result: usage.append((cfg.provider, task, result.ok)))

    context = {
        "decision": {"action": "buy", "strategy": "momentum"},
        "independent_risk_result": {"ok": True, "quantity_estimate": 0.1},
        "system_state": {"emergency_stop": False},
    }
    result = ai_providers.evaluate_decision_advisory(object(), context)

    assert [call[0] for call in cloud_calls] == ["openai"]
    assert len(local_calls) == 1
    assert cloud_calls[0][2] == local_calls[0]
    assert len(result["analysis_context_sha256"]) == 64
    assert result["analysis_context_preserved"] is True
    assert result["advisors"][0]["analysis_context_sha256"] == result["advisors"][1]["analysis_context_sha256"]
    assert usage == [("openai", "analysis", False)]
    assert result["status"] == "completed"
    assert result["advice_count"] == 1
    assert result["attempted_count"] == 2
    assert result["primary_provider"] == "local"
    assert result["fallback_used"] is True
    assert [item["provider"] for item in result["advisors"]] == ["openai", "local"]
    assert result["advisors"][0]["status"] == "unavailable"
    assert result["advisors"][1]["role"] == "fallback"
    assert result["advisors"][1]["status"] == "completed"
    assert result["action_authority"] == "none"
    assert result["decision_modified"] is False
    assert result["final_decision_owner"] == "TITAN"
    assert context["decision"]["action"] == "buy"


def test_local_model_can_be_primary_fallback_when_openai_is_not_configured(monkeypatch):
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": True, "connected": True, "model_available": True})
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: {
        "ok": True, "provider": "local", "model": "local-model", "text": "Local review",
    })
    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "wait"}})
    assert result["status"] == "completed"
    assert result["primary_provider"] == "local"
    assert result["fallback_used"] is True
    assert result["advisors"][0]["role"] == "fallback"
    assert result["action_authority"] == "none"


def test_ai_usage_logging_failure_does_not_block_primary_advisory(monkeypatch):
    primary = SimpleNamespace(enabled=True, provider="openai", model="gpt-primary", tasks=["analysis"])
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [primary])
    from app.integrations import local_ai
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False})
    monkeypatch.setattr(ai_providers, "invoke_provider", lambda cfg, messages, task=None:
                        ai_providers.ProviderResult(True, "openai", "gpt-primary", text="AI review"))
    monkeypatch.setattr(ai_providers, "record_usage", lambda *args: (_ for _ in ()).throw(RuntimeError("db telemetry failure")))

    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "wait"}})

    assert result["status"] == "completed"
    assert result["primary_provider"] == "openai"
    assert result["advisors"][0]["status"] == "completed"
    assert result["advisors"][0]["usage_recording_error"] == "RuntimeError"
    assert result["action_authority"] == "none"


def test_openai_config_limited_to_news_is_not_used_for_decision_analysis(monkeypatch):
    news_only = SimpleNamespace(
        enabled=True, provider="openai", model="news-model", tasks=["news"],
    )
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [news_only])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": True, "connected": True, "model_available": True})
    local_calls = []
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: (
        local_calls.append(messages) or {
            "ok": True, "provider": "local", "model": "local-model",
            "text": "Local decision review",
        }
    ))
    cloud_calls = []
    monkeypatch.setattr(ai_providers, "invoke_provider", lambda *args, **kwargs: (
        cloud_calls.append(args) or ai_providers.ProviderResult(
            True, "openai", "news-model", text="Should not be called",
        )
    ))
    monkeypatch.setattr(ai_providers, "record_usage", lambda *args: None)

    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "wait"}})

    assert cloud_calls == []
    assert len(local_calls) == 1
    assert result["primary_provider"] == "local"
    assert result["fallback_used"] is True
    assert result["advisors"][0]["provider"] == "local"


def test_local_fallback_is_not_attempted_until_runtime_and_model_are_ready(monkeypatch):
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [])
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {
        "enabled": True, "connected": True, "model_available": False,
        "status": "model_not_installed",
    })
    local_calls = []
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: local_calls.append(messages))
    result = ai_providers.evaluate_decision_advisory(object(), {"decision": {"action": "wait"}})
    assert local_calls == []
    assert result["status"] == "unavailable"
    assert result["local_runtime_ready"] is False
    assert result["advice_count"] == 0
