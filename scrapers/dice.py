"""
Dice.com scraper (great for DevOps/cloud roles in the US).
"""

import re
import asyncio
import json
from typing import AsyncIterator

from playwright.async_api import Page

from database import Job
from scrapers.base_scraper import BaseScraper


class DiceScraper(BaseScraper):
    platform = "dice"
    BASE = "https://www.dice.com"

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        page = await self.new_page()

        search_url = (
            f"{self.BASE}/jobs"
            f"?q={query.replace(' ', '+')}"
            f"&countryCode=US"
            f"&radius=30"
            f"&radiusUnit=mi"
            f"&page=1"
            f"&pageSize=20"
            f"&filters.workplaceTypes=Remote"
            f"&filters.postedDate=ONE_WEEK"
            f"&language=en"
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        await self.human_delay(2000, 3000)

        count = 0
        page_num = 1

        while count < max_results:
            await self.scroll_slowly(page, 1500)
            await self.human_delay(1000, 1500)

            # Dice uses dhi-job-card components
            cards = await page.query_selector_all("dhi-job-card, .card.search-card")
            if not cards:
                break

            for card in cards:
                if count >= max_results:
                    break
                try:
                    link_el = await card.query_selector("a.card-title-link, a[data-cy='card-title-link']")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = self.BASE + href

                    job_id_match = re.search(r"/detail/([^/?]+)", href)
                    job_id = job_id_match.group(1) if job_id_match else href[-24:]

                    job = await self._get_details(href, job_id)
                    if job:
                        count += 1
                        yield job
                        await self.human_delay(400, 800)
                except Exception:
                    pass

            # Next page
            next_btn = await page.query_selector("li.pagination-next a, button[data-cy='pagination-next']")
            if not next_btn:
                break
            page_num += 1
            next_url = search_url.replace("page=1", f"page={page_num}")
            await page.goto(next_url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

        await page.close()

    async def _get_details(self, url: str, job_id: str) -> Job | None:
        detail = await self.new_page()
        try:
            await detail.goto(url, wait_until="domcontentloaded")
            await self.human_delay(1200, 2000)

            title = await self._text(detail, "h1[data-cy='jobTitle'], .jobTitle h1")
            company = await self._text(detail, "[data-cy='companyNameLink'], .company-name")
            location = await self._text(detail, "[data-cy='location'], .location")
            description = await self._text(detail, "#jobDescription, .job-description")
            salary = await self._text(detail, ".salary, [data-cy='salary']", default="")

            if not title or not description:
                return None

            return Job(
                platform=self.platform,
                job_id=job_id,
                title=title.strip(),
                company=company.strip(),
                location=location.strip(),
                url=url,
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

    async def apply(self, job: Job, resume_pdf_path: str, cover_letter_text: str) -> bool:
        """Dice redirects to external apply URLs."""
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            apply_btn = await page.query_selector(
                "a[data-cy='apply-button'], button[data-cy='apply-button'], "
                "a.btn-apply, button:has-text('Apply Now')"
            )
            if not apply_btn:
                return False

            # Get the external URL if it's a link
            href = await apply_btn.get_attribute("href")
            if href and href.startswith("http"):
                await page.goto(href, wait_until="domcontentloaded")
                await self.human_delay(2000, 3000)

                file_input = await page.query_selector("input[type='file']")
                if file_input:
                    await file_input.set_input_files(resume_pdf_path)
                    await self.human_delay(800, 1200)

                print(f"\n[Dice] External application opened: {href}")
                print("  Please complete the application manually.")
                input("  Press ENTER when done: ")
                return True

            await apply_btn.click()
            await self.human_delay(2000, 3000)
            print(f"\n[Dice] Application page opened for {job.title}.")
            input("  Complete the form and press ENTER: ")
            return True

        except Exception as e:
            print(f"[Dice] Apply error: {e}")
            return False
        finally:
            await page.close()
