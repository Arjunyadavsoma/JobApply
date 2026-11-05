"""Phase 19A-2 — encrypted credential vault tests."""
from __future__ import annotations

import base64
import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def vault_env(env, monkeypatch):
    """Fresh DB (via conftest env) + isolated master-key location per test."""
    vault = importlib.import_module("jobagent.credentials")
    monkeypatch.delenv(vault.KEY_ENV_VAR, raising=False)
    key_file = env / "data" / ".credentials.key"
    monkeypatch.setattr(vault, "KEY_FILE", key_file)
    yield vault, key_file


class TestMasterKey:
    def test_env_var_key_used_when_set(self, monkeypatch, vault_env):
        vault, key_file = vault_env
        key = base64.b64encode(b"k" * 32).decode()
        monkeypatch.setenv(vault.KEY_ENV_VAR, key)
        assert vault.load_master_key() == b"k" * 32
        # env var wins: no key file is created
        assert not key_file.exists()

    def test_invalid_env_key_rejected(self, monkeypatch, vault_env):
        vault, _ = vault_env
        monkeypatch.setenv(vault.KEY_ENV_VAR, "not-base64!!!")
        with pytest.raises(vault.VaultError):
            vault.load_master_key()
        monkeypatch.setenv(vault.KEY_ENV_VAR, base64.b64encode(b"short").decode())
        with pytest.raises(vault.VaultError):
            vault.load_master_key()

    def test_key_file_generated_on_first_use(self, vault_env):
        vault, key_file = vault_env
        assert not key_file.exists()
        key1 = vault.load_master_key()
        assert key_file.exists()
        assert len(key1) == 32
        assert vault.load_master_key() == key1  # stable across calls
        # key file must not be world/group readable
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_corrupt_key_file_fails_loudly(self, vault_env):
        vault, key_file = vault_env
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(b"garbage")
        with pytest.raises(vault.VaultError) as e:
            vault.load_master_key()
        assert "corrupt" in str(e.value)

    def test_no_create_mode_requires_existing(self, vault_env):
        vault, _ = vault_env
        with pytest.raises(vault.VaultError):
            vault.load_master_key(create=False)


class TestVaultCrud:
    def test_roundtrip_encrypt_decrypt(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        assert vault.get_secret("groq-main") == "gsk_testvalue1234567890"

    def test_plaintext_never_in_database(self, vault_env):
        vault, _ = vault_env
        secret = "gsk_SUPERSECRETVALUE123456"
        vault.add_credential("groq-main", "groq", secret)
        from jobagent.db import DB_PATH
        raw = Path(DB_PATH).read_bytes()
        assert secret.encode() not in raw  # ciphertext only

    def test_list_never_returns_secret(self, vault_env):
        vault, _ = vault_env
        secret = "gsk_testvalue1234567890"
        vault.add_credential("groq-main", "groq", secret)
        items = vault.list_credentials()
        assert len(items) == 1
        item = items[0]
        assert secret not in str(item)
        assert item["masked"].startswith("gsk")
        assert "•" in item["masked"]
        assert secret[4:-4] not in item["masked"]  # middle never visible

    def test_mask_short_secret(self, vault_env):
        vault, _ = vault_env
        assert vault.mask_secret("") == "(empty)"
        assert vault.mask_secret("abc") == "••••"
        assert vault.mask_secret("12345678") == "••••"
        m = vault.mask_secret("123456789")
        assert m.startswith("123") and m.endswith("789")

    def test_duplicate_name_rejected(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        with pytest.raises(vault.VaultError) as e:
            vault.add_credential("groq-main", "groq", "gsk_other987654321")
        assert "already exists" in str(e.value)

    def test_unknown_provider_rejected(self, vault_env):
        vault, _ = vault_env
        with pytest.raises(vault.VaultError):
            vault.add_credential("x", "not-a-provider", "secret")

    def test_empty_inputs_rejected(self, vault_env):
        vault, _ = vault_env
        with pytest.raises(vault.VaultError):
            vault.add_credential("", "groq", "secret")
        with pytest.raises(vault.VaultError):
            vault.add_credential("k", "groq", "")
        with pytest.raises(vault.VaultError):
            vault.add_credential("k", "groq", None)

    def test_update_replaces_secret(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        vault.update_credential("groq-main", "gsk_newvalue99999999999")
        assert vault.get_secret("groq-main") == "gsk_newvalue99999999999"

    def test_revoke_blocks_usage(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        vault.revoke_credential("groq-main")
        with pytest.raises(vault.VaultError) as e:
            vault.get_secret("groq-main")
        assert "revoked" in str(e.value)
        # revoked shows in listing
        assert vault.list_credentials()[0]["revoked"] is True
        # double revoke rejected
        with pytest.raises(vault.VaultError):
            vault.revoke_credential("groq-main")

    def test_delete_removes_row(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        vault.delete_credential("groq-main")
        assert vault.list_credentials() == []
        with pytest.raises(vault.VaultError):
            vault.get_secret("groq-main")

    def test_wrong_master_key_detected_not_silent(self, monkeypatch, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        # Simulate key loss: point KEY_FILE elsewhere and set env key to another
        wrong = base64.b64encode(b"w" * 32).decode()
        monkeypatch.setenv(vault.KEY_ENV_VAR, wrong)
        with pytest.raises(vault.VaultError) as e:
            vault.get_secret("groq-main")
        assert "wrong master key" in str(e.value) or "decryption failed" in str(e.value)
        # listing shows locked, not a fake value
        item = vault.list_credentials()[0]
        assert item["masked"] == "(locked — wrong master key)"

    def test_resolve_api_key_prefers_vault_then_env(self, monkeypatch, vault_env):
        vault, _ = vault_env
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        assert vault.resolve_api_key("GROQ_API_KEY") is None
        monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env_1234567890")
        assert vault.resolve_api_key("GROQ_API_KEY") == "gsk_from_env_1234567890"
        vault.add_credential("groq-vault", "groq", "gsk_from_vault12345678")
        assert vault.resolve_api_key("GROQ_API_KEY", "groq-vault") == "gsk_from_vault12345678"

    def test_tampered_ciphertext_rejected(self, vault_env):
        vault, _ = vault_env
        vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
        from jobagent.db import DB_PATH
        import sqlite3
        with sqlite3.connect(DB_PATH) as conn:
            blob = conn.execute("SELECT ciphertext FROM credentials").fetchone()[0]
            tampered = bytearray(blob)
            tampered[-1] ^= 0xFF  # flip a bit inside the GCM tag
            conn.execute("UPDATE credentials SET ciphertext=?",
                         (bytes(tampered),))
        with pytest.raises(vault.VaultError) as e:
            vault.get_secret("groq-main")
        assert "decryption failed" in str(e.value)


class TestVaultFailsClosed:
    def test_no_key_no_storage(self, monkeypatch, tmp_path, env):
        """With no key available, adding a credential must fail — never plaintext."""
        import importlib as il
        vault = il.import_module("jobagent.credentials")
        monkeypatch.delenv(vault.KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "nonexistent" / "bad" / ".key")
        # KEY_FILE parent dir cannot be created (nonexistent/bad path pieces)
        with pytest.raises((vault.VaultError, OSError)):
            vault.add_credential("groq-main", "groq", "gsk_testvalue1234567890")
