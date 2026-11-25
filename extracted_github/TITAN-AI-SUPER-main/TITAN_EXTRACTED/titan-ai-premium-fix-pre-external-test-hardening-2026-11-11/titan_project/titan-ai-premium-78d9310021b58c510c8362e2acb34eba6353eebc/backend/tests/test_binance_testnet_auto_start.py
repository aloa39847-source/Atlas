from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import star_app


class RequestStub:
    pass


class FakeDB:
    def __init__(self):
        self.row = SimpleNamespace(
            bot_run_state="starting",
            autonomous_enabled=True,
            emergency_stop=False,
            bot_stop_reason=None,
            bot_stopped_at=None,
        )
        self.closed = False
        self.commits = 0

    def get(self, model, ident):
        return self.row

    def add(self, row):
        pass

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_successful_testnet_credential_link_starts_selected_market_automatically(monkeypatch, market):
    db = FakeDB()
    saved = []
    state_calls = []
    worker_calls = []

    class ConnectedTestnetAdapter:
        def __init__(self, config):
            assert config.environment == "testnet"
            assert config.market == market
            assert config.enabled is True

        def status(self):
            return {"configured": True, "connected": True, "execution_ready": True}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "api_key": "testnet-key", "api_secret": "testnet-secret", "market": market,
    }))
    monkeypatch.setattr(star_app, "BinanceTestnetAdapter", ConnectedTestnetAdapter)
    monkeypatch.setattr(star_app, "save_testnet_credentials",
                        lambda key, secret, selected_market: saved.append((key, secret, selected_market)))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {"bot_run_state": "stopped"})
    monkeypatch.setattr(star_app.core_service, "set_state",
                        lambda session, **kwargs: state_calls.append(kwargs) or {"account_target": "testnet"})
    monkeypatch.setattr(star_app.core_service, "start_bot",
                        lambda session, **kwargs: {"ok": True, "state": {"bot_run_state": "starting"}})

    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(
        demo_worker_supervisor, "start",
        lambda **kwargs: worker_calls.append(kwargs) or {
            "ok": True, "running": True, "account_target": "testnet", "market": market,
        },
    )

    response = asyncio.run(star_app.binance_testnet_save(RequestStub()))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["saved"] is True
    assert payload["auto_started"] is True
    assert payload["worker"]["running"] is True
    assert saved == [("testnet-key", "testnet-secret", market)]
    assert state_calls == [{"account_target": "testnet", "market_type": market}]
    assert worker_calls == [{"market": market, "account_target": "testnet", "interval_seconds": 60}]
    assert db.closed is True
    assert "testnet-secret" not in response.body.decode("utf-8")


def test_testnet_connection_failure_does_not_save_credentials_or_start_worker(monkeypatch):
    db = FakeDB()
    saved = []
    started = []

    class DisconnectedTestnetAdapter:
        def __init__(self, config):
            pass

        def status(self):
            return {"configured": True, "connected": False, "execution_ready": False}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "api_key": "bad-key", "api_secret": "bad-secret", "market": "spot",
    }))
    monkeypatch.setattr(star_app, "BinanceTestnetAdapter", DisconnectedTestnetAdapter)
    monkeypatch.setattr(star_app, "save_testnet_credentials", lambda *args: saved.append(args))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)

    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "start", lambda **kwargs: started.append(kwargs))

    response = asyncio.run(star_app.binance_testnet_save(RequestStub()))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 400
    assert payload["saved"] is False
    assert payload["error"] == "testnet_connection_failed"
    assert saved == []
    assert started == []
    # Credential verification fails before a database session is opened.
    assert db.closed is False


def test_testnet_credentials_remain_saved_but_worker_failure_is_reported_without_false_start(monkeypatch):
    db = FakeDB()
    saved = []

    class ConnectedTestnetAdapter:
        def __init__(self, config):
            pass

        def status(self):
            return {"configured": True, "connected": True, "execution_ready": True}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "api_key": "testnet-key", "api_secret": "testnet-secret", "market": "futures",
    }))
    monkeypatch.setattr(star_app, "BinanceTestnetAdapter", ConnectedTestnetAdapter)
    monkeypatch.setattr(star_app, "save_testnet_credentials",
                        lambda key, secret, market: saved.append((key, secret, market)))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {"bot_run_state": "stopped"})
    emergency_calls = []
    monkeypatch.setattr(star_app.core_service, "set_state",
                        lambda *args, **kwargs: emergency_calls.append(kwargs))
    monkeypatch.setattr(star_app.core_service, "start_bot", lambda *args, **kwargs: {"ok": True})

    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "start", lambda **kwargs: {
        "ok": False, "error": "trading_worker_startup_timeout",
    })
    monkeypatch.setattr(demo_worker_supervisor, "status", lambda: {"running": True})

    response = asyncio.run(star_app.binance_testnet_save(RequestStub()))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["saved"] is True
    assert payload["auto_started"] is False
    assert payload["worker"]["ok"] is False
    assert saved == [("testnet-key", "testnet-secret", "futures")]
    assert db.row.bot_run_state == "stopped"
    assert db.row.autonomous_enabled is False
    assert emergency_calls == [{"account_target": "testnet", "market_type": "futures"}, {"emergency_stop": True}]
    assert db.closed is True


def test_testnet_credentials_are_not_replaced_if_previous_worker_cannot_stop(monkeypatch):
    db = FakeDB()
    saved = []
    emergency_calls = []
    started = []

    class ConnectedTestnetAdapter:
        def __init__(self, config):
            pass
        def status(self):
            return {"configured": True, "connected": True, "execution_ready": True}

    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "body", lambda request: asyncio.sleep(0, result={
        "api_key": "replacement-key", "api_secret": "replacement-secret", "market": "spot",
    }))
    monkeypatch.setattr(star_app, "BinanceTestnetAdapter", ConnectedTestnetAdapter)
    monkeypatch.setattr(star_app, "save_testnet_credentials",
                        lambda *args: saved.append(args))
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "bot_run_state": "running", "account_target": "testnet",
    })
    monkeypatch.setattr(star_app.core_service, "set_state",
                        lambda session, **kwargs: emergency_calls.append(kwargs))

    from app.services.demo_worker_supervisor import demo_worker_supervisor
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: {
        "ok": False, "error": "trading_worker_stop_timeout",
    })
    monkeypatch.setattr(demo_worker_supervisor, "start", lambda **kwargs: started.append(kwargs))

    response = asyncio.run(star_app.binance_testnet_save(RequestStub()))
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 503
    assert payload["ok"] is False
    assert payload["saved"] is False
    assert payload["error"] == "previous_worker_did_not_stop"
    assert saved == []
    assert emergency_calls == [{"emergency_stop": True}]
    assert started == []
    assert db.closed is True
