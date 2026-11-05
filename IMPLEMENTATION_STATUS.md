# Implementation Status

Live progress file for the JobApply platform transformation. Updated at the end of every
implementation phase. Nothing is marked done that hasn't been tested.

**Last updated:** 2026-11-05 (Phase 19K) · branch `main` · test suite: **451 passed**
(363 offline + 56 sandbox-E2E; live-adapter tests opt-in; see docs/PHASE_19_TEST_REPORT.md)

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

## Phase 18 — Universal ATS automation, application success & production reliability ✅

Audit first: Phase 17 baseline re-verified (all tests green on checkout) before any
change. Everything below builds on the Phase 17 modules — no rebuilds, no rewrites.

**18.1 Universal ATS automation** (`ats_flow.py`): per-platform `ATSAdapter`s for
Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS, Taleo, Darwinbox, SAP
SuccessFactors, Oracle Recruiting Cloud, Zoho Recruit, Eightfold, Phenom + generic
fallback — layered over the Phase 17 registry, not replacing it. Each adapter knows its
form markers (data-attribute > class > id specificity), advance/submit selectors,
validation-error selectors and confirmation patterns. `FlowDriver` walks ANY supported
application: wait → upload → map → fill → validate → checkpoint → advance → review
pause / authorized submit. Submit is clicked ONLY with `submit=True` (mode-C gated) and
`submitted` is only reported true when the PLATFORM's confirmation is detected.

**18.2 Form intelligence** (`form_intel.py`): deterministic-first field mapping with
confidence levels — high (label/aria match → auto-fill from the verified profile),
medium (name/placeholder → fill + flag for review), low (unmapped → Form Understanding
Agent or ask the user), sensitive (IDs, consents, demographics, salary history →
NEVER auto-filled). Synonym registry covers the requested examples ("Given Name"/
"Legal First Name" → first_name; "Current CTC"/"Present Salary" → current_ctc;
"Passing Year" → graduation_year; work-authorization / relocation / experience
phrasings) plus option matching (numeric ranges, yes/no phrasing, exact text).
AI fallback classifies fields but can never upgrade its answer over the profile
(confidence capped at medium; values still resolved from the profile only).

**18.3 India-specific fields** (`models.py`/`profile.py`): 10th/12th school + board,
intermediate qualification, diploma details, internship experience added to
`CandidateProfile` and exposed through `facts_for_forms()` — fresher screening forms
(backlogs, batch, education gap, notice period) map to real profile data or stay
`needs_human`; nothing is invented.

**18.4 Preparation workflow** (`prep.py`): the 13-step pipeline (JD analysis →
eligibility → match → missing skills → resume selection → tailoring → cover letter →
answers → factual validation → company research → readiness → checkpoint → approval
request) with a complete application preview. LLM steps degrade honestly (marked
skipped with the reason) when Groq is unavailable.

**18.5 Agents** (`agents.py`): now 10 — Company Research and Form Understanding added;
`shared_candidate_context()` gives every agent the same verified facts block (profile +
resume + KB answers + company intel with verified/interpretation split); every run
recorded in the `agent_runs` audit table (agent, job, prompt version, duration,
ok/error, result summary) behind `GET /api/agents/history`.

**18.6 Company intelligence** (`company_intel.py`): research from the posting + the
company's public careers page (when fetchable). Facts tagged `facts_verified` vs
`ai_interpretations`; tech stack only when documented; no careers page → nothing
cited. Cached 7 days per company. Failed research stores NOTHING (no fake data).

**18.7 Readiness score** (`readiness.py`): 9 dimensions (eligibility, match, skill
coverage, formatting, keyword alignment, answer completeness, required fields, factual
consistency, URL validity) → 0-100 with blocking issues / warnings / improvements and
`submission_ready` / `ready_for_authorized_submission` verdicts. Unknown data costs
points — never assumed perfect.

**18.8 Batch workflow** (`batch.py`): controlled batch PREPARATION with per-job gates
(allowed/excluded companies, allowed locations/job types, max batch size, plus every
queue gate). Submissions stay per-job, per-approval — a batch can never mass-submit.
Duplicate protection: already-submitted or in-flight jobs are skipped with a reason.
`batch_runs` table + `GET /api/batch/history` audit.

