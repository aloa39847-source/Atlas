from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import star_app


class RequestStub:
    def __init__(self, payload=None, provider_id=1, query=None):
        self._payload = payload or {}
        self.path_params = {"provider_id": provider_id}
        self.query_params = query or {}

    async def body(self):
        return json.dumps(self._payload).encode("utf-8")


class QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, row=None, query_rows=None):
        self.row = row
        self.query_rows = list(query_rows or [])
        self.query_count = 0
        self.commits = 0
        self.closed = False
        self.added = []

    def get(self, model, key):
        return self.row

    def execute(self, statement):
        rows = self.query_rows[self.query_count] if self.query_count < len(self.query_rows) else []
        self.query_count += 1
        return QueryResult(rows)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def refresh(self, row):
        row.id = 42

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_ai_provider_control_toggles_only_the_selected_provider(monkeypatch):
    cfg = SimpleNamespace(id=1, enabled=True, provider="openai", model="test-model", name="primary")
    db = FakeDB(cfg)
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(star_app, "provider_status", lambda session: {"status": "all_disabled"})
    monkeypatch.setattr(star_app, "log_event", lambda *args, **kwargs: None)

    response = asyncio.run(star_app.ai_provider_control(RequestStub({"enabled": False})))
    assert response.status_code == 200
    assert cfg.enabled is False
    assert db.commits == 1
    assert db.closed is True
    assert b'"enabled":false' in response.body


def test_ai_provider_control_rejects_string_boolean(monkeypatch):
    cfg = SimpleNamespace(id=1, enabled=True)
    db = FakeDB(cfg)
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))

    response = asyncio.run(star_app.ai_provider_control(RequestStub({"enabled": "false"})))
    assert response.status_code == 400
    assert cfg.enabled is True
    assert db.commits == 0


def test_live_credentials_are_saved_for_read_only_monitoring_without_starting_trading(monkeypatch):
    db = FakeDB(SimpleNamespace(bot_run_state="stopped", autonomous_enabled=False, bot_stop_reason=""))
    saved = []
    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass
        def status(self):
            return {"configured": True, "connected": True, "execution_ready": False}
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "BinanceLiveAdapter", FakeAdapter)
    monkeypatch.setattr(star_app, "save_live_credentials", lambda key, secret, market: saved.append((key, secret, market)))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "set_state", lambda *args, **kwargs: {"account_target": "live"})
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "live", "live_confirmed_by_user": False,
        "bot_run_state": "stopped",
    })
    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "start", lambda **kwargs: {"ok": True, "running": True})
    response = asyncio.run(star_app.binance_live_save(RequestStub({
        "market": "futures", "api_key": "test-live-key", "api_secret": "test-live-secret",
    })))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["saved"] is True
    assert payload["live_trading_started"] is False
    assert payload["observer_started"] is True
    assert saved == [("test-live-key", "test-live-secret", "futures")]
    assert b"test-live-secret" not in response.body
    assert db.closed is True


def test_live_credentials_reject_unsupported_market_before_storage(monkeypatch):
    called = []
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "save_live_credentials", lambda *args: called.append(args))
    response = asyncio.run(star_app.binance_live_save(RequestStub({
        "market": "margin", "api_key": "key", "api_secret": "secret",
    })))
    assert response.status_code == 400
    assert called == []


def test_live_status_reports_read_only_until_explicit_start(monkeypatch):
    db = FakeDB()
    class FakeAdapter:
        def status(self):
            return {"configured": True, "connected": True, "execution_ready": False, "live_blocked": True}
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(star_app, "_get_binance_live_adapter", lambda market, session: FakeAdapter())
    monkeypatch.setattr(star_app, "settings", SimpleNamespace(live_trading_enabled=True))
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "live", "live_confirmed_by_user": False,
    })
    response = asyncio.run(star_app.binance_live_status(RequestStub(query={"market": "spot"})))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["live_blocked"] is True
    assert payload["server_live_enabled"] is True
    assert payload["user_live_confirmed"] is False
    assert payload["orders_submitted"] is False
    assert "read-only monitoring" in payload["note"]
    assert db.closed is True


