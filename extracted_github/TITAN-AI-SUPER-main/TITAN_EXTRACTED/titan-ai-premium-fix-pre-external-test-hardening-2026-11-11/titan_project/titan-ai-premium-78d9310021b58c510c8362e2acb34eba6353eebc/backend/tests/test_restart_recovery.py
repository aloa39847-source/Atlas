from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.restart_recovery import recover_interrupted_worker


def _db(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}, future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_restart_recovery_stops_testnet_without_erasing_position_context(tmp_path):
    engine, db = _db(tmp_path, "restart_testnet.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="testnet", market_type="futures",
            bot_run_state="running", autonomous_enabled=True, live_confirmed_by_user=False,
            emergency_stop=False, risk_limits={}, status_summary={"existing": "kept"},
        ))
        db.commit()
        result = recover_interrupted_worker(db)
        state = db.get(SystemState, 1)
        assert result["recovered"] is True
        assert result["automatic_resume"] is False
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
        assert state.live_confirmed_by_user is False
        assert state.emergency_stop is False
        assert state.status_summary["existing"] == "kept"
        assert state.status_summary["restart_recovery"]["positions_preserved_for_reconciliation"] is True
    finally:
        db.close()
        engine.dispose()


def test_restart_recovery_revokes_live_approval_and_latches_emergency_stop(tmp_path):
    engine, db = _db(tmp_path, "restart_live.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="live", account_target="live", market_type="spot",
            bot_run_state="executing", autonomous_enabled=True, live_confirmed_by_user=True,
            emergency_stop=False, risk_limits={}, status_summary={},
        ))
        db.commit()
        result = recover_interrupted_worker(db)
        state = db.get(SystemState, 1)
        assert result["recovered"] is True
        assert state.bot_run_state == "stopped"
        assert state.autonomous_enabled is False
        assert state.live_confirmed_by_user is False
        assert state.emergency_stop is True
        assert state.bot_stop_reason == "process_restart_recovery"
    finally:
        db.close()
        engine.dispose()


def test_restart_recovery_does_not_change_already_stopped_state(tmp_path):
    engine, db = _db(tmp_path, "restart_stopped.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False, live_confirmed_by_user=False,
            emergency_stop=False, risk_limits={}, status_summary={},
        ))
        db.commit()
        result = recover_interrupted_worker(db)
        assert result == {"recovered": False, "reason": "no_interrupted_worker"}
        assert db.get(SystemState, 1).bot_run_state == "stopped"
    finally:
        db.close()
        engine.dispose()
