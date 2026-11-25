import pytest
import time
from types import SimpleNamespace
from app.models import db as dbmod
from app.models.entities import OrderRecord, SystemState
from app.services.core_service import core_service
from app.core.enums import Timeframe
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'emergency_reset.db'}", connect_args={"check_same_thread": False}, future=True)
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    db = dbmod.SessionLocal()
    core_service.ensure_bootstrap(db)
    db.commit()
    return db, engine


def test_emergency_reset_requires_fresh_public_data_and_leaves_worker_stopped(tmp_path, monkeypatch):
    db, engine = _db(tmp_path)
    try:
        state = db.get(SystemState, 1)
        state.emergency_stop = True
        state.bot_run_state = "stopped"
        db.add(state); db.commit()
        def public_batches(symbol, market_type="spot"):
            batches = core_service.market_data.get_multi_timeframe(symbol, seed=7, market_type=market_type)
            for batch in batches.values():
                batch.source = SimpleNamespace(value="live_public")
                batch.market_type = market_type
            batches[Timeframe.M5].df.rows[-1]["close_time"] = time.time() * 1000
            return batches
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", public_batches)
        result = core_service.reset_emergency_stop(db)
        assert result["ok"] is True
        assert result["state"]["emergency_stop"] is False
        assert result["state"]["bot_run_state"] == "stopped"
        assert result["state"]["autonomous_enabled"] is False
    finally:
        db.close(); engine.dispose()


def test_emergency_reset_blocks_when_order_state_is_uncertain(tmp_path, monkeypatch):
    db, engine = _db(tmp_path)
    try:
        state = db.get(SystemState, 1)
        state.emergency_stop = True
        db.add(state)
        db.add(OrderRecord(environment_mode="demo", market_type="spot", symbol="BTC/USDT", side="buy",
            order_type="limit", quantity=0.01, filled_quantity=0, status="submitted",
            shadow=True, idempotency_key="unknown-order-reset-001"))
        db.commit()
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe",
            lambda symbol, market_type="spot": (_ for _ in ()).throw(AssertionError("data fetch must not run before reconciliation")))
        with pytest.raises(ValueError, match="reconciliation_required_before_emergency_reset"):
            core_service.reset_emergency_stop(db)
        assert db.get(SystemState, 1).emergency_stop is True
    finally:
        db.close(); engine.dispose()


def test_emergency_reset_requires_external_account_connection(tmp_path):
    db, engine = _db(tmp_path)
    try:
        state = db.get(SystemState, 1)
        state.account_target = "testnet"
        state.emergency_stop = True
        db.add(state); db.commit()
        with pytest.raises(ValueError, match="binance_account_connection_required_before_emergency_reset"):
            core_service.reset_emergency_stop(db)
    finally:
        db.close(); engine.dispose()


def test_futures_emergency_reset_requires_fresh_futures_mark_and_leaves_worker_stopped(tmp_path, monkeypatch):
    db, engine = _db(tmp_path)
    try:
        state = db.get(SystemState, 1)
        state.market_type = "futures"
        state.emergency_stop = True
        state.bot_run_state = "stopped"
        db.add(state)
        db.commit()

        def public_batches(symbol, market_type="spot"):
            assert market_type == "futures"
            batches = core_service.market_data.get_multi_timeframe(
                symbol, seed=17, market_type="futures",
            )
            for batch in batches.values():
                batch.source = SimpleNamespace(value="live_public")
                batch.market_type = "futures"
            batches[Timeframe.M5].df.rows[-1]["close_time"] = time.time() * 1000
            return batches

        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe", public_batches)
        monkeypatch.setattr(core_service.market_data, "get_public_mark_price", lambda symbol: {
            "mark_price": 100.0, "time": time.time() * 1000,
            "source": "binance_usdm_futures_public_read_only",
        })
        result = core_service.reset_emergency_stop(db)
        assert result["ok"] is True
        assert result["state"]["market_type"] == "futures"
        assert result["state"]["emergency_stop"] is False
        assert result["state"]["bot_run_state"] == "stopped"
    finally:
        db.close()
        engine.dispose()


def test_futures_emergency_reset_blocks_open_positions(tmp_path, monkeypatch):
    from app.models.entities import PositionRecord

    db, engine = _db(tmp_path)
    try:
        state = db.get(SystemState, 1)
        state.market_type = "futures"
        state.emergency_stop = True
        db.add(state)
        db.add(PositionRecord(
            environment_mode="demo", market_type="futures", symbol="BTC/USDT",
            side="long", quantity=0.01, entry_avg_price=100.0, status="open",
            shadow=True, realized_pnl=0.0,
        ))
        db.commit()
        monkeypatch.setattr(core_service.market_data, "get_public_multi_timeframe",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must review positions first")))
        with pytest.raises(ValueError, match="open_futures_positions_require_review_before_emergency_reset"):
            core_service.reset_emergency_stop(db)
        assert db.get(SystemState, 1).emergency_stop is True
    finally:
        db.close()
        engine.dispose()
