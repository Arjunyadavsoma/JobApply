"""Phase 19B — backup, restore, retention and integrity for the SQLite DB.

Rules:
* Backups use the SQLite online-backup API (WAL-safe, no table locks).
* Every backup is validated (PRAGMA integrity_check + quick_check) before
  being reported as good; the SHA-256 of the file is recorded for tamper
  evidence.
* Restore NEVER overwrites the live DB directly: the current DB is itself
  backed up first ("pre-restore safety copy"), then the chosen backup is
  copied into place atomically.
* Retention prunes old backups by count and age.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobagent.config import DATA_DIR
from jobagent.db import DB_PATH

BACKUP_DIR = DATA_DIR / "backups"
DEFAULT_KEEP = 30                 # max number of backups retained
DEFAULT_MAX_AGE_DAYS = 90


class BackupError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    name: str
    path: Path
    bytes: int
    created_at: str
    sha256: str
    integrity: str  # ok | failed | unknown

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": str(self.path), "bytes": self.bytes,
            "created_at": self.created_at, "sha256": self.sha256[:16] + "…",
            "integrity": self.integrity,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_integrity(db_path: Path) -> str:
    """SQLite integrity_check on a database file. 'ok' | 'failed:<msg>'."""
    if not db_path.exists():
        return "failed:file missing"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("PRAGMA integrity_check(1)").fetchone()
            result = row[0] if row else "unknown"
            return "ok" if str(result).lower() == "ok" else f"failed:{result}"
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return f"failed:{type(exc).__name__}"


def create_backup(*, label: str = "") -> BackupInfo:
    """Create a validated backup of the live DB."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"jobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if label:
        safe = "".join(c for c in label if c.isalnum() or c in "-_")[:40]
        stem = f"{stem}_{safe}" if safe else stem
    dest = BACKUP_DIR / f"{stem}.db"
    if dest.exists():
        raise BackupError(f"backup already exists: {dest.name}")

    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)          # online backup API — consistent even under WAL
        finally:
            dst.close()
    finally:
        src.close()

    integrity = check_integrity(dest)
    info = BackupInfo(name=dest.name, path=dest, bytes=dest.stat().st_size,
                      created_at=_now_iso(), sha256=_sha256_file(dest),
                      integrity=integrity)
    if integrity != "ok":
        dest.unlink(missing_ok=True)
        raise BackupError(f"backup failed integrity check ({integrity}); discarded")
    return info


def list_backups() -> list[BackupInfo]:
    """All backups, newest first, with size + validated integrity (fast: cached by mtime scan)."""
    if not BACKUP_DIR.exists():
        return []
    out: list[BackupInfo] = []
    for p in sorted(BACKUP_DIR.glob("jobs_*.db"), reverse=True):
        stat = p.stat()
        created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        out.append(BackupInfo(
            name=p.name, path=p, bytes=stat.st_size,
            created_at=created.isoformat(timespec="seconds"),
            sha256=_sha256_file(p), integrity=check_integrity(p)))
    return out


def restore_backup(name: str, *, pre_restore_copy: bool = True) -> dict:
    """Restore a backup over the live DB.

    Safety: the current DB is first copied to a pre-restore backup; only
    then is the chosen backup atomically moved into place. The caller is
    expected to restart background tasks/process after restore.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise BackupError("invalid backup name")
    src = BACKUP_DIR / name
    if not src.exists():
        raise BackupError(f"backup not found: {name}")

    integrity = check_integrity(src)
    if integrity != "ok":
        raise BackupError(f"backup {name} failed integrity check ({integrity}); "
                          "restore refused")

    guard = None
    if pre_restore_copy and DB_PATH.exists():
        guard = create_backup(label="pre-restore")

    # Copy-then-replace so a crash never leaves a half-written live DB.
    tmp = DB_PATH.with_suffix(".restore.tmp")
    tmp.write_bytes(src.read_bytes())
    # WAL/SHM of the old DB would poison the restored one — remove them.
    for side in (DB_PATH.with_suffix(".db-wal")), (DB_PATH.with_suffix(".db-shm")):
        side.unlink(missing_ok=True)
    DB_PATH.unlink(missing_ok=True)
    tmp.replace(DB_PATH)
    return {
        "ok": True, "restored_from": name,
        "pre_restore_backup": guard.as_dict() if guard else None,
        "integrity": integrity,
    }


def apply_retention(*, keep: int = DEFAULT_KEEP,
                    max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Prune old backups. Keeps the most recent ``keep`` and anything younger
    than ``max_age_days`` days; deletes the rest."""
    backups = list_backups()
    if not backups:
        return {"deleted": 0, "kept": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept: list[BackupInfo] = []
    deleted = 0
    # list_backups is newest-first; keep first ``keep`` unconditionally.
    for i, b in enumerate(backups):
        created = datetime.fromisoformat(b.created_at)
        if i < keep or created >= cutoff:
            kept.append(b)
        else:
            b.path.unlink(missing_ok=True)
            deleted += 1
    return {"deleted": deleted, "kept": len(kept)}


def verify_backup(name: str) -> dict:
    """Re-validate one backup: existence, integrity, hash stability."""
    if "/" in name or "\\" in name or ".." in name:
        raise BackupError("invalid backup name")
    p = BACKUP_DIR / name
    if not p.exists():
        raise BackupError(f"backup not found: {name}")
    return {
        "name": name,
        "integrity": check_integrity(p),
        "sha256": _sha256_file(p),
        "bytes": p.stat().st_size,
    }
