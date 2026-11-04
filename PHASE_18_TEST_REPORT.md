# PHASE 18 TEST REPORT

Date: 2026-11-05 · Commit: see `git log` at review time
Environment: Python 3.12 · pytest 8 · Playwright + headless Chromium 1200 · Node 24 (web build)

All numbers below come from actual runs of the suite in this repository. Nothing was
hand-edited. No real application was submitted at any point; every browser test runs
against local fixture files only.

---

## 1. Summary

| Suite | Tests | Result |
|---|---|---|
| Offline unit + API (mocked LLM, temp DB) | 209 | **209 passed** |
| Sandbox E2E (real headless Chromium, local fixtures) | 26 | **26 passed** |
| Live-adapter tests (real external job APIs) | 7 | deselected by default (`-m "not live"`); unchanged from Phase 17 |
| **Total (default run)** | **242 collected** | **235 passed, 7 deselected, 0 failed** |
| Web build | tsc --noEmit + vite | clean (1899 modules, 335 kB js / 98 kB gzip) |

Wall time for the default run: **~66s** (was ~6:31 before the `data-ja-id` collision
fix and wait scaling — see §4).

---

## 2. New test files (Phase 18)

| File | Tests | Covers |
|---|---|---|
| `tests/test_form_intel.py` | 44 | spec label examples verbatim; confidence ladder (high/medium/low/sensitive); option matching (exact / numeric range / yes-no phrasing); name-splitting; sensitive fields never auto-filled; values never invented; AI fallback caps + refusal to override profile; AI-down degradation; India registry completeness |
| `tests/test_company_intel_readiness.py` | 8 | company research: verified vs interpretation tagging, source citation, 7-day cache (no double LLM billing), no fake research on failure, answer-context separation; readiness: full score, blocking on missing resume/expired URL, low-match flags, unknown-costs-points, keyword alignment |
| `tests/test_phase18_workflows.py` | 13 | prep workflow: all 13 steps, honest offline degradation, queue advance; batch: controls (excluded companies, locations, score floor), never-submits + duplicate skip, size cap, audit history; interview prep: 9 categories, cache, mock flow, progress, LLM-down degradation; agents: 10-agent registry, execution history (success + failure rows), shared context; email: refinements, entity extraction, storage + status transition |
| `tests/test_api_phase18.py` | 9 | new endpoints: ats/matrix, readiness (+404), prepare + preview, company-intel (+cache), batch prepare + history, interview-prep plan/mock/answer (+404s), agent history, resume-session 409 on duplicate + lock conflict |
| `tests/e2e/test_ats_adapters.py` | 20 | see §3 |
| **New total** | **94** | |

Updated Phase 17 tests: `test_agents.py` (registry now 10 agents; interview_prep
routes to the new module), `test_email_sync.py` (unchanged labels still pass),
`test_form_engine.py` (5, unchanged, still green on the shared engine).

---

## 3. Real ATS testing (sandbox, real Chromium)

Fixtures under `tests/e2e/fixtures/` mimic each priority platform's public DOM
patterns. Tests run a real headless Chromium; the LLM answer agent is stubbed with
deterministic payloads (no Groq billing); every submit path is asserted against
LOCAL confirmation elements only.

| Test | Platform | Verified behaviour |
|---|---|---|
| test_adapter_detection_on_page ×5 | all 5 | on-page markers identify the platform even from an unknown URL (marker specificity: data-attr > class > id); URL detection still works |
| test_matrix_reports_tested_vs_untested | all 13 | compatibility matrix is honest: 5 tested, 8 best-effort |
| test_greenhouse_full_flow | Greenhouse | identity auto-fill from profile, resume upload, multi-step advance, graduation select, work-auth radio, review pause (no submit) |
| test_greenhouse_validation_error_blocks_advance | Greenhouse | required-field error detected → flow stops with "validation errors", step 2 never reached, no fake success |
| test_lever_single_page_flow | Lever | full single-page fill incl. URL field, free-text LLM answer, upload; no submit |
| test_ashby_three_step_wizard | Ashby | 3 steps walked; notice period "Immediate" selected from profile; relocation radio; college/CGPA/grad-year filled |
| test_workday_automation_ids | Workday | data-automation-id next/submit navigation; backlogs radio "No" from 0-backlog profile; step-2 fill |
| test_smartrecruiters_single_page | SmartRecruiters | single page fill; notice period; no submit |
| test_authorized_submit_records_confirmation | Greenhouse | mode-C preflight gates pass → driver clicks submit → PLATFORM confirmation captured → submission evidence recorded |
| test_submit_refused_when_sensitive_fields_unanswered | Lever (modified) | government-ID field present → authorized submit REFUSED even with submit=True |
| test_duplicate_submission_prevented | Lever | recorded submission blocks can_apply with "already recorded" |
| test_session_lock_blocks_concurrent_drivers | — | second acquire raises LockHeld; release → reacquire OK |
| test_lock_stale_takeover | — | a 2h-old lock from a "crashed worker" is taken over |
| test_resume_after_crash_skips_filled_fields | Greenhouse | checkpoint after step 1; concurrent-session lock enforced; resume preserves filled values, next_action=continue_flow |
| test_resume_refuses_after_submission | Lever | AlreadySubmitted raised — never resume a submitted application |
| test_failure_recorded_to_needs_manual | — | interruption → needs_manual + notification |

