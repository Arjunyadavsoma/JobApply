# Implementation Status

Live progress file for the JobApply platform transformation. Updated at the end of every
implementation phase. Nothing is marked done that hasn't been tested.

**Last updated:** 2026-11-05 · branch `main` · test suite: **76 passed**

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
| Authorized submission works | ✅ human-in-the-loop submit preserved (policy: agent never submits) |
| Application status persisted | ✅ full CRM + events + notes |
| Analytics use real data | ✅ computed from jobs/events only |
| Multiple users remain isolated | ⚠️ single-user local-first design (see below) |
| Critical workflows have tests | ✅ 76 offline tests |
| Production builds succeed | ✅ tsc + vite + docker (CI wired) |
| Deployment instructions complete | ✅ README + Dockerfile + compose |

## Known limitations & remaining work

1. **Multi-user isolation** — the product is a single-user, local-first tool (one SQLite
   file, localhost dashboard). Multi-tenancy would need auth + per-user data scoping across
   every module; deliberately out of scope for this transformation. Mitigations: optional
   `DASHBOARD_TOKEN`, documented single-user boundary.
2. **Email notifications** — in-app only today; add SMTP/provider (Resend, SES) behind a
   config flag without hardcoding credentials.
3. **Wellfound / Internshala / Foundit / Shine / TimesJobs** — no public APIs and their ToS
   prohibit scraping, so no adapters (by design). Their jobs surface via LinkedIn/aggregators.
4. **Superset/Unstop campus drives** — reachable through the generic portal adapter only
   when a public careers URL exists.
5. **LinkedIn guest search rate limits** — inherent to the logged-out API; keep `pages` low.
6. **Salary parsing** is heuristic; unusual formats stay empty rather than guessed.

## How to verify the claims

```bash
pip install -r requirements.txt pytest
GROQ_API_KEY=gsk_dummy python -m pytest tests/ -q      # 76 passed
cd web && npm ci && npx tsc -p . --noEmit && npm run build
python -m jobagent init && python -m jobagent web      # dashboard at :8600
```
