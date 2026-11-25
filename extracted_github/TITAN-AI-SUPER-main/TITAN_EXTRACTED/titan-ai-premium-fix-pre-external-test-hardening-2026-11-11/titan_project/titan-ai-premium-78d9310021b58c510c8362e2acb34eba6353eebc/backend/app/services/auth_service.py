from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AppUser, AuthSession
from .audit import log_event

PBKDF2_ITERATIONS = 200_000

# Process-local defence-in-depth throttle. Multi-replica deployments should
# additionally enforce a shared rate limit at the API gateway.
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_BLOCK_SECONDS = 15 * 60
_LOGIN_EMAIL_MAX_FAILURES = 5
_LOGIN_IP_MAX_FAILURES = 20
_LOGIN_RATE_STATE: dict[str, dict[str, Any]] = {}
_LOGIN_RATE_LOCK = threading.Lock()


def _login_rate_keys(ip_address: str | None, email: str) -> tuple[tuple[str, int], ...]:
    ip = (ip_address or "unknown").strip()[:128].lower()
    normalized_email = normalize_email(email)
    ip_hash = hashlib.sha256(("ip:" + ip).encode("utf-8")).hexdigest()
    email_hash = hashlib.sha256(("email:" + normalized_email).encode("utf-8")).hexdigest()
    return ((f"ip:{ip_hash}", _LOGIN_IP_MAX_FAILURES),
            (f"email:{email_hash}", _LOGIN_EMAIL_MAX_FAILURES))


def login_rate_limit_remaining(
    ip_address: str | None, email: str, *, now: float | None = None
) -> int:
    """Return Retry-After seconds when either IP or account is temporarily blocked."""
    current = time.monotonic() if now is None else float(now)
    retry_after = 0.0
    with _LOGIN_RATE_LOCK:
        # Prune stale identifiers as well as the current request keys so random
        # email attempts cannot grow the in-memory limiter without bound.
        for stale_key, stale_state in list(_LOGIN_RATE_STATE.items()):
            recent = [stamp for stamp in stale_state.get("failures", [])
                      if current - stamp < _LOGIN_WINDOW_SECONDS]
            stale_state["failures"] = recent
            if not recent and float(stale_state.get("blocked_until", 0.0)) <= current:
                _LOGIN_RATE_STATE.pop(stale_key, None)
        for key, maximum in _login_rate_keys(ip_address, email):
            state = _LOGIN_RATE_STATE.get(key)
            if not state:
                continue
            failures = [stamp for stamp in state.get("failures", [])
                        if current - stamp < _LOGIN_WINDOW_SECONDS]
            state["failures"] = failures
            blocked_until = float(state.get("blocked_until", 0.0))
            if blocked_until <= current and len(failures) >= maximum:
                blocked_until = current + _LOGIN_BLOCK_SECONDS
                state["blocked_until"] = blocked_until
            if blocked_until > current:
                retry_after = max(retry_after, blocked_until - current)
            elif not failures:
                _LOGIN_RATE_STATE.pop(key, None)
    return max(0, int(retry_after + 0.999))


def record_login_attempt(
    ip_address: str | None, email: str, *, success: bool, now: float | None = None
) -> None:
    """Record failed attempts; a successful login clears that IP/account history."""
    current = time.monotonic() if now is None else float(now)
    keys = _login_rate_keys(ip_address, email)
    with _LOGIN_RATE_LOCK:
        if success:
            for key, _ in keys:
                _LOGIN_RATE_STATE.pop(key, None)
            return
        for key, maximum in keys:
            state = _LOGIN_RATE_STATE.setdefault(key, {"failures": [], "blocked_until": 0.0})
            failures = [stamp for stamp in state["failures"]
                        if current - stamp < _LOGIN_WINDOW_SECONDS]
            failures.append(current)
            state["failures"] = failures
            if len(failures) >= maximum:
                state["blocked_until"] = max(
                    float(state.get("blocked_until", 0.0)),
                    current + _LOGIN_BLOCK_SECONDS,
                )


def reset_login_rate_limits_for_tests() -> None:
    with _LOGIN_RATE_LOCK:
        _LOGIN_RATE_STATE.clear()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_admin_emails() -> set[str]:
    raw = os.getenv("TITAN_ADMIN_EMAILS", "").strip()
    return {normalize_email(x) for x in raw.split(",") if normalize_email(x)}


def get_allowed_emails() -> set[str]:
    raw = os.getenv("TITAN_ALLOWED_EMAILS", "").strip()
    if not raw:
        return set()
    return {normalize_email(x) for x in raw.split(",") if normalize_email(x)}


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    if not password:
        raise ValueError("password required")
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, password_hash: str | None, salt_hex: str | None) -> bool:
    if not password or not password_hash or not salt_hex:
        return False
    calc, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(calc, password_hash)


def auth_required() -> bool:
    # Production deployments must never disable authentication via an environment toggle.
    app_env = os.getenv("TITAN_APP_ENV", "development").strip().lower()
    if app_env in {"production", "prod"}:
        return True
    # Match Settings.auth_required: authentication is secure-by-default elsewhere too.
    return os.getenv("TITAN_AUTH_REQUIRED", "true").strip().lower() in {"1", "true", "yes", "on"}


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
        user = AppUser(email=e, is_active=True, is_admin=e in get_admin_emails())
        db.add(user)
        db.flush()
    return user


