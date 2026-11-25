from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db as dbmod
from app.models.entities import AppUser
from app.services import auth_service


def test_disabled_user_cannot_reuse_existing_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'inactive_session.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    monkeypatch.setattr(dbmod, "engine", engine)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        user = AppUser(email="disabled@example.test", is_active=True)
        db.add(user)
        db.flush()
        token = auth_service.create_session(db, user)
        db.commit()

        assert auth_service.get_session_user(db, token).id == user.id

        user.is_active = False
        db.commit()
        assert auth_service.get_session_user(db, token) is None
    finally:
        db.close()
        engine.dispose()
