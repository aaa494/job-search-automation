"""
LinkedIn scraper — search only.

First run: browser opens so you can log in manually.
Subsequent runs: cookies are loaded automatically.
On a headless server: if cookies expire, a Telegram message is sent with
instructions to refresh them from your Mac.
"""

import logging
import re
from typing import AsyncIterator

from playwright.async_api import Page

from config import BROWSER_CONFIG, SEARCH_CONFIG
from database import Job
from scrapers.base_scraper import BaseScraper

log = logging.getLogger("jobsearch")


class LinkedInScraper(BaseScraper):
    platform = "linkedin"
    BASE = "https://www.linkedin.com"

    def __init__(self):
        super().__init__()
        self._logged_in = False

    async def _ensure_logged_in(self):
        """Check login once per scraper session. Sends Telegram alert if session expired."""
        if self._logged_in:
            return

        page = await self.new_page()
        try:
            await page.goto(f"{self.BASE}/feed/", wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            if "login" in page.url or "signup" in page.url or "/authwall" in page.url:
                import telegram_notifier as tg
                await tg.notify_login_required("linkedin")

                if BROWSER_CONFIG.get("headless"):
                    raise RuntimeError(
                        "LinkedIn session expired — refresh cookies/linkedin.json from your Mac"
                    )

                # Non-headless (Mac): allow interactive login
                print(
                    "\n" + "=" * 60 +
                    "\n[LinkedIn] Not logged in. Browser window is open." +
                    "\n  → If you use Google login: click 'Sign in with Google'" +
                    "\n  → Complete login in the browser window" +
                    "\n  → Come back here and press ENTER when you're logged in" +
                    "\n" + "=" * 60
                )
                input("  Press ENTER after logging in: ")
                await self.human_delay(3000, 4000)
                await self._save_cookies()
        finally:
            await page.close()

        self._logged_in = True

    # ── search ────────────────────────────────────────────────────────────────

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        await self._ensure_logged_in()

        page = await self.new_page()
        try:
            # Build search URL: remote, full-time, posted within configured days
            _days = SEARCH_CONFIG.get("posted_within_days", 3)
            _seconds = _days * 24 * 60 * 60
            search_url = (
                f"{self.BASE}/jobs/search/"
                f"?keywords={query.replace(' ', '+')}"
                f"&location=United+States"
                f"&f_WT=2"               # remote
                f"&f_JT=F"               # full-time
                f"&f_TPR=r{_seconds}"    # posted within N days
                f"&sortBy=R"             # most recent
            )
            await page.goto(search_url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)
            log.info("[LinkedIn] After goto search URL: %s", page.url)

            count = 0
            page_num = 0

            while count < max_results:
                await self.scroll_slowly(page, 1200)
                await self.human_delay(1000, 1500)

                # Get all job card links on this page
                hrefs = await page.evaluate("""
                    () => [...document.querySelectorAll('a[href*="/jobs/view/"]')]
                          .map(a => a.href)
                          .filter((v, i, arr) => arr.indexOf(v) === i)
                """)
                log.info("[LinkedIn] JS evaluate found %d job links on page (URL: %s)", len(hrefs), page.url)

                if not hrefs:
                    break

                found_on_page = 0
                for href in hrefs:
                    if count >= max_results:
                        break
                    try:
                        job_id = self._extract_job_id(href)
                        if not job_id:
                            continue

                        # Click the card — LinkedIn renders job details in a right-side panel
                        card_link = page.locator(f'a[href*="/jobs/view/{job_id}"]').first
                        await card_link.click()
                        await self.human_delay(1500, 2500)

                        try:
                            await page.wait_for_selector("h1", timeout=8000)
                        except Exception:
                            pass
                        try:
                            await page.wait_for_selector(
                                "#job-details, .jobs-description__content, "
                                ".jobs-box__html-content, [class*='job-description'], "
                                "[class*='description__text'], article.jobs-description",
                                timeout=6000
                            )
                        except Exception:
                            pass
                        await self.human_delay(1500, 2500)

                        job = await self._extract_panel(page, job_id, href)
                        if job:
                            count += 1
                            found_on_page += 1
                            yield job
                            await self.human_delay(300, 700)
                    except Exception as e:
                        log.debug("[LinkedIn] card click failed for %s: %s", href, e)

                if count >= max_results or found_on_page == 0:
                    break

                page_num += 1
                next_url = f"{search_url}&start={page_num * 25}"
                try:
                    await page.goto(next_url, wait_until="domcontentloaded")
                    await self.human_delay(2000, 3000)
                except Exception:
                    break
        finally:
            await page.close()

    def _extract_job_id(self, url: str) -> str | None:
        match = re.search(r"/jobs/view/(\d+)", url)
        return match.group(1) if match else None

    async def _extract_panel(self, page: Page, job_id: str, href: str) -> Job | None:
        """Extract job data from the detail panel on the search results page."""
        try:
            job_url = f"{self.BASE}/jobs/view/{job_id}/"

            data = await page.evaluate("""
                () => {
                    const titleEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__job-title h1, ' +
                        '.jobs-unified-top-card__job-title h1, ' +
                        'h1.t-24, h1'
                    );
                    const title = titleEl ? titleEl.innerText.trim() : '';

                    const companyEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__company-name a, ' +
                        '.jobs-unified-top-card__company-name a, ' +
                        'a[class*="company"], [class*="company-name"] a, ' +
                        '.topcard__org-name-link'
                    );
                    const company = companyEl ? companyEl.innerText.trim() : '';

                    const locEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__bullet, ' +
                        '.jobs-unified-top-card__bullet, ' +
                        '[class*="workplace-type"], [class*="location"]'
                    );
                    const location = locEl ? locEl.innerText.trim() : '';

                    const descSelectors = [
                        '#job-details',
                        '.jobs-description__content',
                        '.jobs-box__html-content',
                        'article.jobs-description',
                        '[class*="job-description"]',
                        '[class*="description__text"]',
                        '[class*="jobsDescription"]',
                        '.jobs-description',
                        '[data-job-id] .description',
                    ];
                    let descEl = null;
                    for (const sel of descSelectors) {
                        descEl = document.querySelector(sel);
                        if (descEl && descEl.innerText.trim().length > 50) break;
                        descEl = null;
                    }
                    if (!descEl) {
                        const candidates = document.querySelectorAll(
                            '.jobs-search__job-details--wrapper *, ' +
                            '.job-view-layout *, ' +
                            '[class*="detail"] *'
                        );
                        let best = null, bestLen = 0;
                        for (const el of candidates) {
                            const t = el.innerText ? el.innerText.trim() : '';
                            if (t.length > bestLen && t.length < 20000 && el.children.length < 20) {
                                best = el; bestLen = t.length;
                            }
                        }
                        descEl = best;
                    }
                    const description = descEl ? descEl.innerText.trim() : '';

                    return { title, company, location, description };
                }
            """)

            title = data.get("title", "")
            company = data.get("company", "")
            location = data.get("location", "")
            description = data.get("description", "")

            # LinkedIn privacy consent modal sets h1 to this string — navigate to job URL to get real title
            if "linkedin respects your privacy" in title.lower() or not title:
                try:
                    await page.goto(job_url, wait_until="domcontentloaded")
                    await self.human_delay(2000, 3000)
                    data2 = await page.evaluate("""
                        () => {
                            const titleEl = document.querySelector(
                                '.job-details-jobs-unified-top-card__job-title h1, ' +
                                '.jobs-unified-top-card__job-title h1, ' +
                                'h1.t-24'
                            );
                            const companyEl = document.querySelector(
                                '.job-details-jobs-unified-top-card__company-name a, ' +
                                '.jobs-unified-top-card__company-name a, ' +
                                '.topcard__org-name-link'
                            );
                            const descEl = document.querySelector(
                                '#job-details, .jobs-description__content, article.jobs-description'
                            );
                            return {
                                title: titleEl ? titleEl.innerText.trim() : '',
                                company: companyEl ? companyEl.innerText.trim() : '',
                                description: descEl ? descEl.innerText.trim() : '',
                            };
                        }
                    """)
                    if data2.get("title") and "linkedin respects your privacy" not in data2["title"].lower():
                        title = data2["title"]
                        company = company or data2.get("company", "")
                        description = data2.get("description", "") or description
                    log.info("[LinkedIn] Re-fetched job page for real title: %r", title)
                except Exception as e:
                    log.debug("[LinkedIn] Re-fetch for title failed: %s", e)

            log.debug("[LinkedIn] Panel extracted title=%r company=%r desc_len=%d",
                      title, company, len(description))

            if not title or len(description) < 50:
                log.warning("[LinkedIn] Skipped job_id=%s: title=%r desc_len=%d",
                            job_id, title, len(description))
                return None

            return Job(
                platform=self.platform,
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                url=job_url,
                description=description[:5000],
                salary="",
            )
        except Exception as e:
            log.warning("[LinkedIn] _extract_panel exception for job_id=%s: %s", job_id, e)
            return None

    @staticmethod
    async def _text(page: Page, selector: str, default: str = "") -> str:
        try:
            el = await page.query_selector(selector)
            return (await el.inner_text()).strip() if el else default
        except Exception:
            return default
