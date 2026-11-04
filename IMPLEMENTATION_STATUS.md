# Implementation Status

Live progress file for the JobApply platform transformation. Updated at the end of every
implementation phase. Nothing is marked done that hasn't been tested.

**Last updated:** 2026-11-05 (Phase 17) · branch `main` · test suite: **148 passed**
(136 offline + 5 sandbox-E2E + 7 live-adapter; see PHASE_17_TEST_REPORT.md)

---

## Phase log

### Phase 1-3 — Audit, bug triage, architecture ✅
Full audit of the original repo. Stack: Python 3.11+/Typer/FastAPI/SQLite/httpx/Playwright +
React 19/Vite/Tailwind dashboard. Found: Windows-only task management, a prefit bug that
penalised fresher/intern titles (backwards for the target user), no structured job data,
no eligibility engine, no notifications/analytics/timeline, no tests in-repo.

### Phase 4 — Groq AI integration ✅
- `llm.backend: groq` via Groq's OpenAI-compatible endpoint, strict `json_schema` structured
  outputs with automatic JSON-mode fallback for models that lack it.
- **New this phase:** token usage tracking (`ai_usage` table), response cache (score calls),
  context clipping (12k chars per untrusted block), fallback model (`GROQ_FALLBACK_MODEL` /
  `llm.fallback_model`), daily request cap (`llm.max_requests_per_day`), untrusted-data
  framing (`llm.untrusted()`) against prompt injection.
- All external services in tests are mocked; live smoke test done earlier (commit `54d8867`).

### Phase 5 — Candidate profile & knowledge management ✅
- `jobagent/profile.py` + `CandidateProfile` model: India-first fields (graduation year,
  degree/branch, CGPA, 10th/12th %, backlogs, expected CTC/stipend, preferred roles and
  cities), seeded with the requested defaults (B.Tech CSE AIML 2027, Hyderabad, fresher,
  ₹6+ LPA) **as editable profile data, never as hardcoded rules**.
- Verified-answer knowledge base (`answers` table + API) checked BEFORE any AI answer.
- `facts_for_forms()` bridges profile → form filling (10th/12th, CGPA, notice period, CTC…).

### Phase 6 — Job ingestion & sources ✅
- New sources: **Arbeitnow** + **Remotive** public APIs (legitimate, key-less).
- **India MNC career registry** (25+ employers: Microsoft, Amazon, Google, Oracle, NVIDIA,
  Adobe, Salesforce, ServiceNow, Qualcomm, SAP, IBM, Accenture, Deloitte/PwC/EY/KPMG India,
  Freshworks, Zoho, Razorpay, PhonePe, Swiggy, Flipkart, CRED) seeded on first run through
  the existing portal adapters.
- Deterministic **enrichment** on every discovered job: LPA/stipend, experience range,
  employment type, remote/hybrid/onsite, eligible batches, CGPA/% cutoffs, backlog policy,
  deadlines, fresher/walk-in/off-campus flags, fraud heuristics.
- Per-source health tracking (`data/source_stats.json`), enrichment stored on the job row.
- Discovery never fabricates: jobs only come from the configured sources; dedup by id AND
  normalised company+title.

### Phase 7 — Intelligent matching & eligibility ✅
- `jobagent/eligibility.py`: deterministic 4-level engine (eligible / likely_eligible /
  needs_verification / not_eligible) over batch, CGPA, percentage, experience, backlogs,
  fraud flags. Unclear criteria NEVER auto-reject. The LLM cannot override it.
- `jobagent/matcher.py` v2: `MatchResultV2` with per-dimension scores (skills/education/
  experience/location), missing skills, disqualifiers, recommendations; deterministic
  eligibility gates the AI verdict (not_eligible caps score at 25 and forces skip);
  local prefit fallback keeps the pipeline alive when the AI is down.
- `prefit.py`: level penalty is now profile-aware (fresher titles scored fairly for
  freshers, senior titles penalised instead) — fixes the critical inversion.

### Phase 8 — Resume intelligence ✅ (preserved + hardened)
- Existing parse → master_resume.json → tailor (grounded, skill-fabrication guard,
  one-page fitting) preserved. Job descriptions now go through `untrusted()`.
- Every tailor run records a timeline event; versions live in `output/<job>/`.

### Phase 9 — Application answering ✅
- `ai_services.screening_answer()`: answer KB first, then a grounded AI answer with the
  candidate profile + resume; character limits respected; ungrounded facts flagged.
- India-specific form fields covered via profile facts (see Phase 5).