def revoke_user_sessions(db: Session, user_id: int) -> int:
    """Revoke every active session after a credential reset or password change."""
    sessions = db.execute(select(AuthSession).where(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    )).scalars().all()
    revoked_at = _utcnow()
    for session in sessions:
        session.revoked_at = revoked_at
        db.add(session)
    return len(sessions)


def set_password(db: Session, email: str, password: str) -> AppUser:
    if not email_allowed(email):
        raise ValueError("email not allowed")
    user = ensure_user(db, email)
    revoke_user_sessions(db, user.id)
    ph, salt = hash_password(password)
    user.password_hash = ph
    user.password_salt = salt
    user.failed_login_count = 0
    db.add(user)
    log_event(db, action="auth.password_set", actor="local_setup", message="Password hash updated (value not logged).", details={"email": normalize_email(email)})
    return user


def create_session(db: Session, user: AppUser, ttl_hours: int = 24 * 7, ip_address: str | None = None, user_agent: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    sess = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=_utcnow() + timedelta(hours=ttl_hours),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:1000],
    )
    db.add(sess)
    user.last_login_at = _utcnow()
    user.failed_login_count = 0
    db.add(user)
    log_event(db, action="auth.login", actor=normalize_email(user.email), message="User logged in.")
    return token


def revoke_session(db: Session, token: str) -> bool:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))).scalar_one_or_none()
    if not sess:
        return False
    sess.revoked_at = _utcnow()
    db.add(sess)
    log_event(db, action="auth.logout", actor="user", message="Session revoked.")
    return True


def authenticate(db: Session, email: str, password: str) -> AppUser | None:
    e = normalize_email(email)
    if not email_allowed(e):
        log_event(db, action="auth.denied", severity="warning", message="Login denied for non-allowlisted email.", details={"email": e})
        return None
    user = db.execute(select(AppUser).where(AppUser.email == e)).scalar_one_or_none()
    if not user or not user.is_active:
        log_event(db, action="auth.failed", severity="warning", message="Login failed (unknown/inactive user).", details={"email": e})
        return None
    if not verify_password(password, user.password_hash, user.password_salt):
        user.failed_login_count = min(int(user.failed_login_count or 0) + 1, 10)
        db.add(user)
        log_event(db, action="auth.failed", severity="warning", message="Login failed (invalid password).", details={"email": e})
        return None
    return user


def get_session_user(db: Session, token: str | None) -> AppUser | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))).scalar_one_or_none()
    if not sess:
        return None
    exp = sess.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < _utcnow():
        return None
    user = db.get(AppUser, sess.user_id)
    # A disabled account must lose access immediately, even if an older session remains valid.
    if user is None or not user.is_active:
        return None
    return user



def generate_totp_secret() -> str:
    """Generate a Base32 secret for RFC 6238 authenticator apps."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_code(secret: str, timestamp: int, step: int = 30, digits: int = 6) -> str:
    normalized = str(secret or "").strip().upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding, casefold=True)
    counter = int(timestamp // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, *, timestamp: int | None = None, window: int = 1) -> bool:
    """Verify a six-digit RFC 6238 TOTP with a narrow clock-skew window."""
    supplied = str(code or "").strip()
    if len(supplied) != 6 or not supplied.isdigit() or window < 0 or window > 2:
        return False
    try:
        from app.services.credentials import decrypt_secret
        decoded = decrypt_secret(secret)
        if not decoded:
            return False
        now = int(time.time()) if timestamp is None else int(timestamp)
        return any(hmac.compare_digest(_totp_code(decoded, now + offset * 30), supplied)
                   for offset in range(-window, window + 1))
    except (ValueError, TypeError, OverflowError):
        return False


def begin_two_factor_setup(user: AppUser) -> dict[str, Any]:
    """Store an encrypted pending TOTP secret; it is not enabled until confirmed."""
    if user.two_factor_enabled:
        raise ValueError("two_factor_already_enabled")
    from app.services.credentials import encrypt_secret
    secret = generate_totp_secret()
    user.two_factor_secret = encrypt_secret(secret)
    user.two_factor_enabled = False
    return {"secret": secret, "issuer": "TITAN SUPER AI PREMIUM",
            "account": user.email,
            "otpauth_uri": f"otpauth://totp/TITAN%20SUPER%20AI%20PREMIUM:{user.email}?secret={secret}&issuer=TITAN%20SUPER%20AI%20PREMIUM&algorithm=SHA1&digits=6&period=30"}



def verify_login_second_factor(user: AppUser, code: str | None) -> bool:
    """Require a valid enrolled TOTP code before issuing a new session."""
    if not user.two_factor_enabled:
        return True
    if not user.two_factor_secret or not isinstance(code, str):
        return False
    return verify_totp(user.two_factor_secret, code)


def confirm_two_factor(user: AppUser, code: str) -> bool:
    if not user.two_factor_secret or not verify_totp(user.two_factor_secret, code):
        return False
    user.two_factor_enabled = True
    return True


def disable_two_factor(user: AppUser, code: str) -> bool:
    if not user.two_factor_enabled or not user.two_factor_secret:
        return False
    if not verify_totp(user.two_factor_secret, code):
        return False
    user.two_factor_enabled = False
    user.two_factor_secret = None
    return True
