"""
We Work Remotely scraper — uses RSS feeds (no browser, no Cloudflare blocking).

WWR provides public RSS feeds per category. We fetch the DevOps/SysAdmin and
Programming feeds, then filter by the search query.
No login required.
"""

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import AsyncIterator

from playwright.async_api import Page

from database import Job
from scrapers.base_scraper import BaseScraper

log = logging.getLogger("jobsearch")

# WWR RSS feeds that are relevant for DevOps roles
RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
]


def _fetch_rss(url: str) -> list[dict]:
    """Fetch and parse an RSS feed, return list of job dicts."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        items = []
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el  = item.find("link")
            desc_el  = item.find("description")
            guid_el  = item.find("guid")

            raw_title = title_el.text or "" if title_el is not None else ""
            # WWR title format: "CompanyName: Job Title"
            if ": " in raw_title:
                company, title = raw_title.split(": ", 1)
            else:
                company, title = "", raw_title

            link = link_el.text or "" if link_el is not None else ""
            guid = guid_el.text or link if guid_el is not None else link

            # Description is HTML — strip tags for plain text
            raw_desc = desc_el.text or "" if desc_el is not None else ""
            description = re.sub(r"<[^>]+>", " ", raw_desc)
            description = re.sub(r"\s+", " ", description).strip()

            job_id = guid.rstrip("/").split("/")[-1] or re.sub(r"\W", "_", guid)[-40:]

            items.append({
                "job_id": job_id,
                "title": title.strip(),
                "company": company.strip(),
                "url": link.strip(),
                "description": description[:5000],
            })
        return items
    except Exception as e:
        log.warning("[WWR] RSS fetch failed for %s: %s", url, e)
        return []


class WeWorkRemotelyScraper(BaseScraper):
    platform = "weworkremotely"

    async def search_jobs(self, query: str, max_results: int) -> AsyncIterator[Job]:
        """Fetch from RSS feeds and filter by query keywords."""
        query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]

        seen_ids: set[str] = set()
        count = 0

        for feed_url in RSS_FEEDS:
            if count >= max_results:
                break

            items = _fetch_rss(feed_url)
            log.info("[WWR] RSS %s returned %d items", feed_url.split("/")[-1], len(items))

            for item in items:
                if count >= max_results:
                    break
                if item["job_id"] in seen_ids:
                    continue
                seen_ids.add(item["job_id"])

                # Filter: at least one query word must appear in title or description
                text = (item["title"] + " " + item["description"]).lower()
                if not any(w in text for w in query_words):
                    continue

                if not item["title"] or len(item["description"]) < 50:
                    continue

                count += 1
                yield Job(
                    platform=self.platform,
                    job_id=item["job_id"],
                    title=item["title"],
                    company=item["company"],
                    location="Remote",
                    url=item["url"],
                    description=item["description"],
                    salary="",
                )

    async def apply(self, job: Job, resume_pdf_path: str, cover_letter_text: str,
                    resume_data: dict | None = None, non_interactive: bool = False,
                    cover_letter_path: str | None = None) -> bool:
        """WWR redirects to external employer sites — use Claude Vision form filler."""
        from scrapers.employer_site import fill_employer_form
        if not resume_data:
            return False
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)
            return await fill_employer_form(
                page, resume_data, cover_letter_text, resume_pdf_path,
                cover_letter_path=cover_letter_path,
                non_interactive=non_interactive,
            )
        except Exception as e:
            log.warning("[WWR] Apply error for %s: %s", job.url, e)
            return False
        finally:
            await page.close()

    async def _try_generic_apply(self, page: Page, resume_path: str, cover_letter: str) -> bool:
        await self.human_delay(1000, 2000)
        file_input = await page.query_selector("input[type='file']")
        if file_input:
            await file_input.set_input_files(resume_path)
            await self.human_delay(800, 1200)
        for sel in ["textarea[name*='cover']", "textarea[id*='cover']", "textarea[placeholder*='cover']"]:
            el = await page.query_selector(sel)
            if el:
                await el.fill(cover_letter[:3000])
                await self.human_delay(400, 700)
                break
        return False

    @staticmethod
    async def _text(page: Page, selector: str, default: str = "") -> str:
        try:
            el = await page.query_selector(selector)
            return (await el.inner_text()).strip() if el else default
        except Exception:
            return default
