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
  - LinkedIn public search, Naukri, Instahyre, **Remotive** and **Arbeitnow** (public APIs)
  - Location filter: India + remote-open-to-India; title filters; cross-source de-duplication
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
- **Form auto-fill** — reads every field including custom dropdowns, radio groups and uploads;
  answers from your profile + resume; India-specific fields (10th/12th marks, CGPA, graduation
  year, notice period, CTC, relocation); anything unanswerable or legal is outlined red for you
- **Application CRM** — discovered → matched → ready → submitted → assessment → interview →
  offer, with a per-job timeline, notes, duplicate-application guard and deadline tracking
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
| **Job details** (click a row) | Eligibility table, match dimensions and reasons, **Tailor resume**, **Auto-fill application**, pipeline status, notes, fraud warnings, timeline |
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
`output/`); `/api/health` is ready for uptime checks. CI (GitHub Actions) runs the offline test
suite, the TypeScript check, the dashboard build and the Docker build on every push.

## Tests

```bash
pip install -r requirements.txt pytest
GROQ_API_KEY=gsk_dummy python -m pytest tests/ -q     # 76 offline tests, no network
```

Covers: enrichment parsing (LPA/batches/CGPA/deadlines/fraud), eligibility classification,
database migrations/dedup/events, the Groq layer (structured outputs, JSON-mode fallback,
fallback model, usage tracking, caching, request budget), the API lifecycle (auth, filters,
duplicate-application guard, upload validation), profile-aware prefit and the new source
adapters with mocked HTTP.

## Project structure

```
jobagent/
  sources/          discovery: portals.py (any careers URL + detection), ats.py (Greenhouse/
                    Lever/Ashby), ats_more.py (Workday/SmartRecruiters/Workable), linkedin.py,
                    naukri.py, instahyre.py, arbeitnow.py, remotive.py, india_companies.py
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
  db.py             SQLite job tracker (migrations, events, notifications, answers, usage)
  cli.py            command-line entry point
web/                React 19 + TypeScript + Tailwind dashboard (built copy in web/dist)
tests/              76 offline tests
config.example.yaml all settings, documented
IMPLEMENTATION_STATUS.md phase-by-phase progress + known limitations
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
