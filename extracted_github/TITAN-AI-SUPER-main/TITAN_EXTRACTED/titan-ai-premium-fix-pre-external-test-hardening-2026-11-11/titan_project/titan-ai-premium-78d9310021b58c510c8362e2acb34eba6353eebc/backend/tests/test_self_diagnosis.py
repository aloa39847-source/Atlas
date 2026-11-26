from app.models import db as dbmod
from app.services.self_diagnosis import SelfDiagnosisEngine


class _Closable:
    def close(self):
        pass


def test_engine_diagnostics_warn_when_registered_engines_are_partial(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _Closable())
    monkeypatch.setattr(core_service, "engines", lambda db: [
        {"key": "risk_management", "status": "implemented_partial", "runtime_check": {"status": "wired"}, "integration": {"mode": "independent_guardrail"}},
        {"key": "news_analysis", "status": "needs_external_service", "runtime_check": {"status": "wired"}, "integration": {"mode": "decision_pipeline"}},
        {"key": "fundamental_analysis", "status": "not_implemented", "runtime_check": {"status": "wired"}, "integration": {"mode": "on_demand"}},
    ])
    result = SelfDiagnosisEngine()._check_engines(None)
    assert result["status"] == "warning"
    assert result["maturity_counts"] == {
        "implemented_partial": 1,
        "needs_external_service": 1,
        "not_implemented": 1,
    }
    assert result["runtime_wired_count"] == 3
    assert "Implementation maturity remains partial/not-ready for 3 engines" in result["detail"]


def test_engine_diagnostics_accept_only_all_ready_engines(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _Closable())
    monkeypatch.setattr(core_service, "engines", lambda db: [
        {"key": "risk_management", "status": "ready", "runtime_check": {"status": "wired"}, "integration": {"mode": "independent_guardrail"}},
    ])
    result = SelfDiagnosisEngine()._check_engines(None)
    assert result["status"] == "ok"
    assert result["maturity_counts"] == {"ready": 1}
    assert result["runtime_wired_count"] == 1


def test_risk_diagnostics_runs_a_bounded_spot_sizing_check(monkeypatch):
    from app.engines import risk as risk_module

    class PassingRisk:
        def validate_trade(self, **kwargs):
            assert kwargs["market"].value == "spot"
            assert kwargs["environment"].value == "demo"
            return {
                "ok": True, "position_notional_estimate": 50.0,
                "quantity_estimate": 0.5, "leverage_used": 1.0,
            }

    monkeypatch.setattr(risk_module, "RiskEngine", PassingRisk)
    result = SelfDiagnosisEngine()._check_risk()
    assert result["status"] == "ok"


def test_risk_diagnostics_reports_failed_sizing_check(monkeypatch):
    from app.engines import risk as risk_module

    class FailingRisk:
        def validate_trade(self, **kwargs):
            return {"ok": False, "issues": ["test_failure"]}

    monkeypatch.setattr(risk_module, "RiskEngine", FailingRisk)
    result = SelfDiagnosisEngine()._check_risk()
    assert result["status"] == "error"
    assert "test_failure" in result["detail"]


def test_ai_diagnostics_accepts_connection_tested_non_openai_analysis_provider(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "anthropic", "enabled": True, "ready": True, "last_test_success": True, "tasks": ["analysis"]}
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "ok"
    assert result["primary_provider"] == "anthropic"
    assert result["cloud_advisor_count"] == 1
    assert result["connection_tested_count"] == 1



def test_ai_diagnostics_requires_every_enabled_analysis_advisor_to_be_tested(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "deepseek", "enabled": True, "ready": True,
             "last_test_success": True, "tasks": ["analysis"]},
            {"provider": "anthropic", "enabled": True, "ready": True,
             "last_test_success": None, "tasks": ["decision"]},
            {"provider": "openai", "enabled": True, "ready": True,
             "last_test_success": True, "tasks": ["news"]},
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "warning"
    assert result["primary_provider"] == "deepseek"
    assert result["cloud_advisor_count"] == 2
    assert result["connection_tested_count"] == 1
    assert result["untested_providers"] == ["anthropic"]


