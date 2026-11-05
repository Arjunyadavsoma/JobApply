# Production Checklist — JobApply

Work through this before running JobApply as your daily driver.

## 1. Install

- [ ] Python 3.11+ (`python --version`)
- [ ] `pip install -r requirements.txt`
- [ ] `playwright install chromium` (for apply/automation; skip for discovery-only)
- [ ] Frontend (optional, the served dashboard is prebuilt): `cd web && npm ci && npm run build`

## 2. Configure

- [ ] `python -m jobagent init` → edit `config.yaml` (search profile, automation gates)
- [ ] `.env`: add `GROQ_API_KEY=gsk_...` (https://console.groq.com/keys)
- [ ] Optional but recommended: move keys into the encrypted vault
      (System → Credential Manager). Back up `data/.credentials.key`.
- [ ] If the port is reachable by anything other than you:
      `DASHBOARD_TOKEN=<long random string>` in `.env`.

## 3. Candidate data

- [ ] Upload master resume (Resume Studio)
- [ ] Complete the candidate profile (batch/CGPA gates depend on it)
- [ ] Review automation gates in `config.yaml`: `max_per_day`, `max_per_company`,
      `min_match_score`, `min_eligibility`, `min_quality_score`

## 4. Verify the install

- [ ] `curl http://127.0.0.1:8600/health/live` → `{"status":"ok",...}`
- [ ] Dashboard → System → System Health: all critical checks green
      (database, schema_version, db_integrity; Groq green when a key is set)
- [ ] `python -m pytest tests/ -q -m "not e2e and not live"` → all pass
- [ ] First backup: System → Backup & Restore → Back up now

## 5. Safety review (read once)

- [ ] Assisted mode (default) fills but never submits — you press Submit.
- [ ] Authorized mode is OFF unless `automation.authorized_submission=true`
      AND you approve each job explicitly in the UI.
- [ ] The agent stops at CAPTCHA / logins / legal questions. Always.
- [ ] Daily/company limits prevent mass-blasting. Keep them sane.
- [ ] `SUBMISSION_UNCERTAIN` requires your manual verification — the agent
      will never auto-retry an uncertain submission.

## 6. Operations

- [ ] Nightly backup (see docs/BACKUP_RESTORE.md for a cron/curl one-liner)
- [ ] Off-machine copy of `data/`, `profile/` and `.credentials.key` monthly
- [ ] Watch System → System Health when sources act up
      (degraded_today, breaker states in diagnostics)

## 7. Deployment modes

| Mode | How | Notes |
|---|---|---|
| Windows local dev | `Job Agent Dashboard.bat` or `python -m jobagent web` | binds 127.0.0.1 |
| Local production-like | `uvicorn jobagent.server:app --host 127.0.0.1 --port 8600` | set DASHBOARD_TOKEN anyway |
| Docker | `docker compose up -d` | non-root user, healthcheck on /health/live, volumes for data/profile/output |
| Linux server | Docker, or uvicorn behind a reverse proxy | **never** expose without the token; HTTPS terminates at your proxy |

## 8. What needs external services

| Capability | Requirement | Without it |
|---|---|---|
| AI scoring/tailoring/answers | Groq API key | matching degrades to the local keyword score; prep answers stop |
| Job-board APIs | public/keys where offered | those sources skip; others still work |
| Gmail intelligence | OAuth credentials (read-only) | email features off |
| Apply automation | Chromium + visible browser | discovery/scoring still fine headless |

## 9. Rollback

- [ ] App rollback: `git checkout <previous-tag>` (or pip-install the prior
      release) — a newer DB schema refuses to open on older code by design.
- [ ] Data rollback: System → Backup & Restore → pick the pre-incident backup
      (a pre-restore safety copy is always made first).
