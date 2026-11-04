# Deployment

## Enable CI (one time, via the GitHub web UI)

The repository ships the workflow at `docs/ci-workflow.yml`. GitHub only allows workflow
files to be added through the web editor or a token with `workflow` scope, so:

1. Open https://github.com/Arjunyadavsoma/JobApply/new/main
2. Name the file `.github/workflows/ci.yml`
3. Paste the contents of `docs/ci-workflow.yml` and commit.

From then on, every push runs the offline test suite (209 tests), the TypeScript check,
the dashboard build and the Docker image build.

## Phase 18 deployment notes

- **New API endpoints** (all under the same optional `X-Dashboard-Token` auth):
  `/api/ats/matrix`, `/api/jobs/{id}/readiness|prepare|preview|company-intel`,
  `/api/batch/prepare`, `/api/batch/history`, `/api/jobs/{id}/interview-prep*`,
  `/api/agents/history`, `/api/jobs/{id}/apply/resume-session`.
- **Database migrations are automatic and idempotent** — first start after upgrading adds the
  Phase 18 tables (`company_intel`, `agent_runs`, `interview_prep`, `prep_progress`,
  `apply_locks`, `batch_runs`), the `email_messages.entities` column and the performance
  indexes. No data is dropped; keep the `data/` volume mounted.
- **One browser session per application** is enforced by SQLite locks — safe to run multiple
  assisted sessions for DIFFERENT jobs, never for the same job.
- **`JA_WAIT_SCALE`** (default `1`) scales the browser pacing; only set it below `1` for
  test environments, never in production.
- **Batch controls** live under `batch:` in `config.yaml` (size cap, allowed/excluded
  companies, allowed locations/job types). Batches only PREPARE; submissions remain
  per-job approvals behind `automation.authorized_submission`.
- **Chromium fallback**: if the configured Chrome channel is missing in the container, the
  headless apply flows fall back to the bundled Chromium automatically.

## Docker

```bash
cp .env.example .env             # add GROQ_API_KEY
cp config.example.yaml config.yaml
docker compose up -d --build     # dashboard at http://localhost:8600
```

- Image: multi-stage build (node 22 → python 3.12-slim); Chromium included for the apply
  flows. Lean variant: `docker build --build-arg INSTALL_BROWSER=false .`
- State: mounted volumes `data/` (job DB, logs, screenshots), `profile/` (candidate profile
  + resume), `output/` (tailored resumes).
- Health: `GET /api/health` (container HEALTHCHECK uses it).
- Exposure: the compose file binds to 127.0.0.1. To expose it, set `DASHBOARD_TOKEN` in
  `.env` and require it as the `X-Dashboard-Token` header on every `/api` call.

## Low-cost / free-tier notes

- The dashboard + discovery + AI services run fine without Chrome (skip the browser layer);
  only the apply/Naukri flows need it.
- Groq's free tier is enough for scoring/tailoring a fresh grad's volume; set
  `llm.max_requests_per_day` to protect the quota.
- A free-tier container host (Render/Railway/Fly) can run the lean image; keep the volume
  persistent or mount an S3-backed path for the SQLite database.

## Environment variables

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | primary AI provider (required for the recommended backend) |
| `GROQ_MODEL` | override `llm.model` (e.g. `openai/gpt-oss-20b`) |
| `GROQ_FALLBACK_MODEL` | used when the primary model errors or returns junk |
| `AI_PROVIDER` | documentation marker; effective backend is `config.yaml llm.backend` |
| `ANTHROPIC_API_KEY` | only for `llm.backend: api` |
| `DASHBOARD_TOKEN` | optional: require `X-Dashboard-Token` on `/api/*` |
| `JA_WAIT_SCALE` | browser pacing multiplier (tests use 0.15; keep 1 in production) |
| `EMAIL_IMAP_PASSWORD` | IMAP email sync (read-only) |