**18.9 Recovery engine** (`recovery.py`): per-job browser session locks (SQLite,
stale-takeover after 1h) — never two sessions on one application; checkpoints after
every flow step; `resume()` reopens and skips already-filled fields (idempotent);
`AlreadySubmitted` guard refuses to touch submitted applications; failures recorded to
`needs_manual` + notification + last-failure state.

**18.10 Email intelligence** (`email_sync.py`): Phase 17 taxonomy extended with
refinements (online_test, technical_interview, hr_interview, background_verification)
+ deterministic entity extraction (date, time, deadline, meeting link, recruiter
name/email, role) stored on the message. Still strictly read-only.

**18.11 Interview preparation** (`interview_prep.py`): role-specific plan across 9
categories (company, technical, Python, SQL, DSA, AI/ML, project, HR, behavioral) with
explanations + model answers grounded in the verified resume; mock sessions serve the
least-attempted question and track per-question progress (`prep_progress` table).

**18.12 Real ATS testing**: sandbox fixtures mimicking each priority platform's DOM
(Greenhouse multi-step + .field-error, Lever single page, Ashby 3-step wizard, Workday
data-automation-id, SmartRecruiters) + 20 E2E tests with real headless Chromium:
form/field detection, mapping, uploads, multi-step, validation, recovery, approval
gates, duplicate prevention, submit-refusal on sensitive fields. No external site is
touched; no real application is submitted.

**18.13 Production quality:** performance indexes on hot paths (queue listing, status,
company, submissions, emails); `data-ja-id` collision fix across wizard steps (the
Phase 17 engine re-tagged fields per scan — hidden step-1 fields collided with step-2
ids and stalled fills 30s each); browser waits scalable via `JA_WAIT_SCALE` (tests run
4.5x faster without changing production pacing); bundled-Chromium fallback when the
configured Chrome channel is missing.

New API surface: `GET /api/ats/matrix`, `GET /api/jobs/{id}/readiness`,
`POST /api/jobs/{id}/prepare`, `GET /api/jobs/{id}/preview`, `GET /api/jobs/{id}/company-intel`,
`POST /api/batch/prepare`, `GET /api/batch/history`, `GET|POST /api/jobs/{id}/interview-prep*`,
`GET /api/agents/history`, `POST /api/jobs/{id}/apply/resume-session`.

Dashboard: Queue page gained batch selection + "Prepare selected" + per-row readiness
scores with blocking-issue counts; JobDrawer gained a "Prepare & interview" tab
(readiness radar, company research with verified/interpretation split, interview prep
bank + mock sessions). Web dist rebuilt (tsc + vite clean).

---

## Definition of Done — status

### Phase 18 Definition of Done

| Requirement | Status |
|---|---|
| Supported ATS platforms have working adapters | ✅ 13 platform adapters + generic; 5 verified end-to-end in sandbox, 8 best-effort (declared as such) |
| Forms are detected reliably | ✅ semantic extraction + on-page markers, tested on 5 fixtures |
| Candidate information mapped accurately | ✅ form_intel registry + tests (spec examples pass verbatim) |
| Missing values are not invented | ✅ needs_human on unknown; AI capped; tests assert it |
| Resume uploads work | ✅ E2E on all 5 fixtures |
| Multi-step forms work | ✅ E2E: Greenhouse 2-step, Ashby 3-step wizard, Workday automation-ids |
| Application recovery works | ✅ locks + checkpoints + idempotent resume + stale takeover, all tested |
| Groq agents operate with validated structured outputs | ✅ 10 agents, schema-validated, execution history recorded |
| Application quality scoring is functional | ✅ 9 dimensions, blocking/warnings/improvements, tested |
| Batch preparation works | ✅ controls + per-job gates + audit trail, tested |
| Approval gates cannot be bypassed | ✅ mode-C preflight unchanged; submit refusal on sensitive fields E2E-tested |
| Duplicate submission is prevented | ✅ queue gate + recovery guard + batch skip, all tested |
| Email classification works against test fixtures | ✅ 10 categories + entity extraction tested offline |
| Interview preparation is functional | ✅ 9 categories, model answers, mock sessions, progress tracking |
| All critical tests pass | ✅ 235 passed (offline + sandbox E2E) |
| Documentation matches the implementation | ✅ this file + PHASE_18_REPORT + TEST_REPORT + README updated |

