from app.models import db as dbmod
from app.services.self_diagnosis import SelfDiagnosisEngine


class _Closable:
    def close(self):
        pass


def test_engine_diagnostics_warn_when_registered_engines_are_partial(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _Closable())
    monkeypatch.setattr(core_service, "engines", lambda db: [
        {"key": "risk_management", "status": "implemented_partial"},
        {"key": "news_analysis", "status": "needs_external_service"},
        {"key": "fundamental_analysis", "status": "not_implemented"},
    ])
    result = SelfDiagnosisEngine()._check_engines(None)
    assert result["status"] == "warning"
    assert result["status_counts"] == {
        "implemented_partial": 1,
        "needs_external_service": 1,
        "not_implemented": 1,
    }
    assert "3 not verified ready" in result["detail"]


def test_engine_diagnostics_accept_only_all_ready_engines(monkeypatch):
    from app.services.core_service import core_service

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _Closable())
    monkeypatch.setattr(core_service, "engines", lambda db: [
        {"key": "risk_management", "status": "ready"},
    ])
    result = SelfDiagnosisEngine()._check_engines(None)
    assert result["status"] == "ok"
    assert result["status_counts"] == {"ready": 1}


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


def test_ai_diagnostics_does_not_treat_non_openai_provider_as_primary(monkeypatch):
    from app.integrations import ai_providers, local_ai

    monkeypatch.setattr(ai_providers, "provider_status", lambda db: {
        "status": "ready", "providers": [
            {"provider": "anthropic", "enabled": True, "ready": True, "last_test_success": True}
        ],
    })
    monkeypatch.setattr(local_ai, "local_model_status", lambda: {"enabled": False, "connected": False})
    result = SelfDiagnosisEngine()._check_ai_providers(None)
    assert result["status"] == "warning"
    assert result["primary_provider"] is None
    assert "OpenAI primary is not configured" in result["detail"]


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
