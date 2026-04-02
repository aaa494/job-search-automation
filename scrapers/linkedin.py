"""
LinkedIn scraper + Easy Apply handler.

First run: browser opens so you can log in manually.
Subsequent runs: cookies are loaded automatically.
"""

import os
import re
import asyncio
from typing import AsyncIterator

from playwright.async_api import Page, TimeoutError as PWTimeout

from database import Job
from scrapers.base_scraper import BaseScraper


class LinkedInScraper(BaseScraper):
    platform = "linkedin"
    BASE = "https://www.linkedin.com"

    # ── search ────────────────────────────────────────────────────────────────

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        page = await self.new_page()
        await page.goto(f"{self.BASE}/feed/", wait_until="domcontentloaded")
        await self.human_delay(1000, 2000)

        # If not logged in, wait for manual login (works with Google OAuth too)
        if "login" in page.url or "signup" in page.url or "/authwall" in page.url:
            print(
                "\n" + "="*60 +
                "\n[LinkedIn] Not logged in. Browser window is open." +
                "\n  → If you use Google login: click 'Sign in with Google'" +
                "\n  → Complete login in the browser window" +
                "\n  → Come back here and press ENTER when you're logged in" +
                "\n" + "="*60
            )
            input("  Press ENTER after logging in: ")
            await self._save_cookies()

        # Build search URL: remote, full-time, last 7 days
        search_url = (
            f"{self.BASE}/jobs/search/"
            f"?keywords={query.replace(' ', '+')}"
            f"&location=United+States"
            f"&f_WT=2"        # remote
            f"&f_JT=F"        # full-time
            f"&f_TPR=r86400"  # posted within 24 hours
            f"&sortBy=R"      # most recent
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        await self.human_delay(2000, 3000)

        count = 0
        page_num = 0

        while count < max_results:
            # Collect job card links on current page
            await self.scroll_slowly(page, 1200)
            await self.human_delay(1000, 1500)

            cards = await page.query_selector_all(".job-card-container__link")
            if not cards:
                # Try alternate selector
                cards = await page.query_selector_all("a.base-card__full-link")

            for card in cards:
                if count >= max_results:
                    break
                try:
                    href = await card.get_attribute("href")
                    if not href:
                        continue
                    job_id = self._extract_job_id(href)
                    if not job_id:
                        continue

                    job = await self._get_job_details(page, href, job_id)
                    if job:
                        count += 1
                        yield job
                        await self.human_delay(300, 700)
                except Exception as e:
                    pass  # skip broken cards silently

            # Go to next page
            page_num += 1
            next_url = f"{search_url}&start={page_num * 25}"
            try:
                await page.goto(next_url, wait_until="domcontentloaded")
                await self.human_delay(2000, 3000)
            except Exception:
                break

        await page.close()

    def _extract_job_id(self, url: str) -> str | None:
        match = re.search(r"/jobs/view/(\d+)", url)
        return match.group(1) if match else None

    async def _get_job_details(self, page: Page, url: str, job_id: str) -> Job | None:
        detail_page = await self.new_page()
        try:
            job_url = url.split("?")[0] if "?" in url else url
            if not job_url.startswith("http"):
                job_url = self.BASE + job_url

            await detail_page.goto(job_url, wait_until="domcontentloaded")
            await self.human_delay(1000, 2000)

            title = await self._text(detail_page, "h1.top-card-layout__title, h1.job-details-jobs-unified-top-card__job-title")
            company = await self._text(detail_page, "a.topcard__org-name-link, a.job-details-jobs-unified-top-card__company-name")
            location = await self._text(detail_page, ".topcard__flavor--bullet, .job-details-jobs-unified-top-card__bullet")
            description = await self._text(detail_page, ".description__text, .jobs-description__content")
            salary = await self._text(detail_page, ".salary, .compensation__salary", default="")

            if not title or not description:
                return None

            return Job(
                platform=self.platform,
                job_id=job_id,
                title=title.strip(),
                company=(company or "").strip(),
                location=(location or "").strip(),
                url=job_url,
                description=description.strip(),
                salary=salary.strip(),
            )
        except Exception:
            return None
        finally:
            await detail_page.close()

    @staticmethod
    async def _text(page: Page, selector: str, default: str = "") -> str:
        try:
            el = await page.query_selector(selector)
            return (await el.inner_text()).strip() if el else default
        except Exception:
            return default

    # ── Easy Apply ───────────────────────────────────────────────────────────

    async def apply(self, job: Job, resume_pdf_path: str, cover_letter_text: str) -> bool:
        """
        Attempts LinkedIn Easy Apply.
        Returns True on success, False if Easy Apply is not available or fails.
        """
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            # Check if Easy Apply button exists
            easy_apply_btn = await page.query_selector(
                "button.jobs-apply-button, .jobs-s-apply button"
            )
            if not easy_apply_btn:
                return False

            btn_text = (await easy_apply_btn.inner_text()).strip().lower()
            if "easy apply" not in btn_text:
                return False

            await easy_apply_btn.click()
            await self.human_delay(1500, 2500)

            # Handle the modal form (multi-step)
            success = await self._fill_easy_apply_form(
                page, resume_pdf_path, cover_letter_text
            )
            return success

        except Exception as e:
            print(f"[LinkedIn] Apply error for {job.title}: {e}")
            return False
        finally:
            await page.close()

    async def _fill_easy_apply_form(
        self, page: Page, resume_path: str, cover_letter: str
    ) -> bool:
        max_steps = 8
        for step in range(max_steps):
            await self.human_delay(1000, 2000)

            # Upload resume if there's a file input
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)
                await self.human_delay(1000, 1500)

            # Fill cover letter text area if present
            cl_area = await page.query_selector("textarea[id*='cover'], textarea[name*='cover']")
            if cl_area:
                await cl_area.fill(cover_letter[:2000])
                await self.human_delay(500, 800)

            # Fill text inputs (phone, linkedin, etc.) that are empty
            inputs = await page.query_selector_all("input[type='text']:not([readonly]), input[type='tel']")
            for inp in inputs:
                val = await inp.get_attribute("value") or ""
                if not val.strip():
                    placeholder = (await inp.get_attribute("placeholder") or "").lower()
                    if "phone" in placeholder:
                        await inp.fill("+1-555-000-0000")  # placeholder; user should update
                    await self.human_delay(200, 400)

            # Check for "Submit application" button (final step)
            submit = await page.query_selector(
                "button[aria-label*='Submit'], button:has-text('Submit application')"
            )
            if submit:
                await submit.click()
                await self.human_delay(2000, 3000)
                return True

            # Click Next / Continue / Review
            next_btn = await page.query_selector(
                "button[aria-label*='Continue'], button:has-text('Next'), "
                "button:has-text('Continue'), button:has-text('Review')"
            )
            if next_btn:
                await next_btn.click()
            else:
                # No recognized button — might be done or stuck
                break

        return False
