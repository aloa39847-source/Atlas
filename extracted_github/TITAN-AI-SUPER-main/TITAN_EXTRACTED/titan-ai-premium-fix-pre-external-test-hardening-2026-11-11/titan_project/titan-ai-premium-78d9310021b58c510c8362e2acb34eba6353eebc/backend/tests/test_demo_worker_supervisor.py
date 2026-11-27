from __future__ import annotations

import pytest

from app.models import db as dbmod
from app.models.entities import SystemState
from app.scripts import run_demo_loop
from app.services.core_service import core_service
from app.services.demo_worker_supervisor import DemoWorkerSupervisor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_supervisor_owns_one_joinable_worker_and_stops_it(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'supervisor.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    ))
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()
    db = dbmod.SessionLocal()
    core_service.ensure_bootstrap(db)
    state = db.get(SystemState, 1)
    state.bot_run_state = "starting"
    db.add(state)
    db.commit()
    db.close()

    def fake_run(*, interval_seconds, symbols, once, stop_event, market="spot"):
        session = dbmod.SessionLocal()
        try:
            row = session.get(SystemState, 1)
            row.bot_run_state = "running"
            session.add(row)
            session.commit()
        finally:
            session.close()
        assert stop_event.wait(2.0)
        session = dbmod.SessionLocal()
        try:
            row = session.get(SystemState, 1)
            row.bot_run_state = "stopped"
            row.bot_stop_reason = "user_stop"
            session.add(row)
            session.commit()
        finally:
            session.close()
        return 0

    monkeypatch.setattr(run_demo_loop, "run", fake_run)
    supervisor = DemoWorkerSupervisor()
    try:
        started = supervisor.start(interval_seconds=30, symbols=["BTC/USDT"])
        assert started["ok"] is True
        assert supervisor.status()["running"] is True
        assert supervisor.start()["error"] == "demo_worker_already_running"

        stopped = supervisor.stop(timeout=2.0)
        assert stopped["ok"] is True
        assert supervisor.status()["running"] is False
        db = dbmod.SessionLocal()
        try:
            assert db.get(SystemState, 1).bot_run_state == "stopped"
        finally:
            db.close()
    finally:
        supervisor.stop(timeout=2.0)
        engine.dispose()


def test_core_start_claims_demo_spot_or_futures_and_blocks_duplicate_start(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'master_start.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    ))
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()
    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        prepared = core_service.start_bot(db, account_target="demo")
        assert prepared["ok"] is True
        assert prepared["state"]["bot_run_state"] == "starting"
        assert prepared["checks"]["continuous_worker_available"] is True
        # API callers must not switch account or market while the worker owns
        # the current execution context, even when the dashboard is bypassed.
        for change in (
            {"market_type": "futures"},
            {"account_target": "testnet"},
            {"environment_mode": "live"},
        ):
            with pytest.raises(ValueError, match="cannot_change_account_or_market_while_worker_active"):
                core_service.set_state(db, **change)
        assert core_service.get_state(db)["account_target"] == "demo"
        assert core_service.get_state(db)["market_type"] == "spot"
        try:
            core_service.start_bot(db, account_target="demo")
            assert False, "duplicate startup must be rejected"
        except ValueError as exc:
            assert "duplicate" in str(exc).lower()
        core_service.stop_bot(db, reason="test_cleanup")
        state = db.get(SystemState, 1)
        state.market_type = "futures"
        db.add(state)
        db.commit()
        futures_prepared = core_service.start_bot(db, account_target="demo")
        assert futures_prepared["ok"] is True
        assert futures_prepared["state"]["market_type"] == "futures"
        core_service.stop_bot(db, reason="test_cleanup_futures")
        from app.services.binance_execution import binance_execution_service
        monkeypatch.setattr(binance_execution_service, "preflight", lambda *args, **kwargs: {
            "ok": True, "ready": True, "account_target": "testnet", "market": "futures",
        })
        testnet_prepared = core_service.start_bot(db, account_target="testnet")
        assert testnet_prepared["ok"] is True
        assert testnet_prepared["state"]["account_target"] == "testnet"
        assert testnet_prepared["checks"]["execution_scope"] == "supervised_binance_execution"
        core_service.stop_bot(db, reason="test_cleanup_testnet")
    finally:
        db.close()
        engine.dispose()


def test_supervisor_defaults_to_automatic_smart_scan_mode(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'supervisor_auto.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    ))
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()
    db = dbmod.SessionLocal()
    core_service.ensure_bootstrap(db)
    state = db.get(SystemState, 1)
    state.bot_run_state = "starting"
    db.add(state); db.commit(); db.close()
    captured = {}

    def fake_run(*, interval_seconds, symbols, once, stop_event, market="spot"):
        captured["symbols"] = symbols
        session = dbmod.SessionLocal()
        try:
            row = session.get(SystemState, 1)
            row.bot_run_state = "running"
            session.add(row); session.commit()
        finally:
            session.close()
        stop_event.wait(2.0)
        return 0

    monkeypatch.setattr(run_demo_loop, "run", fake_run)
    supervisor = DemoWorkerSupervisor()
    try:
        started = supervisor.start(interval_seconds=30)
        assert started["ok"] is True
        assert captured["symbols"] is None
        assert supervisor.status()["symbol_mode"] == "auto_smart_scan"
        assert supervisor.stop(timeout=2.0)["ok"] is True
    finally:
        supervisor.stop(timeout=2.0)
        engine.dispose()



def test_supervisor_rejects_unknown_account_target_without_starting_thread():
    supervisor = DemoWorkerSupervisor()
    result = supervisor.start(account_target="unknown")
    assert result == {"ok": False, "error": "unsupported_account_target"}
    assert supervisor.status()["running"] is False


def test_unexpected_live_worker_exit_revokes_confirmation_and_latches_emergency(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'live_worker_failure.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    ))
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()
    db = dbmod.SessionLocal()
    core_service.ensure_bootstrap(db)
    state = db.get(SystemState, 1)
    state.account_target = "live"
    state.environment_mode = "live"
    state.market_type = "spot"
    state.bot_run_state = "starting"
    state.autonomous_enabled = True
    state.live_confirmed_by_user = True
    state.emergency_stop = False
    db.add(state)
    db.commit()
    db.close()

    from app.services import binance_trading_worker
    def broken_live_run(*args, **kwargs):
        raise RuntimeError("unexpected_live_worker_failure")
    monkeypatch.setattr(binance_trading_worker, "run", broken_live_run)

    supervisor = DemoWorkerSupervisor()
    try:
        started = supervisor.start(account_target="live", market="spot", interval_seconds=30)
        assert started["ok"] is False
        db = dbmod.SessionLocal()
        try:
            state = db.get(SystemState, 1)
            assert state.bot_run_state == "stopped"
            assert state.autonomous_enabled is False
            assert state.live_confirmed_by_user is False
            assert state.emergency_stop is True
        finally:
            db.close()
    finally:
        supervisor.stop(timeout=2.0)
        engine.dispose()
