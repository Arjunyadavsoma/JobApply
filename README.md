# JobApply — India-first AI job application platform

An AI agent that finds jobs, checks your eligibility, scores fit, tailors your resume and cover
letter, pre-fills application forms in a real browser, and tracks every application through your
pipeline. **It never submits an application: you review every form and click Submit yourself.**

Powered by **Groq** (primary AI provider; Claude subscription/API also supported), Playwright,
FastAPI and a React dashboard. Built for the Indian job market — B.Tech freshers, 2026-2028
batches, internships, off-campus drives, MNC career pages — and international remote roles.

```
 Discover                 Enrich                    Match                      Apply (per job)                You
 career sites      ──►   salary / batch /     ──►   deterministic        ──►   tailored resume +      ──►   review &
 LinkedIn,Naukri,        CGPA / deadlines /         eligibility + AI             form auto-fill in             Submit
 Instahyre,Remotive,     fraud flags                score with reasons            Chrome
 India MNC registry
```

## What it does

- **India-first job discovery**
  - Company career portals: paste any careers URL — Amazon, Google, Apple, Microsoft/Qualcomm
    (Eightfold), JPMorgan (Oracle), Swiggy (MyNextHire), SuccessFactors, or any embedded
    Greenhouse/Lever/Ashby/Workday/SmartRecruiters/Workable board
  - **India MNC registry** seeded automatically (Microsoft, Amazon, Google, Oracle, NVIDIA, Adobe,
    Salesforce, ServiceNow, Accenture, Deloitte, PwC, EY, KPMG, Freshworks, Zoho, Razorpay, PhonePe,
    Swiggy, Flipkart, CRED, …)
  - LinkedIn public search, Naukri, Instahyre, **Remotive**, **Arbeitnow**, **Himalayas** and
    **Jobicy** (public APIs)
  - Location filter: India + remote-open-to-India; title filters; cross-source de-duplication
  - **External-link discovery** for platforms that (legitimately) can't be ingested —
    Internshala, Foundit, Freshersworld, Cutshort, Hirist, Unstop, Superset, TimesJobs, Shine,
    Wellfound, Apna, WorkIndia, FirstNaukri, iimjobs: one click opens a keyword-tuned search,
    with the evidence-based reason (robots.txt / bot protection) shown. No fake listings.
- **Job verification engine** — every new job is checked (availability, redirects, content
  plausibility, expiry markers, deadlines, company-domain match, suspicious domains, salary
  sanity, freshness, source reliability) → *verified open / probably open / needs manual /
  expired / suspicious / unavailable* + a 0-100 **quality score** separate from match score.
  An HTTP 200 alone is never enough.
- **Deterministic enrichment (no AI, fully explainable)** — every job gets structured facts:
  salary (LPA / monthly stipend, never confused with take-home), experience range, employment
  type, remote/hybrid/onsite, eligible graduation batches, CGPA/percentage cutoffs, backlog
  policy, application deadline, fresher/walk-in/off-campus flags, and fraud warnings
  (registration fees, free-mail apply links, "guaranteed job" wording)
- **Eligibility engine** — batch, CGPA, percentage, experience and backlogs checked against your
  profile: *eligible / likely eligible / needs verification / not eligible*, with the exact
  requirement shown for every check. Unclear criteria never auto-reject, and the AI cannot
  override deterministic checks.
- **Explainable matching** — AI score with skills/education/experience/location dimensions,
  reasons, missing skills, disqualifiers and recommendations
- **Resume tailoring on demand** — reorders and rephrases your experience in the job's
  terminology, one-page PDF in your own layout, cover letter, never invents facts; added skills
  are always listed for review
- **Form auto-fill with confidence levels** (Phase 18) — every field is mapped deterministically
  first (labels, ARIA, names, placeholders, nearby text): **high** confidence fills automatically
  from your verified profile, **medium** fills but flags itself for your review, **low** goes to
  the Form Understanding Agent (or asks you), and **sensitive** fields (Aadhaar/PAN, consents,
  demographics, salary history) are never auto-filled. India-specific fields (10th/12th school
  and %, intermediate, diploma, backlogs, batch, notice period, CTC, relocation) map to real
  profile data — missing values stay empty for you, never invented. Option fields match numeric
  ranges ("5-8 LPA" for your 6 LPA) and yes/no phrasings ("No active backlogs").
