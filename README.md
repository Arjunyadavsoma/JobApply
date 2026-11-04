# Job Application Agent

An AI agent that finds jobs, scores them against your resume, tailors your resume and cover letter
for the jobs you pick, and pre-fills application forms in a real browser. **It never submits an
application: you review every form and click Submit yourself.**

Powered by Claude (via your Claude subscription or the Anthropic API) or Groq (Llama, GPT-OSS, ...),
Playwright, FastAPI and a React dashboard.

```
 Search                Score                 Tailor (per job)          Auto-fill (per job)         You
 career sites   ──►   Claude rates   ──►    resume + cover letter ──► opens the form in    ──►   review &
 LinkedIn,Naukri,      each job 0-100        in your resume's layout    Chrome and fills it        Submit
```

## Features

- **Job discovery**
  - **Company career portals directly** – paste any company's careers URL and the agent searches that
    company's own site with your keywords. It detects the site type automatically:
    - Amazon, Google, Apple, Microsoft & Qualcomm (Eightfold), JPMorgan (Oracle Recruiting), Swiggy (MyNextHire)
    - careers pages that embed a Greenhouse, Lever, Ashby, Workday, SmartRecruiters or Workable board
      (e.g. Razorpay → Greenhouse, PhonePe → SmartRecruiters)
    - any other careers site: searched in a hidden browser, with Claude picking the relevant job links
  - Plus the public job feeds of Greenhouse, Lever, Ashby, Workday, SmartRecruiters and Workable boards
  - LinkedIn public job search, Naukri search and Instahyre (skills-based search via its public job API)
  - Location filter: e.g. *India + remote jobs open to India* (drops "US - Remote" and the like)
  - Title filters, de-duplication across sources, India/remote-first ordering
- **Match scoring** – Claude rates each job against your resume with reasons and gaps, so you spend time on real fits
- **Resume tailoring on demand** – one click per job:
  - Reorders and rephrases your experience in the job's terminology and rewrites the headline/summary
  - Optionally adds job-relevant skills to the Skills section, always listed for you to review
  - Never changes employers, titles, dates, degrees or metrics
  - Renders in the same layout as your own resume, fitted to one page, plus a cover letter
- **Form auto-fill**
  - Reads every field, including custom dropdowns, radio groups and file uploads
  - Answers from your resume and a small facts file (notice period, CTC, work authorization, ...)
  - Uploads the resume first so the site can prefill; makes multiple passes for fields that appear
    later; checks every value stuck
  - Anything it can't answer truthfully, plus legal consents, is outlined in red for you
  - Greenhouse, Lever, Ashby and most company forms
  - LinkedIn Easy Apply (fills each step, clicks Next, stops at Submit) and "apply on company site"
- **Dashboard** – search with your settings, live progress log, results table, job details with the
  tailored PDF, and buttons for every action. No terminal needed.

## Screens

| Page | What you do there |
|---|---|
| **Search & jobs** | Set keywords, locations and sources, press **Search**, and watch the live log. Results land in tabs: Matches, Applied, Not scored yet, Low match, All. |
| **Job details** (click a row) | Match reasons and gaps, and buttons: **Tailor resume**, **Tailor & auto-fill**, **Auto-fill application**, **Mark applied**, **Dismiss**. Also the tailored resume PDF with a list of changes and added skills, the cover letter and the job description. |
| **My resume** | Upload your resume PDF; see your original next to the tailoring template. |
| **Settings** | Minimum match score, skipped titles, company lists, form answers, Claude effort levels. |

## Requirements

