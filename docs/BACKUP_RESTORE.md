# Backup & Restore — JobApply

Phase 19B replaced the ad-hoc backup endpoint with a validated backup system
(`jobagent/backup.py`).

## What is backed up

The SQLite database `data/jobs.db` — jobs, events, submissions, queue state,
readiness, company intel, agent runs, interview prep, **encrypted
credentials**, execution logs and the state-machine audit trail.

**Not included**: `data/.credentials.key` (the vault master key — back it up
separately and keep it safe; without it encrypted credentials are
unrecoverable by design), `profile/` (master resume), `output/` (tailored
resumes). Copy those directories manually for a full system backup.

## Creating a backup

* UI: **System → Backup & Restore → Back up now** (optional label)
* API: `POST /api/backup?label=nightly`

Every backup is:

1. taken with the SQLite **online-backup API** (WAL-safe, no locks, safe
   while the app runs),
2. validated with `PRAGMA integrity_check` — a backup that fails is
   **discarded, never reported as good**,
3. hashed (SHA-256) for tamper evidence,
4. stored in `data/backups/jobs_<timestamp>[_<label>].db`.

## Listing / verifying

* `GET /api/backup` — all backups, newest first, with live integrity status.
  Listing also prunes per the retention policy.
* `GET /api/backup/{name}/verify` — re-validate one backup.

## Restoring

1. UI: **System → Backup & Restore → Restore…** (explicit confirmation) or
   `POST /api/backup/{name}/restore`.
2. Safety: the **current database is first copied** to a
   `pre-restore` backup — you can always undo a restore.
3. The chosen backup is copy-then-atomic-replace; stale WAL/SHM files are
   removed so they cannot poison the restored DB.
4. **Restart the application** after a restore.

Restore refuses: corrupted backups, path traversal (`../`), unknown names.

## Retention

Applied on every backup listing: keep the newest **30** backups and anything
younger than **90** days; older ones are deleted. (Constants
`DEFAULT_KEEP` / `DEFAULT_MAX_AGE_DAYS` in `jobagent/backup.py`.)

## Database reliability (same phase)

* Explicit schema versioning: `PRAGMA user_version` stamped by
  `_migrate()`; a database stamped NEWER than the build **refuses to open**
  (downgrade protection).
* WAL journal mode + `foreign_keys=ON` on every connection; 30 s busy timeout.
* `/api/system/diagnostics` runs `PRAGMA quick_check` on the live DB.
* Migration testing covers: fresh install, upgrade from v1, interrupted
  writes, corruption detection, restore, and recovery after process kill.

## Recommended routine

```bash
# nightly before bed (Task Scheduler / cron)
curl -X POST http://127.0.0.1:8600/api/backup?label=nightly -H "X-Dashboard-Token: $DASHBOARD_TOKEN"
# monthly: copy data/ + profile/ + .credentials.key off-machine
```
