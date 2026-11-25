from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "app/services/credentials.py"
content = r'''"""Authenticated encryption for server-side Binance Testnet credentials.

A strong TITAN_SECRET_KEY is mandatory. No plaintext or reversible XOR obfuscation.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings


def _fernet() -> Fernet:
    secret = (settings.secret_key or "").encode()
    if len(secret) < 32 or settings.secret_key == "dev-only-change-locally":
        raise RuntimeError("strong_titan_secret_key_required_for_credential_storage")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"titan-ai-premium-credential-storage-v1",
        info=b"binance-testnet-api-credentials",
    ).derive(secret)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(text: str) -> str:
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError, RuntimeError):
        return ""
    except Exception:
        return ""


def _storage_path() -> Path:
    base = Path(__file__).resolve().parents[2] / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "binance_testnet_creds.json"


def save_testnet_credentials(api_key: str, api_secret: str, market: str = "spot") -> dict[str, Any]:
    key = (api_key or "").strip()
    secret = (api_secret or "").strip()
    mkt = (market or "spot").strip().lower()
    if mkt not in {"spot", "futures"}:
        raise ValueError("unsupported_testnet_market")
    if not key or not secret:
        raise ValueError("api_key_and_secret_required")
    # Validate key strength before writing anything.
    cipher_key = encrypt_secret(key)
    cipher_secret = encrypt_secret(secret)
    data = {
        "version": 1,
        "algorithm": "fernet-hkdf-sha256",
        "market": mkt,
        "api_key_enc": cipher_key,
        "api_secret_enc": cipher_secret,
        "has_key": True,
        "has_secret": True,
    }
    path = _storage_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"ok": True, "saved": True, "market": mkt,
            "note": "Authenticated encryption enabled; secret values are never returned."}


def load_testnet_credentials() -> dict[str, Any]:
    path = _storage_path()
    if not path.exists():
        return {"configured": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("algorithm") != "fernet-hkdf-sha256":
            return {"configured": False, "reason": "credential_format_upgrade_required"}
        market = data.get("market", "spot")
        if market not in {"spot", "futures"}:
            return {"configured": False, "reason": "invalid_credential_market"}
        api_key = decrypt_secret(data.get("api_key_enc", ""))
        api_secret = decrypt_secret(data.get("api_secret_enc", ""))
        configured = bool(data.get("has_key") and data.get("has_secret") and api_key and api_secret)
        return {"configured": configured, "market": market,
                "api_key": api_key if configured else "",
                "api_secret": api_secret if configured else ""}
    except Exception:
        return {"configured": False}


def delete_testnet_credentials() -> dict[str, Any]:
    path = _storage_path()
    if path.exists():
        path.unlink()
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    return {"ok": True, "deleted": True}


def public_status() -> dict[str, Any]:
    creds = load_testnet_credentials()
    return {
        "configured": creds.get("configured", False),
        "market": creds.get("market", "spot"),
        "has_secret": bool(creds.get("configured")),
        "encryption": "fernet-hkdf-sha256",
    }
'''
p.write_text(content)

req = root / "requirements.txt"
s = req.read_text()
if "cryptography" not in s:
    s += "\n# Authenticated encryption for server-side API credentials.\ncryptography>=44.0.0,<47.0.0\n"
    req.write_text(s)

test = root / "tests/test_credentials_security.py"
test.write_text(r'''import json
import os
import pytest

from app.core.config import settings
from app.services import credentials


def test_credential_encryption_is_authenticated_and_randomized(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-strong-test-secret-key-with-32-bytes-minimum")
    one = credentials.encrypt_secret("testnet-api-secret")
    two = credentials.encrypt_secret("testnet-api-secret")
    assert one != two
    assert "testnet-api-secret" not in one
    assert credentials.decrypt_secret(one) == "testnet-api-secret"
    tampered = one[:-2] + ("AA" if one[-2:] != "AA" else "BB")
    assert credentials.decrypt_secret(tampered) == ""


def test_credential_storage_fails_closed_with_default_weak_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "secret_key", "dev-only-change-locally")
    monkeypatch.setattr(credentials, "_storage_path", lambda: tmp_path / "credentials.json")
    with pytest.raises(RuntimeError, match="strong_titan_secret_key_required"):
        credentials.save_testnet_credentials("test-key", "test-secret", "spot")
    assert not (tmp_path / "credentials.json").exists()


def test_credentials_are_encrypted_market_scoped_and_permission_restricted(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "secret_key", "another-strong-test-secret-key-with-at-least-32-bytes")
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(credentials, "_storage_path", lambda: path)
    saved = credentials.save_testnet_credentials("futures-key", "futures-secret", "futures")
    assert saved["ok"] is True
    raw = path.read_text()
    assert "futures-key" not in raw
    assert "futures-secret" not in raw
    loaded = credentials.load_testnet_credentials()
    assert loaded["configured"] is True
    assert loaded["market"] == "futures"
    assert loaded["api_key"] == "futures-key"
    assert loaded["api_secret"] == "futures-secret"
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_old_xor_credential_format_is_not_loaded_as_valid(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "secret_key", "another-strong-test-secret-key-with-at-least-32-bytes")
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"market": "spot", "api_key_enc": "legacy", "api_secret_enc": "legacy",
                                "has_key": True, "has_secret": True}))
    monkeypatch.setattr(credentials, "_storage_path", lambda: path)
    result = credentials.load_testnet_credentials()
    assert result["configured"] is False
    assert result["reason"] == "credential_format_upgrade_required"
''')
