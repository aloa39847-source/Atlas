from __future__ import annotations

import base64

from app.core.config import settings
from app.services.credentials import _key_bytes, _xor, _storage_path, decrypt_secret, encrypt_secret


def test_testnet_credentials_require_strong_application_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "dev-only-change-locally")
    try:
        encrypt_secret("test-secret")
        assert False, "weak application secret must be rejected"
    except RuntimeError as exc:
        assert "strong_titan_secret_key_required" in str(exc)


def test_testnet_credentials_use_authenticated_fernet_encryption(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "A" * 48)
    token = encrypt_secret("binance-testnet-secret")
    assert token.startswith("v2:")
    assert "binance-testnet-secret" not in token
    assert decrypt_secret(token) == "binance-testnet-secret"

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert decrypt_secret(tampered) == ""


def test_legacy_testnet_credentials_remain_readable(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "legacy-secret-key-for-migration")
    raw = b"legacy-api-secret"
    legacy = base64.urlsafe_b64encode(_xor(raw, _key_bytes())).decode()
    assert decrypt_secret(legacy) == "legacy-api-secret"


def test_testnet_credential_storage_respects_persistent_data_directory(tmp_path, monkeypatch):
    target = tmp_path / "persistent-volume"
    monkeypatch.setenv("TITAN_DATA_DIR", str(target))
    path = _storage_path()
    assert path == target / "binance_testnet_creds.json"
    assert target.is_dir()


def test_legacy_credential_file_is_upgraded_atomically_to_fernet(tmp_path, monkeypatch):
    import json
    from app.services import credentials
    monkeypatch.setattr(settings, "secret_key", "strong-migration-secret-" * 3)
    monkeypatch.setenv("TITAN_DATA_DIR", str(tmp_path))
    path = _storage_path("testnet", "spot")
    path.write_text(json.dumps({
        "market": "spot", "has_key": True, "has_secret": True,
        "api_key_enc": base64.urlsafe_b64encode(_xor(b"legacy-api-key", _key_bytes())).decode(),
        "api_secret_enc": base64.urlsafe_b64encode(_xor(b"legacy-api-secret", _key_bytes())).decode(),
    }))
    loaded = credentials.load_testnet_credentials("spot")
    assert loaded["configured"] is True
    assert loaded["api_key"] == "legacy-api-key"
    assert loaded["api_secret"] == "legacy-api-secret"
    stored = json.loads(path.read_text())
    assert stored["api_key_enc"].startswith("v2:")
    assert stored["api_secret_enc"].startswith("v2:")
    assert "legacy-api-secret" not in path.read_text()


def test_legacy_credential_file_fails_closed_when_strong_key_unavailable(tmp_path, monkeypatch):
    import json
    from app.services import credentials
    monkeypatch.setattr(settings, "secret_key", "weak")
    monkeypatch.setenv("TITAN_DATA_DIR", str(tmp_path))
    path = _storage_path("testnet", "spot")
    path.write_text(json.dumps({
        "market": "spot", "has_key": True, "has_secret": True,
        "api_key_enc": base64.urlsafe_b64encode(_xor(b"legacy-api-key", _key_bytes())).decode(),
        "api_secret_enc": base64.urlsafe_b64encode(_xor(b"legacy-api-secret", _key_bytes())).decode(),
    }))
    loaded = credentials.load_testnet_credentials("spot")
    assert loaded["configured"] is False
    assert path.exists()
    assert not json.loads(path.read_text())["api_secret_enc"].startswith("v2:")


def test_credential_storage_rejects_long_placeholder_application_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_CHARACTERS")
    try:
        encrypt_secret("must-not-be-encrypted-with-a-placeholder")
        assert False, "known placeholder secrets must be rejected even when long"
    except RuntimeError as exc:
        assert "strong_titan_secret_key_required" in str(exc)


def test_credential_storage_rejects_missing_application_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    try:
        encrypt_secret("must-not-be-encrypted-without-a-key")
        assert False, "credential encryption must fail closed without an application secret"
    except RuntimeError as exc:
        assert "strong_titan_secret_key_required" in str(exc)


def test_legacy_credential_decryption_rejects_missing_application_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    legacy = base64.urlsafe_b64encode(_xor(b"known-default-key-data", b"titan-dev")).decode()
    assert decrypt_secret(legacy) == ""


def test_saved_credentials_use_private_directory_and_atomic_owner_only_file(tmp_path, monkeypatch):
    import json
    import stat
    from app.services.credentials import save_testnet_credentials

    monkeypatch.setattr(settings, "secret_key", "strong-storage-test-secret-" * 2)
    target = tmp_path / "private-credentials"
    monkeypatch.setenv("TITAN_DATA_DIR", str(target))

    result = save_testnet_credentials("test-api-key", "test-api-secret", "spot")
    path = target / "binance_testnet_creds.json"

    assert result["saved"] is True
    assert path.exists()
    assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    stored = json.loads(path.read_text())
    assert stored["api_key_enc"].startswith("v2:")
    assert stored["api_secret_enc"].startswith("v2:")
    assert "test-api-key" not in path.read_text()
    assert "test-api-secret" not in path.read_text()
    assert not list(target.glob("*.tmp"))
    assert not list(target.glob(".*.tmp"))


def test_save_testnet_credentials_rejects_invalid_market_and_empty_values(monkeypatch):
    from app.services.credentials import save_testnet_credentials

    monkeypatch.setattr(settings, "secret_key", "strong-storage-test-secret-" * 2)
    import pytest
    with pytest.raises(ValueError, match="unsupported_market"):
        save_testnet_credentials("key", "secret", "live")
    with pytest.raises(ValueError, match="api_key_and_secret_required"):
        save_testnet_credentials(" ", "secret", "spot")
