from types import SimpleNamespace

from app.integrations import ai_providers


def test_provider_status_reports_unconfigured_without_enabled_providers(monkeypatch):
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [])
    result = ai_providers.provider_status(object())
    assert result == {
        "status": "not_configured",
        "configured": False,
        "enabled_count": 0,
        "disabled_count": 0,
        "ready_count": 0,
        "providers": [],
    }


def test_provider_status_distinguishes_enabled_provider_missing_key(monkeypatch):
    cfg = SimpleNamespace(
        enabled=True, name="OpenAI main", provider="openai", model="gpt-test",
        tasks=["analysis"], api_key_ref="env:TITAN_OPENAI_KEY",
    )
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [cfg])
    monkeypatch.setattr(ai_providers, "_resolve_api_key", lambda ref: None)
    result = ai_providers.provider_status(object())
    assert result["status"] == "enabled_missing_key"
    assert result["configured"] is False
    assert result["enabled_count"] == 1
    assert result["ready_count"] == 0
    assert result["providers"][0]["key_configured"] is False
    assert result["providers"][0]["enabled"] is True
    assert result["providers"][0]["status"] == "missing_api_key"


def test_provider_status_reports_ready_without_exposing_secret(monkeypatch):
    cfg = SimpleNamespace(
        enabled=True, name="OpenAI main", provider="openai", model="gpt-test",
        tasks=["analysis"], api_key_ref="env:TITAN_OPENAI_KEY",
    )
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [cfg])
    monkeypatch.setattr(ai_providers, "_resolve_api_key", lambda ref: "super-secret-value")
    result = ai_providers.provider_status(object())
    assert result["status"] == "ready"
    assert result["configured"] is True
    assert result["ready_count"] == 1
    assert result["providers"][0]["key_configured"] is True
    assert result["providers"][0]["enabled"] is True
    assert result["providers"][0]["ready"] is True
    assert "api_key_ref" not in str(result)
    assert "super-secret-value" not in str(result)



def test_provider_status_includes_disabled_providers_without_marking_them_ready(monkeypatch):
    cfg = SimpleNamespace(
        id=7, enabled=False, name="Backup model", provider="deepseek", model="deepseek-chat",
        tasks=["analysis"], api_key_ref="env:DEEPSEEK_API_KEY", created_at=None,
    )
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [cfg])
    monkeypatch.setattr(ai_providers, "_resolve_api_key", lambda ref: "configured-secret")
    result = ai_providers.provider_status(object())
    assert result["status"] == "all_disabled"
    assert result["enabled_count"] == 0
    assert result["disabled_count"] == 1
    assert result["ready_count"] == 0
    assert result["providers"][0]["enabled"] is False
    assert result["providers"][0]["key_configured"] is True
    assert result["providers"][0]["ready"] is False
    assert result["providers"][0]["status"] == "disabled"
    assert "configured-secret" not in str(result)


def test_decision_advisory_invokes_all_enabled_analysis_advisors_without_trade_authority(monkeypatch):
    providers = [
        SimpleNamespace(enabled=True, provider="openai", model="gpt-test", tasks=["analysis"]),
        SimpleNamespace(enabled=True, provider="deepseek", model="deepseek-chat", tasks=["decision"]),
        SimpleNamespace(enabled=False, provider="anthropic", model="claude-test", tasks=["analysis"]),
    ]
    calls = []
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: providers)
    from app.integrations import local_ai
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False})

    def fake_invoke(cfg, messages, task=None):
        calls.append((cfg.provider, task, messages[0]["content"]))
        return ai_providers.ProviderResult(
            True, cfg.provider, cfg.model, text=f"{cfg.provider} suggests checking confirmation"
        )

    monkeypatch.setattr(ai_providers, "invoke_provider", fake_invoke)
    monkeypatch.setattr(ai_providers, "record_usage", lambda *args: None)
    result = ai_providers.evaluate_decision_advisory(object(), {"action": "buy"})

    assert [call[0] for call in calls] == ["openai", "deepseek"]
    assert all(call[1] == "analysis" for call in calls)
    assert "NO authority" in calls[0][2] and "TITAN owns the final decision" in calls[0][2]
    assert result["advice_count"] == 1
    assert result["attempted_count"] == 2
    assert result["primary_provider"] == "openai"
    assert result["secondary_cloud_providers_ignored"] == 0
    assert result["action_authority"] == "none"
    assert result["decision_modified"] is False
    assert result["final_decision_owner"] == "TITAN"
    assert {item["provider"] for item in result["advisors"]} == {"openai", "deepseek"}
    assert result["advisors"][0]["role"] == "primary"
    assert result["advisors"][1]["role"] == "independent_advisor"


def test_decision_advisory_uses_local_fallback_when_openai_fails(monkeypatch):
    providers = [
        SimpleNamespace(enabled=True, provider="openai", model="gpt-test", tasks=["analysis"]),
        SimpleNamespace(enabled=True, provider="deepseek", model="deepseek-chat", tasks=["analysis"]),
    ]
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: providers)
    from app.integrations import local_ai
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": True, "connected": True, "model_available": True})
    monkeypatch.setattr(local_ai, "invoke_local_model", lambda messages: {
        "ok": True, "provider": "local", "model": "local-model", "text": "independent local review",
    })

    def fake_invoke(cfg, messages, task=None):
        raise RuntimeError("temporary provider failure")

    monkeypatch.setattr(ai_providers, "invoke_provider", fake_invoke)
    monkeypatch.setattr(ai_providers, "record_usage", lambda *args: None)
    result = ai_providers.evaluate_decision_advisory(object(), {"action": "hold"})

    assert result["status"] == "completed"
    assert result["advice_count"] == 1
    assert result["attempted_count"] == 3
    assert result["primary_provider"] == "local"
    assert result["fallback_used"] is True
    assert result["advisors"][0]["status"] == "unavailable"
    assert result["advisors"][1]["status"] == "completed"
    assert result["action_authority"] == "none"
    assert result["decision_modified"] is False


def test_decision_advisory_reports_no_configured_provider(monkeypatch):
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [])
    from app.integrations import local_ai
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False})
    result = ai_providers.evaluate_decision_advisory(object(), {})
    assert result["status"] == "not_configured"
    assert result["advisors"] == []
    assert result["action_authority"] == "none"
    assert result["decision_modified"] is False



def test_provider_status_recognizes_encrypted_app_key_without_exposing_it(monkeypatch):
    from app.integrations import ai_providers
    from app.services import credentials

    cfg = SimpleNamespace(
        id=None, enabled=True, name="OpenAI app key", provider="openai", model="gpt-test",
        tasks=["analysis"], api_key_ref="encrypted:metadata",
        metadata_json={"api_key_encrypted": "v2:ciphertext"},
    )
    monkeypatch.setattr(ai_providers, "list_configs", lambda db: [cfg])
    monkeypatch.setattr(credentials, "decrypt_secret", lambda token: "sk-app-secret-value")
    result = ai_providers.provider_status(object())
    assert result["status"] == "ready"
    assert result["configured"] is True
    assert result["providers"][0]["key_configured"] is True
    assert "sk-app-secret-value" not in str(result)
    assert "v2:ciphertext" not in str(result)
