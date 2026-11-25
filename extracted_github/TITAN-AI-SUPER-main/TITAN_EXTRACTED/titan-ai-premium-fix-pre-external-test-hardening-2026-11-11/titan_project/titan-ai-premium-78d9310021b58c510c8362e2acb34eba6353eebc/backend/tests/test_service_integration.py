from __future__ import annotations

from app.core.config import settings
from app.models import db as dbmod
from app.models.db import SessionLocal, init_db
from app.services.core_service import core_service
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_core_service_bootstrap_state_engines_and_analysis(tmp_path, monkeypatch):
    db_path = tmp_path / "svc.db"
    settings.database_url = f"sqlite:///{db_path}"
    dbmod.engine.dispose()
    dbmod.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, future=True)
    dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)

    db = dbmod.SessionLocal()
    try:
        core_service.ensure_bootstrap(db)
        state = core_service.get_state(db)
        assert state["environment_mode"] == "demo"
        assert state["market_type"] == "spot"
        assert state["safety"]["allow_demo_to_live_auto"] is False
        engines = core_service.engines(db)
        keys = {e["key"] for e in engines}
        assert "decision_making" in keys and "technical_analysis" in keys and "trade_execution" in keys
        res = core_service.analyze(db, symbol="BTC/USDT", market_type="spot", environment_mode="demo")
        assert res["recorded"] is True and res["decision_id"] > 0
        assert "synthetic_labeled" in str(res["data_sources"])
    finally:
        db.close()
