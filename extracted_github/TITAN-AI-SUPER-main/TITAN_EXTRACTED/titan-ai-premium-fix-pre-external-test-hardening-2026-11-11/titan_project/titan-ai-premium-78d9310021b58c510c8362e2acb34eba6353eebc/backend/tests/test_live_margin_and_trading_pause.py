from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.core.config import Settings, settings
from app.models.entities import OrderRecord, PositionRecord, SystemState
from app.services.core_service import core_service
from app.services.binance_trading_worker import _daily_loss_exceeded, _effective_futures_leverage_cap, _entry_notional_cap, _open_signal, _sync_pending_orders
from app.services.binance_execution import binance_execution_service


def test_spot_entry_uses_approved_100_usdt_notional_cap():
    assert _entry_notional_cap("live", "spot", 1, 500) == 100


def test_futures_entry_respects_800_usdt_cap_and_remaining_exposure():
    assert _entry_notional_cap("live", "futures", 5, 500) == 500
    assert _entry_notional_cap("live", "futures", 5, 500) <= settings.max_futures_trade_notional


def test_futures_entry_respects_lower_remaining_portfolio_exposure():
    assert _entry_notional_cap("live", "futures", 10, 75) == 75


def test_spot_and_futures_caps_are_consistent_across_accounts():
    assert _entry_notional_cap("demo", "spot", 1, 500) == 100
    assert _entry_notional_cap("testnet", "spot", 1, 500) == 100
    assert _entry_notional_cap("testnet", "futures", 10, 1000) == 800


def test_paused_trading_blocks_new_entries_without_touching_exchange():
    _open_signal(
        None, None, {"symbol": "BTCUSDT", "action": "buy", "confidence": 0.99},
        "live", "spot", 100.0, 100.0, {"trading_paused": True}, 0.0, 0,
    )


def test_trading_pause_is_persisted_and_does_not_set_emergency_stop(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'trading_pause.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", factory)
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()
    db = factory()
    try:
        core_service.ensure_bootstrap(db)
        paused = core_service.set_trading_paused(db, True)
        state = db.get(SystemState, 1)
        assert paused["state"]["trading_paused"] is True
        assert state.emergency_stop is False
        assert state.bot_run_state == "stopped"
        resumed = core_service.set_trading_paused(db, False)
        assert resumed["state"]["trading_paused"] is False
        assert state.emergency_stop is False
    finally:
        db.close()
        engine.dispose()



def _execution_test_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live_execution_guard.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, factory()


def test_execution_boundary_requires_explicit_live_confirmation_before_sizing(tmp_path):
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="live", account_target="live", market_type="futures",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            live_confirmed_by_user=False, risk_limits={},
        ))
        db.commit()
        result = binance_execution_service.execute_order(
            db, symbol="BTCUSDT", side="BUY", order_type="MARKET",
            quantity=1.0, price=None, leverage=5.0, reduce_only=False,
            idempotency_key="live-margin-cap-test-001", live_worker_ready=True,
            order_metadata={"intent": "open", "reference_price": 100.0},
        )
        assert result["error"] == "live_requires_explicit_user_confirmation"
        assert result["sent_to_exchange"] is False
    finally:
        db.close()
        engine.dispose()


def test_execution_boundary_blocks_new_entries_while_trading_is_paused(tmp_path):
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="testnet", market_type="futures",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            risk_limits={"trading_paused": True},
        ))
        db.commit()
        result = binance_execution_service.execute_order(
            db, symbol="BTCUSDT", side="BUY", order_type="MARKET",
            quantity=0.001, price=100.0, leverage=2.0, reduce_only=False,
            idempotency_key="paused-entry-test-001", live_worker_ready=False,
            order_metadata={"intent": "open", "reference_price": 100.0},
        )
        assert result["error"] == "new_trade_entries_paused"
        assert result["sent_to_exchange"] is False
    finally:
        db.close()
        engine.dispose()


def test_worker_daily_loss_cap_uses_seven_percent_default():
    from app.services.binance_trading_worker import _daily_loss_cap_usdt
    assert _daily_loss_cap_usdt(1_000.0, {}) == 70.0