### Phase 10 — Browser automation & ATS adapters ✅ (preserved + hardened)
- Existing human-in-the-loop autofill (Playwright, visible Chrome, multi-pass, custom
  dropdowns, file uploads, red/orange flagging, never submits) preserved for Greenhouse/
  Lever/Ashby/Workday/SmartRecruiters/Workable/SuccessFactors/Oracle/Eightfold/MyNextHire
  and generic company forms; LinkedIn Easy Apply stops at Submit.
- **New:** fraud-guard blocks auto-fill on flagged postings (both API + apply layer),
  screenshots after each fill (`data/screenshots/`), apply sessions recorded as events.
- Naukri/Instahyre one-click apply never auto-clicked (they submit instantly).

### Phase 11 — Application tracking ✅
- Extended pipeline: new → scored → tailored → applied → assessment → interview → offer /
  rejected / withdrawn / expired / skipped / dismissed / error.
- `job_events` timeline per job; notes field; duplicate-application guard (API returns 409);
  interview/offer transitions create notifications.

### Phase 12 — Dashboard ✅
- New pages: **Applications** (CRM pipeline with status transitions), **Analytics**
  (weekly applications, score distribution, eligibility breakdown, missing skills, source
  quality, upcoming deadlines — all from persisted data), **AI Assistant** (interview prep,
  follow-up email, skill gap, quality check + answer KB editor), **My Profile** (structured
  profile editor).
- Jobs page: India quick views (Fresher / 2027 batch / Internships / Hyderabad / Remote /
  Deadlines) + facts column (salary, batch, deadline, fraud flag) + extended statuses.
- Job drawer: deterministic eligibility table, dimension scores, timeline, notes, pipeline
  selector, fraud banner. Notification bell with unread count.
- Frontend rebuilt (`web/dist`), TypeScript clean.

### Phase 13 — Notifications & background processing ✅
- In-app notification center (deadline ≤3d, fraud flags, not-eligible, status milestones).
- Background task system made **cross-platform** (Linux/macOS process groups, Windows
  taskkill preserved) + optional auto-discovery loop (`search.autodiscover_hours`).
- Email notifications: **not implemented** (documented; needs an SMTP/provider decision to
  avoid hardcoded credentials — see Remaining).

### Phase 14 — Security hardening ✅
- Optional `DASHBOARD_TOKEN` header auth (off by default for local use; `/api/health` open).
- Upload validation: PDF magic bytes + 10 MB cap. SPA path traversal contained.
- Prompt-injection defence: every job text reaching the LLM is wrapped by `untrusted()`.
- Secrets only in `.env` (gitignored); `.env.example` documents every variable.
- No Aadhaar/PAN fields stored by default; the profile holds no government IDs.

### Phase 15 — Testing ✅
- `tests/` — **76 offline tests** (no network, no browsers, no keys): enrichment parsing,
  eligibility classification, db migrations/dedup/events/notifications/answers, Groq layer
  (strict schema, JSON-mode fallback, fallback model, usage, cache, budget), API lifecycle
  (auth, filters, duplicate guard, upload validation, SPA safety), prefit profile-awareness,
  source adapters with mocked HTTP, India registry validity.
- Browser automation remains human-in-the-loop by design → no live-submit tests (policy).

### Phase 16 — Deployment prep ✅
- `Dockerfile` (multi-stage: node build → slim python runtime, optional Chromium layer,
  healthcheck) + `docker-compose.yml` with volume mounts and env file.
- GitHub Actions CI: pytest + tsc + vite build + docker build.
- `/api/health` endpoint for uptime checks. Cross-platform launcher compatibility (Linux/
  macOS now supported; the `.bat` remains for Windows).
- README rewritten for the new platform.

### Phase 17 — Live production readiness & autonomous applications ✅

**17.1 Repository verification.** Full audit re-run: all 76 pre-existing tests pass,
module-by-module inspection of sources/automation/Groq/profile/DB/tracking/API/Docker.
Gaps identified and closed below (nothing from phases 1-16 was broken).

**17.2 India job sources — evidence-based.** Every Priority-1/2 platform was live-probed
(robots.txt, public API responses, headless Chromium) before writing a line of code:
- **New working adapters:** Himalayas (cursor-paginated public API, India/worldwide filter)
  and Jobicy (tag-search public API) — both live-verified in the test report.
- **Already integrated:** Naukri (real-browser adapter), Instahyre (public JSON API),
  LinkedIn guest search, Remotive, Arbeitnow, 25-company India MNC career registry,
  Greenhouse/Lever/Ashby/Workday/SmartRecruiters/Workable/SuccessFactors/Oracle/
  Eightfold/MyNextHire boards.
