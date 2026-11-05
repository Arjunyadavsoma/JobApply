"""Shared Playwright setup: a persistent Chrome (or Edge) profile so logins (LinkedIn, Naukri) survive runs."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import BrowserContext, sync_playwright

from jobagent.config import ROOT, cfg


@contextmanager
def browser_context(headless: bool = False) -> Iterator[BrowserContext]:
    profile = ROOT / cfg("browser.profile_dir", ".browser_profile")
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile),
            channel=cfg("browser.channel", "chrome"),
            headless=headless,
            viewport=None,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        try:
            yield ctx
        finally:
            ctx.close()


@contextmanager
def pdf_page() -> Iterator:
    """Headless page used only for rendering HTML resumes to PDF."""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=cfg("browser.channel", "chrome"), headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


@contextmanager
def headless_page() -> Iterator:
    """Hidden browser page that identifies as regular Chrome (some job sites block "HeadlessChrome").
    Images, fonts and media are skipped for speed. Falls back to the bundled
    Chromium when the configured Chrome channel is not installed."""
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            browser = p.chromium.launch(channel=cfg("browser.channel", "chrome"),
                                        headless=True, args=args)
        except Exception:                                      # noqa: BLE001 - no Chrome installed
            browser = p.chromium.launch(headless=True, args=args)
        ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{browser.version} Safari/537.36")
        ctx = browser.new_context(user_agent=ua, locale="en-US")
        page = ctx.new_page()
        page.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "font", "media")
                   else r.continue_())
        try:
            yield page
        finally:
            browser.close()
