# PHASE 18 REPORT — Universal ATS Automation, Application Success & Production Reliability

Repository: `Arjunyadavsoma/JobApply` · Branch `main`
Date: 2026-11-05 · Test suite: **235 passed** (see PHASE_18_TEST_REPORT.md)

---

## 1. What was built (and what was NOT rebuilt)

Phase 17 was verified first: the full suite was green on checkout before any change.
Phase 18 layers on top of the Phase 17 modules — the semantic field engine
(`apply.py`), the ATS registry (`ats_registry.py`), the queue and three execution
modes (`queue.py`, `execution.py`), the agent layer (`agents.py`) and the email
monitoring (`email_sync.py`) all remain in place and are extended, not replaced.

| New module | Purpose |
|---|---|
| `jobagent/form_intel.py` | Deterministic-first field mapping with confidence levels + AI fallback rules |
| `jobagent/ats_flow.py` | 13 platform adapters + the FlowDriver that walks any application |
| `jobagent/recovery.py` | Per-job browser locks, checkpoints, idempotent resume |
| `jobagent/company_intel.py` | Company research: verified facts vs AI interpretation, cached |
| `jobagent/readiness.py` | 9-dimension application readiness score |
| `jobagent/prep.py` | The 13-step preparation workflow + application preview |
| `jobagent/batch.py` | Controlled batch preparation with gates and audit trail |
| `jobagent/interview_prep.py` | Role-specific question bank, model answers, mock sessions, progress |

Extended: `models.py` (India fresher fields), `profile.py`, `db.py` (6 new tables +
performance indexes), `agents.py` (10 agents + shared context + run history),
`email_sync.py` (fine-grained categories + entity extraction), `server.py` (11 new
endpoints), `browser.py` (Chromium fallback), the dashboard (batch UI, readiness
column, "Prepare & interview" tab), `config.example.yaml` (`batch:` section).

---

## 2. ATS compatibility matrix

A platform is **tested** only after its sandbox fixture passed the full E2E suite
against real headless Chromium. The rest are detected and driven best-effort by the
same semantic engine — honestly declared, never claimed as verified.

| Platform | Detection | Status | What was verified |
|---|---|---|---|
| Greenhouse | URL + `#application_form` / data-role markers | **Tested (sandbox)** | 2-step form, .field-error validation, resume upload, radio group, review pause, authorized submit + confirmation capture |
| Lever | URL + `form[name=apply]` / `.template__container` | **Tested (sandbox)** | single-page form, URL field, free-text answer, select, upload |
| Ashby | URL + `[data-ashby]` | **Tested (sandbox)** | 3-step wizard, notice-period select, relocation radio, college/CGPA/grad-year fill |
| Workday | URL + `[data-automation-id]` | **Tested (sandbox)** | data-automation-id navigation, country select, backlogs radio, step 2 fill |
| SmartRecruiters | URL + `.sr-application-form` | **Tested (sandbox)** | single page, notice period, availability select |
| iCIMS | URL | Detected; best-effort driver | legacy input[type=submit] selectors, alert-based errors |
| Taleo | URL | Detected; best-effort driver | session-timeout notes; legacy DOM expectations |
| Darwinbox | URL | Detected; best-effort driver | most flows behind logins — driver stops at auth walls |
| SAP SuccessFactors | URL | Detected; best-effort driver | long multi-step, save-and-return |
| Oracle Recruiting Cloud | URL | Detected; best-effort driver | Workday-like flow |
| Zoho Recruit / Freshteam | URL | Detected; best-effort driver | simple forms, India SMBs |
| Eightfold AI | URL | Detected; best-effort driver | resume-first flow |
| Phenom People | URL | Detected; best-effort driver | wrapper — detection follows redirect to underlying ATS |
| Custom portals | on-page heuristics | **Tested (sandbox)** | the Phase 17 `ats_form.html` fixture still passes with the generic adapter |

`GET /api/ats/matrix` returns this matrix live from the adapter registry.

### What every flow guarantees (E2E-proven)

- Submit is **never** clicked in assisted mode; in authorized mode only after the
  unchanged Phase 17 preflight gates, and `submitted=True` requires the platform's
  own confirmation text/element — a bare click without confirmation is reported as
  *"submit clicked but no platform confirmation detected"*.