- **External-link discovery registry** (`sources/external.py`): Internshala, Foundit,
  Freshersworld, Cutshort, Hirist, Unstop, Superset, TimesJobs, Shine, Wellfound, Apna,
  WorkIndia, FirstNaukri, iimjobs each carry an evidence-based reason (robots.txt
  disallow / HTTP 403 / no public API) + ready-to-open search deep links. No fake listings,
  ever. Surfaced via `GET /api/sources/external`.

**17.3 India-first search engine.** `search_profiles.py`: editable saved profiles
(keywords/locations/batch/employment types/salary floor/remote/drive types/sources)
with a default seeded from the candidate profile (2027 B.Tech CSE AIML, Hyderabad +
preferred hubs, ₹6+ LPA, 10 quick views: Fresher/2027 batch/Internships/Off-campus/
Walk-in/Trainee/Location/Remote/Deadlines/MNC). `query_jobs` now also filters by
location, skills, min salary (LPA) and max experience; API + tests.

**17.4 Job verification engine** (`verify.py`): 10 checks (availability, redirects,
application-page plausibility, expiry markers, deadlines, company-domain match,
suspicious domains — free mail/shorteners/raw IP/punycode/typo-squats, salary sanity,
freshness, source reliability) → 6 states (verified_open/probably_open/needs_manual/
expired/suspicious/unavailable) + 0-100 quality score independent of match score.
A bare HTTP 200 is NEVER enough for verified_open (content check required). SSRF guard
blocks private/localhost hosts. New jobs are verified during discovery; batch re-checks
+ `/api/jobs/{id}/verify` + first/last-verified timestamps persisted.

**17.5 ATS automation.** `ats_registry.py`: platform detection for 18 platforms
(Greenhouse/Lever/Ashby/Workday/SmartRecruiters/iCIMS/Taleo/Darwinbox/Zoho/
SuccessFactors/Oracle/Eightfold/Phenom/Workable/MyNextHire/LinkedIn/Naukri/
Instahyre/generic) with per-platform quirks (multi-step hints, submit selectors,
resume-first flows). The semantic field engine (labels/aria/legends/DOM — not CSS
selectors) is preserved and now checkpoints progress into `apply_sessions` after every
step (interruption recovery), logs the detected ATS on the job timeline.

**17.6 Application execution — three modes** (`execution.py`):
- MODE A discovery: search → verify → eligibility → rank → shortlist.
- MODE B assisted (default): tailor resume + cover letter, answer questions, fill the
  form in a visible browser, pause for human review (existing flow, now with sessions).
- MODE C authorized submission: OFF by default; requires BOTH
  `automation.authorized_submission: true` AND per-job approval, then re-checks every
  queue gate, refuses on CAPTCHA/login walls/sensitive+legal questions (Aadhaar, PAN,
  consents, declarations, demographics)/empty required fields, clicks Submit only when
  clean, and captures confirmation evidence (screenshot + confirmation text + final
  URL). A job is marked submitted ONLY on a platform confirmation phrase — otherwise
  `needs_manual` with the reason. `submit-authorized` CLI worker + tests.

**17.7 Smart application queue** (`queue.py`): 11 states (new→analyzing→eligible→
shortlisted→resume_prep→ready→awaiting_approval→applying→submitted/failed/
needs_manual) with quality gates: max/day, max/company, min match score, min
eligibility, min quality, fraud flags, duplicate-submission guard, verification state.
Deadline-urgency prioritisation. Dashboard Queue page (all Phase 17.7 columns + action
buttons). `python -m jobagent queue` CLI.

**17.8 Groq agent orchestration** (`agents.py`): 8 named agents (Job Research — NEW,
Job Matching, Resume Tailoring, Cover Letter, Application Answer, Application Quality,
Interview Prep, Follow-up) behind one dispatch with a versioned prompt registry;
untrusted framing on all job text; reuse of stored tailor results (no double billing);
every run tracked via the existing token/caching/budget layer. `GET /api/agents`,
`POST /api/agents/{agent}/jobs/{id}`.

**17.9 Email monitoring** (`email_sync.py`, optional, read-only): Gmail OAuth or IMAP
fetch → deterministic-first classification (confirmation/assessment/interview/rejection/
offer/recruiter) with Groq fallback for uncertain mail → application matching by company
→ status transitions + notifications; low confidence requires review; nothing is ever
sent (verified by test). `GET /api/emails`, `POST /api/emails/sync`.

