from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/services/auth_service.py"
s = p.read_text()
if "def get_admin_emails()" not in s:
    s = s.replace(
        "from sqlalchemy.orm import Session\n\nfrom app.models.entities",
        "from sqlalchemy.orm import Session\n\nfrom app.core.config import settings\nfrom app.models.entities",
        1,
    )
    old = '''def auth_required() -> bool:
    return os.getenv("TITAN_AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}


def email_allowed(email: str) -> bool:
    allowed = get_allowed_emails()
    if allowed:
        return normalize_email(email) in allowed
    # If auth is required, an explicit allowlist is required for safety.
    if auth_required():
        return False
    return True  # local/dev convenience when auth not required and no allowlist configured


def ensure_user(db: Session, email: str) -> AppUser:
    e = normalize_email(email)
    user = db.execute(select(AppUser).where(AppUser.email == e)).scalar_one_or_none()
    if not user:
        user = AppUser(email=e, is_active=True, is_admin=True)
        db.add(user)
        db.flush()
    return user
'''
    new = '''def auth_required() -> bool:
    # Use the same parsed setting as the HTTP authentication middleware.
    raw = os.getenv("TITAN_AUTH_REQUIRED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(settings.auth_required)


def get_admin_emails() -> set[str]:
    raw = os.getenv("TITAN_ADMIN_EMAILS", "").strip()
    if raw:
        return {normalize_email(x) for x in raw.split(",") if normalize_email(x)}
    # Only a single allowlisted owner is auto-admin; multiple users require an explicit admin list.
    allowed = get_allowed_emails()
    return allowed if len(allowed) == 1 else set()


def is_admin_email(email: str) -> bool:
    normalized = normalize_email(email)
    return bool(normalized and email_allowed(normalized) and normalized in get_admin_emails())


def email_allowed(email: str) -> bool:
    allowed = get_allowed_emails()
    if allowed:
        return normalize_email(email) in allowed
    if auth_required():
        return False
    return True


def ensure_user(db: Session, email: str) -> AppUser:
    e = normalize_email(email)
    user = db.execute(select(AppUser).where(AppUser.email == e)).scalar_one_or_none()
    if not user:
        user = AppUser(email=e, is_active=True, is_admin=is_admin_email(e))
        db.add(user)
        db.flush()
    else:
        user.is_admin = is_admin_email(e)
        db.add(user)
    return user
'''
    if old not in s:
        raise SystemExit("Auth allowlist/admin block marker missing; refusing partial auth patch.")
    s = s.replace(old, new, 1)
    old_auth = '''    if not verify_password(password, user.password_hash, user.password_salt):
        user.failed_login_count += 1
        db.add(user)
        log_event(db, action="auth.failed", severity="warning", message="Login failed (invalid password).", details={"email": e})
        return None
    return user
'''
    new_auth = '''    if not verify_password(password, user.password_hash, user.password_salt):
        user.failed_login_count += 1
        db.add(user)
        log_event(db, action="auth.failed", severity="warning", message="Login failed (invalid password).", details={"email": e})
        return None
    user.is_admin = is_admin_email(e)
    db.add(user)
    return user
'''
    if old_auth not in s:
        raise SystemExit("Auth login privilege reconciliation marker missing.")
    s = s.replace(old_auth, new_auth, 1)
    old_session = '''    if exp < _utcnow():
        return None
    return db.get(AppUser, sess.user_id)
'''
    new_session = '''    if exp < _utcnow():
        return None
    user = db.get(AppUser, sess.user_id)
    if user is not None:
        user.is_admin = is_admin_email(user.email)
        db.add(user)
    return user
'''
    if old_session not in s:
        raise SystemExit("Auth session privilege reconciliation marker missing.")
    s = s.replace(old_session, new_session, 1)
    p.write_text(s)

p = root / "app/models/entities.py"
s = p.read_text().replace(
    "is_admin: Mapped[bool] = mapped_column(Boolean, default=True)",
    "is_admin: Mapped[bool] = mapped_column(Boolean, default=False)",
    1,
)
p.write_text(s)

(root / "tests/test_admin_privileges.py").write_text(r'''from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.db import Base
from app.services import auth_service


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_auth_required_without_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.delenv("TITAN_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("TITAN_ALLOWED_EMAILS", raising=False)
    assert auth_service.auth_required() is True
    assert auth_service.email_allowed("anyone@example.com") is False


def test_single_allowlisted_owner_is_initial_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com")
    monkeypatch.delenv("TITAN_ADMIN_EMAILS", raising=False)
    db = _db(tmp_path)
    try:
        user = auth_service.ensure_user(db, "OWNER@example.com")
        assert user.is_admin is True
    finally:
        db.close()


def test_multiple_allowlisted_users_are_not_automatically_admins(tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com,staff@example.com")
    monkeypatch.delenv("TITAN_ADMIN_EMAILS", raising=False)
    db = _db(tmp_path)
    try:
        owner = auth_service.ensure_user(db, "owner@example.com")
        staff = auth_service.ensure_user(db, "staff@example.com")
        assert owner.is_admin is False
        assert staff.is_admin is False
    finally:
        db.close()


def test_explicit_admin_allowlist_grants_only_named_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com,staff@example.com")
    monkeypatch.setenv("TITAN_ADMIN_EMAILS", "owner@example.com")
    db = _db(tmp_path)
    try:
        owner = auth_service.ensure_user(db, "owner@example.com")
        staff = auth_service.ensure_user(db, "staff@example.com")
        assert owner.is_admin is True
        assert staff.is_admin is False
    finally:
        db.close()


def test_existing_admin_bit_is_demoted_when_email_is_not_admin_allowlisted(tmp_path, monkeypatch):
    from app.models.entities import AppUser
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com,staff@example.com")
    monkeypatch.setenv("TITAN_ADMIN_EMAILS", "owner@example.com")
    db = _db(tmp_path)
    try:
        user = AppUser(email="staff@example.com", is_active=True, is_admin=True)
        db.add(user)
        db.commit()
        reconciled = auth_service.ensure_user(db, "staff@example.com")
        assert reconciled.is_admin is False
    finally:
        db.close()
''')
