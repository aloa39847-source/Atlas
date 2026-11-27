from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import OrderRecord, SystemState
from app.services.binance_execution import binance_execution_service


class FakeTestnetAdapter:
    def __init__(self, place_result=None, lookup_result=None, fee_result=None, place_error=None):
        self.place_result = place_result or {"sent_to_exchange": False, "error": "simulated_transport_failure"}
        self.lookup_result = lookup_result or {"error": "order_lookup_not_found"}
        self.fee_result = fee_result or {
            "ok": False, "pending": True, "error": "fee_history_unavailable",
        }
        self.place_error = place_error
        self.place_calls = 0
        self.lookup_calls = 0
        self.lookup_client_order_ids = []
        self.fee_calls = 0

    def reconcile(self, local_open_orders):
        return {"ok": True, "local_open_orders_count": len(local_open_orders)}

    def place_order(self, **kwargs):
        self.place_calls += 1
        if self.place_error is not None:
            raise self.place_error
        return self.place_result

    def get_order(self, symbol, *, client_order_id=None, order_id=None):
        self.lookup_calls += 1
        self.lookup_client_order_ids.append(client_order_id)
        if not isinstance(self.lookup_result, dict):
            return self.lookup_result
        snapshot = dict(self.lookup_result)
        snapshot.setdefault("symbol", symbol.replace("/", "").upper())
        if client_order_id:
            snapshot.setdefault("clientOrderId", client_order_id)
        return snapshot

    def get_order_fee_quote(self, symbol, order_id, *, fills=None):
        self.fee_calls += 1
        return self.fee_result


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
        "error": "timed out while waiting for Binance response",
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
        assert result["outcome_uncertain"] is True
        assert result["sent_to_exchange"] is None
        assert result["reconciled_after_uncertain_submit"] is False
        assert adapter.lookup_calls == 1
        assert adapter.lookup_client_order_ids == [order.client_order_id]
        assert (order.metadata_json or {})["submission_reconciliation"]["resolved"] is False

        duplicate = _execute(db, "testnet-timeout-order-0001")
        assert duplicate["duplicate"] is True
        assert duplicate["sent_to_exchange"] is False
        assert duplicate["status"] == "unknown"
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
    finally:
        db.close()
        engine.dispose()



def test_timed_out_submission_is_recovered_by_read_only_client_order_id_lookup(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter(
        {
            "sent_to_exchange": False,
            "error": "timed out while waiting for Binance response",
        },
        lookup_result={
            "symbol": "BTCUSDT",
            "orderId": 778899,
            "status": "FILLED",
            "origQty": "0.1",
            "executedQty": "0.1",
            "avgPrice": "101.25",
            "cummulativeQuoteQty": "10.125",
        },
        fee_result={"ok": True, "fees_paid_usdt": 0.01, "fee_breakdown": {"USDT": 0.01}},
    )
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr("app.services.binance_execution.log_event", lambda *args, **kwargs: None)
    try:
        result = _execute(db, "testnet-timeout-order-recovered-0001")
        order = db.execute(select(OrderRecord)).scalar_one()
        state = db.get(SystemState, 1)

        assert result["ok"] is True
        assert result["sent_to_exchange"] is True
        assert result["reconciled_after_uncertain_submit"] is True
        assert result["status"] == "filled"
        assert order.status == "filled"
        assert order.exchange_order_id == "778899"
        assert order.filled_quantity == 0.1
        assert order.avg_fill_price == 101.25
        assert order.fees_paid == 0.01
        assert (order.metadata_json or {})["submission_reconciliation"]["method"] == "read_only_client_order_id_lookup"
        assert state.emergency_stop is False
        assert state.bot_run_state == "running"
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
        assert adapter.fee_calls == 1

        duplicate = _execute(db, "testnet-timeout-order-recovered-0001")
        assert duplicate["duplicate"] is True
        assert duplicate["sent_to_exchange"] is False
        assert duplicate["status"] == "filled"
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
    finally:
        db.close()
        engine.dispose()


def test_timed_out_submission_recovered_as_partial_fill_latches_emergency_stop(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter(
        {
            "sent_to_exchange": False,
            "error": "connection timed out after request write",
        },
        lookup_result={
            "symbol": "BTCUSDT",
            "orderId": 998877,
            "status": "PARTIALLY_FILLED",
            "origQty": "0.1",
            "executedQty": "0.04",
            "avgPrice": "100.5",
            "cummulativeQuoteQty": "4.02",
        },
    )
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr("app.services.binance_execution.log_event", lambda *args, **kwargs: None)
    try:
        result = _execute(db, "testnet-timeout-order-partial-0001")
        order = db.execute(select(OrderRecord)).scalar_one()
        state = db.get(SystemState, 1)

        assert result["ok"] is True
        assert result["sent_to_exchange"] is True
        assert result["status"] == "partially_filled"
        assert order.status == "partially_filled"
        assert order.filled_quantity == 0.04
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        assert state.bot_stop_reason == "partial_exchange_fill_manual_reconciliation_required"
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
    finally:
        db.close()
        engine.dispose()



def test_adapter_exception_after_durable_submission_boundary_uses_read_only_lookup(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter(
        lookup_result={
            "status": "FILLED",
            "orderId": 445566,
            "executedQty": "0.1",
            "avgPrice": "99.5",
            "cummulativeQuoteQty": "9.95",
        },
        fee_result={"ok": True, "fees_paid_usdt": 0.005, "fee_breakdown": {"USDT": 0.005}},
        place_error=TimeoutError("transport raised after request write"),
    )
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr("app.services.binance_execution.log_event", lambda *args, **kwargs: None)
    try:
        result = _execute(db, "testnet-submit-exception-lookup-0001")
        order = db.execute(select(OrderRecord)).scalar_one()
        state = db.get(SystemState, 1)

        assert result["ok"] is True
        assert result["reconciled_after_uncertain_submit"] is True
        assert result["status"] == "filled"
        assert order.status == "filled"
        assert order.exchange_order_id == "445566"
        assert order.fees_paid == 0.005
        assert state.emergency_stop is False
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
    finally:
        db.close()
        engine.dispose()


def test_uncertain_submission_lookup_with_mismatched_client_order_id_stays_unknown(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    adapter = FakeTestnetAdapter(
        {
            "sent_to_exchange": False,
            "error": "timed out after submission",
        },
        lookup_result={
            "symbol": "BTCUSDT",
            "clientOrderId": "titan-some-other-order",
            "orderId": 112233,
            "status": "FILLED",
            "executedQty": "0.1",
            "avgPrice": "100",
        },
    )
    monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr("app.services.binance_execution.log_event", lambda *args, **kwargs: None)
    try:
        result = _execute(db, "testnet-timeout-wrong-client-id-0001")
        order = db.execute(select(OrderRecord)).scalar_one()
        state = db.get(SystemState, 1)

        assert result["ok"] is False
        assert result["outcome_uncertain"] is True
        assert result["sent_to_exchange"] is None
        assert order.status == "unknown"
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        assert adapter.place_calls == 1
        assert adapter.lookup_calls == 1
        assert adapter.fee_calls == 0
    finally:
        db.close()
        engine.dispose()