- CAPTCHA/bot protection stops the flow (never solved, never bypassed).
- Sensitive fields (Aadhaar/PAN/consents/demographics/salary history) are outlined
  red, reported, and — even in authorized mode — **block the submit**.
- Platform validation errors are detected (aria-invalid, [role=alert], .field-error,
  data-automation-id=validationError) and stop the advance honestly.

---

## 3. Form intelligence (the confidence ladder)

Signals: accessible label > ARIA > input name > placeholder > nearby text. Matches
run against a registry of ~45 canonical fields with India-first synonyms.

| Confidence | Trigger | Behaviour |
|---|---|---|
| high | label/aria synonym match | auto-fill from the verified profile |
| medium | name/placeholder match, or AI-classified | fill + orange flag for review |
| low | no match | Form Understanding Agent (Groq) → still capped at medium; else ask the user |
| sensitive | IDs, legal consents, demographics, salary history | NEVER auto-filled — needs the human |

Verified spec examples (all in `tests/test_form_intel.py`): "First Name"/"Given
Name"/"Legal First Name" → first_name; "Current CTC"/"Present Salary"/"Current
Compensation" → current_ctc; "Graduation Year"/"Passing Year"/"Expected Graduation"
→ graduation_year; "Are you legally authorized to work?" → work_authorization;
"Are you willing to relocate?" → relocation_preference; "Total Experience"/"Years
of Experience" → experience.

Option matching: exact text, numeric ranges ("0 - 1 years" for 0 years, "5-8 LPA"
for 6 LPA), yes/no phrasing ("No active backlogs" for 0). Zero is a real value
(0 backlogs → "No"). Missing profile data stays `needs_human` — a fresher without a
current CTC is asked, never invented for.

**AI rules:** the Form Understanding Agent classifies *fields*, not values; its
confidence is capped at medium; "sensitive" classifications always win; a Groq
outage degrades to ask-the-user. AI can never override verified candidate data.

---

## 4. Application readiness score

Nine dimensions, deterministic (no LLM needed to score): candidate eligibility,
resume relevance (match score), skill coverage, resume formatting, keyword
alignment (JD keywords vs tailored resume), screening-answer completeness, missing
required fields (from the last apply checkpoint), factual consistency (profile vs
resume), apply-URL validity. Output: 0-100 + blocking issues + warnings +
improvements + `submission_ready` (≥70 and no blockers) and
`ready_for_authorized_submission` (≥85 and no blockers). Unknown data costs points
— never assumed perfect. No keyword stuffing: the keyword dimension only *measures*
alignment of the tailored resume, and the tailoring agents were already
fabrication-guarded in Phase 17.

---

## 5. Recovery engine

