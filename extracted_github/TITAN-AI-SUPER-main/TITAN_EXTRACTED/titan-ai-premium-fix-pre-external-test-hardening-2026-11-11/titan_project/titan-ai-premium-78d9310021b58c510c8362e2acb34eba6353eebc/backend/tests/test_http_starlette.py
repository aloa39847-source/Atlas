from __future__ import annotations

import json
import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from app.core.config import settings
from app.models import db as dbmod
from app.services import auth_service
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _setup_test_db(db_path):
    settings.database_url = f"sqlite:///{db_path}"
    dbmod.engine.dispose()
    dbmod.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, future=True)
    dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_http_endpoints_real_server(tmp_path, monkeypatch):
    from pathlib import Path
    db_path = tmp_path / "http.db"
    port = _free_port()
    backend_dir = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env.update({
        "TITAN_DATABASE_URL": f"sqlite:///{db_path}",
        "TITAN_AUTH_REQUIRED": "false",
        "TITAN_ALLOWED_EMAILS": "abdoavm02@gmail.com",
        "TITAN_LIVE_TRADING_ENABLED": "false",
        "PYTHONPATH": backend_dir,
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=backend_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        # wait for server
        ready = False
        for _ in range(60):
            try:
                r = httpx.get(f"{base}/api/health", timeout=1.0)
                if r.status_code == 200:
                    ready = True; break
            except Exception:
                time.sleep(0.25)
        if not ready:
            out = ""
            try: out = proc.stdout.read() if proc.stdout else ""
            except Exception: pass
            raise AssertionError(f"Server did not start. Output: {out[-2000:]}")
        with httpx.Client(base_url=base, timeout=10.0) as c:
            r = c.get("/api/health")
            assert r.status_code == 200 and r.json()["ok"] is True
            r = c.get("/api/system/status")
            assert r.status_code == 200
            j = r.json()
            assert j["state"]["environment_mode"] == "demo"
            assert any(e["key"] == "compliance_policy" for e in j["engines"])
            r = c.post("/api/analysis/decision", json={"symbol": "BTC/USDT", "market_type": "spot", "environment_mode": "demo"})
            assert r.status_code == 200, f"analysis endpoint returned {r.status_code}: {r.text}"
            a = r.json()
            assert a["action"] in {"buy","sell","long","short","wait"} and a["recorded"] is True
            assert "synthetic_labeled" in str(a["data_sources"])
            # The HTTP integration test must not start a resident worker that
            # would make external public-market requests during this endpoint test.
            # Successful worker lifecycle is covered by the isolated supervisor test.
            r = c.post("/api/master/start", json={"account_target": "testnet"})
            assert r.status_code == 400
            assert "binance_preflight_not_ready" in r.text
            assert "testnet_auto_trading_disabled_until_separate_approval" not in r.text
            r = c.post("/api/master/stop", json={"reason": "test"})
            assert r.status_code == 200 and r.json()["state"]["bot_run_state"] == "stopped"
            r = c.post("/api/compliance/check-order", json={"symbol": "BTC/USDT", "market_type": "spot", "environment_mode": "demo"})
            assert r.status_code == 200 and "gateway" in r.json()
            r = c.get("/api/knowledge/concepts", params={"q": "BOS"})
            assert r.status_code == 200 and len(r.json()["items"]) >= 1
            r = c.post("/api/knowledge", json={"title": "integration-source", "source_type": "manual", "notes": "integration notes", "reliability_score": 0.9})
            assert r.status_code == 200
            r = c.get("/api/knowledge")
            assert r.status_code == 200
            assert any(item["title"] == "integration-source" and item["notes"] == "integration notes" for item in r.json()["items"])
            r = c.post("/api/ai/providers", json={
                "provider": "openai", "name": "integration-provider", "model": "gpt-4o-mini",
                "api_key_ref": "env:TITAN_TEST_OPENAI_KEY", "enabled": True, "priority": 1, "tasks": ["test"],
            })
            assert r.status_code == 200
            r = c.get("/api/ai/providers")
            assert r.status_code == 200
            provider = next(item for item in r.json()["items"] if item["name"] == "integration-provider")
            assert provider["enabled"] is True and provider["api_key_ref_set"] is True
            r = c.post("/api/chat", json={"message": "ما حالة النظام؟"})
            assert r.status_code == 200 and r.json()["grounded"] is True
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        try:
            if proc.stdout: proc.stdout.close()
        except Exception:
            pass


def test_all_private_binance_testnet_routes_check_auth_first(monkeypatch):
    from app import star_app

    denial = star_app.J({"detail": "Authentication required"}, 401)
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: denial)

    class RequestStub:
        pass

    handlers = [
        star_app.binance_testnet_status,
        star_app.binance_testnet_futures_maintenance_brackets,
        star_app.binance_testnet_order,
        star_app.binance_testnet_account,
        star_app.binance_testnet_reconcile,
        star_app.binance_testnet_orders,
        star_app.binance_testnet_close,
        star_app.binance_testnet_save,
        star_app.binance_testnet_delete,
        star_app.binance_testnet_creds_status,
    ]
    for handler in handlers:
        response = asyncio.run(handler(RequestStub()))
        assert response.status_code == 401


def test_private_data_and_backup_routes_require_authentication(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def close(self):
            pass

    denial = star_app.J({"detail": "Authentication required"}, 401)
    monkeypatch.setattr(star_app, "db_sess", FakeDB)
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (None, denial))

    class RequestStub:
        query_params = {}

    handlers = [
        star_app.system_status,
        star_app.list_decisions,
        star_app.list_audit,
        star_app.knowledge_list,
        star_app.learning_list,
        star_app.backup_info,
        star_app.download_zip,
    ]
    for handler in handlers:
        response = asyncio.run(handler(RequestStub()))
        assert response.status_code == 401


def test_demo_cycle_rejects_string_boolean_before_execution(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def rollback(self):
            pass
        def close(self):
            pass

    async def payload(_request):
        return {"market_type": "spot", "environment_mode": "demo", "execute_shadow": "false"}

    monkeypatch.setattr(star_app, "db_sess", FakeDB)
    monkeypatch.setattr(star_app, "require_user", lambda request, db: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", payload)
    response = asyncio.run(star_app.demo_cycle(SimpleNamespace()))
    assert response.status_code == 400
    assert response.body


def test_auth_configuration_defaults_to_required_and_requires_allowlist(monkeypatch):
    monkeypatch.delenv("TITAN_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("TITAN_ALLOWED_EMAILS", raising=False)
    assert auth_service.auth_required() is True
    assert auth_service.email_allowed("user@example.com") is False

    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "false")
    assert auth_service.auth_required() is False
    assert auth_service.email_allowed("user@example.com") is True


def test_ai_provider_rejects_direct_secret_storage(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        added = False
        def add(self, row):
            self.added = True
        def commit(self):
            raise AssertionError("invalid provider config must not be committed")
        def close(self):
            pass
        def rollback(self):
            pass

    db = FakeDB()
    async def payload(_request):
        return {
            "provider": "openai", "name": "test", "model": "test-model",
            "api_key_ref": "sk-test-secret", "enabled": True,
        }

    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", payload)
    response = asyncio.run(star_app.ai_provider_create(SimpleNamespace()))
    assert response.status_code == 400
    assert db.added is False


def test_ai_provider_rejects_private_custom_endpoint(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        added = False
        def add(self, row):
            self.added = True
        def commit(self):
            raise AssertionError("private endpoint must not be committed")
        def close(self):
            pass
        def rollback(self):
            pass

    db = FakeDB()
    async def payload(_request):
        return {
            "provider": "openai_compatible", "name": "test", "model": "test-model",
            "api_key_ref": "env:TITAN_TEST_API_KEY",
            "base_url": "https://127.0.0.1/v1", "enabled": True,
        }

    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", payload)
    response = asyncio.run(star_app.ai_provider_create(SimpleNamespace()))
    assert response.status_code == 400
    assert db.added is False


def test_ai_provider_resolver_never_uses_plaintext_database_values(monkeypatch):
    from app.integrations.ai_providers import _resolve_api_key

    monkeypatch.setenv("TITAN_TEST_API_KEY", "env-secret")
    assert _resolve_api_key("env:TITAN_TEST_API_KEY") == "env-secret"
    assert _resolve_api_key("sk-plaintext-secret") is None
    assert _resolve_api_key(None) is None


def test_google_ai_key_is_sent_in_header_not_url(monkeypatch):
    from types import SimpleNamespace
    from app.integrations import ai_providers

    monkeypatch.setenv("TITAN_GEMINI_TEST_KEY", "gemini-secret")
    captured = {}

    def fake_http(method, url, headers, payload):
        captured.update({"method": method, "url": url, "headers": headers})
        return {"candidates": [{"content": {"parts": [{"text": "TITAN_OK"}]}}]}

    monkeypatch.setattr(ai_providers, "_http_json", fake_http)
    cfg = SimpleNamespace(
        provider="google", model="gemini-test", enabled=True,
        api_key_ref="env:TITAN_GEMINI_TEST_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    result = ai_providers.invoke_provider(cfg, [{"role": "user", "content": "test"}])
    assert result.ok is True
    assert "key=" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "gemini-secret"


def test_futures_shadow_portfolio_refreshes_with_mark_prices(monkeypatch):
    from types import SimpleNamespace
    from app import star_app
    from app.engines.market_data import MarketDataEngine

    class FakeDB:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    db = FakeDB()
    seen = {}
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: ({"id": 1}, None))
    monkeypatch.setattr(
        star_app.shadow_service, "get_portfolio",
        lambda session, env, market: {"positions": [{"symbol": "BTC/USDT"}], "snapshot": {}},
    )
    monkeypatch.setattr(
        MarketDataEngine, "get_public_last_prices",
        lambda self, symbols, market_type="spot": seen.update({"market_type": market_type}) or {"BTC/USDT": 101.5},
    )
    monkeypatch.setattr(
        star_app.shadow_service, "update_mark_prices",
        lambda session, env, market, prices: seen.update({"prices": prices, "market": market}) or 1,
    )
    monkeypatch.setattr(star_app.shadow_service, "record_snapshot", lambda session, env, market: None)
    request = SimpleNamespace(query_params={"environment_mode": "demo", "market_type": "futures"})
    response = asyncio.run(star_app.portfolio(request))
    data = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert data["mark_price_refresh"]["updated"] == 1
    assert data["mark_price_refresh"]["source"] == "binance_futures_mark_price"
    assert seen == {"market_type": "futures", "prices": {"BTC/USDT": 101.5}, "market": "futures"}


def test_production_cors_rejects_wildcard_default(monkeypatch):
    from starlette.middleware.cors import CORSMiddleware
    from app import star_app

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allowed_origins", "*")
    app = star_app.create_star_app()
    cors = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert cors.kwargs["allow_origins"] == []


def test_production_cors_preserves_explicit_origin_allowlist(monkeypatch):
    from starlette.middleware.cors import CORSMiddleware
    from app import star_app

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allowed_origins", "https://app.example.com, https://admin.example.com")
    app = star_app.create_star_app()
    cors = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert cors.kwargs["allow_origins"] == [
        "https://app.example.com", "https://admin.example.com",
    ]


def test_shadow_order_api_requires_idempotency_key_before_order_service(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def rollback(self): pass
        def close(self): pass

    async def payload(_request):
        return {
            "environment_mode": "demo", "market_type": "spot",
            "symbol": "BTC/USDT", "side": "buy", "quantity": 0.001,
            "price": 50000, "reference_price": 50000,
        }

    monkeypatch.setattr(star_app, "db_sess", lambda: FakeDB())
    monkeypatch.setattr(star_app, "require_user", lambda request, db: ({"id": 1}, None))
    monkeypatch.setattr(star_app, "body", payload)
    monkeypatch.setattr(
        star_app.shadow_service, "create_shadow_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("order service must not run")),
    )
    response = asyncio.run(star_app.shadow_order(SimpleNamespace()))
    assert response.status_code == 400
    assert json.loads(response.body.decode("utf-8"))["detail"] == "valid_idempotency_key_required"


def test_backup_info_reports_configured_credential_storage_path(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def close(self): pass

    monkeypatch.setattr(star_app, "db_sess", FakeDB)
    monkeypatch.setattr(star_app, "require_user", lambda request, db: ({"id": 1}, None))
    monkeypatch.setenv("TITAN_DATA_DIR", "/mnt/titan-persistent")
    monkeypatch.setattr(star_app.settings, "database_url", "sqlite:////mnt/titan-persistent/titan.db")
    response = asyncio.run(star_app.backup_info(SimpleNamespace()))
    data = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert data["credentials_path"] == "/mnt/titan-persistent/binance_testnet_creds.json"
    assert data["db_path"] == "/mnt/titan-persistent/titan.db"


def test_futures_maintenance_bracket_route_returns_verified_testnet_snapshot(monkeypatch):
    from types import SimpleNamespace
    from app import star_app

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    class Adapter:
        def get_futures_maintenance_brackets(self, symbol):
            assert symbol == "BTCUSDT"
            return {"ok": True, "symbol": symbol, "maintenance_brackets": [
                {"notionalFloor": 0, "notionalCap": 50000,
                 "maintMarginRatio": 0.004, "cum": 0},
            ], "source": "binance_futures_testnet_signed_read_only",
                "no_orders_submitted": True}
    monkeypatch.setattr(star_app, "_get_binance_adapter", lambda market: Adapter() if market == "futures" else None)
    request = SimpleNamespace(query_params={"symbol": "BTCUSDT"})
    response = asyncio.run(star_app.binance_testnet_futures_maintenance_brackets(request))
    assert response.status_code == 200
    assert b"binance_futures_testnet_signed_read_only" in response.body
    assert b"no_orders_submitted" in response.body


def test_security_headers_middleware_adds_safe_defaults_without_overwriting_existing_headers():
    from app.star_app import SecurityHeadersMiddleware

    sent = []
    async def downstream(scope, receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"application/json"), (b"x-frame-options", b"SAMEORIGIN")],
        })
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = SecurityHeadersMiddleware(downstream)
    asyncio.run(middleware({"type": "http", "method": "GET", "path": "/api/health"}, receive, send))
    headers = dict(sent[0]["headers"])
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"
    assert headers[b"referrer-policy"] == b"strict-origin-when-cross-origin"
    assert headers[b"permissions-policy"] == b"camera=(), microphone=(), geolocation=()"
    assert b"content-security-policy" in headers
    assert b"connect-src" in headers[b"content-security-policy"]
    assert headers[b"cache-control"] == b"no-store, private"
    assert headers[b"pragma"] == b"no-cache"


def test_security_headers_middleware_passes_non_http_scopes_unchanged():
    from app.star_app import SecurityHeadersMiddleware

    seen = []
    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    middleware = SecurityHeadersMiddleware(downstream)
    asyncio.run(middleware({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


@pytest.mark.parametrize("invalid_value", ["false", "true", 0, 1, None])
def test_master_stop_rejects_non_boolean_close_positions_before_stopping(monkeypatch, invalid_value):
    from types import SimpleNamespace
    from app import star_app

    class FakeDB:
        def close(self):
            pass

    async def payload(_request):
        return {"reason": "test", "close_positions": invalid_value}

    monkeypatch.setattr(star_app, "db_sess", FakeDB)
    monkeypatch.setattr(star_app, "require_user", lambda request, db: (SimpleNamespace(email="owner@example.com"), None))
    monkeypatch.setattr(star_app, "body", payload)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda db: {"bot_run_state": "running"})
    monkeypatch.setattr(
        star_app.core_service, "stop_bot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stop must not run for malformed boolean")),
    )

    response = asyncio.run(star_app.master_stop(SimpleNamespace()))
    assert response.status_code == 400
    assert b"close_positions_must_be_boolean" in response.body
