import json

from app.api import routes
from app.core.config import settings
from app.integrations import ai_providers, local_ai
from app.services import credentials
from app.services.core_service import core_service


def test_system_status_reports_actual_sanitized_integrations(monkeypatch):
    monkeypatch.setattr(core_service, "get_state", lambda db: {
        "account_target": "live",
        "market_type": "spot",
        "live_confirmed_by_user": False,
        "bot_run_state": "stopped",
    })
    monkeypatch.setattr(core_service, "engines", lambda db: [
        {"key": "decision_making", "status": "implemented_partial",
         "runtime_check": {"status": "wired"},
         "integration": {"mode": "decision_pipeline"}}
    ])
    monkeypatch.setattr(credentials, "load_binance_credentials", lambda environment, market: {
        "configured": True, "api_key": "NEVER_RETURN_THIS_KEY",
        "api_secret": "NEVER_RETURN_THIS_SECRET",
    })
    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "providers": [
            {"provider": "deepseek", "model": "deepseek-chat", "enabled": True,
             "tasks": ["analysis"], "key_configured": True, "ready": True,
             "last_test_success": True},
            {"provider": "anthropic", "model": "claude-test", "enabled": True,
             "tasks": ["decision"], "key_configured": True, "ready": True,
             "last_test_success": True},
        ]
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {
        "enabled": True, "connected": True, "model_available": True,
        "model": "local-test", "status": "ready",
    })
    monkeypatch.setattr(settings, "telegram_bot_token", "TELEGRAM_SECRET")
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "12345")

    result = routes.system_status(object())

    assert result["integrations"]["binance"]["configured"] is True
    assert result["integrations"]["binance"]["status"] == "credentials_configured_not_connected"
    assert result["integrations"]["binance"]["connection_checked"] is False
    assert result["integrations"]["binance"]["orders_submitted"] is False
    assert result["integrations"]["ai_providers"]["status"] == "connection_tested"
    assert result["integrations"]["ai_providers"]["enabled_analysis_advisors"] == 2
    assert result["integrations"]["ai_providers"]["connection_tested_count"] == 2
    assert result["integrations"]["telegram"]["configured"] is True
    assert result["engines"][0]["runtime_check"]["status"] == "wired"
    assert result["data_policy"]["public_market_data_supported"] is True

    serialized = json.dumps(result)
    assert "NEVER_RETURN_THIS_KEY" not in serialized
    assert "NEVER_RETURN_THIS_SECRET" not in serialized
    assert "TELEGRAM_SECRET" not in serialized


def test_system_status_keeps_unchecked_or_unconfigured_services_honest(monkeypatch):
    monkeypatch.setattr(core_service, "get_state", lambda db: {
        "account_target": "testnet", "market_type": "futures",
    })
    monkeypatch.setattr(core_service, "engines", lambda db: [])
    monkeypatch.setattr(credentials, "load_binance_credentials", lambda environment, market: {
        "configured": False, "environment": environment, "market": market,
    })
    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {"providers": []})
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {
        "enabled": False, "connected": False, "model_available": False,
    })
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "")

    result = routes.system_status(object())

    assert result["integrations"]["binance"]["status"] == "needs_credentials"
    assert result["integrations"]["binance"]["account_target"] == "testnet"
    assert result["integrations"]["ai_providers"]["status"] == "not_configured"
    assert result["integrations"]["telegram"]["configured"] is False
