from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import star_app
from app.services.demo_worker_supervisor import demo_worker_supervisor


class FakeDB:
    def __init__(self):
        self.closed = False
        self.rollbacks = 0

    def close(self):
        self.closed = True

    def rollback(self):
        self.rollbacks += 1


def _request(market=None):
    params = {} if market is None else {"market": market}
    return SimpleNamespace(query_params=params)


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_testnet_delete_rejects_invalid_market_without_deleting_all_credentials(monkeypatch):
    deleted = []
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "delete_testnet_credentials", lambda market=None: deleted.append(market))
    monkeypatch.setattr(star_app, "db_sess", lambda: (_ for _ in ()).throw(
        AssertionError("invalid market must be rejected before opening the database")
    ))

    response = asyncio.run(star_app.binance_testnet_delete(_request("invalid")))

    assert response.status_code == 400
    assert _payload(response)["error"] == "market_must_be_spot_or_futures"
    assert deleted == []


def test_testnet_delete_stops_selected_worker_before_removing_credentials(monkeypatch):
    db = FakeDB()
    calls = []
    deleted = []
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "testnet", "market_type": "spot", "bot_run_state": "running",
    })
    monkeypatch.setattr(demo_worker_supervisor, "status", lambda: {
        "running": True, "account_target": "testnet", "market": "spot",
    })
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: (
        calls.append(("stop", kwargs)) or {"ok": True, "stopped": True}
    ))
    monkeypatch.setattr(star_app.core_service, "stop_bot", lambda session, **kwargs: (
        calls.append(("stop_bot", kwargs)) or {"ok": True}
    ))
    monkeypatch.setattr(star_app, "delete_testnet_credentials", lambda market=None: (
        deleted.append(market) or {"ok": True, "deleted": True, "files_deleted": 1}
    ))

    response = asyncio.run(star_app.binance_testnet_delete(_request("spot")))

    assert response.status_code == 200
    assert _payload(response)["deleted"] is True
    assert calls == [
        ("stop", {"timeout": 30.0}),
        ("stop_bot", {"reason": "delete_testnet_credentials"}),
    ]
    assert deleted == ["spot"]
    assert db.closed is True


def test_testnet_delete_preserves_credentials_and_latches_emergency_if_worker_cannot_stop(monkeypatch):
    db = FakeDB()
    deleted = []
    emergency = []
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "testnet", "market_type": "futures", "bot_run_state": "running",
    })
    monkeypatch.setattr(demo_worker_supervisor, "status", lambda: {
        "running": True, "account_target": "testnet", "market": "futures",
    })
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: {
        "ok": False, "error": "trading_worker_stop_timeout",
    })
    monkeypatch.setattr(star_app.core_service, "set_state", lambda session, **kwargs: (
        emergency.append(kwargs) or {"emergency_stop": True}
    ))
    monkeypatch.setattr(star_app, "delete_testnet_credentials", lambda market=None: deleted.append(market))

    response = asyncio.run(star_app.binance_testnet_delete(_request("futures")))

    assert response.status_code == 503
    data = _payload(response)
    assert data["deleted"] is False
    assert data["error"] == "account_worker_did_not_stop_credentials_retained"
    assert emergency == [{"emergency_stop": True}]
    assert deleted == []
    assert db.closed is True


def test_live_delete_stops_read_only_observer_before_removing_credentials(monkeypatch):
    db = FakeDB()
    calls = []
    deleted = []
    monkeypatch.setattr(star_app, "_binance_auth_error", lambda request: None)
    monkeypatch.setattr(star_app, "db_sess", lambda: db)
    monkeypatch.setattr(star_app.core_service, "get_state", lambda session: {
        "account_target": "live", "market_type": "futures", "bot_run_state": "running",
    })
    monkeypatch.setattr(demo_worker_supervisor, "status", lambda: {
        "running": True, "account_target": "live", "market": "futures",
    })
    monkeypatch.setattr(demo_worker_supervisor, "stop", lambda **kwargs: (
        calls.append(("stop", kwargs)) or {"ok": True, "stopped": True}
    ))
    monkeypatch.setattr(star_app.core_service, "stop_bot", lambda session, **kwargs: (
        calls.append(("stop_bot", kwargs)) or {"ok": True}
    ))
    monkeypatch.setattr(star_app, "delete_live_credentials", lambda market: (
        deleted.append(market) or {"ok": True, "deleted": True}
    ))

    response = asyncio.run(star_app.binance_live_delete(_request("futures")))

    assert response.status_code == 200
    assert _payload(response)["deleted"] is True
    assert calls == [
        ("stop", {"timeout": 30.0}),
        ("stop_bot", {"reason": "delete_live_credentials"}),
    ]
    assert deleted == ["futures"]
    assert db.closed is True
