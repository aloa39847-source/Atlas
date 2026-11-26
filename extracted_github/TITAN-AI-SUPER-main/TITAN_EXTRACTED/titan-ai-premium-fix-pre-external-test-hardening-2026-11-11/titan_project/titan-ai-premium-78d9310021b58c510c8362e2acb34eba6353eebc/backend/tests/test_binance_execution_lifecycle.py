from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import OrderRecord, SystemState
from app.services.binance_execution import binance_execution_service


class FakeTestnetAdapter:
    def __init__(self, place_result):
        self.place_result = place_result
        self.place_calls = 0

    def reconcile(self, local_open_orders):
        return {"ok": True, "local_open_orders_count": len(local_open_orders)}

    def place_order(self, **kwargs):
        self.place_calls += 1
        return self.place_result


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'binance-execution.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    state = SystemState(
        id=1,
        environment_mode="testnet",
        account_target="testnet",
        market_type="spot",
        bot_run_state="running",
        autonomous_enabled=True,
        emergency_stop=False,
        risk_limits={},
    )
    db.add(state)
    db.commit()
    return engine, db


def _execute(db, key):
    return binance_execution_service.execute_order(
        db,
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        quantity=0.1,
        price=100.0,
        leverage=1.0,
        idempotency_key=key,
        order_metadata={"reference_price": 100.0},
    )


def test_partial_fill_is_durably_recorded_and_duplicate_request_is_not_resubmitted(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter({
        "sent_to_exchange": True,
        "response": {
            "status": "PARTIALLY_FILLED",
            "orderId": 12345,
            "clientOrderId": "titan-test-partial",
            "executedQty": "0.04",
            "avgPrice": "100.0",
        },
    })
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    try:
        result = _execute(db, "testnet-partial-fill-0001")
        assert result["ok"] is True
        assert result["status"] == "partially_filled"
        order = db.execute(select(OrderRecord)).scalar_one()
        assert order.status == "partially_filled"
        assert order.filled_quantity == 0.04
        assert order.exchange_order_id == "12345"

        duplicate = _execute(db, "testnet-partial-fill-0001")
        assert duplicate["duplicate"] is True
        assert duplicate["sent_to_exchange"] is False
        assert adapter.place_calls == 1
    finally:
        db.close()
        engine.dispose()


def test_timed_out_testnet_submission_is_unknown_and_never_resent(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter({
        "sent_to_exchange": False,
        "error": "<urlopen error timed out while waiting for Binance response>",
    })
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr("app.services.binance_execution.log_event", lambda *args, **kwargs: None)
    try:
        result = _execute(db, "testnet-timeout-order-0001")
        assert result["ok"] is False
        assert result["status"] == "unknown"
        assert result["order_id"] is not None

        order = db.execute(select(OrderRecord)).scalar_one()
        state = db.get(SystemState, 1)
        assert order.status == "unknown"
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
        assert state.bot_stop_reason == "binance_order_outcome_uncertain"

        duplicate = _execute(db, "testnet-timeout-order-0001")
        assert duplicate["duplicate"] is True
        assert duplicate["sent_to_exchange"] is False
        assert duplicate["status"] == "unknown"
        assert adapter.place_calls == 1
    finally:
        db.close()
        engine.dispose()
