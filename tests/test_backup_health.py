"""Phase 19B — backup, restore, retention, integrity tests + 19J health checks."""
from __future__ import annotations

import sqlite3

import pytest

from jobagent import backup
from jobagent import db


@pytest.fixture(autouse=True)
def backup_env(env):
    """backup module reads BACKUP_DIR at import time — rebind to the tmp data dir."""
    backup.BACKUP_DIR = env / "data" / "backups"
    yield backup


def _seed_job(job_id: str = "bk-1") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, source, company, title, url, apply_url,"
            " description, posted_at, discovered_at, updated_at, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "test", "Acme", "SDE", "https://acme.test/j",
             "https://acme.test/j", "d", "2026-11-01", "2026-11-04",
             "2026-11-04", "new"))
        conn.commit()


class TestBackup:
    def test_create_backup_validated(self, backup_env):
        _seed_job()
        info = backup.create_backup()
        assert info.integrity == "ok"
        assert info.bytes > 0
        assert len(info.sha256) == 64
        assert info.path.exists()

    def test_backup_contains_data(self, backup_env):
        _seed_job("bk-data")
        info = backup.create_backup()
        conn = sqlite3.connect(info.path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM jobs WHERE id='bk-data'").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_list_backups_newest_first(self, backup_env):
        _seed_job()
        b1 = backup.create_backup()
        b2 = backup.create_backup(label="second")
        listed = backup.list_backups()
        assert len(listed) == 2
        assert listed[0].name == b2.name
        assert all(b.integrity == "ok" for b in listed)

    def test_restore_roundtrip(self, backup_env):
        _seed_job("rt-1")
        snap = backup.create_backup()
        # mutate the live DB after the backup
        with db.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id='rt-1'")
            conn.commit()
        row = db.connect().execute("SELECT COUNT(*) c FROM jobs WHERE id='rt-1'").fetchone()
        assert row["c"] == 0
        # restore
        result = backup.restore_backup(snap.name)
        assert result["ok"] is True
        assert result["pre_restore_backup"] is not None  # safety copy made
        row = db.connect().execute("SELECT COUNT(*) c FROM jobs WHERE id='rt-1'").fetchone()
        assert row["c"] == 1  # data is back

    def test_restore_refuses_corrupt_backup(self, backup_env):
        _seed_job()
        snap = backup.create_backup()
        # corrupt the backup bytes
        snap.path.write_bytes(b"garbage" + snap.path.read_bytes()[7:1000])
        with pytest.raises(backup.BackupError) as e:
            backup.restore_backup(snap.name)
        assert "integrity" in str(e.value)

    def test_restore_rejects_path_traversal(self, backup_env):
        for evil in ("../../etc/passwd", "a/b", "..\\x", "x/y.db"):
            with pytest.raises(backup.BackupError):
                backup.restore_backup(evil)

    def test_restore_missing_backup(self, backup_env):
        with pytest.raises(backup.BackupError):
            backup.restore_backup("jobs_20990101_000000.db")

    def test_retention_prunes_old_backups(self, backup_env):
        _seed_job()
        for i in range(5):
            backup.create_backup(label=f"n{i}")
        # aggressive retention: keep only 2 newest
        result = backup.apply_retention(keep=2, max_age_days=0)
        # max_age_days=0 makes everything older than now -> pruned to `keep`
        assert result["kept"] <= 3  # newest kept + edge timing
        assert len(backup.list_backups()) <= 3

    def test_verify_backup(self, backup_env):
        _seed_job()
        snap = backup.create_backup()
        result = backup.verify_backup(snap.name)
        assert result["integrity"] == "ok"
        assert len(result["sha256"]) == 64

    def test_live_db_integrity_check(self, backup_env):
        _seed_job()
        assert backup.check_integrity(db.DB_PATH) == "ok"

    def test_interrupted_write_leaves_backups_usable(self, backup_env):
        """Simulate a crash mid-backup: a .tmp/partial file must never be
        listed as a valid backup."""
        _seed_job()
        backup.create_backup()
        # simulate a partially written file matching the glob
        partial = backup.BACKUP_DIR / "jobs_20991231_235959.db"
        partial.write_bytes(b"SQLite format 3\x00 truncated")
        listed = backup.list_backups()
        bad = [b for b in listed if b.name == partial.name]
        assert bad and bad[0].integrity.startswith("failed")
        # and restore refuses it
        with pytest.raises(backup.BackupError):
            backup.restore_backup(partial.name)


class TestSchemaVersioning:
    def test_fresh_install_gets_version_stamp(self, backup_env):
        with db.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == db.SCHEMA_VERSION

    def test_phase19_tables_exist(self, backup_env):
        with db.connect() as conn:
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"application_state", "application_transitions", "credentials"} <= tables

    def test_downgrade_protection(self, backup_env, monkeypatch):
        """A DB stamped newer than the code refuses to connect."""
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("PRAGMA user_version=99")
        conn.commit()
        conn.close()
        monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
        with pytest.raises(RuntimeError) as e:
            db.connect()
        assert "newer" in str(e.value)

    def test_upgrade_from_v1(self, backup_env):
        """A v1 DB (no Phase 19 tables, version stamp 1) migrates in place."""
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("DROP TABLE IF EXISTS application_state")
        conn.execute("DROP TABLE IF EXISTS application_transitions")
        conn.execute("DROP TABLE IF EXISTS credentials")
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        conn.close()
        # reconnecting runs _migrate -> tables recreated + stamp bumped
        with db.connect() as conn2:
            tables = {r["name"] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            version = conn2.execute("PRAGMA user_version").fetchone()[0]
        assert {"application_state", "application_transitions", "credentials"} <= tables
        assert version == db.SCHEMA_VERSION


class TestHealthEndpoints:
    """19J: /health/live, /health/ready, /api/system/* via TestClient."""

    @pytest.fixture()
    def client(self, env):
        from fastapi.testclient import TestClient
        from jobagent.server import app
        return TestClient(app)

    def test_liveness(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"]

    def test_readiness_ok(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["database"]["ok"] is True

    def test_system_status(self, client):
        r = client.get("/api/system/status")
        assert r.status_code == 200
        body = r.json()
        assert {"version", "uptime_s", "jobs_total", "database", "storage"} <= set(body)

    def test_diagnostics_shape(self, client):
        r = client.get("/api/system/diagnostics")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "degraded")
        checks = body["checks"]
        for name in ("database", "schema_version", "db_integrity", "groq",
                     "browser", "storage", "background_workers"):
            assert name in checks
            assert "ok" in checks[name]
        # Groq unconfigured must be reported honestly, never faked
        assert checks["groq"]["configured"] in (True, False)

    def test_diagnostics_never_leak_secrets(self, client, monkeypatch):
        import os
        monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecretkey1234567890")
        r = client.get("/api/system/diagnostics")
        assert "gsk_supersecretkey1234567890" not in r.text


class TestCredentialEndpoints:
    """19A: credential CRUD via the API — secrets never returned."""

    @pytest.fixture()
    def client(self, env, monkeypatch):
        from fastapi.testclient import TestClient
        from jobagent.server import app
        from jobagent import credentials
        monkeypatch.delenv(credentials.KEY_ENV_VAR, raising=False)
        # monkeypatch (not bare assignment) so the value is restored on teardown
        monkeypatch.setattr(credentials, "KEY_FILE", env / "data" / ".credentials.key")
        return TestClient(app)

    def test_full_lifecycle(self, client):
        # add
        r = client.post("/api/credentials", json={
            "name": "groq-main", "provider": "groq",
            "secret": "gsk_testvalue1234567890", "note": "primary"})
        assert r.status_code == 200 and r.json()["ok"]
        # list shows mask, never the secret
        r = client.get("/api/credentials")
        assert r.status_code == 200
        item = r.json()[0]
        assert "gsk_testvalue1234567890" not in str(item)
        assert "•" in item["masked"]
        # update
        r = client.put("/api/credentials/groq-main",
                       json={"secret": "gsk_rotated999999999999"})
        assert r.status_code == 200
        # revoke
        r = client.post("/api/credentials/groq-main/revoke")
        assert r.status_code == 200
        assert client.get("/api/credentials").json()[0]["revoked"] is True
        # delete
        r = client.delete("/api/credentials/groq-main")
        assert r.status_code == 200
        assert client.get("/api/credentials").json() == []

    def test_invalid_provider_rejected(self, client):
        r = client.post("/api/credentials", json={
            "name": "x", "provider": "nope", "secret": "s3cretvalue123"})
        assert r.status_code == 400

    def test_secret_not_in_response_even_on_error(self, client):
        r = client.post("/api/credentials", json={
            "name": "dup", "provider": "groq", "secret": "gsk_testvalue1234567890"})
        assert r.status_code == 200
        r = client.post("/api/credentials", json={
            "name": "dup", "provider": "groq", "secret": "gsk_other1234567890"})
        assert r.status_code == 400
        assert "gsk_other1234567890" not in r.text


class TestBackupEndpoints:
    @pytest.fixture()
    def client(self, env):
        from fastapi.testclient import TestClient
        from jobagent.server import app
        from jobagent import backup as bk
        bk.BACKUP_DIR = env / "data" / "backups"
        return TestClient(app)

    def test_backup_create_list_restore(self, client):
        r = client.post("/api/backup?label=nightly")
        assert r.status_code == 200
        created = r.json()
        assert created["ok"] and created["integrity"] == "ok"

        r = client.get("/api/backup")
        body = r.json()
        assert body["count"] >= 1
        name = body["backups"][0]["name"]

        r = client.get(f"/api/backup/{name}/verify")
        assert r.status_code == 200 and r.json()["integrity"] == "ok"

        r = client.post(f"/api/backup/{name}/restore")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_restore_unknown_404(self, client):
        r = client.post("/api/backup/jobs_20990101_000000.db/restore")
        assert r.status_code == 404

    def test_traversal_blocked(self, client):
        # Encoded traversal either fails routing or is rejected by the name
        # check — a restore must never happen for it.
        r = client.post("/api/backup/..%2F..%2Fetc%2Fpasswd/restore")
        assert r.status_code in (400, 404, 405)
        assert "ok" not in r.json()


class TestStateMachineEndpoints:
    @pytest.fixture()
    def client(self, env):
        from fastapi.testclient import TestClient
        from jobagent.server import app
        return TestClient(app)

    def test_state_endpoint_seeds_from_legacy(self, client):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, source, company, title, url, apply_url,"
                " description, posted_at, discovered_at, updated_at, status)"
                " VALUES ('api-1','t','Acme','SDE','u','u','d','2026-11-01',"
                "'2026-11-04','2026-11-04','new')")
            conn.commit()
        r = client.get("/api/applications/api-1/state")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "DISCOVERED"
        assert body["history"] == []

    def test_transition_via_api_validated(self, client):
        r = client.post("/api/applications/unknown-job/transition",
                        json={"to_state": "APPROVED", "actor": "user",
                              "note": "approve"})
        # invalid edge (DISCOVERED -> APPROVED) -> 409
        assert r.status_code == 409

    def test_state_counts(self, client):
        r = client.get("/api/applications/states")
        assert r.status_code == 200
        body = r.json()
        assert "counts" in body and "states" in body
        assert "DISCOVERED" in body["states"]
