from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/services/core_service.py"
s = p.read_text()
old = '''        existing = {r.engine_key for r in db.execute(select(EngineState)).scalars().all()}
        for key, name, status in ENGINE_REGISTRY:
            if key not in existing:
                db.add(EngineState(engine_key=key, name=name, status=status, message="Registered by TITAN AI CORE bootstrap."))
        db.commit()
'''
new = '''        existing_rows = {
            r.engine_key: r for r in db.execute(select(EngineState)).scalars().all()
        }
        implementation_states = {"not_implemented", "needs_external_service", "implemented_partial"}
        for key, name, status in ENGINE_REGISTRY:
            row = existing_rows.get(key)
            if row is None:
                db.add(EngineState(engine_key=key, name=name, status=status,
                                   message="Registered by TITAN AI CORE bootstrap."))
            elif row.status in implementation_states and row.status != status:
                previous = row.status
                row.status = status
                row.name = name
                row.message = f"Implementation status synchronized from {previous} to {status} by TITAN AI CORE bootstrap."
                db.add(row)
        db.commit()
'''
if old in s:
    s = s.replace(old, new, 1)
elif "existing_rows = {" not in s or "Implementation status synchronized" not in s:
    raise SystemExit("Engine registry bootstrap block is neither original nor patched.")
p.write_text(s)

test = r'''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.db import Base
from app.models.entities import EngineState
from app.services.core_service import TitanCoreService


def test_bootstrap_syncs_changed_implementation_status_without_overwriting_runtime_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'engine-registry.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add_all([
            EngineState(engine_key="portfolio_management", name="Portfolio Management Engine",
                        status="not_implemented", message="Old registry status"),
            EngineState(engine_key="technical_analysis", name="Technical Analysis Engine",
                        status="running", message="Started by operator"),
            EngineState(engine_key="fundamental_analysis", name="Fundamental Analysis Engine",
                        status="not_implemented", message="No provider"),
        ])
        db.commit()
        TitanCoreService().ensure_bootstrap(db)
        portfolio = db.query(EngineState).filter_by(engine_key="portfolio_management").one()
        technical = db.query(EngineState).filter_by(engine_key="technical_analysis").one()
        fundamental = db.query(EngineState).filter_by(engine_key="fundamental_analysis").one()
        assert portfolio.status == "implemented_partial"
        assert "synchronized" in portfolio.message
        assert technical.status == "running"
        assert technical.message == "Started by operator"
        assert fundamental.status == "not_implemented"
'''
(root / "tests/test_engine_registry_sync.py").write_text(test)
