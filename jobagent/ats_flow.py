"""Phase 18.1/18.5 — universal ATS automation: platform adapters + flow driver.

Layered on the Phase 17 pieces instead of duplicating them:

  * jobagent.apply          — semantic field extraction + low-level fill primitives
  * jobagent.ats_registry   — URL detection + per-platform quirks
  * jobagent.form_intel     — deterministic field mapping (confidence levels)
  * jobagent.execution      — the three execution modes + confirmation evidence

This module adds the *flow*: per-platform adapters that know where their form
lives, which button advances a step, what their validation errors look like —
and a single FlowDriver that walks ANY supported application:

    open -> wait -> upload -> map (form_intel) -> fill (profile first, LLM for the
    rest) -> validate -> checkpoint -> advance -> ... -> review pause / submit

Rules that hold on every platform:
  * The submit button is NEVER clicked unless `submit=True` AND every mode-C
    preflight gate passed (the caller proves that, execution.mode_c_preflight).
  * CAPTCHA / login walls / unexpected sensitive questions stop the flow with a
    screenshot and a precise reason. Nothing is bypassed.
  * A checkpoint (field mappings + step + screenshots) is saved after EVERY step
    so recovery.py can resume without re-submitting anything.
  * A platform is listed as 'tested' in the compatibility matrix ONLY after its
    sandbox fixture passed; the rest are 'detected, driver best-effort'.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from playwright.sync_api import Error as PWError, Locator, Page

from jobagent import db
from jobagent.apply import (FormField, _fill_one, _flag, _is_empty, _screenshot,
                            _upload_files, ask_claude, extract_fields)
from jobagent.ats_registry import detect as registry_detect
from jobagent.ats_registry import quirks as registry_quirks
from jobagent.form_intel import map_fields
from jobagent.llm import LLMError


def _wait_ms(base: int) -> int:
    """Human-speed waits in production, fast waits under test (JA_WAIT_SCALE=0.15)."""
    try:
        scale = float(os.environ.get("JA_WAIT_SCALE", "1"))
    except ValueError:
        scale = 1.0
    return max(0, int(base * scale))

# --------------------------------------------------------------------------- adapters


@dataclass(frozen=True)
class ATSAdapter:
    """Per-platform DOM knowledge. Subclasses override only what differs."""
    key: str
    name: str
    tested: bool = False                     # sandbox fixture passed end-to-end
    page_markers: tuple[str, ...] = ()       # on-page selectors confirming the platform
    advance_selectors: tuple[str, ...] = ()  # Next/Submit button, most specific first
    submit_text: re.Pattern = re.compile(r"^(submit( application)?|apply now)$", re.I)
    next_text: re.Pattern = re.compile(r"^(next|continue|next step|save and continue)$", re.I)
    form_root: str = ""                      # scope the form (frames handle themselves)
    error_selectors: tuple[str, ...] = (
        "[aria-invalid='true']", "[role='alert']", ".field-error", ".error-message",
        ".form-error", ".invalid-feedback", "[data-automation-id='validationError']",
    )
    confirmation_selectors: tuple[str, ...] = (
        "[class*='confirmation' i]", "[class*='thank-you' i]", "[class*='success' i]",
    )
    wait_after_load_ms: int = 2500

    def advance_button(self, page: Page) -> tuple[str, Locator, str] | None:
        """('next'|'submit', locator, label) for the current step, or None."""
        # 1. platform-specific selectors first
        for sel in self.advance_selectors:
            try:
                loc = page.locator(sel).filter(visible=True)
                if loc.count():
                    label = (loc.first.inner_text() or "").strip()[:60]
                    kind = "submit" if self.submit_text.search(label) else "next"
                    return kind, loc.first, label or sel
            except PWError:
                continue
        # 2. generic semantic search over visible buttons
        try:
            buttons = page.locator("button:visible, input[type='submit']:visible")
            for i in range(min(buttons.count(), 40)):
                b = buttons.nth(i)
                try:
                    label = (b.inner_text() or b.get_attribute("value") or "").strip()
                except PWError:
                    continue
                if self.submit_text.search(label):
                    return "submit", b, label
                if self.next_text.search(label):
                    return "next", b, label
        except PWError:
            pass
        return None

    def validation_errors(self, page: Page) -> list[str]:
        out: list[str] = []
        for sel in self.error_selectors:
            try:
                loc = page.locator(sel).filter(visible=True)
                for i in range(min(loc.count(), 10)):
                    text = (loc.nth(i).inner_text() or "").strip()
                    if text and text not in out:
                        out.append(text[:200])
            except PWError:
                continue
        return out

    def is_confirmation(self, page: Page) -> str | None:
        """Confirmation phrase found on-page, or None (never guessed)."""
        from jobagent.execution import confirm_from_text
        try:
            phrase = confirm_from_text(page.inner_text("body", timeout=5000))
        except PWError:
            return None
        if phrase:
            return phrase
        for sel in self.confirmation_selectors:
            try:
                if page.locator(sel).filter(visible=True).count():
                    return f"confirmation element {sel}"
            except PWError:
                continue
        return None

    def wait_ready(self, page: Page) -> None:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(_wait_ms(self.wait_after_load_ms))


GREENHOUSE = ATSAdapter(
    "greenhouse", "Greenhouse", tested=True,
    page_markers=("#application_form", "[data-role='apply-form']", "form#app_form"),
    advance_selectors=("button#submit_app", "button[type='submit']"),
    form_root="#application_form, [data-role='apply-form'], form",
    error_selectors=("[aria-invalid='true']", ".field-error", "[role='alert']",
                     ".error", ".field_with_errors"),
)

LEVER = ATSAdapter(
    "lever", "Lever", tested=True,
    page_markers=("form[name='apply']", ".application-form", ".template__container"),
    advance_selectors=("button[type='submit']", ".btn-primary"),
    error_selectors=("[aria-invalid='true']", ".error", ".field-error", "[role='alert']"),
)

ASHBY = ATSAdapter(
    "ashby", "Ashby", tested=True,
    page_markers=("[data-ashby]", ".ashby-application-form", "form.ashby"),
    advance_selectors=("button[type='submit']", "button[data-testid='next-button']"),
    error_selectors=("[aria-invalid='true']", "[role='alert']", ".field-error",
                     ".ashby-field-error"),
)

WORKDAY = ATSAdapter(
    "workday", "Workday", tested=True,
    page_markers=("[data-automation-id]",),
    advance_selectors=("button[data-automation-id='bottom-navigation-next-button']",
                       "button[data-automation-id='bottom-navigation-submit-button']",
                       "button[data-automation-id='bottom-navigation-submit-and-apply-button']"),
    submit_text=re.compile(r"(submit|apply now|submit application)", re.I),
    error_selectors=("[data-automation-id='validationError']", "[role='alert']",
                     "[aria-invalid='true']", ".wdg-error"),
    wait_after_load_ms=4500,               # Workday is slow; extra waits are normal
)

SMARTRECRUITERS = ATSAdapter(
    "smartrecruiters", "SmartRecruiters", tested=True,
    page_markers=(".sr-application-form", "form[data-testid]", "#application_form"),
    advance_selectors=("button[type='submit']", "button[data-testid='submit-button']"),
    error_selectors=("[aria-invalid='true']", "[role='alert']", ".error-message",
                     ".field-error"),
)

ICIMS = ATSAdapter(
    "icims", "iCIMS", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=(".iCIMS_Table", "#icims_form", ".iCIMS_JobPage"),
    advance_selectors=("input[type='submit']", "button[type='submit']"),
    submit_text=re.compile(r"(submit|next|continue)", re.I),
    error_selectors=(".error", "[role='alert']", ".iCIMS_ErrorMsg"),
)

TALEO = ATSAdapter(
    "taleo", "Oracle Taleo", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("#taleo_form", ".taleo-candidate-gateway", "#errorMessages"),
    advance_selectors=("input#submit", "input[type='submit']", "button[type='submit']"),
    error_selectors=(".error", "#errorMessages", "[role='alert']"),
    wait_after_load_ms=3500,
)

DARWINBOX = ATSAdapter(
    "darwinbox", "Darwinbox", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("[data-db-form]", ".dbx-apply-form"),
    advance_selectors=("button[type='submit']",),
    error_selectors=("[role='alert']", ".error"),
    # India-native ATS: most application flows live behind a login the candidate
    # owns; the driver fills what is public and stops at the wall.
)

SUCCESSFACTORS = ATSAdapter(
    "successfactors", "SAP SuccessFactors", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("[data-sap-ui='applicationForm']", ".sapMMsgStrip"),
    advance_selectors=("button[type='submit']", "#nextBtn", "input[type='submit']"),
    error_selectors=("[aria-invalid='true']", "[role='alert']", ".sapMMsgStrip"),
    wait_after_load_ms=4000,
)

ORACLE_RECRUITING = ATSAdapter(
    "oracle_recruiting", "Oracle Recruiting Cloud", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("[data-oracle-candidate]", ".oj-form"),
    advance_selectors=("button[type='submit']", "[data-component-id*='NextButton']"),
    error_selectors=("[role='alert']", ".p_OraError"),
    wait_after_load_ms=4000,
)

ZOHO_RECRUIT = ATSAdapter(
    "zoho_recruit", "Zoho Recruit / Freshteam", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("#zoho_form", ".zc-formbody", ".zcformbody"),
    advance_selectors=("button[type='submit']", "#submit"),
    error_selectors=(".error", "[role='alert']"),
)

EIGHTFOLD = ATSAdapter(
    "eightfold", "Eightfold AI", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=("#ef_form", ".ef-application-form"),
    # resume-first flow: upload, review parsed fields, continue
    advance_selectors=("button[type='submit']",),
    error_selectors=("[aria-invalid='true']", "[role='alert']"),
)

PHENOM = ATSAdapter(
    "phenom", "Phenom People", tested=True,   # sandbox fixture E2E passed (Phase 19C)
    page_markers=(".ph-apply-button", ".ph-landing"),
    # career-site wrapper: apply usually redirects to an underlying ATS; detection
    # follows the redirect, so this adapter is mostly a pass-through.
    advance_selectors=("button[type='submit']",),
)

GENERIC = ATSAdapter(
    "generic", "Generic company portal", tested=True,       # sandbox ats_form.html
    advance_selectors=(),
)

ADAPTERS: dict[str, ATSAdapter] = {a.key: a for a in (
    GREENHOUSE, LEVER, ASHBY, WORKDAY, SMARTRECRUITERS, ICIMS, TALEO, DARWINBOX,
    SUCCESSFACTORS, ORACLE_RECRUITING, ZOHO_RECRUIT, EIGHTFOLD, PHENOM, GENERIC,
)}


def detect_adapter(url: str, page: Page | None = None) -> tuple[ATSAdapter, dict]:
    """Registry URL detection first; on-page markers can confirm the platform.

    When several platforms share a generic marker (both Greenhouse and
    SmartRecruiters use #application_form in real life), the more SPECIFIC
    marker wins: data-attributes > classes > bare ids."""
    info = registry_detect(url or "")
    adapter = ADAPTERS.get(info["platform"], GENERIC)
    if page is not None:
        matches: list[tuple[ATSAdapter, str, int]] = []
        for candidate in ADAPTERS.values():
            if candidate.key == "generic":
                continue
            for marker in candidate.page_markers:
                try:
                    if page.locator(marker).count() > 0:
                        rank = (0 if marker.startswith("[data-")
                                else 1 if marker.startswith(".") else 2)
                        matches.append((candidate, marker, rank))
                        break
                except PWError:
                    continue
        if matches:
            matches.sort(key=lambda m: m[2])            # most specific marker first
            best, marker, _ = matches[0]
            return best, {**info, "platform": best.key,
                          "evidence": f"page marker {marker}"}
    return adapter, info


def compatibility_matrix() -> list[dict[str, Any]]:
    """Phase 19C formal support levels (never inflated):

    - SANDBOX_VERIFIED : the local fixture passed the full Playwright E2E
                         suite (form detection, mapping, upload, multi-step,
                         validation, recovery). NOT a live-platform claim.
    - DETECTION_ONLY   : URL + on-page markers detected; the semantic engine
                         drives best-effort but no sandbox evidence exists.
    - MANUAL_HANDOFF   : apply flow is behind a login the candidate owns
                         (LinkedIn/Naukri/Instahyre/Darwinbox portal) — the
                         agent prepares everything, the user submits.
    - VERIFIED         : reserved for live-platform evidence with a real
                         authorized account. NONE is claimed today.
    """
    manual_handoff = {"linkedin", "naukri", "instahyre"}
    out = []
    for a in ADAPTERS.values():
        if a.key == "generic":
            continue
        q = registry_quirks(a.key)
        if a.key in manual_handoff:
            level, status = "MANUAL_HANDOFF", "login-walled: agent prepares, user submits"
        elif a.tested:
            level, status = "SANDBOX_VERIFIED", "sandbox E2E suite passed (local fixtures)"
        else:
            level, status = "DETECTION_ONLY", "detected; best-effort driver, no sandbox evidence"
        out.append({"platform": a.key, "name": a.name, "tested": a.tested,
                    "support_level": level,
                    "multi_step": q.get("multi_step", False),
                    "status": status,
                    "notes": q.get("notes", "")})
    # registry-only platforms (no flow adapter yet): report them honestly
    adapter_keys = {a.key for a in ADAPTERS.values()}
    for key in ("linkedin", "naukri", "instahyre"):
        if key not in adapter_keys:
            out.append({"platform": key, "name": registry_quirks(key)["name"],
                        "tested": False, "support_level": "MANUAL_HANDOFF",
                        "multi_step": registry_quirks(key).get("multi_step", False),
                        "status": "login-walled: agent prepares, user submits",
                        "notes": registry_quirks(key).get("notes", "")})
    return out


# --------------------------------------------------------------------------- fill step

@dataclass
class StepReport:
    step: int
    url: str = ""
    fields_total: int = 0
    filled: int = 0                                   # fields the driver set
    auto_filled: int = 0                              # from verified profile (high conf)
    flagged_for_review: int = 0                       # medium confidence
    needs_human: list[str] = field(default_factory=list)
    ai_answered: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    screenshot: str = ""
    advanced: str = ""                                # next | submit | review_pause | end


def fill_step(page: Page, job, profile, files: dict[str, Any], resume, cover_letter: str,
              root: str = "", llm_answers: bool = True) -> StepReport:
    """Fill ONE visible step with the deterministic-first pipeline.

    Order matters: uploads first (many ATSs parse the resume and prefill), then
    profile-mapped fields (high = auto, medium = fill+flag), then the LLM answers
    only what remains (ai_or_ask). Sensitive fields are outlined red and reported,
    never touched.
    """
    report = StepReport(step=0, url=page.url[:300])
    fields: dict[str, FormField] = extract_fields(page, read_options=False, root=root or None)
    if not fields:
        report.needs_human.append("(no fields on this step)")
        return report
    report.fields_total = len(fields)

    _upload_files(fields, files)
    page.wait_for_timeout(_wait_ms(1500))             # let any resume parser prefill

    metas = [f.meta for f in fields.values()]
    mappings = map_fields(metas, profile)
    by_id = {f.meta["id"]: f for f in fields.values()}
    map_by_id = {m.field_id: m for m in mappings}

    # 1. deterministic fills from the verified candidate profile
    for m in mappings:
        if m.action not in ("auto_fill", "fill_flagged"):
            continue
        f = by_id.get(m.field_id)
        if not f or not _is_empty(f):
            continue                                  # respect site-prefilled values
        try:
            _fill_one(f, str(m.profile_value), files)
            report.filled += 1
            if m.action == "auto_fill":
                report.auto_filled += 1
            else:
                report.flagged_for_review += 1
                _flag(f, "orange")
        except PWError as e:
            report.failed[f.meta["label"]] = str(e).splitlines()[0][:80]

    # 2. remaining fields through the (stubbed-in-tests) LLM answer agent
    todo = [by_id[m.field_id] for m in mappings
            if m.action == "ai_or_ask" and m.field_id in by_id and _is_empty(by_id[m.field_id])]
    if todo and llm_answers and resume is None:
        try:
            from jobagent.resume import load_master
            resume = load_master()                  # same fact source autofill() uses
        except SystemExit:                           # no master resume configured
            resume = None
        except Exception:                             # noqa: BLE001
            resume = None
    if todo and llm_answers and resume is not None:
        try:
            answers = ask_claude(todo, job, resume, cover_letter,
                                 [f for f in fields.values() if f not in todo])
            for a in answers.answers:
                f = by_id.get(a.field_id)
                if not f or not a.value.strip():
                    continue
                try:
                    _fill_one(f, a.value, files)
                    report.filled += 1
                    report.ai_answered.append(f.meta["label"])
                except PWError as e:
                    report.failed[f.meta["label"]] = str(e).splitlines()[0][:80]
        except LLMError:
            pass                                      # LLM down: those fields stay for the human

    # 3. flag what needs a human decision
    for m in mappings:
        if m.action == "needs_human" and m.field_id in by_id:
            _flag(by_id[m.field_id], "red")
            report.needs_human.append(m.label)

    page.wait_for_timeout(_wait_ms(800))
    report.screenshot = _screenshot(page, job["id"], f"step_{report.step}") or ""
    report.validation_errors = detect_adapter(page.url, page)[0].validation_errors(page)
    return report


# --------------------------------------------------------------------------- flow driver

@dataclass
class FlowResult:
    ok: bool
    steps: list[StepReport] = field(default_factory=list)
    stopped_reason: str = ""
    confirmation: str = ""
    final_url: str = ""
    submitted: bool = False
    needs_human: list[str] = field(default_factory=list)


class FlowDriver:
    """Walks an entire application on any supported ATS, checkpointing every step.

    `on_step(step_report, state)` is called after every step with the state dict
    that gets persisted to apply_sessions.state_json — recovery.py resumes from it.
    `submit=True` is ONLY ever set by the authorized mode after preflight gates.
    """

    def __init__(self, page: Page, job, adapter: ATSAdapter, *, profile, resume,
                 cover_letter: str = "", files: dict[str, Any] | None = None,
                 on_step: Callable[[StepReport, dict], None] | None = None,
                 max_steps: int = 10):
        self.page, self.job, self.adapter = page, job, adapter
        self.profile, self.resume = profile, resume
        self.cover_letter, self.files = cover_letter, files or {}
        self.on_step = on_step
        self.max_steps = max_steps

    # -- checkpoint state ----------------------------------------------------
    def _state(self, step_no: int, report: StepReport | None) -> dict[str, Any]:
        return {
            "step": step_no, "url": self.page.url[:300], "ats": self.adapter.key,
            "last_report": (report.__dict__ if report else {}),
            "updated_at": db.now(),
        }

    def _checkpoint(self, step_no: int, report: StepReport) -> None:
        db.save_session(self.job["id"], "assisted", self._state(step_no, report))
        if self.on_step:
            try:
                self.on_step(report, self._state(step_no, report))
            except Exception:                                   # noqa: BLE001
                pass                                            # checkpointing never kills the flow

    # -- main loop -----------------------------------------------------------
    def run(self, submit: bool = False) -> FlowResult:
        from jobagent.execution import captcha_present, sensitive_fields
        from jobagent import execution_log as xlog

        result = FlowResult(ok=False, final_url=self.page.url[:300])
        self.adapter.wait_ready(self.page)
        xlog.log_action(job_id=self.job["id"], action="flow_start", result="ok",
                        ats=self.adapter.key, step=0,
                        detail=f"mode={'authorized' if submit else 'assisted'}")

        for step_no in range(1, self.max_steps + 1):
            if captcha_present(self.page):
                result.stopped_reason = "CAPTCHA/bot protection present — never bypassed"
                _screenshot(self.page, self.job["id"], f"stopped_{step_no}")
                xlog.log_action(job_id=self.job["id"], action="captcha_stop",
                                result="blocked", ats=self.adapter.key, step=step_no,
                                error_class="captcha_wall",
                                recovery="user must solve the challenge; agent stops")
                break

            report = fill_step(self.page, self.job, self.profile, self.files,
                               self.resume, self.cover_letter)
            report.step = step_no
            result.steps.append(report)
            result.needs_human.extend(report.needs_human)
            xlog.log_action(
                job_id=self.job["id"], action="fill_step", result="ok",
                ats=self.adapter.key, step=step_no,
                detail=f"filled={report.filled} auto={report.auto_filled} "
                       f"flagged={report.flagged_for_review} "
                       f"needs_human={len(report.needs_human)}")
            self._checkpoint(step_no, report)

            if report.validation_errors:
                # validation errors block advancing: report and re-check once
                self.page.wait_for_timeout(_wait_ms(1000))
                still = self.adapter.validation_errors(self.page)
                if still:
                    result.stopped_reason = f"validation errors: {'; '.join(still[:3])}"
                    xlog.log_action(
                        job_id=self.job["id"], action="validation_stop", result="blocked",
                        ats=self.adapter.key, step=step_no, error_class="form_validation",
                        detail="; ".join(still[:3]),
                        recovery="correct the flagged fields, then resume from checkpoint")
                    break

            advance = self.adapter.advance_button(self.page)
            if not advance:
                report.advanced = "end"                       # single-page form, no button
                result.ok = True
                break

            kind, loc, label = advance
            if kind == "submit":
                if not submit:
                    # assisted mode: everything is filled, the human reviews + submits
                    if sensitive_fields([f.meta for f in self._current_fields()]):
                        result.stopped_reason = "sensitive fields need a human decision"
                    else:
                        result.ok = True
                        result.stopped_reason = "ready for human review (submit paused)"
                    report.advanced = "review_pause"
                    self._checkpoint(step_no, report)
                    break
                # authorized mode: every preflight gate already passed upstream.
                # One last look for unanswered sensitive fields before clicking.
                if sensitive_fields([f.meta for f in self._current_fields()]):
                    result.stopped_reason = "sensitive fields unanswered — refusing to submit"
                    report.advanced = "review_pause"
                    self._checkpoint(step_no, report)
                    break
                try:
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    self.page.wait_for_timeout(_wait_ms(4000))
                    confirmation = self.adapter.is_confirmation(self.page)
                    if confirmation:
                        result.submitted = True
                        result.ok = True
                        result.confirmation = confirmation
                        result.stopped_reason = ""
                        xlog.log_action(job_id=self.job["id"], action="submit",
                                        result="ok", ats=self.adapter.key, step=step_no,
                                        detail=f"platform confirmation: {confirmation[:120]}")
                    else:
                        # honesty rule: a click is not a submission. Only the
                        # platform's own confirmation counts (mode C records it).
                        result.ok = False
                        result.stopped_reason = ("submit clicked but no platform "
                                                 "confirmation detected — verify manually")
                        xlog.log_action(job_id=self.job["id"], action="submit",
                                        result="failed", ats=self.adapter.key, step=step_no,
                                        error_class="unknown",
                                        detail="no platform confirmation after click",
                                        recovery="manual verification required before any retry")
                    report.advanced = "submit"
                except PWError as e:
                    result.stopped_reason = f"submit click failed: {str(e).splitlines()[0]}"
                    xlog.log_action(job_id=self.job["id"], action="submit",
                                    result="failed", ats=self.adapter.key, step=step_no,
                                    error_class=xlog.classify_exception(e),
                                    detail=str(e), recovery="check page state, resume from checkpoint")
                self._checkpoint(step_no, report)
                break

            # a 'next' button: advance to the following step
            try:
                loc.scroll_into_view_if_needed()
                loc.click()
                self.page.wait_for_timeout(_wait_ms(2500))
                report.advanced = "next"
                # the ATS may reject the step: check its validation immediately
                errs = self.adapter.validation_errors(self.page)
                if errs:
                    result.stopped_reason = f"validation errors: {'; '.join(errs[:3])}"
                    report.validation_errors = errs
                    self._checkpoint(step_no, report)
                    break
                self._checkpoint(step_no, report)
                # new step may render inside the same page
                self.adapter.wait_ready(self.page)
            except PWError as e:
                result.stopped_reason = f"advance failed: {str(e).splitlines()[0]}"
                break
        else:
            result.stopped_reason = f"stopped after {self.max_steps} steps (unexpected depth)"

        result.final_url = self.page.url[:300]
        if not result.confirmation and submit and result.submitted:
            result.confirmation = self.adapter.is_confirmation(self.page) or ""
        db.record_event(self.job["id"], "apply",
                        f"FlowDriver[{self.adapter.key}] steps={len(result.steps)} "
                        f"submitted={result.submitted} reason={result.stopped_reason or 'ok'}")
        return result

    def _current_fields(self):
        return list(extract_fields(self.page, read_options=False).values())
