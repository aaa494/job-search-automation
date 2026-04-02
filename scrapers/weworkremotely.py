"""
We Work Remotely scraper (no login required, public listings).
Applies by opening the external employer URL.
"""

import re
import asyncio
from typing import AsyncIterator

from playwright.async_api import Page

from database import Job
from scrapers.base_scraper import BaseScraper


class WeWorkRemotelyScraper(BaseScraper):
    platform = "weworkremotely"
    BASE = "https://weworkremotely.com"

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        page = await self.new_page()
        search_url = f"{self.BASE}/remote-jobs/search?term={query.replace(' ', '+')}"
        await page.goto(search_url, wait_until="domcontentloaded")
        await self.human_delay(1500, 2500)

        count = 0
        cards = await page.query_selector_all("li.feature:not(.view-all), section.jobs article")

        for card in cards:
            if count >= max_results:
                break
            try:
                link_el = await card.query_selector("a")
                if not link_el:
                    continue
                href = await link_el.get_attribute("href")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = self.BASE + href

                # Skip non-job links
                if "/remote-jobs/" not in href:
                    continue

                job_id = href.rstrip("/").split("/")[-1]

                detail = await self.new_page()
                try:
                    await detail.goto(href, wait_until="domcontentloaded")
                    await self.human_delay(1000, 2000)

                    title = await self._text(detail, "h1.listing-header-container h1, h2.listing-company-name")
                    company = await self._text(detail, "h2.listing-company span, .company strong")
                    description = await self._text(detail, ".listing-container, #job-listing-show-container")
                    salary = await self._text(detail, ".listing-tag.salary, .salary", default="")

                    # External apply URL
                    apply_link = await detail.query_selector("a.button:has-text('Apply'), a[href*='apply']")
                    external_url = href
                    if apply_link:
                        ext = await apply_link.get_attribute("href")
                        if ext and ext.startswith("http"):
                            external_url = ext

                    if not title or not description:
                        continue

                    count += 1
                    yield Job(
                        platform=self.platform,
                        job_id=job_id,
                        title=title.strip(),
                        company=company.strip(),
                        location="Remote",
                        url=external_url,
                        description=description.strip(),
                        salary=salary.strip(),
                    )
                finally:
                    await detail.close()

                await self.human_delay(500, 1000)
            except Exception:
                continue

        await page.close()

    @staticmethod
    async def _text(page: Page, selector: str, default: str = "") -> str:
        try:
            el = await page.query_selector(selector)
            return (await el.inner_text()).strip() if el else default
        except Exception:
            return default

    async def apply(self, job: Job, resume_pdf_path: str, cover_letter_text: str) -> bool:
        """
        WWR redirects to external employer sites.
        We open the URL so the user can complete the application in the browser,
        or implement generic form-filling if possible.
        """
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            # Try to fill generic form
            success = await self._try_generic_apply(page, resume_pdf_path, cover_letter_text)
            if not success:
                print(f"\n[WWR] External application: {job.url}")
                print("  Please complete the application manually in the browser.")
                input("  Press ENTER when done (or type 's' to skip): ")
                return True  # mark as handled

            return success
        except Exception as e:
            print(f"[WWR] Apply error: {e}")
            return False
        finally:
            await page.close()

    async def _try_generic_apply(self, page: Page, resume_path: str, cover_letter: str) -> bool:
        """Generic form-filler for common employer application forms."""
        await self.human_delay(1000, 2000)

        # Upload resume if file input exists
        file_input = await page.query_selector("input[type='file']")
        if file_input:
            await file_input.set_input_files(resume_path)
            await self.human_delay(800, 1200)

        # Fill cover letter text area
        for sel in ["textarea[name*='cover']", "textarea[id*='cover']", "textarea[placeholder*='cover']"]:
            el = await page.query_selector(sel)
            if el:
                await el.fill(cover_letter[:3000])
                await self.human_delay(400, 700)
                break

        return False  # Signal to fall back to manual
