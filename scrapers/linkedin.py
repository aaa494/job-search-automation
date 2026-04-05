"""
LinkedIn scraper + Easy Apply handler.

First run: browser opens so you can log in manually.
Subsequent runs: cookies are loaded automatically.
"""

import asyncio
import base64
import json
import logging
import os
import re
from typing import AsyncIterator

import anthropic
from playwright.async_api import Page, TimeoutError as PWTimeout

from config import AI_CONFIG, SEARCH_CONFIG
from database import Job
from scrapers.base_scraper import BaseScraper

_ai = anthropic.AsyncAnthropic()

log = logging.getLogger("jobsearch")


class LinkedInScraper(BaseScraper):
    platform = "linkedin"
    BASE = "https://www.linkedin.com"

    def __init__(self):
        super().__init__()
        self._logged_in = False

    async def _ensure_logged_in(self):
        """Check login once per scraper session. Opens a page, waits for manual login if needed."""
        if self._logged_in:
            return

        page = await self.new_page()
        try:
            await page.goto(f"{self.BASE}/feed/", wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

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
                # Wait for any post-login redirects to fully settle
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
                        # on the same page (no new page needed, stays authenticated)
                        card_link = page.locator(f'a[href*="/jobs/view/{job_id}"]').first
                        await card_link.click()
                        await self.human_delay(1500, 2500)

                        # Wait for h1 (title) to confirm panel is loaded
                        try:
                            await page.wait_for_selector("h1", timeout=8000)
                        except Exception:
                            pass
                        # Then wait for description — try known selectors first
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
                    // Title: h1 or job title element in the detail panel
                    const titleEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__job-title h1, ' +
                        '.jobs-unified-top-card__job-title h1, ' +
                        'h1.t-24, h1'
                    );
                    const title = titleEl ? titleEl.innerText.trim() : '';

                    // Company
                    const companyEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__company-name a, ' +
                        '.jobs-unified-top-card__company-name a, ' +
                        'a[class*="company"], [class*="company-name"] a, ' +
                        '.topcard__org-name-link'
                    );
                    const company = companyEl ? companyEl.innerText.trim() : '';

                    // Location
                    const locEl = document.querySelector(
                        '.job-details-jobs-unified-top-card__bullet, ' +
                        '.jobs-unified-top-card__bullet, ' +
                        '[class*="workplace-type"], [class*="location"]'
                    );
                    const location = locEl ? locEl.innerText.trim() : '';

                    // Description panel — try multiple selector strategies
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
                    // Last resort: find the largest text block on the right-side panel
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

                    // Debug: log class names near description for diagnostics
                    const classSnapshot = [...document.querySelectorAll('[class*="description"], [class*="Description"]')]
                        .slice(0, 5).map(e => e.className).join(' | ');

                    return { title, company, location, description, classSnapshot };
                }
            """)

            title = data.get("title", "")
            company = data.get("company", "")
            location = data.get("location", "")
            description = data.get("description", "")

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

    # ── Probe ─────────────────────────────────────────────────────────────────

    async def probe_apply(self, job: Job) -> dict:
        """
        Preflight check for LinkedIn:
        - If Easy Apply button found → can_automate=True, needs_cover_letter=False
          (Easy Apply rarely needs cover letter; our form filler handles optional ones)
        - If external Apply button → follow the popup/redirect, probe that page
        - If neither → can_automate=False
        """
        from scrapers.employer_site import probe_form
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(1500, 2500)

            # Check for Easy Apply via JS (most reliable)
            easy_apply = await page.evaluate("""
                () => {
                    const btn = [...document.querySelectorAll('button')]
                        .find(b => b.offsetParent !== null &&
                                   b.textContent.trim().toLowerCase() === 'easy apply');
                    return btn ? true : false;
                }
            """)
            if easy_apply:
                return {"can_automate": True, "needs_cover_letter": False,
                        "reason": "LinkedIn Easy Apply (no separate cover letter needed)"}

            # Look for any Apply button to follow
            apply_btn = None
            for sel in [
                "button.jobs-apply-button", ".jobs-s-apply button",
                "button:has-text('Apply')", "[aria-label*='Apply']",
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        apply_btn = btn
                        break
                except Exception:
                    continue

            if not apply_btn:
                return {"can_automate": False, "needs_cover_letter": False,
                        "reason": "No Apply button found on LinkedIn job page"}

            # Follow the Apply button to the ATS
            popup_future: asyncio.Future = asyncio.get_event_loop().create_future()
            def _on_popup(p):
                if not popup_future.done():
                    popup_future.set_result(p)
            page.context.on("page", _on_popup)
            original_url = page.url
            try:
                await apply_btn.click()
                await self.human_delay(2500, 3500)
            finally:
                page.context.remove_listener("page", _on_popup)

            target = None
            if popup_future.done():
                target = popup_future.result()
                try:
                    await target.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
            elif page.url != original_url and "linkedin.com" not in page.url:
                target = page

            if not target or "linkedin.com" in target.url:
                # Check for Easy Apply modal on same page
                modal = await page.query_selector(
                    ".jobs-easy-apply-modal, [aria-label*='Easy Apply'][role='dialog']"
                )
                if modal:
                    return {"can_automate": True, "needs_cover_letter": False,
                            "reason": "LinkedIn Easy Apply modal"}
                return {"can_automate": False, "needs_cover_letter": False,
                        "reason": "Apply button did not open external ATS"}

            result = await probe_form(target, target.url)
            try:
                if target is not page:
                    await target.close()
            except Exception:
                pass
            return result

        except Exception as e:
            log.warning("[LinkedIn] probe_apply error: %s", e)
            return {"can_automate": True, "needs_cover_letter": True,
                    "reason": f"probe_error:{e}"}
        finally:
            await page.close()

    # ── Easy Apply ───────────────────────────────────────────────────────────

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
        Attempts LinkedIn Easy Apply first.
        If no Easy Apply button, finds the external Apply link and runs the
        generic employer-site form filler on the employer's ATS page.
        Returns True on success, False otherwise.
        """
        page = await self.new_page()
        try:
            await page.goto(job.url, wait_until="domcontentloaded")
            await self.human_delay(2000, 3000)

            # Use JS to find the Easy Apply button — more reliable than CSS selectors
            # because LinkedIn's class names change frequently
            easy_apply_btn = await page.evaluate_handle("""
                () => {
                    const btns = [...document.querySelectorAll('button')];
                    return btns.find(b =>
                        b.offsetParent !== null &&
                        b.textContent.trim().toLowerCase() === 'easy apply'
                    ) || null;
                }
            """)
            # evaluate_handle returns JSHandle; check if it resolved to a real element
            try:
                is_element = await easy_apply_btn.get_property("tagName")
                tag = await is_element.json_value()
            except Exception:
                tag = None

            if tag:
                log.info("[LinkedIn] Found Easy Apply button for %s", job.url)
                await easy_apply_btn.click()
                await self.human_delay(1500, 2500)
                return await self._fill_easy_apply_form(page, resume_pdf_path, cover_letter_text)

            log.info("[LinkedIn] No Easy Apply button — trying external apply for %s", job.url)
            if resume_data:
                return await self._apply_external(
                    page, job, resume_data, resume_pdf_path,
                    cover_letter_text, non_interactive,
                    cover_letter_path=cover_letter_path,
                )
            return False

        except Exception as e:
            log.warning("[LinkedIn] Apply error for %s: %s", job.url, e)
            return False
        finally:
            await page.close()

    async def _apply_external(
        self,
        page: Page,
        job: Job,
        resume_data: dict,
        resume_pdf_path: str,
        cover_letter_text: str,
        non_interactive: bool,
        cover_letter_path: str | None = None,
    ) -> bool:
        """
        Find the external Apply button on a LinkedIn job listing, follow it to the
        employer's ATS (popup or redirect), and run the generic form filler there.
        """
        from scrapers.employer_site import fill_employer_form

        external_btn = None
        for sel in [
            "button.jobs-apply-button",
            ".jobs-s-apply button",
            "button:has-text('Apply')",
            "a:has-text('Apply on company website')",
            "a:has-text('Apply on employer site')",
            "[class*='apply-button']",
            "[data-tracking-control-name*='apply'] button",
            "[aria-label*='Apply']",
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    external_btn = btn
                    break
            except Exception:
                continue

        if not external_btn:
            log.info("[LinkedIn] No external Apply button found for %s", job.url)
            return False

        btn_text = (await external_btn.inner_text()).strip()
        log.info("[LinkedIn] External apply button: %r for %s", btn_text, job.url)

        # Listen for a new page (popup) before clicking
        new_page_future: asyncio.Future = asyncio.get_event_loop().create_future()

        def _on_new_page(new_pg):
            if not new_page_future.done():
                new_page_future.set_result(new_pg)

        page.context.on("page", _on_new_page)
        original_url = page.url

        try:
            await external_btn.click()
            await self.human_delay(2500, 3500)
        finally:
            page.context.remove_listener("page", _on_new_page)

        target_page = None
        if new_page_future.done():
            target_page = new_page_future.result()
            try:
                await target_page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            log.info("[LinkedIn] External apply popup URL: %s", target_page.url)
        elif page.url != original_url:
            target_page = page
            log.info("[LinkedIn] External apply navigated to: %s", page.url)
        else:
            # No popup, no URL change — check if an Easy Apply modal opened on the same page
            modal = await page.query_selector(
                ".jobs-easy-apply-modal, "
                "[aria-label*='Easy Apply'][role='dialog'], "
                ".artdeco-modal:has(button[aria-label*='Submit application']), "
                "[class*='easy-apply-modal']"
            )
            if modal:
                log.info("[LinkedIn] Easy Apply modal detected on same page for %s", job.url)
                return await self._fill_easy_apply_form(page, resume_pdf_path, cover_letter_text)
            log.info("[LinkedIn] No popup, navigation, or modal after clicking Apply for %s", job.url)
            return False

        if not target_page or "linkedin.com" in target_page.url:
            # Still on LinkedIn — check for modal
            modal = await page.query_selector(
                ".jobs-easy-apply-modal, [aria-label*='Easy Apply'][role='dialog']"
            )
            if modal:
                log.info("[LinkedIn] Easy Apply modal detected for %s", job.url)
                return await self._fill_easy_apply_form(page, resume_pdf_path, cover_letter_text)
            log.info("[LinkedIn] Still on LinkedIn after clicking Apply — skipping for %s", job.url)
            return False

        try:
            success = await fill_employer_form(
                target_page, resume_data, cover_letter_text, resume_pdf_path,
                cover_letter_path=cover_letter_path,
                non_interactive=non_interactive,
            )
            log.info("[LinkedIn] External form filler result=%s for %s", success, job.url)
            return success
        except Exception as e:
            log.warning("[LinkedIn] External form filler error: %s", e)
            return False
        finally:
            if target_page is not page:
                try:
                    await target_page.close()
                except Exception:
                    pass

    # ── standard answers for common Easy Apply questions ─────────────────────
    _FIELD_ANSWERS = {
        # Name
        "first":           "Aidarbek",
        "last":            "Abdyk",
        "preferred name":  "Aidarbek Abdyk",
        "full name":       "Aidarbek Abdyk",
        # Contact
        "phone":           "773-757-2279",
        "email":           "aidarbek.a@yahoo.com",
        # Location
        "city":            "Chicago",
        "location":        "Chicago, IL, USA",
        "based in":        "Chicago, IL, USA",
        "country":         "United States",
        "state":           "Illinois",
        "zip":             "60601",
        "postal":          "60601",
        "address":         "Chicago, IL",
        # Compensation
        "salary":          "130000",
        "compensation":    "130000",
        "expected":        "130000",
        "desired":         "130000",
        # Profiles
        "linkedin":        "https://www.linkedin.com/in/aidarbek-devops/",
        "github":          "",
        "website":         "https://www.linkedin.com/in/aidarbek-devops/",
        "portfolio":       "https://www.linkedin.com/in/aidarbek-devops/",
        # Availability
        "notice":          "2 weeks",
        "availability":    "2 weeks",
        "start":           "2 weeks",
        # Experience
        "experience":      "7",
        "years":           "7",
        # Work authorization
        "authorized":      "Yes",
        "sponsorship":     "No",
        "visa":            "No",
        "work auth":       "Yes",
        # Work style
        "remote":          "Yes",
        "relocate":        "No",
        "relocation":      "No",
        # EEO / diversity
        "gender":          "Male",
        "hispanic":        "No",
        "latino":          "No",
        "ethnicity":       "Asian",
        "race":            "Asian",
        "veteran":         "No",
        "disability":      "No",
        # Common yes/no
        "referred":        "No",
        "previously employed": "No",
        "worked here":     "No",
        "former employee": "No",
    }

    def _quick_answer(self, label: str) -> str:
        """Return a canned answer based on field label keywords."""
        label_lower = label.lower()
        for kw, ans in self._FIELD_ANSWERS.items():
            if kw in label_lower:
                return ans
        return ""

    async def _fill_easy_apply_form(
        self, page: Page, resume_path: str, cover_letter: str
    ) -> bool:
        """
        Drives the LinkedIn Easy Apply multi-step modal.
        Tries fast path (DOM inspection) first; falls back to Claude Vision when stuck.
        """
        max_steps = 18
        vision_attempts = 0
        max_vision = 5

        for step in range(max_steps):
            await self.human_delay(800, 1500)

            # ── 1. File upload ────────────────────────────────────────────────
            file_inputs = await page.query_selector_all("input[type='file']")
            for fi in file_inputs:
                try:
                    await fi.set_input_files(resume_path)
                    await self.human_delay(800, 1200)
                except Exception:
                    pass

            # ── 2. Fill text / tel / email inputs ────────────────────────────
            for sel in ["input[type='text']", "input[type='tel']", "input[type='email']",
                        "input[type='number']"]:
                inputs = await page.query_selector_all(
                    f"{sel}:not([readonly]):not([disabled]):not([aria-hidden='true'])"
                )
                for inp in inputs:
                    try:
                        val = (await inp.get_attribute("value") or "").strip()
                        # Always overwrite email fields with the correct address
                        if sel == "input[type='email']" or "email" in (await inp.get_attribute("name") or "").lower() or "email" in (await inp.get_attribute("placeholder") or "").lower():
                            await inp.fill("aidarbek.a@yahoo.com")
                            await self.human_delay(150, 300)
                            continue
                        if val:
                            continue
                        label = await self._field_label(page, inp)
                        answer = self._quick_answer(label)
                        if answer:
                            await inp.fill(answer)
                            await self.human_delay(150, 300)
                    except Exception:
                        pass

            # ── 3. Fill textareas (cover letter, motivation, etc.) ────────────
            textareas = await page.query_selector_all(
                "textarea:not([readonly]):not([disabled])"
            )
            for ta in textareas:
                try:
                    val = (await ta.input_value()).strip()
                    if val:
                        continue
                    await ta.fill(cover_letter[:2000])
                    await self.human_delay(200, 400)
                except Exception:
                    pass

            # ── 4. Handle <select> dropdowns ─────────────────────────────────
            selects = await page.query_selector_all(
                "select:not([disabled]):not([aria-hidden='true'])"
            )
            for sel_el in selects:
                try:
                    current = await sel_el.input_value()
                    if current and current != "Select an option" and current != "":
                        continue
                    label = await self._field_label(page, sel_el)
                    options = await sel_el.query_selector_all("option")
                    opt_texts = []
                    for o in options:
                        t = (await o.inner_text()).strip()
                        if t and t.lower() not in ("select an option", "please select", "", "--"):
                            opt_texts.append(t)
                    if not opt_texts:
                        continue
                    chosen = await self._pick_option(label, opt_texts)
                    if chosen:
                        await sel_el.select_option(label=chosen)
                        await self.human_delay(150, 300)
                except Exception:
                    pass

            # ── 5. Handle radio buttons ───────────────────────────────────────
            fieldsets = await page.query_selector_all("fieldset")
            for fs in fieldsets:
                try:
                    # Already answered?
                    checked = await fs.query_selector("input[type='radio']:checked")
                    if checked:
                        continue
                    legend = await fs.query_selector("legend")
                    label = (await legend.inner_text()).strip() if legend else ""
                    radios = await fs.query_selector_all("input[type='radio']")
                    radio_labels = []
                    for r in radios:
                        rid = await r.get_attribute("id") or ""
                        lbl = await page.query_selector(f"label[for='{rid}']")
                        radio_labels.append(
                            ((await lbl.inner_text()).strip() if lbl else ""), r
                        )
                    answer = self._quick_answer(label)
                    for lbl_text, radio_el in radio_labels:
                        if answer and lbl_text.lower().startswith(answer.lower()):
                            await radio_el.click()
                            await self.human_delay(150, 300)
                            break
                    else:
                        # Default: pick "Yes" or first option
                        yes_opt = next(
                            (r for lt, r in radio_labels if "yes" in lt.lower()), None
                        )
                        if yes_opt:
                            await yes_opt.click()
                except Exception:
                    pass

            # ── 6. Check for Submit button ────────────────────────────────────
            submit = None
            for sel in [
                "button[aria-label='Submit application']",
                "button[aria-label*='Submit']",
                "button:has-text('Submit application')",
                "button:has-text('Submit Application')",
            ]:
                submit = await page.query_selector(sel)
                if submit and await submit.is_visible():
                    break
                submit = None

            if submit:
                await submit.click()
                await self.human_delay(2000, 3000)
                log.info("[LinkedIn] Application submitted.")
                return True

            # ── 7. Click Next / Continue / Review ─────────────────────────────
            advanced = False
            for sel in [
                "button[aria-label='Continue to next step']",
                "button[aria-label*='Continue']",
                "button[aria-label*='Next']",
                "button[aria-label='Review your application']",
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button:has-text('Review')",
                "button:has-text('Proceed')",
            ]:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    await self.human_delay(1000, 1800)
                    advanced = True
                    break

            if advanced:
                continue

            # ── 8. Stuck — use Claude Vision to figure out what's on screen ───
            if vision_attempts >= max_vision:
                log.warning("[LinkedIn] Vision budget exhausted, giving up.")
                break

            vision_attempts += 1
            log.info("[LinkedIn] Stuck at step %d, using Claude Vision (attempt %d)",
                     step, vision_attempts)
            result = await self._vision_step(page, resume_path, cover_letter)
            if result == "submitted":
                return True
            if result == "failed":
                break
            # result == "continue" → loop again

        return False

    async def _field_label(self, page: Page, element) -> str:
        """Try to find the visible label text for a form element."""
        try:
            el_id = await element.get_attribute("id") or ""
            if el_id:
                lbl = await page.query_selector(f"label[for='{el_id}']")
                if lbl:
                    return (await lbl.inner_text()).strip()
            placeholder = await element.get_attribute("placeholder") or ""
            if placeholder:
                return placeholder
            aria = await element.get_attribute("aria-label") or ""
            if aria:
                return aria
            name = await element.get_attribute("name") or ""
            return name
        except Exception:
            return ""

    async def _pick_option(self, label: str, options: list[str]) -> str | None:
        """
        Choose the best option from a select dropdown given the field label.
        Uses simple heuristics; falls back to picking the highest/last numeric option.
        """
        label_lower = label.lower()
        answer = self._quick_answer(label)

        if answer:
            # Try exact or prefix match
            for opt in options:
                if opt.lower().startswith(answer.lower()):
                    return opt

        # "Years of experience" — pick highest
        if "year" in label_lower or "experience" in label_lower:
            nums = []
            for opt in options:
                digits = re.findall(r"\d+", opt)
                if digits:
                    nums.append((int(digits[-1]), opt))
            if nums:
                return max(nums, key=lambda x: x[0])[1]

        # Work authorization / sponsorship
        if "authorized" in label_lower or "eligible" in label_lower or "legal" in label_lower:
            for opt in options:
                if "yes" in opt.lower():
                    return opt
        if "sponsor" in label_lower or "visa" in label_lower:
            for opt in options:
                if "no" in opt.lower():
                    return opt

        # Default: first non-empty option
        return options[0] if options else None

    async def _vision_step(self, page: Page, resume_path: str, cover_letter: str) -> str:
        """
        Use Claude Vision to analyze the current Easy Apply modal screenshot
        and take the appropriate action.
        Returns "submitted" | "continue" | "failed".
        """
        try:
            screenshot = await page.screenshot(full_page=False)
            b64 = base64.standard_b64encode(screenshot).decode()

            prompt = """You are helping automate a LinkedIn Easy Apply job application.

Candidate profile:
- Name: Aidarbek A., DevOps Engineer, Chicago IL
- Phone: +1-312-555-0000
- Authorized to work in US: Yes (Green Card)
- Requires sponsorship: No
- Years of experience: 7
- Expected salary: $130,000-$150,000
- Preferred work: Remote

Look at this screenshot of a LinkedIn Easy Apply modal and return JSON:
{
  "page_state": "form_fields | submit_ready | already_submitted | error | unknown",
  "blocking_issue": "describe what is preventing progress, or null",
  "actions": [
    {
      "type": "fill_text | select_option | click_radio | click_checkbox | click_button | upload_file",
      "find_by": "aria_label | placeholder | label_text | button_text",
      "find_value": "the text to find the element",
      "fill_value": "value to fill or option to select or button text to click"
    }
  ]
}

Rules:
- For "How many years of experience?": fill "7" or pick the highest option
- For "Are you authorized to work in US?" or similar: pick/fill "Yes"
- For "Do you require sponsorship?": pick/fill "No"
- For "Submit application" button: include a click_button action with fill_value "Submit application"
- For "Next" or "Continue" button: include a click_button action
- If you see a success/confirmation message: set page_state to "already_submitted"
- Only include actions for fields that need filling (empty or unanswered)

Return ONLY valid JSON, no markdown."""

            resp = await _ai.messages.create(
                model=AI_CONFIG["model"],
                max_tokens=1000,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            text = next(b.text for b in resp.content if b.type == "text").strip()
            if text.startswith("```"):
                text = text.split("```")[1].lstrip("json").strip()
            data = json.loads(text)

            state = data.get("page_state", "unknown")
            if state == "already_submitted":
                log.info("[LinkedIn Vision] Detected submission confirmation.")
                return "submitted"

            actions = data.get("actions", [])
            log.info("[LinkedIn Vision] state=%s, %d actions", state, len(actions))

            for action in actions:
                await self._execute_vision_action(page, action, resume_path, cover_letter)
                await self.human_delay(300, 600)

            return "continue"

        except Exception as e:
            log.warning("[LinkedIn Vision] Error: %s", e)
            return "continue"  # try loop again rather than hard-fail

    async def _execute_vision_action(
        self, page: Page, action: dict, resume_path: str, cover_letter: str
    ):
        """Execute a single action dict returned by Claude Vision."""
        atype = action.get("type", "")
        find_by = action.get("find_by", "")
        find_val = action.get("find_value", "")
        fill_val = action.get("fill_value", "")

        # Build selector candidates
        selectors = []
        if find_by == "aria_label":
            selectors = [f"[aria-label='{find_val}']", f"[aria-label*='{find_val}']"]
        elif find_by == "placeholder":
            selectors = [f"[placeholder='{find_val}']", f"[placeholder*='{find_val}']"]
        elif find_by == "label_text":
            selectors = [
                f"label:has-text('{find_val}') + input",
                f"label:has-text('{find_val}') + textarea",
                f"label:has-text('{find_val}') ~ input",
                f"label:has-text('{find_val}') ~ select",
            ]
        elif find_by == "button_text":
            selectors = [f"button:has-text('{find_val}')"]

        el = None
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    break
                el = None
            except Exception:
                pass

        if not el and atype == "click_button":
            el = await page.query_selector(f"button:has-text('{fill_val}')")

        if not el:
            return

        try:
            if atype == "fill_text":
                await el.fill(fill_val)
            elif atype == "select_option":
                await el.select_option(label=fill_val)
            elif atype in ("click_radio", "click_checkbox", "click_button"):
                await el.click()
                await self.human_delay(500, 1000)
            elif atype == "upload_file":
                await el.set_input_files(resume_path)
        except Exception as e:
            log.debug("[LinkedIn Vision] action %s failed: %s", atype, e)