- **Universal ATS automation** (Phase 18) — per-platform drivers for Greenhouse, Lever, Ashby,
  Workday, SmartRecruiters (each verified end-to-end in a sandbox) plus iCIMS, Taleo, Darwinbox,
  SAP SuccessFactors, Oracle Recruiting Cloud, Zoho Recruit, Eightfold, Phenom and custom
  portals (best-effort, honestly labelled). Multi-step wizards, platform validation errors,
  resume uploads and interruption recovery (one browser session per application, checkpoints
  after every step, idempotent resume, duplicate-submission guard).
- **Application readiness score** (Phase 18) — nine dimensions (eligibility, match, skills,
  resume, keywords, answers, required fields, factual consistency, URL validity) with blocking
  issues, warnings and improvements; `ready to submit` only when nothing blocks
- **Company research** (Phase 18) — verified facts from the posting + the company's public
  careers page, clearly separated from AI interpretation; nothing invented when nothing is found
- **Preparation workflow + batch** (Phase 18) — one click runs the full 13-step preparation
  (analysis → eligibility → match → resume → cover letter → answers → validation → company
  research → readiness → preview); batches prepare many jobs under your controls (allowed/
  excluded companies, locations, job types, size cap) — every submission still needs its own
  approval
- **Interview preparation** (Phase 18) — per-application question bank across 9 categories
  (company, technical, Python, SQL, DSA, AI/ML, project, HR, behavioral) with explanations and
  model answers grounded in your resume, mock sessions and per-question progress tracking
- **Application CRM** — discovered → matched → ready → submitted → assessment → interview →
  offer, with a per-job timeline, notes, duplicate-application guard and deadline tracking
- **Application queue with quality gates** — every opportunity walks new → analyzing →
  eligible → shortlisted → resume prep → ready → awaiting approval → applying → submitted,
  blocked automatically by daily/company caps, score/eligibility/quality floors, fraud flags
  and duplicate submissions
- **Three execution modes** — *Discovery* (search/verify/rank), *Assisted* (agent fills the
  form, you review and submit — default), and *Authorized submission* (off by default;
  needs a config flag **and** per-job approval **and** every gate, stops on CAPTCHA/login/
  legal/sensitive questions, records confirmation evidence; a job is "submitted" only when
  the platform confirms it)
- **Ten Groq agents** — Job Research, Company Research, Matching, Resume Tailoring, Cover
  Letter, Application Answers, Application Quality, Form Understanding, Interview Prep,
  Follow-up — one orchestration layer with a shared verified-facts context, versioned prompts,
  full execution history, and all external text framed as untrusted
- **Optional email monitoring (read-only)** — Gmail OAuth or IMAP; classifies confirmations /
  online tests / technical & HR interviews / assessments / rejections / offers / background
  verification, extracts dates, times, deadlines, meeting links and recruiter contacts, and
  matches them to your applications. Low-confidence mail is flagged for review. Nothing is
  ever auto-sent.
- **Analytics** — applications per week, score distribution, eligibility breakdown, missing
  skills, source quality: all from your own data, nothing fabricated
- **AI assistant** — interview prep, recruiter follow-up emails, skill-gap reports with learning
  plans, application quality checks, and a verified answer knowledge base that always wins over
  AI-generated answers
- **Security** — keys only in `.env`, job text treated as untrusted data (prompt-injection
  defence), upload validation, optional dashboard token, fraud guard blocks auto-fill on
  suspicious postings

## Screens

| Page | What you do there |
|---|---|
| **Jobs** | Search with your settings; India quick views (Fresher, 2027 batch, Internships, Hyderabad, Remote, Deadlines); live log; results with salary/batch/deadline facts |
| **Queue** | The application queue: match/quality/eligibility/readiness/deadline columns, limits header, batch selection + "Prepare selected", Analyze / Shortlist / Prepare / Approve / Assisted / Auto-submit actions |
| **Job details** (click a row) | Eligibility table, match dimensions and reasons, **Tailor resume**, **Auto-fill application**, **Prepare & interview** tab (readiness score, company research, interview question bank + mock sessions), pipeline status, notes, fraud warnings, timeline |
| **Applications** | CRM pipeline: ready → submitted → assessment → interview → offer, one-click transitions |
| **Analytics** | Weekly applications, score and eligibility distributions, source quality, missing skills, upcoming deadlines |
| **AI Assistant** | Interview prep / follow-up / skill gap / quality check per job + answer knowledge base editor |
| **Resume Studio** | Upload your resume PDF; original next to the tailoring template |
| **My Profile** | Structured candidate profile (graduation year, CGPA, 10th/12th, backlogs, preferences) that powers eligibility and form answers |
| **Settings** | Sources, keywords, companies, minimum match score, AI model and effort levels |

