"""Phase 19A-2 — encrypted credential vault.

API keys (Groq, OpenAI, Anthropic, Gmail OAuth) are stored AES-256-GCM
encrypted in the SQLite database. Design rules:

* The master key NEVER lives in the database. It comes from
  ``JOBAGENT_MASTER_KEY`` (base64, 32 bytes) or is generated once into
  ``data/.credentials.key`` (chmod 0600). Losing the key means the stored
  credentials are unrecoverable — that is the safe failure mode: we surface
  a clear "wrong master key" error instead of pretending all is well.
* Secrets are never returned by list/search APIs — only masked previews.
* Every stored blob is authenticated (GCM tag) so tampering is detected.
* Revocation is a hard flag: revoked credentials fail every lookup.

If a secure master key cannot be established, the vault refuses to store
anything — it fails closed rather than storing plaintext.
"""
from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.config import DATA_DIR

# cryptography is a hard dependency of the vault — no silent fallback.
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ENV_VAR = "JOBAGENT_MASTER_KEY"
KEY_FILE = DATA_DIR / ".credentials.key"
KEY_BYTES = 32

SUPPORTED_PROVIDERS = {
    "groq": "Groq API",
    "openai": "OpenAI API",
    "openrouter": "OpenRouter API",
    "together": "Together AI API",
    "fireworks": "Fireworks AI API",
    "gemini": "Google Gemini API (OpenAI-compatible)",
    "deepseek": "DeepSeek API",
    "mistral": "Mistral API",
    "anthropic": "Anthropic API",
    "gmail": "Gmail OAuth (client secret or refresh token)",
    "other": "Other service",
}

CREDENTIALS_TABLE = """
CREATE TABLE IF NOT EXISTS credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    provider    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    ciphertext  BLOB NOT NULL,                 -- AES-256-GCM: nonce || tag || ct
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_tested_at  TEXT,
    last_test_result TEXT,                     -- 'ok' | 'fail:<reason>' (redacted)
    revoked     INTEGER NOT NULL DEFAULT 0
)
"""


class VaultError(RuntimeError):
    """Vault misconfiguration — surfaced to the user, never silently ignored."""


# ---------------------------------------------------------------- master key

def _ensure_key_file_perms(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def load_master_key(*, create: bool = True) -> bytes:
    """Resolve the master key. Priority: env var > key file > (create) new key.

    Raises VaultError when the key cannot be established securely.
    """
    env = os.environ.get(KEY_ENV_VAR, "").strip()
    if env:
        try:
            raw = base64.b64decode(env, validate=True)
        except Exception as exc:  # noqa: BLE001 - bad user input
            raise VaultError(f"{KEY_ENV_VAR} is not valid base64") from exc
        if len(raw) != KEY_BYTES:
            raise VaultError(f"{KEY_ENV_VAR} must be {KEY_BYTES} bytes "
                             f"(base64 of 32 bytes), got {len(raw)}")
        return raw

    if KEY_FILE.exists():
        raw = KEY_FILE.read_bytes()
        if len(raw) == KEY_BYTES:
            return raw
        # Tolerate a text-editor trailing newline, but NEVER strip the key
        # itself: it is random binary and may legitimately start or end with
        # a whitespace byte (b" ", b"\n", ...) — stripping that would turn a
        # healthy key into a wrong key (Phase 20 flake fix).
        stripped = raw.strip()
        if len(stripped) == KEY_BYTES:
            return stripped
        # Anything else is a real misconfiguration.
        raise VaultError(
            f"{KEY_FILE} is corrupt (expected {KEY_BYTES} bytes, got {len(raw)}). "
            "Restore it from backup or delete it and re-enter credentials.")

    if not create:
        raise VaultError("no master key configured")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    tmp = KEY_FILE.with_suffix(".tmp")
    tmp.write_bytes(key)
    _ensure_key_file_perms(tmp)
    tmp.replace(KEY_FILE)
    _ensure_key_file_perms(KEY_FILE)
    return key


# ---------------------------------------------------------------- crypto core

def _encrypt(key: bytes, plaintext: str) -> bytes:
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)  # GCM standard 96-bit nonce
    # Associated data binds ciphertext to purpose — blocks cross-use.
    aad = b"jobagent-credentials-v1"
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return nonce + ct  # nonce || ciphertext+tag


