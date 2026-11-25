"""Secure credential storage for Binance Testnet keys.
Keys are obfuscated with server secret and stored server-side only.
Never log or expose secrets to frontend after save.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _key_bytes() -> bytes:
    secret = str(settings.secret_key or "").strip()
    if not secret:
        raise RuntimeError("strong_titan_secret_key_required_for_credential_storage")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _fernet() -> Fernet:
    secret = str(settings.secret_key or "").strip()
    normalized = secret.lower()
    known_placeholders = (
        "dev-only-change-locally", "replace_with", "change_me", "changeme",
        "your_secret", "example_secret", "placeholder",
    )
    if len(secret) < 32 or any(marker in normalized for marker in known_placeholders):
        raise RuntimeError("strong_titan_secret_key_required_for_credential_storage")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(text: str) -> str:
    if not text:
        return ""
    return "v2:" + _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    if token.startswith("v2:"):
        try:
            return _fernet().decrypt(token[3:].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, RuntimeError, UnicodeDecodeError):
            return ""
    # Backward compatibility for credentials saved by the original
    # unauthenticated XOR format. New writes always use Fernet v2.
    try:
        enc = base64.urlsafe_b64decode(token.encode())
        raw = _xor(enc, _key_bytes())
        return raw.decode()
    except Exception:
        return ""


def _upgrade_legacy_credentials(data: dict[str, Any], path: Path) -> bool:
    """Atomically migrate legacy XOR values to authenticated Fernet v2 before use."""
    key_token = data.get("api_key_enc", "")
    secret_token = data.get("api_secret_enc", "")
    if isinstance(key_token, str) and isinstance(secret_token, str) and key_token.startswith("v2:") and secret_token.startswith("v2:"):
        return True
    try:
        api_key = decrypt_secret(key_token)
        api_secret = decrypt_secret(secret_token)
        if not api_key or not api_secret:
            return False
        upgraded = dict(data)
        upgraded["api_key_enc"] = encrypt_secret(api_key)
        upgraded["api_secret_enc"] = encrypt_secret(api_secret)
        _atomic_write_json(path, upgraded)
        return True
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write encrypted credentials atomically with owner-only permissions."""
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"), ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _storage_path(environment: str = "testnet", market: str | None = None) -> Path:
    if environment not in {"testnet", "live"}:
        raise ValueError("unsupported_binance_credential_environment")
    # Use a configured persistent data directory when deployed; otherwise keep
    # the local development default. Secrets remain server-side only.
    configured_dir = os.getenv("TITAN_DATA_DIR", "").strip()
    base = Path(configured_dir) if configured_dir else Path(__file__).resolve().parents[2] / "data"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(base, 0o700)
    suffix = "_futures" if market == "futures" else ""
    if market not in {None, "spot", "futures"}:
        raise ValueError("unsupported_market")
    if environment == "live":
        return base / ("binance_live" + suffix + "_creds.json")
    return base / ("binance_testnet" + suffix + "_creds.json")


def save_testnet_credentials(api_key: str, api_secret: str, market: str = "spot") -> dict[str, Any]:
    if market not in {"spot", "futures"}:
        raise ValueError("unsupported_market")
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise ValueError("api_key_and_secret_must_be_strings")
    api_key, api_secret = api_key.strip(), api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("api_key_and_secret_required")
    data = {
        "market": market,
        "api_key_enc": encrypt_secret(api_key),
        "api_secret_enc": encrypt_secret(api_secret),
        "has_key": True,
        "has_secret": True,
    }
    path = _storage_path("testnet", market)
    _atomic_write_json(path, data)
    return {"ok": True, "saved": True, "market": market, "note": "Secrets stored encrypted server-side. Not shown in UI."}


def load_testnet_credentials(market: str | None = None) -> dict[str, Any]:
    path = _storage_path("testnet", market)
    if not path.exists():
        return {"configured": False}
    try:
        data = json.loads(path.read_text())
        if not _upgrade_legacy_credentials(data, path):
            return {"configured": False, "reason": "credential_encryption_upgrade_required"}
        data = json.loads(path.read_text())
        api_key = decrypt_secret(data.get("api_key_enc", ""))
        api_secret = decrypt_secret(data.get("api_secret_enc", ""))
        return {
            "configured": bool(data.get("has_key") and data.get("has_secret") and api_key and api_secret),
            "market": data.get("market", "spot"),
            "api_key": api_key,
            "api_secret": api_secret,
        }
    except Exception:
        return {"configured": False}


def delete_testnet_credentials(market: str | None = None) -> dict[str, Any]:
    targets = [_storage_path("testnet", market)] if market in {"spot", "futures"} else [_storage_path("testnet", "spot"), _storage_path("testnet", "futures")]
    deleted = 0
    for path in targets:
        if path.exists():
            path.unlink()
            deleted += 1
    return {"ok": True, "deleted": True, "files_deleted": deleted}


def public_status() -> dict[str, Any]:
    creds = load_testnet_credentials()
    return {
        "configured": creds.get("configured", False),
        "market": creds.get("market", "spot"),
        "has_secret": bool(creds.get("configured")),
        # Never expose key/secret
    }


def save_live_credentials(api_key: str, api_secret: str, market: str = "spot") -> dict[str, Any]:
    """Store Live credentials separately; no connection or order is attempted here."""
    if market not in {"spot", "futures"}:
        raise ValueError("unsupported_market")
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise ValueError("api_key_and_secret_must_be_strings")
    api_key, api_secret = api_key.strip(), api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("api_key_and_secret_required")
    data = {
        "environment": "live",
        "market": market,
        "api_key_enc": encrypt_secret(api_key),
        "api_secret_enc": encrypt_secret(api_secret),
        "has_key": True,
        "has_secret": True,
    }
    path = _storage_path("live", market)
    _atomic_write_json(path, data)
    return {"ok": True, "saved": True, "environment": "live", "market": market,
            "note": "Live credentials encrypted and stored separately; no connectivity check or order was performed."}


def load_binance_credentials(environment: str = "testnet", market: str | None = None) -> dict[str, Any]:
    """Load only the credential file belonging to the explicitly selected environment."""
    if environment not in {"testnet", "live"}:
        return {"configured": False}
    path = _storage_path(environment, market)
    if not path.exists():
        return {"configured": False, "environment": environment}
    try:
        data = json.loads(path.read_text())
        if data.get("environment", environment) != environment:
            return {"configured": False, "environment": environment}
        if not _upgrade_legacy_credentials(data, path):
            return {"configured": False, "environment": environment,
                    "reason": "credential_encryption_upgrade_required"}
        data = json.loads(path.read_text())
        api_key = decrypt_secret(data.get("api_key_enc", ""))
        api_secret = decrypt_secret(data.get("api_secret_enc", ""))
        return {
            "configured": bool(data.get("has_key") and data.get("has_secret") and api_key and api_secret),
            "environment": environment,
            "market": data.get("market", "spot"),
            "api_key": api_key,
            "api_secret": api_secret,
        }
    except Exception:
        return {"configured": False, "environment": environment}


def delete_live_credentials(market: str | None = None) -> dict[str, Any]:
    targets = [_storage_path("live", market)] if market in {"spot", "futures"} else [_storage_path("live", "spot"), _storage_path("live", "futures")]
    deleted = 0
    for path in targets:
        if path.exists():
            path.unlink()
            deleted += 1
    return {"ok": True, "deleted": True, "environment": "live", "files_deleted": deleted}
