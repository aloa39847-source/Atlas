from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import star_app
from app.models import db as dbmod
from app.models.entities import OrderRecord, SystemState


def test_testnet_reconcile_passes_durable_local_pending_orders(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    captured = {}

    class FakeAdapter:
        def reconcile(self, local_open_orders):
            captured["orders"] = local_open_orders
            return {"ok": True, "local_open_orders_count": len(local_open_orders)}

    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="testnet",
            market_type="spot", bot_run_state="stopped", emergency_stop=True,
            autonomous_enabled=False, risk_limits={},
        ))
        db.add(OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="buy", quantity=0.01, status="submitted", shadow=False,
            client_order_id="titan-client-123", exchange_order_id="98765",
        ))
        db.commit()
        monkeypatch.setattr(star_app, "db_sess", lambda: factory())
        monkeypatch.setattr(star_app, "_binance_auth_error", lambda _request: None)
        monkeypatch.setattr(star_app, "_get_binance_adapter", lambda _market: FakeAdapter())
        monkeypatch.setattr("app.services.demo_worker_supervisor.demo_worker_supervisor.status", lambda: {
            "running": False, "account_target": None, "market": None,
        })
        from app.services import binance_trading_worker
        sync_calls = []
        monkeypatch.setattr(binance_trading_worker, "_sync_pending_orders", lambda session, adapter, target, market: (
            sync_calls.append((target, market)) or {
                "ok": True, "orders_checked": 1, "orders_status_updated": 1,
                "orders_with_fills": 0, "positions_updated": 0,
                "fees_pending": 0, "orders_resolved": 1,
            }
        ))

        response = asyncio.run(star_app.binance_testnet_reconcile(
            SimpleNamespace(query_params={"market": "spot"}),
        ))
        payload = json.loads(response.body)

        assert payload["ok"] is True
        assert payload["order_sync"]["orders_checked"] == 1
        assert payload["open_order_reconciliation"]["local_open_orders_count"] == 1
        assert payload["emergency_stop_active"] is True
        assert payload["orders_submitted"] is False
        assert sync_calls == [("testnet", "spot")]
        assert captured["orders"][0]["orderId"] == "98765"
        assert captured["orders"][0]["clientOrderId"] == "titan-client-123"
    finally:
        db.close()
        engine.dispose()



def test_testnet_reconcile_refuses_to_run_while_worker_is_active(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-active.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="testnet",
            market_type="spot", bot_run_state="running", emergency_stop=False,
            autonomous_enabled=True, risk_limits={},
        ))
        db.commit()
        monkeypatch.setattr(star_app, "db_sess", lambda: factory())
        monkeypatch.setattr(star_app, "_binance_auth_error", lambda _request: None)
        monkeypatch.setattr("app.services.demo_worker_supervisor.demo_worker_supervisor.status", lambda: {
            "running": True, "account_target": "testnet", "market": "spot",
        })
        called = []
        monkeypatch.setattr(star_app, "_get_binance_adapter", lambda _market: called.append(_market))

        response = asyncio.run(star_app.binance_testnet_reconcile(
            SimpleNamespace(query_params={"market": "spot"}),
        ))
        payload = json.loads(response.body)
        assert response.status_code == 409
        assert payload["error"] == "stop_testnet_worker_before_manual_reconciliation"
        assert called == []
    finally:
        db.close()
        engine.dispose()


def test_testnet_reconcile_requires_testnet_selection_and_matching_market(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconcile-scope.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo",
            market_type="spot", bot_run_state="stopped", emergency_stop=False,
            autonomous_enabled=False, risk_limits={},
        ))
        db.commit()
        monkeypatch.setattr(star_app, "db_sess", lambda: factory())
        monkeypatch.setattr(star_app, "_binance_auth_error", lambda _request: None)
        called = []
        monkeypatch.setattr(star_app, "_get_binance_adapter", lambda _market: called.append(_market))

        response = asyncio.run(star_app.binance_testnet_reconcile(
            SimpleNamespace(query_params={"market": "spot"}),
        ))
        payload = json.loads(response.body)
        assert response.status_code == 409
        assert payload["error"] == "testnet_account_not_selected"
        assert called == []
    finally:
        db.close()
        engine.dispose()