def _decrypt(key: bytes, blob: bytes) -> str:
    if len(blob) < 12 + 16:
        raise VaultError("credential blob too short — database corruption?")
    aes = AESGCM(key)
    try:
        pt = aes.decrypt(blob[:12], blob[12:], b"jobagent-credentials-v1")
    except InvalidTag as exc:
        raise VaultError(
            "credential decryption failed: wrong master key or tampered data. "
            "Restore the correct key (see docs/SECURITY.md) or re-enter "
            "credentials after deleting the vault rows.") from exc
    return pt.decode("utf-8")


# ---------------------------------------------------------------- storage API

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREDENTIALS_TABLE)


def _connect() -> sqlite3.Connection:
    from jobagent.db import connect as db_connect
    conn = db_connect()
    ensure_schema(conn)
    return conn


def mask_secret(secret: str) -> str:
    """Masked preview: first 3 + last 4 chars for reasonable-length secrets."""
    if not secret:
        return "(empty)"
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:3]}•••••{secret[-4:]}"


def add_credential(name: str, provider: str, secret: str, *, note: str = "",
                   conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Store a new credential (encrypted). Never stores or logs plaintext."""
    name = (name or "").strip()
    if not name:
        raise VaultError("credential name is required")
    if provider not in SUPPORTED_PROVIDERS:
        raise VaultError(f"unknown provider '{provider}' "
                         f"(supported: {', '.join(sorted(SUPPORTED_PROVIDERS))})")
    if not secret or not isinstance(secret, str):
        raise VaultError("secret value is required")

    key = load_master_key()  # fails closed when unconfigured
    owns = conn is None
    c = conn or _connect()
    try:
        try:
            c.execute("INSERT INTO credentials "
                      "(name, provider, note, ciphertext, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?)",
                      (name, provider, note or "", _encrypt(key, secret),
                       _now(), _now()))
        except sqlite3.IntegrityError as exc:
            raise VaultError(f"credential '{name}' already exists "
                             "(revoke and re-add, or use update)") from exc
        if owns:
            c.commit()
        return {"ok": True, "id": c.execute(
            "SELECT last_insert_rowid()").fetchone()[0], "name": name}
    finally:
        if owns:
            c.close()


def update_credential(name: str, secret: str, *, note: str | None = None,
                      conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Replace the secret for an existing (non-revoked) credential."""
    key = load_master_key()
    owns = conn is None
    c = conn or _connect()
    try:
        row = c.execute("SELECT id, revoked FROM credentials WHERE name = ?",
                        (name,)).fetchone()
        if not row:
            raise VaultError(f"credential '{name}' not found")
        if row["revoked"]:
            raise VaultError(f"credential '{name}' is revoked — re-add it instead")
        c.execute("UPDATE credentials SET ciphertext=?, updated_at=?, "
                  "last_tested_at=NULL, last_test_result=NULL, note=COALESCE(?, note) "
                  "WHERE name=?",
                  (_encrypt(key, secret), _now(), note, name))
        if owns:
            c.commit()
        return {"ok": True, "name": name}
    finally:
        if owns:
            c.close()


def revoke_credential(name: str, *, conn: sqlite3.Connection | None = None
                      ) -> dict[str, Any]:
    """Hard-revoke: flagged, unusable by any lookup, ciphertext retained."""
    owns = conn is None
    c = conn or _connect()
    try:
        cur = c.execute("UPDATE credentials SET revoked=1, updated_at=? "
                        "WHERE name=? AND revoked=0", (_now(), name))
        if owns:
            c.commit()
        if cur.rowcount == 0:
            raise VaultError(f"credential '{name}' not found or already revoked")
        return {"ok": True, "name": name}
    finally:
        if owns:
            c.close()


def delete_credential(name: str, *, conn: sqlite3.Connection | None = None
                      ) -> dict[str, Any]:
    """Remove a credential row entirely (ciphertext included)."""
    owns = conn is None
    c = conn or _connect()
    try:
        cur = c.execute("DELETE FROM credentials WHERE name=?", (name,))
        if owns:
            c.commit()
        if cur.rowcount == 0:
            raise VaultError(f"credential '{name}' not found")
        return {"ok": True, "name": name}
    finally:
        if owns:
            c.close()


def list_credentials(*, conn: sqlite3.Connection | None = None
                     ) -> list[dict[str, Any]]:
    """Safe listing: masked previews only, never the secret."""
    owns = conn is None
    c = conn or _connect()
    try:
        rows = c.execute(
            "SELECT name, provider, note, created_at, updated_at, "
            "last_tested_at, last_test_result, revoked, ciphertext, id "
            "FROM credentials ORDER BY name").fetchall()
        out = []
        for r in rows:
            masked = "••••"
            try:  # best-effort mask; wrong key shows (locked) instead
                masked = mask_secret(_decrypt(load_master_key(create=False),
                                              r["ciphertext"]))
            except VaultError as exc:
                if "no master key configured" in str(exc):
                    masked = "(vault locked — no master key file)"
                else:
                    masked = "(locked — wrong master key)"
            out.append({
                "id": r["id"], "name": r["name"], "provider": r["provider"],
                "provider_label": SUPPORTED_PROVIDERS.get(r["provider"], r["provider"]),
                "note": r["note"], "masked": masked,
                "created_at": r["created_at"], "updated_at": r["updated_at"],
                "last_tested_at": r["last_tested_at"],
                "last_test_result": r["last_test_result"],
                "revoked": bool(r["revoked"]),
            })
        return out
    finally:
        if owns:
            c.close()


def get_secret(name: str, *, conn: sqlite3.Connection | None = None) -> str:
    """Internal-only: decrypt a credential for use by an integration.

    Raises VaultError for missing / revoked / undecryptable entries so the
    caller can fall back to the environment variable instead of guessing.
    """
    owns = conn is None
    c = conn or _connect()
    try:
        row = c.execute("SELECT ciphertext, revoked FROM credentials WHERE name=?",
                        (name,)).fetchone()
        if not row:
            raise VaultError(f"credential '{name}' not found")
        if row["revoked"]:
            raise VaultError(f"credential '{name}' is revoked")
        return _decrypt(load_master_key(create=False), row["ciphertext"])
    finally:
        if owns:
            c.close()


def record_test_result(name: str, ok: bool, reason: str = "",
                       conn: sqlite3.Connection | None = None) -> None:
    from jobagent.security import redact_secrets
    owns = conn is None
    c = conn or _connect()
    try:
        c.execute("UPDATE credentials SET last_tested_at=?, last_test_result=? "
                  "WHERE name=?",
                  (_now(), "ok" if ok else f"fail:{redact_secrets(reason)[:120]}",
                   name))
        if owns:
            c.commit()
    finally:
        if owns:
            c.close()


def test_credential(name: str) -> dict[str, Any]:
    """Live-test a stored credential against its provider (redacted result)."""
    provider_row = None
    owns = True
    c = _connect()
    try:
        provider_row = c.execute(
            "SELECT provider FROM credentials WHERE name=?", (name,)).fetchone()
    finally:
        c.close()
    if not provider_row:
        raise VaultError(f"credential '{name}' not found")

    provider = provider_row["provider"]
    secret = get_secret(name)
    try:
        if provider == "groq":
            import httpx
            with httpx.Client(timeout=15) as http:
                resp = http.get("https://api.groq.com/openai/v1/models",
                                headers={"Authorization": f"Bearer {secret}"})
            if resp.status_code == 200:
                record_test_result(name, True)
                return {"ok": True, "name": name, "detail": "Groq API accepted the key"}
            detail = f"HTTP {resp.status_code}"
            record_test_result(name, False, detail)
            return {"ok": False, "name": name, "detail": detail}
        # Providers without a cheap validation call: report untested honestly.
        record_test_result(name, False, f"no automated test for provider '{provider}'")
        return {"ok": False, "name": name,
                "detail": f"no automated connectivity test for '{provider}' — "
                          "verify manually"}
    except httpx.HTTPError as exc:  # network-level failure
        from jobagent.security import sanitize_error_message
        detail = sanitize_error_message(exc, context="credential-test")
        record_test_result(name, False, detail)
        return {"ok": False, "name": name, "detail": detail}


def resolve_api_key(provider_env_var: str, vault_name: str | None = None) -> str | None:
    """Resolution order: vault (if a name is given) -> environment variable.

    Used by the LLM layer so keys move from .env into the vault without
    code changes everywhere.
    """
    if vault_name:
        try:
            return get_secret(vault_name)
        except VaultError:
            pass  # fall through to env — explicit, not a silent failure
    env = os.environ.get(provider_env_var, "").strip()
    return env or None
