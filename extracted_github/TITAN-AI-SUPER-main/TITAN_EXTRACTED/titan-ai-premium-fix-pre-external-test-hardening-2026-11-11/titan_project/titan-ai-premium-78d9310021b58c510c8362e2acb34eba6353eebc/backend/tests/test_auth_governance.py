from __future__ import annotations

from app.models import db as dbmod
from app.models.db import SessionLocal
from app.services import auth_service
from app.services.governance import record_config_change
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.models.entities import ConfigChange


def test_password_hash_and_verify():
    ph, salt = auth_service.hash_password("CorrectHorseBatteryStaple123")
    assert auth_service.verify_password("CorrectHorseBatteryStaple123", ph, salt) is True
    assert auth_service.verify_password("wrong", ph, salt) is False


def test_allowlist_enforced_when_required(monkeypatch):
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com")
    assert auth_service.email_allowed("owner@example.com") is True
    assert auth_service.email_allowed("other@example.com") is False


def test_config_change_recorded(tmp_path, monkeypatch):
    db_path = tmp_path / "gov.db"
    dbmod.engine.dispose()
    dbmod.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    dbmod.SessionLocal = sessionmaker(bind=dbmod.engine, autoflush=False, autocommit=False, future=True)
    dbmod.Base.metadata.create_all(bind=dbmod.engine)
    db = dbmod.SessionLocal()
    try:
        record_config_change(db, actor="test", scope="risk", key="max_leverage", old_value={"v": 3}, new_value={"v": 2}, reason="test", approved=True)
        db.commit()
        row = db.execute(select(ConfigChange)).scalar_one()
        assert row.scope == "risk" and row.approved is True
    finally:
        db.close()


def test_new_users_are_not_admin_by_default(monkeypatch):
    monkeypatch.delenv("TITAN_ADMIN_EMAILS", raising=False)
    from app.models.entities import AppUser
    assert AppUser.__table__.c.is_admin.default.arg is False
    assert auth_service.get_admin_emails() == set()


def test_admin_allowlist_is_explicit_and_normalized(monkeypatch):
    monkeypatch.setenv("TITAN_ADMIN_EMAILS", " Owner@Example.com,second@example.com ")
    assert auth_service.get_admin_emails() == {"owner@example.com", "second@example.com"}


def test_totp_enrollment_is_encrypted_and_requires_confirmation(monkeypatch):
    from app.core.config import settings
    from app.models.entities import AppUser
    monkeypatch.setattr(settings, "secret_key", "strong-test-secret-" * 3)
    user = AppUser(email="owner@example.com")
    setup = auth_service.begin_two_factor_setup(user)
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert user.two_factor_secret != setup["secret"]
    assert user.two_factor_enabled is False
    import time
    now = int(time.time())
    code = auth_service._totp_code(setup["secret"], now)
    assert auth_service.verify_totp(user.two_factor_secret, code, timestamp=now)
    assert auth_service.confirm_two_factor(user, code) is True
    assert user.two_factor_enabled is True
    assert auth_service.disable_two_factor(user, "000000") is False
    assert user.two_factor_enabled is True
    assert auth_service.disable_two_factor(user, code) is True
    assert user.two_factor_secret is None


def test_totp_rejects_malformed_and_out_of_window_codes():
    assert auth_service.verify_totp("not-a-secret", "abc123", timestamp=1_700_000_000) is False
    assert auth_service.verify_totp("not-a-secret", "1234567", timestamp=1_700_000_000) is False


def test_two_factor_setup_cannot_replace_an_already_enabled_factor(monkeypatch):
    from app.core.config import settings
    from app.models.entities import AppUser
    monkeypatch.setattr(settings, "secret_key", "strong-test-secret-" * 3)
    user = AppUser(email="owner@example.com")
    setup = auth_service.begin_two_factor_setup(user)
    now = __import__("time").time()
    code = auth_service._totp_code(setup["secret"], int(now))
    assert auth_service.confirm_two_factor(user, code) is True
    active_secret = user.two_factor_secret
    try:
        auth_service.begin_two_factor_setup(user)
        assert False, "enabled 2FA must not be silently replaced"
    except ValueError as exc:
        assert str(exc) == "two_factor_already_enabled"
    assert user.two_factor_enabled is True
    assert user.two_factor_secret == active_secret

def test_login_requires_totp_only_when_second_factor_is_enabled(monkeypatch):
    from app.models.entities import AppUser

    user = AppUser(email="owner@example.com", two_factor_enabled=False)
    assert auth_service.verify_login_second_factor(user, None) is True

    user.two_factor_enabled = True
    user.two_factor_secret = "encrypted-secret"
    checked = []
    monkeypatch.setattr(auth_service, "verify_totp", lambda secret, code: checked.append((secret, code)) or code == "123456")

    assert auth_service.verify_login_second_factor(user, None) is False
    assert auth_service.verify_login_second_factor(user, "000000") is False
    assert auth_service.verify_login_second_factor(user, "123456") is True
    assert checked == [
        ("encrypted-secret", "000000"),
        ("encrypted-secret", "123456"),
    ]


def test_login_second_factor_fails_closed_without_enrolled_secret():
    from app.models.entities import AppUser

    user = AppUser(email="owner@example.com", two_factor_enabled=True, two_factor_secret=None)
    assert auth_service.verify_login_second_factor(user, "123456") is False

