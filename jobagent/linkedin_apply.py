"""LinkedIn application flow (Easy Apply and "Apply on company website").

- Makes sure the agent's Chrome profile is logged in (waits for you to log in once).
- Easy Apply: opens the dialog, fills every step and clicks Next / Review, and STOPS at
  "Submit application" - you review and submit yourself.
- External apply: clicks Apply, follows the company-site tab and auto-fills that form.

Note: LinkedIn's terms prohibit automated tools. This keeps a human on the final submit and runs
one job at a time, but heavy use can still get an account restricted.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Error as PWError, Page, TimeoutError as PWTimeout

from jobagent.apply import FormField, _flag, autofill, console, extract_fields
from jobagent.models import Resume

MODAL = ('div.jobs-easy-apply-modal, div[data-test-modal][role="dialog"], '
         'div.artdeco-modal[role="dialog"]')
ERRORS = (".artdeco-inline-feedback--error:visible, "
          "[data-test-form-element-error-messages]:visible, .fb-dash-form-element-error:visible")


def is_linkedin(url: str) -> bool:
    return "linkedin.com" in (url or "")


def _job_url(url: str) -> str:
    m = re.search(r"currentJobId=(\d+)", url) or re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", url)
    return f"https://www.linkedin.com/jobs/view/{m.group(1)}/" if m else url


def _logged_in(page: Page) -> bool:
    try:
        return page.locator("#global-nav, nav.global-nav, header.global-nav").count() > 0
    except PWError:
        return False


def ensure_login(page: Page, url: str, wait_minutes: int = 10) -> bool:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    if _logged_in(page):
        return True
    console.print(f"[yellow]Chrome isn't logged in to LinkedIn. Log in in the Chrome window - the agent waits "
                  f"up to {wait_minutes} minutes and remembers the login next time.[/yellow]")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    for _ in range(wait_minutes * 60):
        page.wait_for_timeout(1000)
        if _logged_in(page) or "/feed" in page.url:
            console.print("Logged in.")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            return True
    console.print("[red]Timed out waiting for LinkedIn login.[/red]")
    return False


def _apply_button(page: Page):
    candidates = [
        page.locator("button.jobs-apply-button:visible, a.jobs-apply-button:visible"),
        page.get_by_role("button", name=re.compile(r"easy apply", re.I)),
        page.get_by_role("link", name=re.compile(r"^\s*apply", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*apply", re.I)),
    ]
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except PWError:
            continue
    return None


def _primary_button(page: Page):
    modal = page.locator(MODAL).first
    btn = modal.locator(
        "footer button.artdeco-button--primary, button[aria-label*='Continue to next step'], "
        "button[aria-label*='Review your application'], button[aria-label*='Submit application']"
    )
    return btn.first if btn.count() else None


def _step_label(btn) -> str:
    return f"{btn.get_attribute('aria-label') or ''} {btn.inner_text()}".strip().lower()


def easy_apply_steps(page: Page, job, resume: Resume, cover_letter: str, files: dict[str, Path],
                     max_steps: int = 15) -> None:
    """Fill the Easy Apply dialog step by step, clicking Next/Review, stopping before Submit."""
    for step in range(1, max_steps + 1):
        page.wait_for_timeout(1500)
        if not page.locator(MODAL).count():
            console.print("The Easy Apply dialog is closed.")
            return
        console.rule(f"Easy Apply - step {step}")
        autofill(page, job, resume, cover_letter, files, passes=2, root=MODAL)

        btn = _primary_button(page)
        if not btn:
            console.print("[yellow]Couldn't find the Next button - continue in the browser, then press "
                          "'Fill current page'.[/yellow]")
            return
        label = _step_label(btn)
        if "submit" in label:
            console.print("[green]Reached the last step. Review your application in Chrome and click "
                          "'Submit application' yourself, then 'Mark applied' in the dashboard.[/green]")
            return
        before = page.locator(MODAL).first.inner_text()
        btn.click()
        page.wait_for_timeout(2000)
        errors = page.locator(ERRORS)
        if errors.count():
            msgs = "; ".join(t.strip() for t in errors.all_inner_texts() if t.strip())
            for f in extract_fields(page, read_options=False, root=MODAL).values():
                if f.meta.get("required") and not (f.meta.get("value") or "").strip():
                    _flag(f, "red")
            console.print(f"[red]LinkedIn needs more on this step: {msgs or 'see the red messages'}[/red]\n"
                          "Fix it in Chrome and click Next, then press 'Fill current page' to continue.")
            return
        if page.locator(MODAL).count() and page.locator(MODAL).first.inner_text() == before:
            console.print("[yellow]The step didn't change - continue in Chrome, then press "
                          "'Fill current page'.[/yellow]")
            return
    console.print("[yellow]Stopped after many steps - check the dialog in Chrome.[/yellow]")


def apply(page: Page, job, resume: Resume, cover_letter: str, files: dict[str, Path]) -> Page:
    """Run the LinkedIn flow. Returns the page the application ended up on."""
    url = _job_url(job["apply_url"] or job["url"])
    if not ensure_login(page, url):
        return page
    if page.get_by_text(re.compile(r"Applied \d+ (second|minute|hour|day|week|month)", re.I)).count():
        console.print("[yellow]LinkedIn shows you've already applied to this job.[/yellow]")
        return page

    btn = _apply_button(page)
    if not btn:
        console.print("[yellow]No Apply button found (the job may be closed). Check the page in Chrome.[/yellow]")
        return page

    if "easy apply" in _step_label(btn):
        console.print("Easy Apply - opening the application...")
        btn.click()
        try:
            page.wait_for_selector(MODAL, timeout=15000)
        except PWTimeout:
            console.print("[yellow]The Easy Apply dialog didn't open - click Easy Apply in Chrome, then press "
                          "'Fill current page'.[/yellow]")
            return page
        easy_apply_steps(page, job, resume, cover_letter, files)
        return page

    # "Apply on company website": LinkedIn opens the company's form in a new tab.
    console.print("This job applies on the company's website - following the Apply link...")
    target = page
    try:
        with page.context.expect_page(timeout=10000) as new_tab:
            btn.click()
        target = new_tab.value
    except PWTimeout:
        cont = page.get_by_role("button", name=re.compile(r"continue", re.I))  # "share your profile?" prompt
        if cont.count():
            try:
                with page.context.expect_page(timeout=10000) as new_tab:
                    cont.first.click()
                target = new_tab.value
            except PWTimeout:
                pass
    try:
        target.wait_for_load_state("domcontentloaded", timeout=30000)
        target.wait_for_timeout(4000)
    except PWError:
        pass
    console.print(f"Company site: {target.url}")
    target.bring_to_front()
    autofill(target, job, resume, cover_letter, files)
    return target


def fill_current(page: Page, job, resume: Resume, cover_letter: str, files: dict[str, Path]) -> None:
    """'Fill current page' from the dashboard: continue the Easy Apply dialog if one is open."""
    if page.locator(MODAL).count():
        easy_apply_steps(page, job, resume, cover_letter, files)
    else:
        autofill(page, job, resume, cover_letter, files)


__all__ = ["apply", "fill_current", "is_linkedin", "FormField"]