def test_chat_reports_include_saved_external_ai_advisory_without_provider_secrets(monkeypatch):
    advisory = {
        "status": "completed",
        "advisors": [{
            "provider": "openai", "model": "gpt-test",
            "status": "completed", "advisory_text": "15m RSI supports caution",
            "action_authority": "none",
        }],
        "action_authority": "none",
        "decision_modified": False,
    }
    decision = SimpleNamespace(
        id=5, created_at=None, symbol="BTC/USDT", action="wait", strategy="test",
        confidence=0.5, entry_price=None, stop_loss=None, take_profit=None,
        leverage=1, rationale="test rationale", data_source="synthetic",
        environment_mode="demo", market_type="spot",
        indicators={"ai_evaluation": advisory},
    )
    db = FakeDB(query_rows=[[decision], [], [], [], []])
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))

    response = asyncio.run(star_app.chat_reports(RequestStub(query={"limit": "5"})))
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["decisions"][0]["ai_evaluation"] == advisory
    assert payload["decisions"][0]["ai_evaluation"]["action_authority"] == "none"
    assert db.closed is True

def test_ai_provider_create_encrypts_app_supplied_api_key_and_never_returns_it(monkeypatch):
    from app.services import credentials

    secret = "sk-test-app-supplied-secret-value"
    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(credentials, "encrypt_secret", lambda value: "v2:encrypted-ciphertext")
    response = asyncio.run(star_app.ai_provider_create(RequestStub({
        "provider": "openai", "name": "OpenAI primary", "model": "gpt-4o-mini",
        "api_key": secret, "enabled": True,
    })))

    assert response.status_code == 200
    assert db.commits == 1
    assert len(db.added) == 1
    row = db.added[0]
    assert row.api_key_ref == "encrypted:metadata"
    assert row.metadata_json == {"api_key_encrypted": "v2:encrypted-ciphertext"}
    assert secret not in str(row.metadata_json)
    assert secret.encode() not in response.body
    assert b"api_key" not in response.body
    assert db.closed is True


def test_ai_provider_create_rejects_unencrypted_raw_key_when_encryption_is_unavailable(monkeypatch):
    from app.services import credentials

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(credentials, "encrypt_secret", lambda value: (_ for _ in ()).throw(
        RuntimeError("strong_titan_secret_key_required_for_credential_storage")
    ))
    response = asyncio.run(star_app.ai_provider_create(RequestStub({
        "provider": "openai", "name": "OpenAI primary", "model": "gpt-4o-mini",
        "api_key": "sk-test-app-supplied-secret-value", "enabled": True,
    })))
    assert response.status_code == 400
    assert db.added == []
    assert db.commits == 0
    assert db.closed is True



def test_native_openai_key_cannot_be_sent_to_custom_base_url(monkeypatch):
    from app.services import credentials

    db = FakeDB()
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user", lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(credentials, "encrypt_secret", lambda value: "v2:encrypted-ciphertext")
    response = asyncio.run(star_app.ai_provider_create(RequestStub({
        "provider": "openai", "name": "OpenAI primary", "model": "gpt-model",
        "api_key": "sk-test-app-supplied-secret-value",
        "base_url": "https://attacker.example/v1", "enabled": True,
    })))
    assert response.status_code == 400
    assert json.loads(response.body)["detail"] == "custom_base_url_only_for_openai_compatible"
    assert db.added == []
    assert db.commits == 0
    assert db.closed is True