**17.10 E2E testing** (see PHASE_17_TEST_REPORT.md): offline suite 136 tests; sandbox ATS
E2E with real Chromium against a local HTML fixture (field extraction, all input types,
file upload, multi-step, sensitive-field stops, CAPTCHA detection, session checkpoints,
authorized-mode refusal); live-adapter suite (opt-in `-m live`) hitting real public APIs.
Report generator `scripts/e2e_report.py` writes real numbers only.

**17.11 Performance & reliability:** concurrent ingestion (thread pool for the simple
API sources, configurable `search.ingest_concurrency`), per-source error isolation,
persistent task history table + `GET /api/tasks`, WAL-safe DB backup (`python -m jobagent
backup`, `POST /api/backup`), degraded-mode health endpoint (Groq/source/DB status),
polite client delays in every fetcher.

DB migration: idempotent columns (verification_*, quality_score, queue_*, apply_mode)
and tables (apply_sessions, email_messages, task_history, submissions). No data dropped.

---

## Definition of Done — status

| Requirement | Status |
|---|---|
| Existing functionality preserved | ✅ original pipeline intact (discover→score→tailor→apply) |
| Groq integration works | ✅ primary backend, hardened + mocked tests; live smoke passed |
| API keys remain secure | ✅ `.env` only, never in frontend/logs; token auth optional |
| Jobs discovered from real sources | ✅ 10+ legitimate sources incl. India registry |
| Job links validated | ✅ `/api/jobs/{id}/verify` + CLI `verify` |
| Duplicate jobs detected | ✅ id + normalised company/title (tested) |
| Matching scores explainable | ✅ dimensions + reasons + deterministic eligibility table |
| Resumes parsed and tailored | ✅ preserved, fabrication guard, untrusted framing |
| AI answers factually grounded | ✅ KB-first + grounded-only prompts + ungrounded flag |
| Application forms completed | ✅ preserved autofill (human reviews) |
| Manual verification supported | ✅ red/orange flags + needs_human + verification status |
| Authorized submission works | ✅ 3 modes: assisted default; authorized submission behind config flag + per-job approval + confirmation evidence |
| Application status persisted | ✅ full CRM + events + notes |
| Analytics use real data | ✅ computed from jobs/events only |
| Multiple users remain isolated | ⚠️ single-user local-first design (see below) |
| Critical workflows have tests | ✅ 148 tests (offline + sandbox E2E + live adapters) |
| Production builds succeed | ✅ tsc + vite + docker (CI wired) |
| Deployment instructions complete | ✅ README + Dockerfile + compose |

## Known limitations & remaining work

1. **Multi-user isolation** — the product is a single-user, local-first tool (one SQLite
   file, localhost dashboard). Multi-tenancy would need auth + per-user data scoping across
   every module; deliberately out of scope for this transformation. Mitigations: optional
   `DASHBOARD_TOKEN`, documented single-user boundary.
2. **India portals without programmatic access** — Internshala, Foundit, Freshersworld,
   Cutshort, Hirist, Unstop, Superset, TimesJobs, Shine, Wellfound, Apna, WorkIndia,
   FirstNaukri, iimjobs: robots.txt/ToS/bot-protection rules (evidence in
   `sources/external.py` and PHASE_17_REPORT.md). They are external-link discovery only —
   one click opens a tuned search; their listings surface via the integrated sources.
3. **LinkedIn guest search rate limits** — inherent to the logged-out API; keep `pages` low.
4. **Salary parsing** is heuristic; unusual formats stay empty rather than guessed.
5. **Gmail OAuth** needs the user's own Google credentials (one-time setup in README);
   IMAP works with any provider via app password. Both are read-only.
6. **Authorized submission (Mode C)** is intentionally conservative: it stops on anything
   unexpected. Jobs on Naukri/Instahyre/LinkedIn never auto-submit (one-click/profile
   flows) — assisted mode only.

## How to verify the claims

```bash
pip install -r requirements.txt pytest
GROQ_API_KEY=gsk_dummy python -m pytest tests/ -q      # 136 offline tests
GROQ_API_KEY=gsk_dummy python -m pytest tests/e2e/ -m e2e -q   # 5 sandbox browser tests
python -m pytest tests/e2e/test_live_adapters.py -m live -q    # 7 live API tests
cd web && npm ci && npx tsc -p . --noEmit && npm run build
python -m jobagent init && python -m jobagent web      # dashboard at :8600
python scripts/e2e_report.py                            # regenerates the test report
```