- **One browser session per application**: SQLite-backed `apply_locks` with stale
  takeover after 1h (crashed workers don't wedge a job forever).
- **Checkpoints after every step**: step number, URL, ATS, per-step fill report
  (auto/flagged/needs-human/failed/validation) into `apply_sessions`.
- **Idempotent resume**: reopen → re-extract → skip fields that already carry
  values → continue. `AlreadySubmitted` guard refuses to touch submitted
  applications.
- **Honest failures**: `record_failure()` sets `needs_manual`, files a
  notification, and stores the failure reason + screenshot reference.

A real bug was found and fixed here: the Phase 17 extraction re-tagged
`data-ja-id`s per scan, so a wizard's hidden step-1 fields collided with step-2
ids — locators matched two elements and every fill stalled 30s on a hidden input.
The engine now clears stale tags before each scan (fill time on the 3-step Ashby
fixture: 124s → 3s).

---

## 6. Agents, company intelligence, preparation, batch

- **10 agents** behind one dispatch: the 8 from Phase 17 plus **Company Research**
  and **Form Understanding**. Every run is recorded in `agent_runs` (agent, job,
  prompt version, duration, ok/error, result summary) — `GET /api/agents/history`.
- **Shared candidate context**: one verified-facts block (profile facts, master
  resume summary, saved answers, company intel) reused by every agent — the
  AI-never-overrides-candidate rule starts here.
- **Company research** separates `facts_verified` (with source quotes) from
  `ai_interpretations`; tech stack only when documented; a failed/offline research
  stores NOTHING rather than fake data; cached 7 days.
- **Preparation workflow** (`prep.py`): 13 steps, each recording what actually ran;
  LLM steps degrade to "skipped (offline)" instead of pretending. Full application
  preview (resume JSON, cover letter, saved answers, verification, approval
  requirement) before anything is submitted.
- **Batch = preparation only.** Controls: allowed/excluded companies, allowed
  locations/job types, batch size cap, plus every standing queue gate. Each job
  gets its own outcome row (prepared / skipped with reason / error) and its own
  audit events; submission remains per-job and per-approval. Already-submitted or
  in-flight jobs are skipped by the duplicate guard.

---

## 7. Email intelligence (still read-only)

Categories: the Phase 6 primaries (confirmation, assessment, interview, offer,
rejection, recruiter, other) refined with **online_test**, **technical_interview**,
**hr_interview**, **background_verification**. Deterministic entity extraction
stores date, time, deadline, meeting link, recruiter name/email, role and matched
company on each message. Nothing is ever sent; outbound drafts remain a
human-copied action.

---

## 8. Interview preparation

Per-application plan across **9 categories** (company-specific, technical, Python,
SQL, DSA, AI/ML, project, HR, behavioral), each question with an explanation (why
it's asked) and a model answer grounded in the candidate's real resume — no
invented experience; HR/behavioral answers are truthful templates with
placeholders. Mock sessions serve the least-attempted question first and track
per-question attempted/answered progress. Cached per job; regenerates on demand.

---

## 9. Performance & production quality

- Performance indexes: queue listing (queue_state + score), status, company
  (lower), submissions by day, emails by received time.
- Test-time browser wait scaling (`JA_WAIT_SCALE`) — full E2E suite now runs in
  ~66s (was 6:31 before the collision fix + scaling).
- Browser resilience: bundled-Chromium fallback when the configured Chrome channel
  is absent; single-session-per-job locking prevents resource fights.
- Concurrency: batch preparation is sequential by design (one LLM context at a
  time); ingestion concurrency remains from Phase 17.
- Backups, task history, WAL mode, health endpoint: unchanged from Phase 17, all
  still tested.

---

## 10. Security & compliance posture (unchanged red lines, re-verified)

- Never bypass CAPTCHA / MFA / login walls / bot protection — stop and hand over.
- External JD, page and email text is always untrusted input (injection framing).
- AI never overrides verified candidate data; sensitive answers always human.
- Authorized submission requires config flag + per-job approval + preflight gates
  + platform confirmation; evidence recorded per submission.
- Duplicate submissions blocked at three layers (queue gate, recovery guard, batch
  skip).
- Read-only email; no auto-sent mail of any kind.
- API keys only in `.env`; optional dashboard token; no secrets in the frontend.

---

## 11. Known limitations

1. **8 ATS adapters are best-effort, not sandbox-verified** (iCIMS, Taleo,
   Darwinbox, SuccessFactors, Oracle Recruiting Cloud, Zoho Recruit, Eightfold,
   Phenom). Their selectors encode public DOM knowledge; real-world variance
   means the assisted flow (human reviews) is the right mode for them until a
   controlled account test is done. The matrix says exactly this.
2. **Workday/Darwinbox/SuccessFactors often sit behind logins** — the driver fills
   what is public and stops at the wall; the candidate logs in and continues.
3. **Company research depends on fetchability** — blocked or missing careers
   pages yield an honest "no data" instead of invented overviews.
4. **Entity extraction is regex-based** — unusual date/time formats may be missed
   (stays empty rather than guessed); AI fallback for classification only.
5. **Single-user, local-first** (unchanged from Phase 17).
6. **Sandbox fixtures mimic, not mirror** production ATS DOMs; they encode each
   platform's documented patterns (automation ids, error classes, wizard shapes).
7. LinkedIn/Naukri/Instahyre remain assisted-only (profile-based one-click flows).

---

## 12. How to run

```bash
# full offline + sandbox-E2E suite (no network, no real applications)
python -m pytest tests/ -q

# dashboard
python -m jobagent web        # http://localhost:8600

# key new endpoints
curl localhost:8600/api/ats/matrix
curl localhost:8600/api/jobs/<id>/readiness
curl -X POST localhost:8600/api/jobs/<id>/prepare
curl -X POST localhost:8600/api/batch/prepare -d '{"job_ids": ["..."]}' -H 'content-type: application/json'
```