def test_worker_daily_loss_cap_never_accepts_configured_percentage_above_seven():
    from app.services.binance_trading_worker import _daily_loss_cap_usdt
    assert _daily_loss_cap_usdt(1_000.0, {"max_daily_loss_cap": 0.10}) == 70.0


def test_worker_daily_loss_cap_respects_stricter_configured_percentage_and_cash_limit():
    from app.services.binance_trading_worker import _daily_loss_cap_usdt
    assert _daily_loss_cap_usdt(1_000.0, {"max_daily_loss_cap": 0.05}) == 50.0
    assert _daily_loss_cap_usdt(1_000.0, {"max_daily_loss_cap": 0.07, "max_daily_loss_usdt": 40}) == 40.0


def test_worker_daily_loss_cap_fails_closed_for_invalid_cash_limit():
    from app.services.binance_trading_worker import _daily_loss_cap_usdt
    assert _daily_loss_cap_usdt(1_000.0, {"max_daily_loss_usdt": 0}) == 0.0


def test_emergency_stop_blocks_new_testnet_entries_but_allows_position_exit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="testnet", account_target="testnet", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=True,
            risk_limits={},
        ))
        db.commit()

        blocked = binance_execution_service.execute_order(
            db, symbol="BTCUSDT", side="BUY", order_type="MARKET",
            quantity=0.001, price=100.0, leverage=1.0, reduce_only=False,
            idempotency_key="emergency-new-entry-01", live_worker_ready=False,
            order_metadata={"intent": "open", "reference_price": 100.0},
        )
        assert blocked["error"] == "master_worker_not_running"
        assert blocked["sent_to_exchange"] is False

        class FakeAdapter:
            def reconcile(self, local_open_orders):
                return {"ok": True}
            def place_order(self, **kwargs):
                return {"sent_to_exchange": True, "response": {
                    "status": "FILLED", "orderId": 12345,
                    "clientOrderId": kwargs["idempotency_key"],
                    "executedQty": "0.001", "avgPrice": "100",
                    "cummulativeQuoteQty": "0.1",
                }}

        fake = FakeAdapter()
        monkeypatch.setattr(
            binance_execution_service, "adapter_for",
            lambda state, live_worker_ready=False: fake,
        )
        monkeypatch.setattr(
            binance_execution_service, "preflight",
            lambda db, live_worker_ready=False, allow_emergency_exit=False: {
                "ok": True, "ready": allow_emergency_exit,
            },
        )
        closing = binance_execution_service.execute_order(
            db, symbol="BTCUSDT", side="SELL", order_type="MARKET",
            quantity=0.001, price=100.0, leverage=1.0, reduce_only=False,
            idempotency_key="emergency-close-order-01", live_worker_ready=False,
            order_metadata={"intent": "close", "position_id": 1},
        )
        assert closing["ok"] is True
        assert closing["sent_to_exchange"] is True
        assert closing["status"] == "filled"
    finally:
        db.close()
        engine.dispose()


def test_execution_partial_fill_is_durably_recorded_and_duplicate_key_never_resubmits(tmp_path, monkeypatch):
    from app.models.entities import OrderRecord
    engine, db = _execution_test_db(tmp_path)
    calls = {"place": 0}

    class FakeAdapter:
        def status(self):
            return {"configured": True, "connected": True}
        def reconcile(self, local_open_orders):
            return {"ok": True}
        def place_order(self, **kwargs):
            calls["place"] += 1
            return {"sent_to_exchange": True, "response": {
                "status": "PARTIALLY_FILLED", "orderId": 77,
                "clientOrderId": "titan-partial-01", "executedQty": "0.0004",
                "avgPrice": "50000",
            }}

    try:
        db.add(SystemState(
            id=1, environment_mode="testnet", account_target="testnet", market_type="spot",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            risk_limits={},
        ))
        db.commit()
        monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *a, **k: FakeAdapter())
        monkeypatch.setattr(binance_execution_service, "preflight", lambda *a, **k: {"ready": True})
        args = dict(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
                    price=50000, leverage=1, reduce_only=False,
                    idempotency_key="partial-fill-idempotency-01", live_worker_ready=False,
                    order_metadata={"intent": "open", "reference_price": 50000})
        first = binance_execution_service.execute_order(db, **args)
        assert first["sent_to_exchange"] is True
        assert first["status"] == "partially_filled"
        order = db.get(OrderRecord, first["order_id"])
        assert order.filled_quantity == 0.0004
        second = binance_execution_service.execute_order(db, **args)
        assert second["duplicate"] is True
        assert calls["place"] == 1
    finally:
        db.close()
        engine.dispose()