## Requirements

- Python 3.11+ (Linux, macOS or Windows)
- Google Chrome or Microsoft Edge (only needed for apply / Naukri search)
- A **Groq API key** (free tier available) — recommended; or Claude access
  (subscription or Anthropic API key)
- Node.js 22+ only if you change the dashboard (a built copy is included)

## Setup

```bash
git clone https://github.com/Arjunyadavsoma/JobApply.git
cd JobApply
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
python -m jobagent init        # creates config.yaml and .env from the examples
```

1. **AI provider:** add `GROQ_API_KEY=gsk_...` to `.env` (get one at
   [console.groq.com/keys](https://console.groq.com/keys)). `config.yaml` defaults to
   `llm.backend: groq` with `openai/gpt-oss-120b` and a `gpt-oss-20b` fallback.
   Optional: `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `AI_PROVIDER` env overrides.
2. **Profile:** open the dashboard and complete **My Profile** (graduation year, CGPA, 10th/12th
   marks, backlogs, preferred cities) — the eligibility engine depends on it.
3. **Start the dashboard:** `python -m jobagent web` (Windows: double-click
   `Job Agent Dashboard.bat`) → http://localhost:8600
4. **Upload your resume** on the **Resume Studio** page. Check the parsed data — every tailored
   resume is built from it.

The defaults ship tuned for a B.Tech CSE (AI & ML) 2027 fresher in Hyderabad (AI/ML/software
roles, ₹6+ LPA, open to Bengaluru/Pune/Chennai/Mumbai/Gurugram/Noida + remote India). Everything
is editable — profile, keywords, locations — none of it is a hardcoded rule.

### Multiple Groq keys + automatic rotation (anti rate-limit)

Groq's free tier rate-limits a single key quickly during scoring or batch runs. The agent
supports **any number of keys** and rotates them on every call:

```bash
# .env — commas (or semicolons / newlines) all work
GROQ_API_KEYS=gsk_key_one,gsk_key_two,gsk_key_three
```

- Keys are combined from `GROQ_API_KEYS` + `GROQ_API_KEY` + every non-revoked `groq`
  credential in **System → Credential Manager** (AES-256-GCM encrypted vault). Duplicates
  are removed automatically; vault changes apply live, no restart.
- **Round-robin per call** spreads load evenly across keys.
- **429 rate limit:** that key rests for the server's `retry-after` window (default 60 s,
  capped at 15 min; back-to-back 429s double the rest) while the other keys keep serving.
  Per-key 429s never trip the provider circuit breaker.
- **401/403 (bad key):** the key is disabled until you fix/remove it — a disabled key is
  never retried, so it can't burn quota or spam logs.
- **All keys cooling:** the call waits (up to `llm.key_wait_seconds`, default 30 s) for the
  soonest key, then fails with an honest message instead of hanging.
- Live status (masked keys, per-key request/429 stats, cooldowns) and a
  **Reset cooldowns** button: **System → Credential Manager → Groq key rotation**.
  The API exposes the same at `GET /api/llm/keys` and `POST /api/llm/keys/reset`.

## Using it

1. **Jobs → Search.** Optionally score the new jobs right away (~5-10 s per job on Groq).
2. Open a job from **Matches**: check the **eligibility table** (batch/CGPA checks) and the match
   dimensions.
3. Click **Tailor resume**. Review the PDF, the change list and any **Added skills**.
4. Click **Auto-fill application**. A Chrome window opens with the form filled:
   - **red** outline = needs your answer · **orange** = couldn't be filled automatically
   - **LinkedIn:** log in once in the agent's Chrome window (it waits, then remembers). Easy Apply
     fills step by step and stops at *Submit application*.
   - **Naukri / Instahyre:** their Apply buttons submit instantly, so the agent never clicks
     them. Click Apply yourself, then **Fill current page** if a questionnaire opens.
   - Postings with fraud warnings (fees, suspicious links) are never auto-filled.
5. Click **Submit** in the browser yourself, then **Mark applied** — the duplicate guard takes
   over from there.
6. Track everything on **Applications**, and use **AI Assistant** before interviews.

### Command line

```bash
python -m jobagent discover [--source companies,linkedin,naukri,instahyre,remotive,arbeitnow]
python -m jobagent score --limit 30
python -m jobagent tailor --job <id>
python -m jobagent apply            # interactive terminal version of auto-fill
python -m jobagent enrich --all     # re-run salary/batch/deadline extraction
python -m jobagent verify           # re-check apply links of matched jobs
python -m jobagent analytics        # pipeline + source quality in the terminal
python -m jobagent status
python -m jobagent web              # dashboard
```

### Docker deployment

```bash
cp .env.example .env       # add GROQ_API_KEY
cp config.example.yaml config.yaml
docker compose up -d --build
# dashboard at http://localhost:8600 (set DASHBOARD_TOKEN in .env if you expose it)
```

The image includes Chromium for the apply flows; build with `--build-arg INSTALL_BROWSER=false`
for a lean dashboard+discovery deployment. State lives in mounted volumes (`data/`, `profile/`,
`output/`); `/health/live` + `/health/ready` are the uptime endpoints. CI (GitHub
Actions) runs the offline test
suite, the TypeScript check, the dashboard build and the Docker build on every push.

## Tests

```bash
pip install -r requirements.txt pytest pytest-httpserver
GROQ_API_KEY=gsk_dummy python -m pytest tests/ -q --ignore=tests/e2e/test_live_adapters.py  # 451 tests (offline + sandbox E2E)
python -m pytest tests/e2e/ -m e2e -q      # 56 sandbox browser tests (local fixtures, real Chromium)
python -m pytest tests/e2e/test_live_adapters.py -m live --live -q # live public-API tests (read-only, opt-in)
cd web && npm ci && npx tsc --noEmit && npm run build              # frontend gates
```

Covers: enrichment parsing (LPA/batches/CGPA/deadlines/fraud), eligibility classification,
database migrations/dedup/events, the Groq layer (structured outputs, JSON-mode fallback,
fallback model, usage tracking, caching, request budget), the API lifecycle (auth, filters,
duplicate-application guard, upload validation), profile-aware prefit, source adapters with
mocked HTTP — plus the Phase 17 suites: verification engine (10 checks / 6 states / quality
score / SSRF + typo-squat guards), queue gates + limits + actions, execution modes
(sensitive-field stops, confirmation evidence, no-submission-without-confirmation), ATS
registry detection for 18 platforms, the agent orchestration (untrusted framing, prompt
versions, execution history), search profiles + new filters, email classification/matching
(nothing ever sent) — and the Phase 18 suites: form-intelligence mapping with confidence
levels and sensitive-field refusal, company research (verified vs interpretation, cache,
no fake data), readiness scoring (9 dimensions, unknown-costs-points), the 13-step
preparation workflow, batch gates + duplicate protection, interview prep with progress,
email entity extraction, the new API surface, and sandbox E2E adapters for Greenhouse,
Lever, Ashby, Workday and SmartRecruiters (detection, mapping, uploads, multi-step,
validation errors, recovery, locks, approval gates, duplicate prevention — with real
headless Chromium against local fixtures; no external site is touched and no real
application is ever submitted) — **and the Phase 19 production suites**: SSRF-safe URL
validation (redirect-hop re-checks, DNS rebinding), the AES-256-GCM credential vault
(masked listings, tamper detection, fail-closed), schema versioning + validated
backups/restore/retention, the 18-state application lifecycle (human-only approval,
idempotent submissions, uncertain-submission human gate, forged-state refusal), health
endpoints + diagnostics, the Groq circuit breaker (backoff + jitter), structured
execution logs, eight more sandbox-verified ATS platforms (iCIMS, Taleo, Darwinbox,
Zoho, SuccessFactors, Oracle, Eightfold, Phenom — 13 total), the 25-step
production acceptance scenario — **and the Phase 19K rotation suite**: multi-key parsing,
vault-sourced keys, round-robin distribution, retry-after cooldowns with escalation and
caps, auth-failure disabling, bounded all-cooling waits, per-key client isolation,
429-rotation that leaves the provider circuit breaker closed, masked status endpoints,
live pool reloads after vault changes.

## Project structure

```
jobagent/
  sources/          discovery: portals.py (any careers URL + detection), ats.py (Greenhouse/
                    Lever/Ashby), ats_more.py (Workday/SmartRecruiters/Workable), linkedin.py,
                    naukri.py, instahyre.py, arbeitnow.py, remotive.py, himalayas.py, jobicy.py,
                    india_companies.py, external.py (evidence-based external-link registry)
  verify.py         Phase 17 job verification engine (10 checks -> 6 states + quality score)
  queue.py          application queue (11 states, quality gates, limits)
  execution.py      execution modes (discovery / assisted / authorized)
  form_intel.py     Phase 18 form intelligence (canonical fields, confidence levels,
                    sensitive-field refusal, AI fallback rules)
  ats_flow.py       Phase 18 universal ATS adapters (13 platforms) + FlowDriver
  ats_registry.py   ATS platform detection + automation quirks
  recovery.py       Phase 18 session locks, checkpoints, idempotent resume
  company_intel.py  Phase 18 company research (verified facts vs AI interpretation)
  readiness.py      Phase 18 application readiness score (9 dimensions)
  prep.py           Phase 18 13-step preparation workflow + application preview
  batch.py          Phase 18 controlled batch preparation (gates + audit)
  interview_prep.py Phase 18 question bank, model answers, mock sessions, progress
  agents.py         Groq agent orchestration (10 agents, shared context, run history)
  email_sync.py     optional read-only email monitoring (10 categories + entities)
  security.py       Phase 19 SSRF-safe URL validation, secret redaction, safe errors
  credentials.py    Phase 19 AES-256-GCM encrypted credential vault
  groq_keys.py      Phase 19K multi-key Groq pool: round-robin rotation, per-key
                    429 cooldowns (retry-after), auth-failure disable, masked status
  backup.py         Phase 19 validated backups, restore, retention
  state_machine.py  Phase 19 formal 18-state lifecycle (approval gate, idempotency)
  health.py         Phase 19 /health/live /health/ready + system diagnostics
  llm_resilience.py Phase 19 circuit breaker + exponential backoff retries
  execution_log.py  Phase 19 structured automation logs (error taxonomy)
  server.py        FastAPI API + built dashboard (token middleware)
  search_profiles.py India-first saved search profiles
  enrich.py         deterministic India-first enrichment (salary/batch/CGPA/deadlines/fraud)
  eligibility.py    4-level eligibility engine (batch, CGPA, backlogs, experience)
  profile.py        structured candidate profile + form-facts bridge
  matcher.py        explainable matching (AI dimensions + eligibility gating)
  prefit.py         local fit estimate (profile-aware level penalty)
  resume.py         resume parsing, tailoring, one-page PDF rendering
  apply.py          form reading + AI answers + filling (multi-pass, verified, fraud-guarded)
  linkedin_apply.py LinkedIn Easy Apply / external-apply flow
  llm.py            LLM layer (Groq primary, Anthropic API, Claude Code CLI): structured
                    outputs, usage tracking, caching, fallback models, injection defence
  ai_services.py    interview prep, follow-ups, skill gaps, quality checks, answer KB
  analytics.py      real-data analytics for the dashboard
  server.py         FastAPI backend + dashboard hosting
  tasks.py          cross-platform background tasks + optional auto-discovery
  db.py             SQLite job tracker (migrations, events, notifications, answers, usage,
                    sessions, submissions, company intel, agent runs, locks, batches)
  cli.py            command-line entry point
web/                React 19 + TypeScript + Tailwind dashboard (built copy in web/dist)
tests/              209 offline tests + 26 sandbox E2E tests (real Chromium, local fixtures)
config.example.yaml all settings, documented
IMPLEMENTATION_STATUS.md phase-by-phase progress + known limitations
PHASE_18_REPORT.md  Phase 18 features, ATS compatibility matrix, limitations
PHASE_18_TEST_REPORT.md Phase 18 test results
```

Your personal files are never committed: `config.yaml`, `.env`, `profile/` (resume), `output/`
(tailored resumes), `data/` (job database) and the browser profile are in `.gitignore`.

## Ethics & compliance notes

- Only legitimate sources: official career pages, public ATS feeds and documented public APIs.
  Sites without public APIs whose terms prohibit scraping (Internshala, Foundit, Shine,
  TimesJobs, Wellfound) are not scraped; their roles surface via aggregators.
- LinkedIn is used through its public (logged-out) pages, throttled, one job at a time, with the
  final submit left to you.
- The agent never pays fees, never bypasses CAPTCHA/MFA/anti-bot systems, and pauses for manual
  verification when a site requires it.
