from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import star_app
from app.models import db as dbmod
from app.models.entities import OrderRecord


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
        db.add(OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="buy", quantity=0.01, status="submitted", shadow=False,
            client_order_id="titan-client-123", exchange_order_id="98765",
        ))
        db.commit()
        monkeypatch.setattr(star_app, "db_sess", lambda: factory())
        monkeypatch.setattr(star_app, "_binance_auth_error", lambda _request: None)
        monkeypatch.setattr(star_app, "_get_binance_adapter", lambda _market: FakeAdapter())

        response = asyncio.run(star_app.binance_testnet_reconcile(
            SimpleNamespace(query_params={"market": "spot"}),
        ))
        payload = json.loads(response.body)

        assert payload["ok"] is True
        assert payload["local_open_orders_count"] == 1
        assert captured["orders"][0]["orderId"] == "98765"
        assert captured["orders"][0]["clientOrderId"] == "titan-client-123"
    finally:
        db.close()
        engine.dispose()