**Zero real applications were submitted. Zero external sites were contacted.**

---

## 4. Bugs found by the new tests (and fixed)

1. **`data-ja-id` collision across wizard steps** — the Phase 17 extractor re-tagged
   controls on every scan; hidden step-1 fields kept their old ids and collided with
   step-2 numbering. Symptom: every step-2 fill matched two elements and stalled
   Playwright's 30s actionability timeout (Ashby fixture: 124s). Fix: clear stale
   `data-ja-id`/`data-ja-opt` attributes before each scan. Regression risk: none —
   the Phase 17 `test_form_engine.py` suite still passes.
2. **Label fallback grabbed the wrong label** — fields without `label[for]` matched
   the FIRST label inside any ancestor; an unlabeled select showed the form's first
   label ("Full name") and would have been auto-filled with the candidate's name.
   Fix: nearest PRECEDING label in document order (the HTML convention).
3. **`resume=None` crash in the fill pipeline** — `ask_claude` dereferenced the
   master resume unconditionally; now loads it lazily exactly like `autofill()`.
4. **Boolean-ish number fields** — "Do you have any active backlogs?" with 0 in the
   profile filled nothing; option matching now handles numeric ranges and yes/no
   phrasing ("No active backlogs").
5. **Graduation-year question forms** — "What year will/did you graduate?" wasn't in
   the synonym registry (only noun-phrase forms were); question-form aliases added.

---

## 5. Performance results

| Metric | Before Phase 18 | After |
|---|---|---|
| Full E2E suite wall time | 6:31 (with collisions) | **1:07** (full suite incl. unit) |
| Ashby 3-step fill | 124s (4×30s stalls) | **3.2s** |
| SQLite hot paths | sequential scans | indexes on queue/status/company/submissions/emails |
| Production browser pacing | fixed waits | unchanged (JA_WAIT_SCALE defaults to 1; only tests scale down) |

---

## 6. Coverage of the Definition of Done

| DoD item | Evidence |
|---|---|
| Working adapters | 5 sandbox-verified E2E (§3); 8 declared best-effort (matrix test) |
| Reliable form detection | detection tests ×5 + Phase 17 extraction suite |
| Accurate mapping | 44 form_intel tests incl. every spec example |
| No invented values | test_values_never_invented, needs_human paths |
| Resume uploads | E2E on all 5 fixtures (files.length assertions) |
| Multi-step forms | Greenhouse 2-step, Ashby 3-step, Workday E2E |
| Recovery works | lock/stale/resume/duplicate tests (§3) |
| Agents validated outputs | schema-validated structured() (Phase 17 layer) + history test |
| Quality scoring | readiness tests (5) + API test |
| Batch preparation | workflow tests (4) + API test |
| Approval gates unbypassable | submit-refusal test + unchanged Phase 17 preflight tests |
| Duplicate prevention | queue gate + recovery guard + batch skip tests |
| Email classification on fixtures | refinement + entity + storage tests |
| Interview preparation | plan/mock/progress tests |
| All critical tests pass | **235/235** |
| Docs match implementation | IMPLEMENTATION_STATUS.md, this report, PHASE_18_REPORT.md, README |

---

## 7. How to reproduce

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                 # 235 passed, 7 deselected (live)
python -m pytest tests/e2e/ -m e2e -q      # 26 sandbox browser tests alone
python -m pytest tests/e2e/test_live_adapters.py -m live --live -q   # real APIs, opt-in
cd web && npm ci && npx tsc --noEmit && npm run build
```
