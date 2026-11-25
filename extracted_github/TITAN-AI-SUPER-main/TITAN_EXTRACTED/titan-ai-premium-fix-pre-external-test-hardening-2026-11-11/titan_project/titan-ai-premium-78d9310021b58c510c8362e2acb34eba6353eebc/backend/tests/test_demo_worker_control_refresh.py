from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import SystemState
from app.services.core_service import core_service
from app.scripts.run_demo_loop import _read_state


def test_worker_refreshes_control_state_written_by_another_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker_control_refresh.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(dbmod, "engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", factory)
    monkeypatch.setattr(dbmod, "init_db", lambda: dbmod.Base.metadata.create_all(bind=engine))
    dbmod.init_db()

    worker_db = factory()
    control_db = factory()
    try:
        core_service.ensure_bootstrap(control_db)
        control_db.commit()

        first = _read_state(worker_db)
        assert first is not None
        assert first["emergency_stop"] is False

        # Simulate the API process setting Emergency Stop while the worker's
        # SQLAlchemy session still holds the previous SystemState identity.
        state = control_db.get(SystemState, 1)
        state.emergency_stop = True
        state.bot_run_state = "stopped"
        control_db.add(state)
        control_db.commit()

        refreshed = _read_state(worker_db)
        assert refreshed is not None
        assert refreshed["emergency_stop"] is True
        assert refreshed["bot_run_state"] == "stopped"
    finally:
        worker_db.close()
        control_db.close()
        engine.dispose()
