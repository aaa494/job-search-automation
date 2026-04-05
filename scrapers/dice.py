"""
Dice.com scraper (great for DevOps/cloud roles in the US).
"""

import logging
import re
from typing import AsyncIterator

log = logging.getLogger("jobsearch")

from playwright.async_api import Page

from config import SEARCH_CONFIG
from database import Job
from scrapers.base_scraper import BaseScraper


class DiceScraper(BaseScraper):
    platform = "dice"
    BASE = "https://www.dice.com"

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        page = await self.new_page()

        _days = SEARCH_CONFIG.get("posted_within_days", 3)
        _dice_date = "THREE_DAYS" if _days <= 3 else "ONE_WEEK" if _days <= 7 else "ONE_MONTH"
        search_url = (
            f"{self.BASE}/jobs"
            f"?q={query.replace(' ', '+')}"
            f"&countryCode=US"
            f"&radius=30"
            f"&radiusUnit=mi"
            f"&page=1"
            f"&pageSize=20"
            f"&filters.workplaceTypes=Remote"
            f"&filters.postedDate={_dice_date}"
            f"&language=en"
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        # Wait for Dice web components to render (they use custom elements)
        try:
            await page.wait_for_selector("dhi-job-card, [data-cy='search-card']", timeout=8000)
        except Exception:
            pass
        await self.human_delay(2000, 3000)

        count = 0
        page_num = 1

        while count < max_results:
            await self.scroll_slowly(page, 1500)
            await self.human_delay(1000, 1500)

            # Extract job links by URL pattern — robust against web component internals
            hrefs = await page.evaluate("""
                () => [...document.querySelectorAll('a[href*="/job-detail/"]')]
                      .map(a => a.href)
                      .filter((v, i, arr) => arr.indexOf(v) === i)
            """)
            log.info("[Dice] JS evaluate found %d job links (URL: %s)", len(hrefs), page.url)

            if not hrefs:
                break  # no job links found

            found_on_page = 0
            for href in hrefs:
                if count >= max_results:
                    break
                try:
                    if not href.startswith("http"):
                        href = self.BASE + href

                    job_id_match = re.search(r"/job-detail/([^/?]+)", href)
                    job_id = job_id_match.group(1) if job_id_match else href[-24:]

                    job = await self._get_details(href, job_id)
                    if job:
                        count += 1
                        found_on_page += 1
                        yield job
                        await self.human_delay(400, 800)
                except Exception:
                    pass

            if count >= max_results or found_on_page == 0:
                break

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
            # Wait for Dice's web components to render
            try:
                await detail.wait_for_selector("h1, #jobDescription, [data-testid='jobDescription']", timeout=8000)
            except Exception:
                pass
            await self.human_delay(1500, 2500)

            final_url = detail.url
            log.debug("[Dice] Detail page landed on: %s", final_url)

            data = await detail.evaluate("""
                () => {
                    const h1 = document.querySelector('h1');
                    const title = h1 ? h1.innerText.trim() : '';

                    const companyEl = document.querySelector(
                        '[data-cy="companyNameLink"], .company-name, ' +
                        'a[class*="company"], [class*="company-name"], ' +
                        '[class*="employer"], a[href*="/company/"]'
                    );
                    // Fallback: find text near the h1
                    let company = companyEl ? companyEl.innerText.trim() : '';
                    if (!company) {
                        const h1 = document.querySelector('h1');
                        const next = h1 && h1.nextElementSibling;
                        if (next) company = next.innerText.trim().split('\\n')[0];
                    }

                    const locEl = document.querySelector(
                        '[data-cy="location"], .location, li[class*="location"]'
                    );
                    const location = locEl ? locEl.innerText.trim() : '';

                    const descEl = document.querySelector(
                        '#jobDescription, [data-testid="jobDescription"], ' +
                        '[class*="jobDescription"], .job-description'
                    );
                    const description = descEl
                        ? descEl.innerText.trim()
                        : document.body.innerText.slice(0, 4000);

                    return { title, company, location, description };
                }
            """)

            title = data.get("title", "")
            company = data.get("company", "")
            location = data.get("location", "")
            description = data.get("description", "")

            log.debug("[Dice] Extracted title=%r company=%r desc_len=%d",
                      title, company, len(description))

            if not title or len(description) < 50:
                log.warning("[Dice] Skipped job_id=%s: title=%r desc_len=%d (URL: %s)",
                            job_id, title, len(description), final_url)
                return None

            return Job(
                platform=self.platform,
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                url=url,
                description=description[:5000],
                salary="",
            )
        except Exception as e:
            log.warning("[Dice] _get_details exception for %s: %s", url, e)
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

