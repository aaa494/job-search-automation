"""
Base class for all job scrapers.
Handles cookie persistence, human-like delays, and common browser setup.
"""

import asyncio
import json
import os
import random
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page, async_playwright

from config import BROWSER_CONFIG, PATHS
from database import Job


class BaseScraper:
    platform: str = "base"

    def __init__(self):
        self._context: BrowserContext | None = None
        self._playwright = None
        self._browser = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=BROWSER_CONFIG["headless"],
            slow_mo=BROWSER_CONFIG["slow_mo"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=3000,0",   # open off-screen so it doesn't steal focus
            ],
        )
        self._context = await self._browser.new_context(
            viewport=BROWSER_CONFIG["viewport"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await self._load_cookies()
        return self

    async def __aexit__(self, *_):
        await self._save_cookies()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── cookies ───────────────────────────────────────────────────────────────

    @property
    def _cookie_path(self) -> Path:
        return Path(PATHS["cookies_dir"]) / f"{self.platform}.json"

    async def _load_cookies(self):
        if self._cookie_path.exists():
            cookies = json.loads(self._cookie_path.read_text())
            await self._context.add_cookies(cookies)

    async def _save_cookies(self):
        if self._context:
            cookies = await self._context.cookies()
            self._cookie_path.parent.mkdir(parents=True, exist_ok=True)
            self._cookie_path.write_text(json.dumps(cookies, indent=2))

    # ── helpers ───────────────────────────────────────────────────────────────

    async def new_page(self) -> Page:
        return await self._context.new_page()

    @staticmethod
    async def human_delay(min_ms: int = 400, max_ms: int = 1200):
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    @staticmethod
    async def type_human(page: Page, selector: str, text: str):
        await page.click(selector)
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.04, 0.12))

    @staticmethod
    async def scroll_slowly(page: Page, distance: int = 600):
        for i in range(0, distance, 80):
            await page.evaluate(f"window.scrollBy(0, 80)")
            await asyncio.sleep(random.uniform(0.05, 0.15))

    # ── interface to implement ─────────────────────────────────────────────────

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        raise NotImplementedError

    async def probe_apply(self, job: Job) -> dict:
        """
        Lightweight preflight check: navigate to the apply page and ask Claude if
        the form can be auto-submitted and whether a cover letter is needed.
        Returns {"can_automate": bool, "needs_cover_letter": bool, "reason": str}.
        Default implementation navigates to job.url and probes with Claude Vision.
        """
        from scrapers.employer_site import probe_form
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)
            return await probe_form(page, page.url)
        except Exception as e:
            return {"can_automate": True, "needs_cover_letter": True, "reason": f"probe_error:{e}"}
        finally:
            await page.close()

    async def apply(
        self,
        job: Job,
        resume_pdf_path: str,
        cover_letter_text: str,
        resume_data: dict | None = None,
        non_interactive: bool = False,
        cover_letter_path: str | None = None,
    ) -> bool:
        """
        Returns True if application was submitted successfully.
        Platform-specific implementation required.
        """
        raise NotImplementedError
