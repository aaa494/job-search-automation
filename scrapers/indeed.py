"""
Indeed scraper + Quick Apply handler.
"""

import logging
import re
import asyncio
from typing import AsyncIterator

from playwright.async_api import Page

from config import SEARCH_CONFIG
from database import Job
from scrapers.base_scraper import BaseScraper

log = logging.getLogger("jobsearch")


class IndeedScraper(BaseScraper):
    platform = "indeed"
    BASE = "https://www.indeed.com"

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        page = await self.new_page()

        _days = SEARCH_CONFIG.get("posted_within_days", 3)
        search_url = (
            f"{self.BASE}/jobs"
            f"?q={query.replace(' ', '+')}"
            f"&l=Remote"
            f"&sc=0kf%3Aattr(DSQF7)%3B"  # remote filter
            f"&fromage={_days}"
            f"&sort=date"
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        await self.human_delay(2000, 3000)
        log.info("[Indeed] After goto search URL: %s", page.url)

        # Handle login wall
        if "login" in page.url or "signin" in page.url or "auth" in page.url:
            print(
                "\n" + "="*60 +
                "\n[Indeed] Not logged in. Browser window is open." +
                "\n  → Click 'Sign in with Google' or log in any way" +
                "\n  → Come back here and press ENTER when done" +
                "\n" + "="*60
            )
            input("  Press ENTER after logging in: ")
            await page.goto(search_url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

        count = 0
        page_num = 0

        while count < max_results:
            await self.scroll_slowly(page, 1000)
            await self.human_delay(1000, 1500)

            # Each job card
            cards = await page.query_selector_all(
                ".job_seen_beacon, .resultContent, "
                "[data-testid='slider_item'], "
                ".css-1m4cuuf"
            )

            log.info("[Indeed] Found %d cards on page (URL: %s)", len(cards), page.url)
            if not cards:
                break  # no cards found, stop paginating

            found_on_page = 0
            for card in cards:
                if count >= max_results:
                    break
                try:
                    job = await self._parse_card(page, card)
                    if job:
                        count += 1
                        found_on_page += 1
                        yield job
                        await self.human_delay(300, 600)
                except Exception:
                    pass

            if count >= max_results or found_on_page == 0:
                break

            # Next page
            next_btn = await page.query_selector("a[data-testid='pagination-page-next']")
            if not next_btn:
                break
            await next_btn.click()
            await self.human_delay(2500, 4000)
            page_num += 1

        await page.close()

    async def _parse_card(self, list_page: Page, card) -> Job | None:
        # Get the job link
        link_el = await card.query_selector("h2 a, a.jcs-JobTitle")
        if not link_el:
            return None
        href = await link_el.get_attribute("href")
        if not href:
            return None
        if not href.startswith("http"):
            href = self.BASE + href

        job_id_match = re.search(r"jk=([a-f0-9]+)", href)
        job_id = job_id_match.group(1) if job_id_match else href[-20:]

        # Open detail page for full description
        detail = await self.new_page()
        try:
            await detail.goto(href, wait_until="domcontentloaded")
            await self.human_delay(1500, 2500)

            title = await self._text(detail,
                "h1.jobsearch-JobInfoHeader-title, "
                "h1[data-testid='simpleHeader'], "
                "h1[data-testid='jobsearch-JobInfoHeader-title']")
            company = await self._text(detail,
                "[data-testid='inlineHeader-companyName'], "
                "[data-testid='company-name'], "
                ".icl-u-lg-mr--sm")
            location = await self._text(detail,
                "[data-testid='job-location'], "
                "[data-testid='inlineHeader-location'], "
                ".icl-IconedList-item")
            description = await self._text(detail,
                "#jobDescriptionText, "
                ".jobsearch-jobDescriptionText, "
                "[data-testid='jobsearch-JobComponent-description']")
            salary = await self._text(detail,
                "#salaryInfoAndJobType, "
                ".attribute_snippet, "
                "[data-testid='jobsearch-JobMetadataHeader-salaryContainer']",
                default="")

            if not title or not description:
                return None

            return Job(
                platform=self.platform,
                job_id=job_id,
                title=title.strip(),
                company=company.strip(),
                location=location.strip(),
                url=href,
                description=description.strip(),
                salary=salary.strip(),
            )
        except Exception:
            return None
        finally:
            await detail.close()

    @staticmethod
    async def _text(page: Page, selector: str, default: str = "") -> str:
        try:
            el = await page.query_selector(selector)
            return (await el.inner_text()).strip() if el else default
        except Exception:
            return default

    # ── Quick Apply ───────────────────────────────────────────────────────────

    async def apply(self, job: Job, resume_pdf_path: str, cover_letter_text: str,
                    resume_data: dict | None = None, non_interactive: bool = False,
                    cover_letter_path: str | None = None) -> bool:
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            # Look for Quick Apply / Apply Now button
            apply_btn = await page.query_selector(
                "button#indeedApplyButton, button:has-text('Apply now'), "
                "a.indeed-apply-button"
            )
            if not apply_btn:
                return False

            await apply_btn.click()
            await self.human_delay(2000, 3000)

            # Indeed may open an iframe or a new page
            # Handle iframe flow
            frame = page.frame_locator("iframe[title*='Apply'], iframe[src*='apply']")
            if frame:
                return await self._fill_indeed_form(page, frame, resume_pdf_path, cover_letter_text)

            return await self._fill_indeed_form(page, None, resume_pdf_path, cover_letter_text)

        except Exception as e:
            print(f"[Indeed] Apply error for {job.title}: {e}")
            return False
        finally:
            await page.close()

    async def _fill_indeed_form(self, page: Page, frame, resume_path: str, cover_letter: str) -> bool:
        target = frame or page
        max_steps = 10

        for step in range(max_steps):
            await self.human_delay(1000, 2000)

            # Resume upload
            try:
                file_input = await target.locator("input[type='file']").first.element_handle()
                if file_input:
                    await file_input.set_input_files(resume_path)
                    await self.human_delay(1000, 1500)
            except Exception:
                pass

            # Cover letter
            try:
                cl = await target.locator("textarea").first.element_handle()
                if cl:
                    await cl.fill(cover_letter[:3000])
                    await self.human_delay(500, 800)
            except Exception:
                pass

            # Submit button
            try:
                submit = target.locator("button:has-text('Submit'), button:has-text('Apply')")
                if await submit.count() > 0:
                    await submit.first.click()
                    await self.human_delay(2000, 3000)
                    return True
            except Exception:
                pass

            # Next / Continue
            try:
                nxt = target.locator("button:has-text('Continue'), button:has-text('Next')")
                if await nxt.count() > 0:
                    await nxt.first.click()
                    continue
            except Exception:
                pass

            break

        return False