def test_password_reset_revokes_all_existing_sessions(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import db as dbmod

    monkeypatch.setenv("TITAN_ALLOWED_EMAILS", "owner@example.com")
    db_path = tmp_path / "password-reset-sessions.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    dbmod.Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        user = auth_service.set_password(db, "owner@example.com", "OldPassword123!")
        db.commit()
        token_a = auth_service.create_session(db, user)
        token_b = auth_service.create_session(db, user)
        db.commit()
        assert auth_service.get_session_user(db, token_a) is not None
        assert auth_service.get_session_user(db, token_b) is not None

        auth_service.set_password(db, "owner@example.com", "NewPassword456!")
        db.commit()

        assert auth_service.get_session_user(db, token_a) is None
        assert auth_service.get_session_user(db, token_b) is None
        assert auth_service.authenticate(db, "owner@example.com", "NewPassword456!") is not None
    finally:
        db.close()
        engine.dispose()

def test_login_route_denies_session_until_totp_verifies(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api.routes import LoginRequest, login
    from app.models.entities import AppUser

    user = AppUser(email="owner@example.com", two_factor_enabled=True, two_factor_secret="encrypted")
    issued = []
    events = []

    class FakeDB:
        commits = 0
        def commit(self):
            self.commits += 1

    db = FakeDB()
    request = Request({
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/api/auth/login", "query_string": b"",
        "headers": [(b"user-agent", b"test-agent")],
        "client": ("127.0.0.1", 12345), "server": ("localhost", 80),
    })
    monkeypatch.setattr(auth_service, "authenticate", lambda db, email, password: user)
    monkeypatch.setattr(auth_service, "verify_login_second_factor", lambda user, code: code == "123456")
    monkeypatch.setattr(auth_service, "log_event", lambda *args, **kwargs: events.append(kwargs))
    monkeypatch.setattr(auth_service, "create_session", lambda *args, **kwargs: issued.append(user.email) or "session-token")

    try:
        login(LoginRequest(email=user.email, password="correct"), request, db)
        assert False, "missing TOTP must not issue a session"
    except HTTPException as exc:
        assert exc.status_code == 401
    assert issued == []
    assert events and events[0]["action"] == "auth.2fa_failed"

    result = login(LoginRequest(email=user.email, password="correct", totp_code="123456"), request, db)
    assert result["token"] == "session-token"
    assert issued == [user.email]

def test_active_starlette_login_requires_totp_before_session_creation(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from app import star_app
    from app.models.entities import AppUser

    user = AppUser(email="owner@example.com", two_factor_enabled=True, two_factor_secret="encrypted")
    issued = []
    class FakeDB:
        def commit(self): pass
        def close(self): pass
        def add(self, value): pass

    class FakeRequest:
        client = SimpleNamespace(host="127.0.0.1")
        headers = {"user-agent": "test-agent"}
        def __init__(self, payload):
            self.payload = payload
        async def body(self):
            return json.dumps(self.payload).encode()

    monkeypatch.setattr(star_app, "db_sess", FakeDB)
    monkeypatch.setattr(star_app.auth_service, "authenticate", lambda db, email, password: user)
    monkeypatch.setattr(star_app.auth_service, "verify_totp", lambda secret, code: code == "123456")
    monkeypatch.setattr(star_app.auth_service, "create_session", lambda *args, **kwargs: issued.append(user.email) or "starlette-session")
    monkeypatch.setattr(star_app, "log_event", lambda *args, **kwargs: None)

    denied = asyncio.run(star_app.auth_login(FakeRequest({"email": user.email, "password": "correct"})))
    assert denied.status_code == 401
    assert issued == []

    accepted = asyncio.run(star_app.auth_login(FakeRequest({
        "email": user.email, "password": "correct", "totp_code": "123456",
    })))
    assert accepted.status_code == 200
    assert json.loads(accepted.body)["token"] == "starlette-session"
    assert issued == [user.email]


def test_auth_cannot_be_disabled_in_production(monkeypatch):
    monkeypatch.setenv("TITAN_APP_ENV", "production")
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "false")
    monkeypatch.delenv("TITAN_ALLOWED_EMAILS", raising=False)

    assert auth_service.auth_required() is True
    assert auth_service.email_allowed("anyone@example.com") is False


def test_auth_can_be_disabled_for_explicit_local_development(monkeypatch):
    monkeypatch.setenv("TITAN_APP_ENV", "development")
    monkeypatch.setenv("TITAN_AUTH_REQUIRED", "false")
    monkeypatch.delenv("TITAN_ALLOWED_EMAILS", raising=False)

    assert auth_service.auth_required() is False
    assert auth_service.email_allowed("local@example.com") is True


def test_private_history_read_routes_require_authenticated_user():
    from fastapi.routing import APIRoute
    from app.api.routes import get_current_user, router

    protected_paths = {"/decisions", "/audit", "/knowledge", "/learning"}
    routes = {
        route.path: route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in protected_paths and "GET" in route.methods
    }
    assert set(routes) == protected_paths
    for path, route in routes.items():
        assert any(dependency.call is get_current_user for dependency in route.dependant.dependencies), path


def test_login_rate_limit_is_temporary_and_clears_after_success():
    auth_service.reset_login_rate_limits_for_tests()
    ip, email = "192.0.2.44", "owner@example.com"
    start = 10_000.0
    for offset in range(5):
        assert auth_service.login_rate_limit_remaining(ip, email, now=start + offset) == 0
        auth_service.record_login_attempt(ip, email, success=False, now=start + offset)
    assert auth_service.login_rate_limit_remaining(ip, email, now=start + 6) > 0
    assert auth_service.login_rate_limit_remaining(ip, email, now=start + 905) == 0

    for offset in range(5):
        auth_service.record_login_attempt(ip, email, success=False, now=start + 1_000 + offset)
    assert auth_service.login_rate_limit_remaining(ip, email, now=start + 1_006) > 0
    auth_service.record_login_attempt(ip, email, success=True, now=start + 1_007)
    assert auth_service.login_rate_limit_remaining(ip, email, now=start + 1_008) == 0
    auth_service.reset_login_rate_limits_for_tests()