def test_uncertain_execution_outcome_latches_emergency_stop_and_never_resubmits(tmp_path, monkeypatch):
    from app.models.entities import OrderRecord
    engine, db = _execution_test_db(tmp_path)
    calls = {"place": 0}

    class FakeAdapter:
        def status(self):
            return {"configured": True, "connected": True}
        def reconcile(self, local_open_orders):
            return {"ok": True}
        def place_order(self, **kwargs):
            calls["place"] += 1
            return {"sent_to_exchange": False, "error": "timeout waiting for exchange response"}

    try:
        state = SystemState(
            id=1, environment_mode="testnet", account_target="testnet", market_type="spot",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            risk_limits={},
        )
        db.add(state)
        db.commit()
        monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *a, **k: FakeAdapter())
        monkeypatch.setattr(binance_execution_service, "preflight", lambda *a, **k: {"ready": True})
        args = dict(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
                    price=50000, leverage=1, reduce_only=False,
                    idempotency_key="uncertain-idempotency-key-01", live_worker_ready=False,
                    order_metadata={"intent": "open", "reference_price": 50000})
        first = binance_execution_service.execute_order(db, **args)
        db.refresh(state)
        assert first["status"] == "unknown"
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        second = binance_execution_service.execute_order(db, **args)
        assert second["duplicate"] is True
        assert second["sent_to_exchange"] is False
        assert calls["place"] == 1
        recorded = db.get(OrderRecord, first["order_id"])
        assert recorded.status == "unknown"
    finally:
        db.close()
        engine.dispose()


def test_execution_exception_after_durable_order_record_latches_unknown_and_never_resubmits(tmp_path, monkeypatch):
    from app.models.entities import OrderRecord
    engine, db = _execution_test_db(tmp_path)
    calls = {"place": 0}

    class RaisingAdapter:
        def status(self):
            return {"configured": True, "connected": True}
        def reconcile(self, local_open_orders):
            return {"ok": True}
        def place_order(self, **kwargs):
            calls["place"] += 1
            raise RuntimeError("simulated socket read timed out after request transmission")

    try:
        state = SystemState(
            id=1, environment_mode="testnet", account_target="testnet", market_type="spot",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            risk_limits={},
        )
        db.add(state)
        db.commit()
        monkeypatch.setattr(binance_execution_service, "adapter_for", lambda *a, **k: RaisingAdapter())
        monkeypatch.setattr(binance_execution_service, "preflight", lambda *a, **k: {"ready": True})
        args = dict(
            symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
            price=50000, leverage=1, reduce_only=False,
            idempotency_key="raised-exception-idempotency-01", live_worker_ready=False,
            order_metadata={"intent": "open", "reference_price": 50000},
        )
        first = binance_execution_service.execute_order(db, **args)
        db.refresh(state)
        assert first["status"] == "unknown"
        assert first["error"] == "binance_execution_outcome_uncertain"
        assert state.emergency_stop is True
        assert state.bot_run_state == "stopped"
        recorded = db.get(OrderRecord, first["order_id"])
        assert recorded.status == "unknown"
        second = binance_execution_service.execute_order(db, **args)
        assert second["duplicate"] is True
        assert second["sent_to_exchange"] is False
        assert calls["place"] == 1
    finally:
        db.close()
        engine.dispose()