- Windows 10/11 (macOS/Linux work too, but the launcher is a `.bat`)
- Python 3.11+
- Google Chrome (or Microsoft Edge)
- Claude access, one of:
  - a **Claude Pro/Max subscription** with [Claude Code](https://claude.com/claude-code) installed and
    signed in (the CLI bundled with the VS Code extension is detected automatically), or
  - an **Anthropic API key** with credits
- *or* a **Groq API key** (free tier available) - see [Groq backend](#groq-backend) below
- Node.js 18+ only if you want to change the dashboard UI (a built copy is included)

## Setup

```powershell
git clone https://github.com/VedantSagare/job-application-agent.git
cd job-application-agent
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m jobagent init        # creates config.yaml and .env from the examples
```

1. **LLM access:** leave `llm.backend: subscription` in `config.yaml` to use your Claude subscription,
   set `llm.backend: api` with `ANTHROPIC_API_KEY=...` in `.env`, or set `llm.backend: groq` with
   `GROQ_API_KEY=...` in `.env`.
2. **Settings:** edit `config.yaml` (or use the dashboard's Settings page later) – keywords,
   locations, companies and the `applicant:` answers used for forms.
3. **Start the dashboard:** double-click **`Job Agent Dashboard.bat`** (or run
   `.venv\Scripts\python -m jobagent web`). It opens at http://localhost:8600.
4. **Upload your resume** on the **My resume** page. Check the parsed data (**Edit data**) – every
   tailored resume is built from it.

## Using it

1. **Search & jobs → Search.** Optionally score the new jobs right away (~10 s per job).
2. Open a job from **Matches** and read why it fits.
3. Click **Tailor resume** (~30–60 s). Review the PDF, the change list and any **Added skills**.
4. Click **Auto-fill application**. A Chrome window opens with the form filled:
   - **red** outline = needs your answer · **orange** = couldn't be filled automatically
   - **LinkedIn:** log in once in the agent's Chrome window (it waits, then remembers). Easy Apply is
     filled step by step and stops at *Submit application*.
   - **Naukri / Instahyre:** their Apply buttons submit instantly with your profile on that site, so
     the agent never clicks them. Click Apply yourself, then **Fill current page** if a questionnaire opens.
   - Multi-step forms: after moving to the next page, click **Fill current page**.
5. Click **Submit** in the browser yourself, then **Mark applied** in the dashboard.

### Command line (optional)

```powershell
python -m jobagent discover [--source companies,linkedin,naukri,instahyre]
python -m jobagent score --limit 30
python -m jobagent tailor --job <id>
python -m jobagent apply            # interactive terminal version of auto-fill
python -m jobagent status
python -m jobagent web              # dashboard
```

### Groq backend

To run the agent on [Groq](https://groq.com) instead of Claude (fast inference, free tier available):

1. Create a key at [console.groq.com/keys](https://console.groq.com/keys) and put it in `.env`:
   ```
   GROQ_API_KEY=gsk_...
   ```
2. In `config.yaml` (or Settings → AI model in the dashboard) set:
   ```yaml
   llm:
     backend: groq
     groq_model: openai/gpt-oss-120b   # or openai/gpt-oss-20b, qwen/qwen3.8-27b, ...
   ```

Notes:
- `llm.model` is used as-is when it isn't a Claude model, so you can also just set `model: openai/gpt-oss-20b`.
- Groq has no document attachment, so your resume PDF is sent as extracted text.
- The Claude `score_effort` / `tailor_effort` / `form_effort` levels map automatically to Groq's
  `reasoning_effort` (low/medium/high) on the `openai/gpt-oss-*` models, and are ignored on others.
  You can also pin `llm.reasoning_effort: low|medium|high` explicitly.
- Answers use Groq's structured-output (JSON schema) mode with an automatic fallback to JSON mode
  for models that don't support it, so any Groq model works.

### Instahyre settings

Instahyre searches by **skills** rather than job titles. Set them in `config.yaml` under
`sources.instahyre`: `skills` (e.g. `[Java, Spring Boot]`), `job_functions` (10 = Backend,
1 = Full-Stack, 76 = Other Software Development) and `years` (blank = your experience from the form answers).

### Adding company career portals

In **Settings → Career portals (any company)**, paste `Name | careers URL`, for example:

```
Amazon | https://www.amazon.jobs/en/
JPMorgan Chase | https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs
Atlassian | https://www.atlassian.com/company/careers/all-jobs
```

The first search detects the site type and remembers it (`data/portal_cache.json`). Tips for sites the
agent has to browse: paste the page that lists jobs (not the company homepage); if the site's search puts
the keyword in the URL, you can paste that URL with `{keyword}` in place of the search term, e.g.
`https://careers.example.com/search?q={keyword}&location=India`. Browsed sites use one Claude call per
company per search, and jobs whose location can't be confirmed as India/remote are skipped.

### Adding companies

Add a company in **Settings → Company career sites** (or under `sources.companies` in `config.yaml`):

| System | What to enter | Example |
|---|---|---|
| Greenhouse | board name from `job-boards.greenhouse.io/<name>` | `gitlab` |
| Lever | board name from `jobs.lever.co/<name>` | `zeta` |
| Ashby | board name from `jobs.ashbyhq.com/<name>` | `notion` |
| **Workday** | careers URL, optionally with a display name | `Mastercard \| https://mastercard.wd1.myworkdayjobs.com/CorporateCareers` |
| SmartRecruiters | company id from `jobs.smartrecruiters.com/<Id>` | `ServiceNow` |
| Workable | slug from `apply.workable.com/<slug>` | `apna` |

Greenhouse, Lever, Ashby and Workable list every open role; Workday and SmartRecruiters companies are
searched with your keywords (big employers list thousands of roles). Title and location filters apply
to all of them. Workday applications need a per-company account: sign in when the form asks, then use
**Fill current page**. iCIMS, Taleo, SuccessFactors and custom career sites aren't scanned directly –
those jobs usually reach you through LinkedIn, Naukri or Instahyre.

## Project structure

```
jobagent/
  sources/          job discovery: portals.py (any careers URL: detection + Amazon, Google, Apple,
                    Eightfold, Oracle, MyNextHire, generic), ats.py (Greenhouse/Lever/Ashby), ats_more.py (Workday/
                    SmartRecruiters/Workable), linkedin.py, naukri.py,
                    instahyre.py, location.py
  matcher.py        Claude match scoring
  resume.py         resume parsing, tailoring, one-page PDF rendering
  templates/        resume HTML template
  apply.py          form reading + Claude answers + filling (multi-pass, verified)
  linkedin_apply.py LinkedIn Easy Apply / external-apply flow
  llm.py            LLM calls (Claude Code CLI / Anthropic API / Groq API) -> Pydantic models
  server.py         FastAPI backend for the dashboard
  tasks.py          background tasks with live logs
  db.py             SQLite job tracker
  cli.py            command-line entry point
web/                React + TypeScript + Tailwind dashboard (built copy in web/dist)
config.example.yaml all settings, documented
```

Your personal files are never committed: `config.yaml`, `.env`, `profile/` (resume), `output/`
(tailored resumes), `data/` (job database) and the browser profile are in `.gitignore`.

### Changing the dashboard UI

```powershell
cd web
npm install
npm run dev      # live reload at http://localhost:5173 (keep `python -m jobagent web` running)
npm run build    # updates web/dist, served by the Python app
```

## Safeguards and limits

- **Human in the loop:** the agent never clicks a final Submit button.
- **Truthful answers:** form answers come only from your resume and the facts you enter; salaries,
  notice period, date of birth etc. are never guessed. Legal consents are always left to you.
- **Tailoring:** employers, titles, dates, degrees and numbers are never changed. Added skills (on by
  default, `llm.allow_added_skills`) go only in the Skills section and are listed per job, so be ready
  to discuss them in interviews, or turn the option off.
- **LinkedIn / Naukri terms of service** prohibit automated tools. Discovery reads public pages with
  rate limiting and applying stays one job at a time with a manual submit, but heavy use can still get
  an account restricted. Use it moderately.
- **Usage:** on the subscription backend, every score/tailor/fill counts toward your plan's usage
  limits; lower the effort settings if you run large batches.

## Tech stack

Python · Claude (Claude Code CLI / Anthropic SDK, structured outputs) or Groq (OpenAI-compatible SDK,
structured outputs) · Playwright · FastAPI · SQLite · Jinja2 · React 19 · TypeScript · Tailwind CSS · Vite
