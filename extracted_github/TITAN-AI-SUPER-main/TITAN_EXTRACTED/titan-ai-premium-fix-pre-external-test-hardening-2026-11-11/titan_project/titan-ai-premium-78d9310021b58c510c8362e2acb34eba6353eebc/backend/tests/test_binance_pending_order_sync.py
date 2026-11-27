from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import OrderRecord, PositionRecord, SystemState
from app.services.binance_trading_worker import _sync_pending_orders


class FakeOrderStatusAdapter:
    def __init__(self, response, fee_result=None):
        self.response = response
        self.fee_result = fee_result or {
            "ok": True, "fees_paid_usdt": 0.0, "fee_breakdown": {},
        }
        self.order_calls = []
        self.fee_calls = 0

    def get_order(self, symbol, *, client_order_id=None, order_id=None):
        self.order_calls.append((symbol, client_order_id, order_id))
        return dict(self.response)

    def get_order_fee_quote(self, symbol, order_id, *, fills=None):
        self.fee_calls += 1
        return self.fee_result


def _session(tmp_path, *, order_meta=None, applied_quantity=0.04, position_quantity=0.06):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pending-order-sync.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    db.add(SystemState(
        id=1, environment_mode="demo", account_target="testnet",
        market_type="spot", bot_run_state="stopped", emergency_stop=False,
        autonomous_enabled=False, risk_limits={},
    ))
    db.add(PositionRecord(
        id=1, environment_mode="testnet", market_type="spot",
        symbol="BTCUSDT", side="long_spot", quantity=position_quantity,
        entry_avg_price=100.0, mark_price=100.0, leverage=1.0,
        realized_pnl=0.0, status="open", shadow=False, metadata_json={},
    ))
    metadata = dict(order_meta or {
        "intent": "close",
        "position_id": 1,
        "applied_filled_quantity": applied_quantity,
        "applied_filled_quote": 4.0,
        "applied_filled_quote_estimated": False,
    })
    db.add(OrderRecord(
        id=1, environment_mode="testnet", market_type="spot",
        symbol="BTCUSDT", side="sell", order_type="market",
        quantity=0.1, filled_quantity=applied_quantity,
        avg_fill_price=100.0, status="partially_filled", shadow=False,
        client_order_id="titan-close-1-abc123",
        exchange_order_id="101", idempotency_key="close-key-1",
        metadata_json=metadata,
    ))
    db.commit()
    return engine, db


def _partial_close_response(**changes):
    response = {
        "symbol": "BTCUSDT",
        "clientOrderId": "titan-close-1-abc123",
        "orderId": 101,
        "status": "PARTIALLY_FILLED",
        "origQty": "0.1",
        "executedQty": "0.07",
        "avgPrice": "102",
        "cummulativeQuoteQty": "7.14",
    }
    response.update(changes)
    return response


def test_close_partial_fill_uses_incremental_cumulative_quote_and_is_idempotent(tmp_path):
    engine, db = _session(tmp_path)
    adapter = FakeOrderStatusAdapter(_partial_close_response())
    try:
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")

        position = db.get(PositionRecord, 1)
        order = db.get(OrderRecord, 1)
        state = db.get(SystemState, 1)
        # Previously applied 0.04 @ 100. The next 0.03 has quote value
        # 7.14 - 4.00 = 3.14, i.e. an incremental exit price of 104.666...
        assert position.quantity == pytest.approx(0.03)
        assert position.realized_pnl == pytest.approx(0.14)
        assert order.filled_quantity == pytest.approx(0.07)
        assert order.metadata_json["applied_filled_quantity"] == pytest.approx(0.07)
        assert order.metadata_json["applied_filled_quote"] == pytest.approx(7.14)
        assert order.metadata_json["applied_filled_quote_estimated"] is False
        assert state.emergency_stop is True

        first_pnl = position.realized_pnl
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        db.refresh(position)
        assert position.quantity == pytest.approx(0.03)
        assert position.realized_pnl == pytest.approx(first_pnl)
        assert order.metadata_json["applied_filled_quote"] == pytest.approx(7.14)
        assert len(adapter.order_calls) == 2
    finally:
        db.close()
        engine.dispose()


def test_cumulative_quote_correction_without_new_quantity_adjusts_only_realized_pnl(tmp_path):
    engine, db = _session(tmp_path)
    adapter = FakeOrderStatusAdapter(_partial_close_response(
        executedQty="0.04",
        avgPrice="101",
        cummulativeQuoteQty="4.04",
    ))
    try:
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        position = db.get(PositionRecord, 1)
        order = db.get(OrderRecord, 1)
        # Same 0.04 already applied, but the authoritative cumulative quote is
        # 0.04 higher than the provisional average-price estimate.
        assert position.quantity == pytest.approx(0.06)
        assert position.realized_pnl == pytest.approx(0.04)
        assert order.metadata_json["applied_filled_quantity"] == pytest.approx(0.04)
        assert order.metadata_json["applied_filled_quote"] == pytest.approx(4.04)
    finally:
        db.close()
        engine.dispose()


def test_order_sync_rejects_mismatched_exchange_identity_and_latches_emergency(tmp_path):
    engine, db = _session(tmp_path)
    adapter = FakeOrderStatusAdapter(_partial_close_response(
        clientOrderId="titan-another-order",
    ))
    try:
        with pytest.raises(RuntimeError, match="binance_pending_order_identity_mismatch"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        order = db.get(OrderRecord, 1)
        state = db.get(SystemState, 1)
        position = db.get(PositionRecord, 1)
        assert order.status == "unknown"
        assert position.quantity == pytest.approx(0.06)
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
    finally:
        db.close()
        engine.dispose()


def test_legacy_close_fill_without_quote_watermark_fails_closed(tmp_path):
    engine, db = _session(tmp_path, order_meta={
        "intent": "close",
        "position_id": 1,
        "applied_filled_quantity": 0.04,
    })
    adapter = FakeOrderStatusAdapter(_partial_close_response())
    try:
        with pytest.raises(RuntimeError, match="pending_close_quote_watermark_missing"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        order = db.get(OrderRecord, 1)
        position = db.get(PositionRecord, 1)
        state = db.get(SystemState, 1)
        assert order.status == "unknown"
        assert position.quantity == pytest.approx(0.06)
        assert position.realized_pnl == pytest.approx(0.0)
        assert state.emergency_stop is True
    finally:
        db.close()
        engine.dispose()


def test_spot_order_lookup_derives_average_from_cumulative_quote_when_avg_price_absent(tmp_path):
    engine, db = _session(tmp_path)
    response = _partial_close_response()
    response.pop("avgPrice")
    adapter = FakeOrderStatusAdapter(response)
    try:
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        position = db.get(PositionRecord, 1)
        order = db.get(OrderRecord, 1)
        assert order.avg_fill_price == pytest.approx(7.14 / 0.07)
        assert position.quantity == pytest.approx(0.03)
        assert position.realized_pnl == pytest.approx(0.14)
        assert order.metadata_json["applied_filled_quote"] == pytest.approx(7.14)
    finally:
        db.close()
        engine.dispose()