def test_live_trading_is_disabled_when_environment_flag_is_missing(monkeypatch):
    monkeypatch.delenv("TITAN_LIVE_TRADING_ENABLED", raising=False)
    assert Settings().live_trading_enabled is False


def test_live_trading_can_only_be_enabled_by_server_environment_flag(monkeypatch):
    monkeypatch.setenv("TITAN_LIVE_TRADING_ENABLED", "true")
    assert Settings().live_trading_enabled is True


def test_daily_loss_guard_counts_recorded_usdt_commissions(tmp_path):
    from datetime import datetime, timezone

    engine, db = _execution_test_db(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        db.add(PositionRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="long_spot", quantity=0.0, realized_pnl=-60.0,
            status="closed", shadow=False, updated_at=now,
        ))
        fee_order = OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="buy", quantity=1.0, status="filled", shadow=False,
            fees_paid=5.0, fee_asset="USDT", created_at=now, updated_at=now,
        )
        db.add(fee_order)
        db.commit()
        assert _daily_loss_exceeded(db, "testnet", "spot", 1_000.0, {}) is False
        fee_order.fees_paid = 11.0
        db.add(fee_order)
        db.commit()
        assert _daily_loss_exceeded(db, "testnet", "spot", 1_000.0, {}) is True
    finally:
        db.close()
        engine.dispose()


def test_daily_loss_guard_fails_closed_when_exchange_fee_conversion_is_pending(tmp_path):
    from datetime import datetime, timezone

    engine, db = _execution_test_db(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        db.add(OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="buy", quantity=1.0, status="filled", shadow=False,
            fees_paid=0.0, fee_asset=None,
            metadata_json={"fee_accounting_status": "pending"},
            created_at=now, updated_at=now,
        ))
        db.commit()
        assert _daily_loss_exceeded(db, "testnet", "spot", 1_000.0, {}) is True
    finally:
        db.close()
        engine.dispose()


def test_immediate_close_persists_applied_fill_watermark(tmp_path, monkeypatch):
    from app.services import binance_trading_worker as worker

    engine, db = _execution_test_db(tmp_path)
    try:
        position = PositionRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="long_spot", quantity=1.0, entry_avg_price=100.0,
            status="open", shadow=False,
        )
        db.add(position)
        db.commit()
        order = OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="sell", quantity=1.0, filled_quantity=1.0,
            avg_fill_price=110.0, status="filled", shadow=False,
            metadata_json={"intent": "close", "position_id": position.id},
        )
        db.add(order)
        db.commit()

        monkeypatch.setattr(worker.binance_execution_service, "execute_order", lambda *args, **kwargs: {
            "ok": True, "sent_to_exchange": True, "order_id": order.id,
        })
        result = worker._close_position(
            db, None, position, 110.0, "testnet", "spot",
        )

        assert result["position_updated"] is True
        assert position.quantity == 0.0
        assert position.realized_pnl == 10.0
        assert order.metadata_json["applied_filled_quantity"] == 1.0
    finally:
        db.close()
        engine.dispose()



def test_partial_fill_fee_accounting_refreshes_until_order_is_fully_filled(tmp_path, monkeypatch):
    from app.services import binance_trading_worker as worker

    engine, db = _execution_test_db(tmp_path)
    try:
        order = OrderRecord(
            environment_mode="testnet", market_type="spot", symbol="BTCUSDT",
            side="buy", quantity=1.0, filled_quantity=0.5,
            avg_fill_price=100.0, status="partially_filled", shadow=False,
            client_order_id="titan-partial-fee-refresh",
            metadata_json={
                "intent": "open", "reference_price": 100.0,
                "fee_accounting_status": "complete",
            },
        )
        db.add(order)
        db.commit()

        class FakeAdapter:
            fee_total = 0.75
            calls = 0

            def get_order(self, symbol, *, client_order_id=None):
                return {
                    "status": "PARTIALLY_FILLED", "executedQty": "0.75",
                    "avgPrice": "100", "orderId": "123",
                }

            def get_order_fee_quote(self, symbol, order_id, *, fills=None):
                self.calls += 1
                return {"ok": True, "fees_paid_usdt": self.fee_total,
                        "fee_breakdown": {"USDT": self.fee_total}}

        adapter = FakeAdapter()
        monkeypatch.setattr(worker, "_flag_stop", lambda *args, **kwargs: None)

        import pytest
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        assert adapter.calls == 1
        assert order.fees_paid == 0.75

        adapter.fee_total = 0.80
        with pytest.raises(RuntimeError, match="partial_exchange_fill_manual_reconciliation_required"):
            _sync_pending_orders(db, adapter, "testnet", "spot")
        assert adapter.calls == 2
        assert order.fees_paid == 0.80
    finally:
        db.close()
        engine.dispose()