def test_live_read_only_observer_failure_leaves_account_stopped_and_unapproved(monkeypatch):
    row = SimpleNamespace(
        bot_run_state="stopped", autonomous_enabled=False, bot_stop_reason="",
        live_confirmed_by_user=False, bot_stopped_at=None,
    )
    db = FakeDB(row)
    saved = []

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass
        def status(self):
            return {"configured": True, "connected": True, "execution_ready": False}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "BinanceLiveAdapter", FakeAdapter)
    monkeypatch.setattr(star_app, "save_live_credentials", lambda key, secret, market: saved.append((key, secret, market)))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "set_state", lambda *args, **kwargs: {"account_target": "live"})
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "live", "live_confirmed_by_user": False,
        "bot_run_state": row.bot_run_state,
    })
    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "start", lambda **kwargs: {
        "ok": False, "error": "live_read_only_worker_start_failed",
    })

    response = asyncio.run(star_app.binance_live_save(RequestStub({
        "market": "spot", "api_key": "test-live-key", "api_secret": "test-live-secret",
    })))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["saved"] is True
    assert payload["observer_started"] is False
    assert payload["live_trading_started"] is False
    assert row.bot_run_state == "stopped"
    assert row.autonomous_enabled is False
    assert row.live_confirmed_by_user is False
    assert row.bot_stop_reason == "live_read_only_worker_start_failed"
    assert saved == [("test-live-key", "test-live-secret", "spot")]
    assert b"test-live-secret" not in response.body
    assert db.closed is True


def test_live_credentials_are_not_replaced_if_previous_worker_cannot_stop(monkeypatch):
    row = SimpleNamespace(bot_run_state="running", autonomous_enabled=True,
                          emergency_stop=False, bot_stop_reason="")
    db = FakeDB(row)
    saved = []
    emergency_calls = []

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass
        def status(self):
            return {"configured": True, "connected": True, "execution_ready": False}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "BinanceLiveAdapter", FakeAdapter)
    monkeypatch.setattr(star_app, "save_live_credentials", lambda *args: saved.append(args))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "testnet", "live_confirmed_by_user": False,
        "bot_run_state": "running",
    })
    monkeypatch.setattr(star_app.core_service, "set_state",
                        lambda session, **kwargs: emergency_calls.append(kwargs))
    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: {
        "ok": False, "error": "trading_worker_stop_timeout",
    })

    response = asyncio.run(star_app.binance_live_save(RequestStub({
        "market": "spot", "api_key": "replacement-live-key", "api_secret": "replacement-live-secret",
    })))
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["saved"] is False
    assert payload["error"] == "previous_worker_did_not_stop"
    assert saved == []
    assert emergency_calls == [{"emergency_stop": True}]
    assert db.closed is True


def test_failed_live_start_revokes_approval_and_stops_partial_worker(monkeypatch):
    row = SimpleNamespace(
        bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
        live_confirmed_by_user=False, bot_stop_reason="", bot_stopped_at=None,
    )
    db = FakeDB(row)
    stop_calls = []

    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app, "require_user",
                        lambda request, session: (SimpleNamespace(email="user"), None))
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "account_target": "live", "live_confirm": True,
    }))
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "live", "live_confirmed_by_user": True,
        "bot_run_state": "stopped",
    })

    def fail_after_confirmation(session, **kwargs):
        row.live_confirmed_by_user = True
        row.bot_run_state = "starting"
        row.autonomous_enabled = True
        raise ValueError("binance_preflight_not_ready")

    monkeypatch.setattr(star_app.core_service, "start_bot", fail_after_confirmation)
    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: stop_calls.append(kwargs) or {
        "ok": True, "stopped": True,
    })

    response = asyncio.run(star_app.master_start(RequestStub()))
    payload = json.loads(response.body)

    assert response.status_code == 400
    assert "binance_preflight_not_ready" in payload["detail"]
    assert stop_calls == [{"timeout": 30.0}]
    assert row.live_confirmed_by_user is False
    assert row.bot_run_state == "stopped"
    assert row.autonomous_enabled is False
    assert row.bot_stop_reason == "live_start_failed"
    assert db.closed is True
