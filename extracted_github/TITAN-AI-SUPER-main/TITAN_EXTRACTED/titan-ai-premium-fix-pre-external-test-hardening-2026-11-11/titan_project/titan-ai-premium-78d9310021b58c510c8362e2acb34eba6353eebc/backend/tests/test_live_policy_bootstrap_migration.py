from __future__ import annotations

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.core_service import core_service
from app.core.config import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _session(tmp_path, name):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    dbmod.Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_bootstrap_preserves_explicitly_confirmed_live_worker_state(tmp_path):
    engine, db = _session(tmp_path, "legacy_live.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="live", account_target="live", market_type="spot",
            bot_run_state="running", autonomous_enabled=True,
            live_trading_enabled=True, live_confirmed_by_user=True,
            emergency_stop=False, risk_limits={},
        ))
        db.commit()

        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)

        assert state.environment_mode == "live"
        assert state.account_target == "live"
        assert state.bot_run_state == "running"
        assert state.autonomous_enabled is True
        assert state.live_trading_enabled is bool(settings.live_trading_enabled)
        assert state.live_confirmed_by_user is True
        assert state.emergency_stop is False
        public_state = core_service.get_state(db)
        assert public_state["live_trading_enabled"] is bool(settings.live_trading_enabled)
        assert public_state["live_confirmed_by_user"] is True
    finally:
        db.close()
        engine.dispose()


def test_bootstrap_revokes_stale_live_flags_without_stopping_demo_worker(tmp_path):
    engine, db = _session(tmp_path, "demo_legacy_flags.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="demo", market_type="spot",
            bot_run_state="stopped", autonomous_enabled=False,
            live_trading_enabled=True, live_confirmed_by_user=True,
            emergency_stop=False, risk_limits={},
        ))
        db.commit()

        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)

        assert state.environment_mode == "demo"
        assert state.account_target == "demo"
        assert state.live_trading_enabled is bool(settings.live_trading_enabled)
        assert state.live_confirmed_by_user is False
        assert state.emergency_stop is False
        assert state.bot_run_state == "stopped"
    finally:
        db.close()
        engine.dispose()


def test_bootstrap_preserves_supervised_testnet_worker_state(tmp_path):
    engine, db = _session(tmp_path, "legacy_testnet_worker.db")
    try:
        db.add(SystemState(
            id=1, environment_mode="demo", account_target="testnet", market_type="futures",
            bot_run_state="running", autonomous_enabled=True,
            live_trading_enabled=False, live_confirmed_by_user=False,
            emergency_stop=False, risk_limits={},
        ))
        db.commit()

        core_service.ensure_bootstrap(db)
        state = db.get(SystemState, 1)

        assert state.account_target == "testnet"
        assert state.bot_run_state == "running"
        assert state.autonomous_enabled is True
        assert state.emergency_stop is False
    finally:
        db.close()
        engine.dispose()