def test_futures_worker_uses_saved_leverage_cap_and_system_hard_cap(monkeypatch):
    monkeypatch.setattr(settings, "max_leverage_cap", 10.0)
    assert _effective_futures_leverage_cap({"max_leverage_cap": 4.0}) == 4.0
    assert _effective_futures_leverage_cap({"max_leverage_cap": 20.0}) == 10.0
    assert _effective_futures_leverage_cap({}) == 10.0


def test_futures_worker_rejects_invalid_saved_leverage_cap(monkeypatch):
    import pytest

    monkeypatch.setattr(settings, "max_leverage_cap", 10.0)
    for bad in (0, -1, float("nan"), float("inf"), "invalid", True):
        with pytest.raises(ValueError, match="invalid_futures_leverage_cap"):
            _effective_futures_leverage_cap({"max_leverage_cap": bad})


def test_live_approval_is_revoked_after_normal_stop(tmp_path):
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="live", account_target="live", market_type="spot",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            live_confirmed_by_user=True, risk_limits={},
        ))
        db.commit()
        result = core_service.stop_bot(db, reason="user_stop")
        state = db.get(SystemState, 1)
        assert result["ok"] is True
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
        assert state.live_confirmed_by_user is False
    finally:
        db.close()
        engine.dispose()


def test_live_approval_is_revoked_by_emergency_stop(tmp_path):
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="live", account_target="live", market_type="spot",
            bot_run_state="running", autonomous_enabled=True, emergency_stop=False,
            live_confirmed_by_user=True, risk_limits={},
        ))
        db.commit()
        state = core_service.set_state(db, emergency_stop=True)
        assert state["emergency_stop"] is True
        assert state["live_confirmed_by_user"] is False
        assert state["bot_run_state"] == "stopped"
    finally:
        db.close()
        engine.dispose()


def test_core_live_start_requires_explicit_confirmation_even_when_server_enabled(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(settings, "live_trading_enabled", True)
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
            live_confirmed_by_user=False, risk_limits={},
        ))
        db.commit()
        with pytest.raises(ValueError, match="live_requires_explicit_user_confirmation"):
            core_service.start_bot(db, account_target="live", live_confirm=False)
        state = db.get(SystemState, 1)
        assert state.account_target == "live"
        assert state.live_confirmed_by_user is False
        assert state.bot_run_state == "stopped"
    finally:
        db.close()
        engine.dispose()


def test_core_live_start_accepts_only_explicit_confirmation_and_stays_supervised(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    monkeypatch.setattr(
        binance_execution_service, "preflight",
        lambda db, live_worker_ready=False: {"ready": True, "connected": True},
    )
    engine, db = _execution_test_db(tmp_path)
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="futures",
            bot_run_state="stopped", autonomous_enabled=False, emergency_stop=False,
            live_confirmed_by_user=False, risk_limits={"max_daily_loss_cap": 0.07},
        ))
        db.commit()
        result = core_service.start_bot(db, account_target="live", live_confirm=True)
        state = db.get(SystemState, 1)
        assert result["ok"] is True
        assert state.account_target == "live"
        assert state.environment_mode == "live"
        assert state.live_confirmed_by_user is True
        assert state.bot_run_state == "starting"
        assert state.autonomous_enabled is True
    finally:
        db.close()
        engine.dispose()