### Earlier phases

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

### Phase 19 — production readiness ✅ (see docs/PHASE_19_REPORT.md)
Security (SSRF guard + AES-256-GCM credential vault + secret redaction), DB schema
versioning + validated backups/restore/retention, formal 18-state application
lifecycle with human-only approval + idempotent submissions, /health/live|ready +
/api/system/status|diagnostics monitoring, Groq circuit breaker + backoff/jitter
retries + injection scanner, 8 new ATS sandbox fixtures (13 platforms
SANDBOX_VERIFIED, 3 MANUAL_HANDOFF, VERIFIED honestly unclaimed), structured
execution logs with error taxonomy, System dashboard page (health/matrix/
credentials/backups), real GitHub Actions CI (tests, lint, TS build, gitleaks,
E2E, Docker smoke), non-root Dockerfile, 25-step production acceptance test.
Fixed real bugs: registry double-slash signature matching, company-intel
crash without LLM key, state-machine approval bypass via forged rows.

### Phase 19K — multiple Groq API keys + rotation (anti rate-limit) ✅

- `jobagent/groq_keys.py`: thread-safe key pool combining `GROQ_API_KEYS`
  (comma/semicolon/newline) + `GROQ_API_KEY` + every non-revoked `groq`
  credential from the encrypted vault (dedup by secret, env wins).
- Round-robin per call; **429** rests the key using the server's `retry-after`
  header (default 60 s, 1 s floor, 15 min cap, doubling on consecutive hits)
  while other keys serve; **401/403** disables the key until an operator reset
  (disabled keys are never retried — no quota burn, no log spam).
- All keys cooling → bounded wait (`llm.key_wait_seconds`, 30 s) for the
  soonest key, then an honest actionable error (never a hang).
- Per-key OpenAI clients (`max_retries=0` so rotation sees 429s immediately);
  key-level errors are `pass_through` in `call_with_resilience` — they rotate
  without tripping the provider circuit breaker (verified by test).
- New API: `GET /api/llm/keys` (masked status + per-key stats),
  `POST /api/llm/keys/reset`; credential CRUD hot-reloads the pool (no restart).
  `/api/health` + diagnostics now report `keys_total/active/cooling/disabled`.
- Dashboard: "Groq key rotation" card in System → Credential Manager (live
  states, 429 counters, cooldown countdowns, reset button).
- Tests: `tests/test_groq_keys.py` — 32 offline tests (parsing, dedup, vault,
  rotation fairness, cooldown escalation/caps, disable/reset, bounded waits,
  breaker isolation, endpoint masking, live reload). Suite: 451 passed.

### Carried limitations

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
7. **Live-ATS verification** requires authorized test accounts (none owned);
   sandbox evidence only. Docker images are CI-built (not built in the dev sandbox).
   pip-audit advisory. mypy not configured.

## How to verify the claims

```bash
pip install -r requirements.txt pytest pytest-httpserver
GROQ_API_KEY=gsk_dummy python -m pytest tests/ -q --ignore=tests/e2e/test_live_adapters.py   # 451 offline tests
GROQ_API_KEY=gsk_dummy python -m pytest tests/e2e/ -m e2e -q   # 56 sandbox browser tests
GROQ_API_KEY=gsk_real_key python -m pytest scripts/test_groq_backend.py  # real Groq smoke
cd web && npm ci && npx tsc -p . --noEmit && npm run build
python -m jobagent init && python -m jobagent web      # dashboard at :8600
curl http://127.0.0.1:8600/health/live                  # liveness
curl http://127.0.0.1:8600/api/llm/keys                 # rotation pool status (masked)
```