def test_ai_diagnostics_requires_successful_openai_connection_test(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "openai", "enabled": True, "ready": True, "last_test_success": None}
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "warning"
    assert result["primary_provider"] == "openai"
    assert "connection test" in result["detail"]


def test_ai_diagnostics_accepts_tested_openai_primary(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "openai", "enabled": True, "ready": True, "last_test_success": True}
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "ok"
    assert result["primary_provider"] == "openai"
    assert "connection-tested" in result["detail"]


def test_ai_diagnostics_reports_local_as_fallback_not_primary(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {"status": "not_configured", "providers": []})
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {
        "enabled": True, "connected": True, "model_available": True,
    })
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "warning"
    assert result["primary_provider"] is None
    assert result["local_fallback_connected"] is True
    assert "fallback-only" in result["detail"]


def test_ai_diagnostics_does_not_let_tested_secondary_mask_untested_primary(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "openai", "enabled": True, "ready": True, "last_test_success": None},
            {"provider": "openai", "enabled": True, "ready": True, "last_test_success": True},
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "warning"
    assert "connection test" in result["detail"]


def test_strategy_diagnostics_reports_partial_registry_maturity(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(core_service, "strategies", lambda: [
        {"key": "trend_following_ema", "status": "implemented"},
        {"key": "ict_smc_model", "status": "implemented_partial"},
        {"key": "funding_rate", "status": "implemented_partial"},
    ])
    result = SelfDiagnosisEngine()._check_strategies(None)
    assert result["status"] == "warning"
    assert result["maturity_counts"] == {
        "implemented": 1,
        "implemented_partial": 2,
    }
    assert result["verified_complete_count"] == 1
    assert "2 partial" in result["detail"]


def test_strategy_diagnostics_rejects_duplicate_registry_keys(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(core_service, "strategies", lambda: [
        {"key": "trend_following_ema", "status": "implemented"},
        {"key": "trend_following_ema", "status": "implemented"},
    ])
    result = SelfDiagnosisEngine()._check_strategies(None)
    assert result["status"] == "error"
    assert result["duplicate_keys"] == ["trend_following_ema"]


def test_strategy_diagnostics_accepts_unique_implemented_strategies(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(core_service, "strategies", lambda: [
        {"key": "trend_following_ema", "status": "implemented"},
        {"key": "momentum_macd_rsi", "status": "implemented"},
    ])
    result = SelfDiagnosisEngine()._check_strategies(None)
    assert result["status"] == "ok"
    assert result["verified_complete_count"] == 2


def test_news_source_diagnostics_reports_official_and_secondary_feeds(monkeypatch):
    from app.data import public_news

    monkeypatch.setattr(public_news, "NEWS_FEEDS", (
        {"name": "Official", "url": "https://official.example/rss", "source_type": "official_primary"},
        {"name": "Secondary", "url": "https://media.example/rss", "source_type": "secondary_media"},
    ))
    result = SelfDiagnosisEngine()._check_news_sources()
    assert result["status"] == "ok"
    assert result["feed_count"] == 2
    assert result["official_source_count"] == 1
    assert result["secondary_source_count"] == 1
    assert result["network_checked"] is False


def test_news_source_diagnostics_rejects_duplicate_or_insecure_feed_urls(monkeypatch):
    from app.data import public_news

    monkeypatch.setattr(public_news, "NEWS_FEEDS", (
        {"name": "Official", "url": "https://official.example/rss", "source_type": "official_primary"},
        {"name": "Duplicate", "url": "https://official.example/rss", "source_type": "secondary_media"},
        {"name": "Insecure", "url": "http://media.example/rss", "source_type": "secondary_media"},
    ))
    result = SelfDiagnosisEngine()._check_news_sources()
    assert result["status"] == "error"
    assert result["invalid_url_count"] == 1
    assert result["duplicate_urls"] == ["https://official.example/rss"]
