"""
Generic employer website form filler using Claude Vision.

Flow:
  1. Navigate to the application URL
  2. Take a screenshot
  3. Claude Vision analyzes the current page state
  4. Fill fields / register / login programmatically
  5. Repeat for multi-step forms
  6. Submit when ready

Handles registration automatically: if an ATS requires an account, we create one
using aidarbek.a@yahoo.com and store the credentials in credentials/ats_passwords.json.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import string
import secrets
from pathlib import Path
import urllib.request
from urllib.parse import urlparse

import anthropic
from playwright.async_api import Page

from config import AI_CONFIG

log = logging.getLogger("jobsearch")
_client = anthropic.AsyncAnthropic()

CREDS_FILE = Path("credentials/ats_passwords.json")
FORM_ANSWERS_FILE = Path("credentials/form_answers.json")


# ── Credential storage ────────────────────────────────────────────────────────

def _load_creds() -> dict:
    if CREDS_FILE.exists():
        try:
            return json.loads(CREDS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_creds(creds: dict):
    CREDS_FILE.parent.mkdir(exist_ok=True)
    CREDS_FILE.write_text(json.dumps(creds, indent=2))


def _make_password(domain: str) -> str:
    """Deterministic strong password per domain (reproducible if file is lost)."""
    seed = f"jobsearch-{domain}-aidarbek2024"
    h = hashlib.sha256(seed.encode()).hexdigest()
    # Build: 2 uppercase + 2 digits + rest lowercase + special char
    pw = h[:6].upper()[:2] + h[6:8] + h[8:14] + "!K"
    return pw


def _get_or_create_password(url: str) -> str:
    domain = urlparse(url).netloc.replace("www.", "")
    creds = _load_creds()
    if domain not in creds:
        creds[domain] = {
            "email": "aidarbek.a@yahoo.com",
            "password": _make_password(domain),
        }
        _save_creds(creds)
        log.info("[form] Stored new ATS credentials for %s", domain)
    return creds[domain]["password"]


def _get_stored_password(url: str) -> str | None:
    domain = urlparse(url).netloc.replace("www.", "")
    return _load_creds().get(domain, {}).get("password")


# ── Persistent form answers (Telegram Q&A) ────────────────────────────────────

def _load_form_answers() -> dict:
    """Load saved field answers from previous Telegram Q&A sessions."""
    if FORM_ANSWERS_FILE.exists():
        try:
            return json.loads(FORM_ANSWERS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_form_answers(answers: dict):
    """Merge new answers into the persisted answers file."""
    existing = _load_form_answers()
    existing.update({k.lower(): v for k, v in answers.items()})
    FORM_ANSWERS_FILE.parent.mkdir(exist_ok=True)
    FORM_ANSWERS_FILE.write_text(json.dumps(existing, indent=2))


def _match_saved_answer(field_desc: str, saved: dict) -> str | None:
    """Fuzzy-match a field description against saved answer keys."""
    field_lower = field_desc.lower()
    for key, val in saved.items():
        if key in field_lower or field_lower in key:
            return val
    return None


def _normalize_value(field_name: str, raw: str) -> str:
    """
    Clean up whatever the user typed into a properly formatted value.
    Works even if the format is wrong — strips spaces, fixes URLs, formats phone.
    """
    raw = raw.strip()
    fl = field_name.lower()

    # LinkedIn URL
    if "linkedin" in fl:
        # Strip any surrounding spaces/punctuation
        raw = raw.strip("/ ")
        if "linkedin.com/in/" in raw:
            # Already has the path — just ensure https://
            if not raw.startswith("http"):
                raw = "https://" + raw
        elif "linkedin.com" in raw:
            if not raw.startswith("http"):
                raw = "https://" + raw
        else:
            # Bare username like "aidarbek-devops" or "/in/aidarbek-devops"
            slug = raw.lstrip("/").replace("in/", "")
            raw = f"https://www.linkedin.com/in/{slug}"
        if not raw.endswith("/"):
            raw += "/"
        return raw

    # GitHub URL
    if "github" in fl:
        raw = raw.strip("/ ")
        if "github.com/" in raw:
            if not raw.startswith("http"):
                raw = "https://" + raw
        else:
            slug = raw.lstrip("/")
            raw = f"https://github.com/{slug}"
        return raw

    # Generic URL / website / portfolio
    if any(k in fl for k in ("url", "website", "portfolio", "site")):
        if raw and not raw.startswith("http"):
            raw = "https://" + raw
        return raw

    # Phone number — normalize to XXX-XXX-XXXX
    if any(k in fl for k in ("phone", "tel", "mobile", "cell")):
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return raw  # return as-is if unexpected length

    # Salary — strip $ commas, keep number
    if any(k in fl for k in ("salary", "compensation", "pay", "rate")):
        digits = re.sub(r"[^\d.]", "", raw)
        return digits if digits else raw

    return raw


def _telegram_get_updates(token: str, offset: int, poll_timeout: int = 30) -> list:
    """Long-poll Telegram getUpdates. Returns list of update dicts."""
    url = (
        f"https://api.telegram.org/bot{token}/getUpdates"
        f"?offset={offset}&timeout={poll_timeout}&limit=10"
    )
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=poll_timeout + 5) as resp:
            data = json.loads(resp.read())
            return data.get("result", [])
    except Exception:
        return []


def _telegram_current_offset(token: str) -> int:
    """Return offset = last_update_id + 1, so we only receive new messages."""
    updates = _telegram_get_updates(token, -100, poll_timeout=1)
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


# ── Main entry point ──────────────────────────────────────────────────────────

async def probe_form(page: Page, url: str) -> dict:
    """
    Lightweight preflight: take a screenshot and ask Claude if the form can be
    auto-submitted and whether a cover letter field exists.
    Returns {"can_automate": bool, "needs_cover_letter": bool, "reason": str}
    Does NOT fill or submit anything.
    """
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
        b64 = base64.standard_b64encode(screenshot_bytes).decode()

        prompt = f"""You are looking at a job application page (URL: {url}).

Answer two questions about what you see:
1. Can a script fully fill and submit this form automatically?
   - NO (can_automate=false) ONLY if:
       * An interactive visual CAPTCHA challenge is shown (e.g. "click all traffic lights")
       * SMS or phone verification code is required
       * A hardware security key or authenticator app (MFA/2FA) is required
   - YES (can_automate=true) for EVERYTHING else, including:
       * Login / sign-in forms — the script can fill email+password and submit
       * Registration / sign-up forms — the script can create an account automatically
       * Email confirmation pages — the script checks the inbox automatically
       * Standard form fields, dropdowns, file uploads, checkboxes
       * Job listing pages with an Apply button (the script will click it)
       * Passive/invisible reCAPTCHA (v3) — does NOT require user interaction
2. Is there a cover letter / motivation letter TEXT field visible on this page?

Return ONLY valid JSON:
{{"can_automate": true/false, "needs_cover_letter": true/false, "reason": "one sentence"}}"""

        response = await _client.messages.create(
            model=AI_CONFIG["model"],
            max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        text = next(b.text for b in response.content if b.type == "text").strip()
        j_start = text.find("{")
        j_end = text.rfind("}")
        if j_start != -1 and j_end > j_start:
            text = text[j_start:j_end + 1]
        result = json.loads(text)
        # Remove blocked_by_login if present (no longer used as a skip gate)
        result.pop("blocked_by_login", None)
        log.info("[probe] url=%s can_automate=%s needs_cover_letter=%s reason=%s",
                 url, result.get("can_automate"), result.get("needs_cover_letter"), result.get("reason"))
        return result
    except Exception as e:
        log.warning("[probe] Failed to probe %s: %s", url, e)
        # When in doubt, assume automatable so we don't skip good jobs
        return {"can_automate": True, "needs_cover_letter": True, "reason": f"probe_error: {e}"}


async def fill_employer_form(
    page: Page,
    resume_data: dict,
    cover_letter: str,
    resume_pdf_path: str,
    cover_letter_path: str | None = None,
    max_steps: int = 20,
    non_interactive: bool = False,
) -> bool:
    """
    Returns True if the form was submitted successfully.
    Handles login/registration automatically.
    Only fills required fields (marked with *).
    Sends Telegram message if a required field can't be answered automatically.
    """
    p = resume_data["personal"]

    candidate_info = {
        "full_name":            p["name"],
        "first_name":           p["name"].split()[0],
        "last_name":            "Abdyk",          # always use legal last name
        "email":                p["email"],
        "phone":                p.get("phone", "773-757-2279"),
        "city":                 "Chicago",
        "location":             "Chicago, IL",
        "country":              "United States",
        "country_code":         "+1",
        "linkedin":             p.get("linkedin", ""),
        "github":               p.get("github", ""),
        "cover_letter_excerpt": cover_letter[:800],
        "years_experience":     "7",
        "authorized_to_work":   "Yes",
        "requires_sponsorship": "No",
        "salary_expectation":   "130000",
        "availability":         "2 weeks",
        "preferred_work":       "Remote",
        "visa_status":          "Green Card",
        "gender":               "Prefer not to say",
        "ethnicity":            "Prefer not to say",
        "veteran":              "No",
        "disability":           "No",
    }

    # Merge any previously saved Q&A answers into candidate_info
    saved_answers = _load_form_answers()
    for key, val in saved_answers.items():
        if key not in candidate_info or not candidate_info[key]:
            candidate_info[key] = val
    # Also patch specific well-known fields from saved answers
    for saved_key, info_key in [
        ("linkedin url", "linkedin"), ("linkedin profile", "linkedin"),
        ("github url", "github"), ("portfolio", "github"),
    ]:
        if saved_key in saved_answers and not candidate_info.get(info_key):
            candidate_info[info_key] = saved_answers[saved_key]

    registered_domains: set[str] = set()
    login_attempts = 0
    last_action = ""
    stuck_count = 0
    clicked_nav_buttons: set[str] = set()  # tracks fallback buttons already clicked

    for step in range(max_steps):
        await asyncio.sleep(1.5)

        screenshot_bytes = await page.screenshot(full_page=False)
        b64 = base64.standard_b64encode(screenshot_bytes).decode()

        analysis = await _analyze_form(b64, candidate_info, page.url)
        action = analysis.get("action", "unknown")
        log.info("[form] step=%d action=%s url=%s", step + 1, action, page.url)

        # ── Stuck-loop detection ───────────────────────────────────────────────
        if action == last_action and action in ("fill_and_next", "next", "unknown"):
            stuck_count += 1
            if stuck_count >= 3:
                log.warning("[form] Stuck in loop (action=%r for %d steps) — escalating to Computer Use", action, stuck_count)
                result = await _escalate_to_computer_use(page, candidate_info, cover_letter, resume_pdf_path)
                if result:
                    return True
                await _notify_stuck_telegram(page, candidate_info)
                return False
        else:
            stuck_count = 0
        last_action = action

        if action == "already_applied":
            log.info("[form] Already applied.")
            return False

        if action == "completed":
            log.info("[form] Application submitted successfully!")
            return True

        if action == "confirm_email":
            # ATS wants email confirmation — check Yahoo inbox automatically
            domain = urlparse(page.url).netloc.replace("www.", "")
            log.info("[form] Email confirmation required — checking Yahoo inbox for %s", domain)
            confirm_link = await asyncio.to_thread(
                _fetch_confirmation_link, domain, 300  # wait up to 5 min
            )
            if confirm_link:
                log.info("[form] Opening confirmation link: %s", confirm_link[:80])
                await page.goto(confirm_link, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                last_action = ""  # reset stuck detector after navigation
                continue
            else:
                log.warning("[form] Could not get confirmation email — escalating to Computer Use")
                result = await _escalate_to_computer_use(page, candidate_info, cover_letter, resume_pdf_path)
                if result:
                    return True
                await _notify_stuck_telegram(page, candidate_info)
                return False

        if action == "login_required":
            if login_attempts >= 2:
                log.warning("[form] Login failed after %d attempts — escalating to Computer Use", login_attempts)
                result = await _escalate_to_computer_use(page, candidate_info, cover_letter, resume_pdf_path)
                if result:
                    return True
                await _notify_stuck_telegram(page, candidate_info)
                return False
            login_attempts += 1
            pw = _get_or_create_password(page.url)
            log.info("[form] Auto-login with email=%s", candidate_info["email"])
            filled = await _attempt_login(page, candidate_info["email"], pw)
            if not filled:
                log.warning("[form] Could not fill login form — escalating to Computer Use")
                result = await _escalate_to_computer_use(page, candidate_info, cover_letter, resume_pdf_path)
                if result:
                    return True
                await _notify_stuck_telegram(page, candidate_info)
                return False
            await asyncio.sleep(3)
            continue

        if action == "register":
            domain = urlparse(page.url).netloc
            if domain in registered_domains:
                # Already tried registering — attempt login with stored credentials
                pw = _get_or_create_password(page.url)
                log.info("[form] Already registered at %s — trying login", domain)
                await _attempt_login(page, candidate_info["email"], pw)
                await asyncio.sleep(3)
                login_attempts += 1
                if login_attempts >= 3:
                    log.warning("[form] Registration+login loop at %s — escalating to Computer Use", domain)
                    result = await _escalate_to_computer_use(page, candidate_info, cover_letter, resume_pdf_path)
                    if result:
                        return True
                    await _notify_stuck_telegram(page, candidate_info)
                    return False
                continue
            registered_domains.add(domain)
            pw = _get_or_create_password(page.url)
            log.info("[form] Auto-registering at %s with email=%s", domain, candidate_info["email"])
            await _attempt_register(page, candidate_info, pw)
            await asyncio.sleep(3)
            # After registering, check if we land on an email-confirmation page (DOM check)
            # before the next Claude Vision screenshot
            page_text = (await page.content()).lower()
            email_confirm_phrases = (
                "check your email", "verify your email", "confirm your email",
                "confirmation sent", "verification email", "click the link",
                "we sent you", "email has been sent",
            )
            if any(p in page_text for p in email_confirm_phrases):
                domain_clean = urlparse(page.url).netloc.replace("www.", "")
                log.info("[form] Email confirmation needed after registration at %s", domain_clean)
                confirm_link = await asyncio.to_thread(
                    _fetch_confirmation_link, domain_clean, 300
                )
                if confirm_link:
                    log.info("[form] Opening confirmation link: %s", confirm_link[:80])
                    await page.goto(confirm_link, wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    last_action = ""
            continue

        # ── Always do direct DOM fill first (fast path for common ATS fields) ──
        await _direct_fill_common_fields(page, candidate_info)
        await asyncio.sleep(0.3)

        # ── Fill fields returned by Claude Vision ─────────────────────────────
        unknown_required: list[str] = []
        fields = analysis.get("fields", [])
        for field in fields:
            filled = await _fill_field(page, field, resume_pdf_path, cover_letter)
            if not filled and field.get("required"):
                desc = field.get("description", field.get("find_value", "unknown field"))
                unknown_required.append(desc)

        # ── Ask via Telegram for required fields we couldn't fill ────────────
        if unknown_required:
            new_answers = await _ask_via_telegram(unknown_required, page, candidate_info)
            if new_answers:
                # Update candidate_info with received answers
                for field_name, answer in new_answers.items():
                    candidate_info[field_name.lower()] = answer
                    # Also patch well-known keys so _direct_fill_common_fields picks them up
                    fl = field_name.lower()
                    if "linkedin" in fl:
                        candidate_info["linkedin"] = answer
                    elif "github" in fl:
                        candidate_info["github"] = answer
                    elif "phone" in fl or "tel" in fl:
                        candidate_info["phone"] = answer
                # Retry filling those fields with the new values
                for field in fields:
                    desc = field.get("description", field.get("find_value", ""))
                    if desc in unknown_required:
                        matched = _match_saved_answer(desc, new_answers)
                        if matched:
                            field["value_to_fill"] = matched
                            await _fill_field(page, field, resume_pdf_path, cover_letter)

        # ── File uploads: resume to resume input, cover letter to CL input ────
        if analysis.get("has_resume_upload"):
            await _upload_file_to_input(page, resume_pdf_path, field_type="resume")
        if analysis.get("has_cover_letter_upload") and cover_letter_path:
            await _upload_file_to_input(page, cover_letter_path, field_type="cover_letter")
        elif analysis.get("has_file_upload"):
            # Fallback: generic upload (detect by input name/id)
            await _upload_files_smart(page, resume_pdf_path, cover_letter_path)

        # ── Navigate ──────────────────────────────────────────────────────────
        if action in ("submit",) and analysis.get("has_submit"):
            submitted = await _click_submit(page)
            if submitted:
                await asyncio.sleep(2.5)
                if await _check_confirmation(page):
                    return True
                continue

        elif action in ("next", "continue", "fill_and_next"):
            clicked = await _click_next(page, clicked_nav_buttons)
            if not clicked:
                log.warning("[form] No next button found at step %d", step + 1)
                break

        else:
            log.warning("[form] Unknown action=%r at step %d", action, step + 1)
            if not non_interactive:
                print(f"\n  [form] Unclear state at step {step + 1}. URL: {page.url}")
                resp = input("  'done'=submitted, 'skip'=cancel, ENTER=retry: ").strip().lower()
                if resp == "done":
                    return True
                if resp == "skip":
                    return False
            else:
                return False

    log.warning("[form] Reached max_steps=%d without completing", max_steps)
    if not non_interactive:
        ans = input("\n  [form] Could not fully automate. 'done' or 'skip': ").strip().lower()
        return ans == "done"
    return False


# ── Login / Registration ──────────────────────────────────────────────────────

async def _attempt_login(page: Page, email: str, password: str) -> bool:
    """Try to fill an email+password login form and submit it."""
    filled = False
    for sel in ['input[type="email"]', 'input[name*="email"]', 'input[name*="username"]',
                'input[placeholder*="email" i]', 'input[placeholder*="Email" i]']:
        el = await page.query_selector(sel)
        if el and await el.is_visible():
            await el.fill(email)
            filled = True
            break

    for sel in ['input[type="password"]', 'input[name*="password"]',
                'input[placeholder*="password" i]']:
        el = await page.query_selector(sel)
        if el and await el.is_visible():
            await el.fill(password)
            break

    if filled:
        # Try all known login submit button texts
        login_button_texts = [
            "Sign In", "Sign in", "sign in",
            "Log In", "Log in", "log in",
            "Login", "login",
            "Submit", "Continue",
            "Access", "Enter",
        ]
        clicked = False
        for btn_text in login_button_texts:
            try:
                el = await page.query_selector(f"button:has-text('{btn_text}')")
                if not el:
                    el = await page.query_selector(f"[role='button']:has-text('{btn_text}')")
                if not el:
                    el = await page.query_selector(f"input[type='submit'][value*='{btn_text}']")
                if el and await el.is_visible() and await el.is_enabled():
                    await el.click()
                    log.info("[form] Clicked login button: %r", btn_text)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            submitted = await _click_submit(page)
            if not submitted:
                el = await page.query_selector('input[type="password"]')
                if el:
                    await el.press("Enter")
    return filled


async def _attempt_register(page: Page, candidate_info: dict, password: str) -> bool:
    """Fill a registration form with candidate info and submit."""
    email = candidate_info["email"]
    first = candidate_info["first_name"]
    last = candidate_info["last_name"]
    full = candidate_info["full_name"]

    async def _fill_if_found(selectors: list[str], value: str):
        for sel in selectors:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(value)
                return True
        return False

    await _fill_if_found([
        'input[name*="first" i]', 'input[placeholder*="first name" i]',
        'input[id*="first" i]', 'input[aria-label*="first name" i]',
    ], first)

    await _fill_if_found([
        'input[name*="last" i]', 'input[placeholder*="last name" i]',
        'input[id*="last" i]', 'input[aria-label*="last name" i]',
    ], last)

    await _fill_if_found([
        'input[name*="name" i][not([name*="first"]):not([name*="last"])]',
        'input[placeholder*="full name" i]', 'input[id*="fullname" i]',
        'input[aria-label*="full name" i]', 'input[name="name"]',
    ], full)

    await _fill_if_found([
        'input[type="email"]', 'input[name*="email" i]',
        'input[placeholder*="email" i]', 'input[id*="email" i]',
    ], email)

    # Fill both password fields (password + confirm password)
    pw_fields = await page.query_selector_all('input[type="password"]')
    for pw_field in pw_fields:
        try:
            if await pw_field.is_visible():
                await pw_field.fill(password)
        except Exception:
            pass

    await asyncio.sleep(0.5)

    # Check any required checkboxes (terms of service, etc.)
    checkboxes = await page.query_selector_all('input[type="checkbox"]:not(:checked)')
    for cb in checkboxes:
        try:
            label_text = ""
            label_id = await cb.get_attribute("id")
            if label_id:
                label_el = await page.query_selector(f'label[for="{label_id}"]')
                if label_el:
                    label_text = (await label_el.inner_text()).lower()
            # Check terms/privacy/agree checkboxes automatically
            if any(kw in label_text for kw in ("terms", "privacy", "agree", "accept", "consent")):
                await cb.check()
        except Exception:
            pass

    await asyncio.sleep(0.3)

    # Try all known registration submit button texts before falling back to generic submit
    register_button_texts = [
        "Sign Up", "Sign up", "sign up",
        "Create Account", "Create account", "create account",
        "Create Profile", "Create profile",
        "Register", "register",
        "Join", "Join Now", "join now",
        "Get Started", "Get started",
        "Complete Registration", "Complete registration",
        "Submit", "Continue", "Next",
    ]
    for btn_text in register_button_texts:
        try:
            el = await page.query_selector(f"button:has-text('{btn_text}')")
            if not el:
                el = await page.query_selector(f"[role='button']:has-text('{btn_text}')")
            if not el:
                el = await page.query_selector(f"input[type='submit'][value*='{btn_text}']")
            if el and await el.is_visible() and await el.is_enabled():
                await el.click()
                log.info("[form] Clicked registration button: %r", btn_text)
                return True
        except Exception:
            continue

    # Last resort: generic submit
    submitted = await _click_submit(page)
    if not submitted:
        clicked = await _click_next(page)
        return clicked
    return submitted


# ── Claude Vision analysis ────────────────────────────────────────────────────

async def _analyze_form(screenshot_b64: str, candidate_info: dict, url: str) -> dict:
    """Call Claude Vision to analyze the current form state."""
    info_str = json.dumps(candidate_info, indent=2)

    prompt = f"""You are analyzing a job application form screenshot. Current URL: {url}

Candidate info (use these values to fill fields):
{info_str}

Return JSON describing the current form state:
{{
  "action": "fill_and_next|submit|completed|login_required|register|confirm_email|already_applied|unknown",
  "has_submit": true/false,
  "has_resume_upload": true/false,
  "has_cover_letter_upload": true/false,
  "has_file_upload": true/false,
  "page_description": "brief description",
  "fields": [
    {{
      "description": "field label as shown",
      "how_to_find": "placeholder|label|aria_label|name|id",
      "find_value": "exact text to locate the element",
      "element_type": "input_text|input_email|input_tel|textarea|select|checkbox|radio",
      "value_to_fill": "value from candidate info",
      "required": true/false
    }}
  ]
}}

Rules:
- "completed": thank-you / confirmation / application submitted page
- "already_applied": explicitly says you already applied
- "login_required": shows a LOGIN form (email + password, no name field). Buttons: "Sign in", "Log in", "Login", "Sign in with Google" etc.
- "register": shows a REGISTRATION / SIGN-UP form with name+email+password fields. Buttons: "Sign up", "Create account", "Register", "Join", "Get started", "Create profile" etc. ALSO use "register" if the page says "create an account" or "join us" even if not all fields are visible yet.
- "confirm_email": page says "check your email", "verify your email", "confirmation sent", "click the link in your email" etc.
- "submit": all required fields filled, main CTA is Submit/Apply
- "fill_and_next": fields to fill OR Next/Continue button is the main CTA
- "unknown": none of the above

IMPORTANT — only include in "fields":
- Fields that are EMPTY (not already filled)
- Fields marked as REQUIRED with an asterisk (*)
- Skip optional fields that are empty unless they are important (cover letter text, linkedin)

For file uploads, set has_resume_upload=true if there's a Resume/CV upload field.
Set has_cover_letter_upload=true if there's a Cover Letter upload field.
Set has_file_upload=true as fallback if you see file upload but can't tell which type.
Do NOT include file upload fields in the "fields" array.

Return ONLY valid JSON, no markdown."""

    response = await _client.messages.create(
        model=AI_CONFIG["model"],
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    text = next(b.text for b in response.content if b.type == "text").strip()
    # Extract JSON object robustly — works even if Claude adds explanation text around it
    j_start = text.find("{")
    j_end = text.rfind("}")
    if j_start != -1 and j_end > j_start:
        text = text[j_start:j_end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("[form] Claude returned invalid JSON: %s", text[:200])
        return {"action": "unknown", "fields": [], "has_submit": False, "has_file_upload": False}


# ── Field filling ─────────────────────────────────────────────────────────────

async def _direct_fill_common_fields(page: Page, info: dict) -> None:
    """
    Fast-path: directly fill the most common ATS fields by known name/id patterns.
    Runs before Claude-identified fields to pre-populate standard inputs.
    """
    mapping = [
        # (selectors_list, value)
        (['input[name*="first_name" i]', 'input[id*="first_name" i]', 'input[id="first" i]',
          'input[autocomplete="given-name"]'], info["first_name"]),
        (['input[name*="last_name" i]', 'input[id*="last_name" i]', 'input[id="last" i]',
          'input[autocomplete="family-name"]'], info["last_name"]),
        (['input[name*="email" i]', 'input[id*="email" i]', 'input[type="email"]',
          'input[autocomplete="email"]'], info["email"]),
        (['input[name*="phone" i]', 'input[id*="phone" i]', 'input[type="tel"]',
          'input[autocomplete="tel"]'], info["phone"]),
        (['input[name*="city" i]', 'input[id*="city" i]',
          'input[name*="location" i]', 'input[id*="location" i]'], info["city"]),
        (['input[name*="linkedin" i]', 'input[id*="linkedin" i]'], info["linkedin"]),
    ]
    for selectors, value in mapping:
        if not value:
            continue
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    existing = (await el.get_attribute("value") or "").strip()
                    if not existing:
                        try:
                            await el.fill(value)
                        except Exception:
                            # Non-standard element (e.g. custom phone input) — try JS then keyboard
                            try:
                                await el.click()
                                await el.evaluate(
                                    "(el, v) => { el.value = v; "
                                    "el.dispatchEvent(new Event('input', {bubbles:true})); "
                                    "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                                    value,
                                )
                            except Exception:
                                try:
                                    await el.click()
                                    await page.keyboard.type(value, delay=30)
                                except Exception:
                                    pass
                    break
            except Exception:
                continue

    # Country select
    country_selectors = [
        'select[name*="country" i]', 'select[id*="country" i]',
        'select[name*="Country" i]', 'select[id*="Country" i]',
    ]
    for sel in country_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                for val in ("United States", "United States of America", "US", "USA"):
                    try:
                        await el.select_option(label=val)
                        break
                    except Exception:
                        try:
                            await el.select_option(value=val)
                            break
                        except Exception:
                            continue
                break
        except Exception:
            continue


async def _fill_field(page: Page, field: dict, resume_path: str, cover_letter: str) -> bool:
    """Attempt to fill a single form field identified by Claude. Returns True if filled."""
    val = field.get("find_value", "")
    elem_type = field.get("element_type", "input_text")
    fill_value = field.get("value_to_fill", "")

    if not val or not fill_value:
        return False

    val_lower = val.lower()
    selectors = [
        f'[placeholder="{val}"]',
        f'[placeholder*="{val}" i]',
        f'[aria-label="{val}"]',
        f'[aria-label*="{val}" i]',
        f'[name="{val}"]',
        f'[name*="{val_lower}"]',
        f'[id="{val}"]',
        f'[id*="{val_lower}"]',
        f'label:has-text("{val}") + input',
        f'label:has-text("{val}") + textarea',
        f'label:has-text("{val}") + select',
        f'label:has-text("{val}") ~ input',
        f'label:has-text("{val}") ~ textarea',
        f'label:has-text("{val}") ~ select',
    ]

    el = None
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                break
            el = None
        except Exception:
            continue

    if not el:
        return False

    try:
        if elem_type in ("input_text", "input_email", "input_tel", "input_number", "textarea"):
            current = (await el.get_attribute("value") or "").strip()
            if not current:
                try:
                    await el.fill(fill_value)
                except Exception:
                    # Non-standard element (e.g. custom phone component) — JS then keyboard fallback
                    try:
                        await el.click()
                        await el.evaluate(
                            "(el, v) => { el.value = v; "
                            "el.dispatchEvent(new Event('input', {bubbles:true})); "
                            "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                            fill_value,
                        )
                        log.info("[form] Filled field %r via JS evaluate", val)
                    except Exception:
                        try:
                            await el.click()
                            await page.keyboard.type(fill_value, delay=40)
                            log.info("[form] Filled field %r via keyboard.type", val)
                        except Exception as e2:
                            log.debug("[form] Could not fill field %r: %s", val, e2)
                            return False
        elif elem_type == "select":
            for opt in (fill_value, fill_value.title(), fill_value.upper()):
                try:
                    await el.select_option(label=opt)
                    break
                except Exception:
                    try:
                        await el.select_option(value=opt)
                        break
                    except Exception:
                        continue
        elif elem_type == "checkbox":
            if fill_value.lower() in ("yes", "true", "1", "checked"):
                await el.check()
        elif elem_type == "radio":
            await el.click()
        return True
    except Exception as e:
        log.debug("[form] Could not fill field %r: %s", val, e)
        return False


async def _upload_file_to_input(page: Page, file_path: str, field_type: str = "resume") -> bool:
    """Upload a file to the first matching input for the given field_type (resume or cover_letter)."""
    if not file_path or not Path(file_path).exists():
        return False
    inputs = await page.query_selector_all("input[type='file']")
    for inp in inputs:
        name = (await inp.get_attribute("name") or "").lower()
        id_ = (await inp.get_attribute("id") or "").lower()
        label_text = ""
        try:
            id_attr = await inp.get_attribute("id") or ""
            lbl = await page.query_selector(f'label[for="{id_attr}"]')
            if lbl:
                label_text = (await lbl.inner_text()).lower()
        except Exception:
            pass

        is_cover = any(kw in (name + id_ + label_text) for kw in ("cover", "letter", "motivation"))
        is_resume = any(kw in (name + id_ + label_text) for kw in ("resume", "cv", "curriculum"))

        if field_type == "cover_letter" and is_cover:
            try:
                await inp.set_input_files(file_path)
                await asyncio.sleep(1)
                log.info("[form] Uploaded cover letter to input")
                return True
            except Exception:
                pass
        elif field_type == "resume" and (is_resume or (not is_cover)):
            try:
                await inp.set_input_files(file_path)
                await asyncio.sleep(1)
                log.info("[form] Uploaded resume to input")
                return True
            except Exception:
                pass
    return False


async def _upload_files_smart(page: Page, resume_path: str, cover_letter_path: str | None) -> None:
    """Upload resume and cover letter to the correct file inputs based on name/id/label."""
    inputs = await page.query_selector_all("input[type='file']")
    for inp in inputs:
        name = (await inp.get_attribute("name") or "").lower()
        id_ = (await inp.get_attribute("id") or "").lower()
        label_text = ""
        try:
            id_attr = await inp.get_attribute("id") or ""
            lbl = await page.query_selector(f'label[for="{id_attr}"]')
            if lbl:
                label_text = (await lbl.inner_text()).lower()
        except Exception:
            pass

        combined = name + id_ + label_text
        is_cover = any(kw in combined for kw in ("cover", "letter", "motivation"))

        try:
            if is_cover:
                if cover_letter_path and Path(cover_letter_path).exists():
                    await inp.set_input_files(cover_letter_path)
                    log.info("[form] Uploaded cover letter to input (smart)")
                # If no cover letter file, leave it empty
            else:
                await inp.set_input_files(resume_path)
                log.info("[form] Uploaded resume to input (smart)")
            await asyncio.sleep(1)
        except Exception:
            pass


async def _ask_via_telegram(
    unknown_fields: list[str], page: Page, candidate_info: dict
) -> dict[str, str]:
    """
    For each unknown required field:
      1. Check saved answers — skip if already known.
      2. Send a Telegram photo asking for just that one value.
      3. Wait up to 10 min for a plain-text reply (no formatting needed).
      4. Normalize the raw reply (fix phone format, LinkedIn URL, etc.).
      5. Save the normalized value and move to the next field.
    Returns {field_name: normalized_value} for all fields answered.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    saved = _load_form_answers()
    all_answers: dict[str, str] = {}

    # Pre-fill from saved answers
    still_unknown = []
    for f in unknown_fields:
        ans = _match_saved_answer(f, saved)
        if ans:
            all_answers[f] = ans
            log.info("[form] Saved answer for %r: %r", f, ans)
        else:
            still_unknown.append(f)

    if not still_unknown:
        return all_answers

    if not token or not chat_id:
        log.warning("[form] Telegram not configured — cannot ask about: %s", still_unknown)
        return all_answers

    # Take one screenshot to attach to the first question
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
    except Exception:
        screenshot_bytes = None

    for field_name in still_unknown:
        try:
            # Snapshot offset BEFORE sending so we only catch replies after this message
            offset = await asyncio.to_thread(_telegram_current_offset, token)

            caption = (
                f"❓ <b>Job application form needs:</b>\n"
                f"<b>{field_name}</b>\n\n"
                f"Just reply with the value — no formatting needed.\n"
                f"<i>(I'll fix the format automatically)</i>"
            )

            def _send(sc=screenshot_bytes, cap=caption):
                if sc:
                    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
                    parts = [
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{cap}".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML".encode(),
                        (
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\";"
                            f" filename=\"form.png\"\r\nContent-Type: image/png\r\n\r\n"
                        ).encode() + sc,
                        f"--{boundary}--".encode(),
                    ]
                    body = b"\r\n".join(parts)
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        data=body,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    )
                else:
                    # No screenshot — send plain text message
                    payload = json.dumps({"chat_id": chat_id, "text": cap, "parse_mode": "HTML"}).encode()
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read())

            await asyncio.to_thread(_send)
            log.info("[form] Asked Telegram for field: %r", field_name)

            # Only attach screenshot to the first question
            screenshot_bytes = None

            # Wait up to 10 min for a plain reply
            loop = asyncio.get_event_loop()
            deadline = loop.time() + 600
            current_offset = offset
            got_reply = False

            while loop.time() < deadline:
                updates = await asyncio.to_thread(_telegram_get_updates, token, current_offset, 30)
                for update in updates:
                    current_offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                        continue
                    raw = msg.get("text", "").strip()
                    if not raw:
                        continue
                    normalized = _normalize_value(field_name, raw)
                    _save_form_answers({field_name: normalized})
                    all_answers[field_name] = normalized
                    log.info("[form] Got %r = %r → normalized: %r", field_name, raw, normalized)
                    # Send confirmation back
                    try:
                        def _confirm(fn=field_name, nv=normalized):
                            payload = json.dumps({
                                "chat_id": chat_id,
                                "text": f"✅ Saved: <b>{fn}</b> = <code>{nv}</code>",
                                "parse_mode": "HTML",
                            }).encode()
                            req = urllib.request.Request(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                data=payload,
                                headers={"Content-Type": "application/json"},
                            )
                            urllib.request.urlopen(req, timeout=10)
                        await asyncio.to_thread(_confirm)
                    except Exception:
                        pass
                    got_reply = True
                    break
                if got_reply:
                    break

            if not got_reply:
                log.warning("[form] No reply for %r within 10 min — skipping", field_name)

        except Exception as e:
            log.warning("[form] Telegram Q&A failed for %r: %s", field_name, e)

    return all_answers


def _fetch_confirmation_link(domain: str, timeout_secs: int = 300) -> str | None:
    """
    Poll Yahoo IMAP for a confirmation/verification email from `domain`.
    Looks for emails received in the last `timeout_secs` seconds.
    Returns the first https:// link found in the email body, or None.

    Runs synchronously (call via asyncio.to_thread).
    """
    import imaplib
    import email as email_lib
    import time
    import re as _re

    yahoo_email = os.getenv("YAHOO_EMAIL", "")
    app_password = os.getenv("YAHOO_APP_PASSWORD", "")
    if not yahoo_email or not app_password:
        log.warning("[email] Yahoo credentials not configured — cannot check confirmation email")
        return None

    deadline = time.time() + timeout_secs
    poll_interval = 15  # seconds between IMAP polls

    log.info("[email] Waiting up to %ds for confirmation email from domain %s", timeout_secs, domain)

    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
            mail.login(yahoo_email, app_password)
            mail.select("INBOX")

            # Search for recent unseen emails
            _, msg_ids = mail.search(None, "UNSEEN")
            ids = msg_ids[0].split()

            for msg_id in reversed(ids):  # newest first
                try:
                    _, data = mail.fetch(msg_id, "(RFC822)")
                    raw = data[0][1]
                    msg = email_lib.message_from_bytes(raw)

                    sender = msg.get("From", "").lower()
                    subject = msg.get("Subject", "").lower()

                    # Only consider emails from the ATS domain
                    domain_clean = domain.replace("www.", "").lower()
                    if domain_clean not in sender:
                        continue

                    # Check subject for confirmation keywords
                    confirm_keywords = (
                        "confirm", "verif", "activate", "verify",
                        "email confirm", "click to", "complete registration",
                    )
                    if not any(k in subject for k in confirm_keywords):
                        # Also accept if domain matches and body has a verify link
                        pass

                    # Extract body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct in ("text/plain", "text/html"):
                                payload = part.get_payload(decode=True)
                                if payload:
                                    charset = part.get_content_charset() or "utf-8"
                                    body += payload.decode(charset, errors="replace")
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            charset = msg.get_content_charset() or "utf-8"
                            body = payload.decode(charset, errors="replace")

                    # Find confirmation links — prefer ones with "confirm", "verify", "activate"
                    links = _re.findall(r'https?://[^\s\'"<>]+', body)
                    priority_link = next(
                        (l for l in links if any(k in l.lower() for k in (
                            "confirm", "verif", "activate", "token", "email",
                        ))),
                        None,
                    )
                    link = priority_link or (links[0] if links else None)

                    if link:
                        # Clean up trailing punctuation/quotes
                        link = link.rstrip(".,;:\"')")
                        log.info("[email] Found confirmation link from %s: %s", domain, link[:80])
                        mail.logout()
                        return link

                except Exception as e:
                    log.debug("[email] Error parsing email %s: %s", msg_id, e)

            mail.logout()
        except Exception as e:
            log.warning("[email] IMAP error: %s", e)

        remaining = deadline - time.time()
        if remaining > 0:
            log.info("[email] No confirmation email yet — waiting %ds (%.0fs remaining)",
                     poll_interval, remaining)
            time.sleep(min(poll_interval, remaining))

    log.warning("[email] Timed out waiting for confirmation email from %s", domain)
    return None


async def _escalate_to_computer_use(
    page: Page,
    candidate_info: dict,
    cover_letter: str,
    resume_pdf_path: str,
) -> bool:
    """
    Escalation entry point: try real macOS Computer Use first (visible on screen),
    fall back to the in-browser Vision loop if macOS CU is unavailable.
    """
    url = page.url
    try:
        from computer_use_macos import run_computer_use
        log.info("[CU] Escalating to macOS Computer Use for %s", url)
        return await run_computer_use(
            url=url,
            resume_pdf_path=resume_pdf_path,
            cover_letter=cover_letter,
            candidate_info=candidate_info,
            max_turns=50,
            open_browser=True,
        )
    except ImportError:
        log.warning("[CU] computer_use_macos not available — falling back to in-browser loop")
    except Exception as e:
        log.warning("[CU] macOS Computer Use error: %s — falling back to in-browser loop", e)

    # Fallback: old in-browser Vision loop
    return await _computer_use_loop(page, candidate_info, cover_letter, resume_pdf_path)


async def _computer_use_loop(
    page: Page,
    candidate_info: dict,
    cover_letter: str,
    resume_path: str,
    max_turns: int = 20,
) -> bool:
    """
    Claude Computer Use fallback: Vision loop that takes direct browser control.

    Each turn:
      1. Take a full screenshot of the current page.
      2. Ask Claude what single action to take next (JSON response).
      3. Execute that action via Playwright.
      4. Repeat until submitted or max turns exhausted.

    Action JSON format Claude must return:
    {
      "action": "click|type|press|scroll|select|upload|done|failed",
      "selector": "CSS selector (preferred over coords)",
      "x": 0, "y": 0,         // fallback coords if selector not found
      "text":  "...",          // for type action
      "key":   "Enter",        // for press action
      "delta_y": 300,          // for scroll (pixels, positive=down)
      "value": "...",          // for select_option
      "reason": "..."
    }
    """
    import os

    log.info("[CU] Starting Computer Use loop for %s", page.url)

    # Notify Telegram that CU is starting
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        try:
            screenshot_bytes = await page.screenshot(full_page=False)
            _telegram_send_photo(
                token, chat_id, screenshot_bytes,
                f"🤖 <b>Stuck on form — Claude Computer Use taking over</b>\n"
                f"<i>{page.url[:80]}</i>\n\n"
                f"I'll try to finish the application automatically."
            )
        except Exception:
            pass

    candidate_summary = json.dumps({
        k: v for k, v in candidate_info.items()
        if k in ("full_name", "first_name", "last_name", "email", "phone",
                 "city", "location", "linkedin", "github", "years_experience",
                 "authorized_to_work", "requires_sponsorship", "salary_expectation",
                 "availability", "visa_status")
    }, indent=2)

    prompt_template = f"""You are controlling a browser to fill a job application form.

Candidate info:
{candidate_summary}

Cover letter excerpt (use if a motivation/cover letter text field is visible):
{cover_letter[:400]}

Your job: decide the single best next action to advance the application toward submission.

Rules:
- Fill ONLY empty required fields (marked with * or clearly required).
- For file upload inputs (input[type=file]): use action="upload" — the system handles the file.
- After filling all visible fields, click the Next/Continue/Submit button.
- If you see a confirmation/thank-you page: action="done".
- If you are certain the form cannot be completed automatically: action="failed".
- Prefer CSS selector over coordinates — it's more reliable.
- For clicking buttons, use the exact visible text in the selector: button:has-text('Next').

Return ONLY valid JSON, no markdown:
{{
  "action": "click|type|press|scroll|select|upload|done|failed",
  "selector": "CSS selector or empty string",
  "x": 0,
  "y": 0,
  "text": "",
  "key": "",
  "delta_y": 0,
  "value": "",
  "reason": "one sentence"
}}"""

    for turn in range(max_turns):
        await asyncio.sleep(1.0)
        try:
            screenshot = await page.screenshot(full_page=False)
            b64 = base64.standard_b64encode(screenshot).decode()

            resp = await _client.messages.create(
                model=AI_CONFIG["model"],
                max_tokens=500,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt_template},
                ]}],
            )
            raw = next(b.text for b in resp.content if b.type == "text").strip()
            # Extract JSON object regardless of surrounding text or markdown fences
            j_start = raw.find("{")
            j_end = raw.rfind("}")
            if j_start != -1 and j_end > j_start:
                raw = raw[j_start:j_end + 1]
            act = json.loads(raw)

            action = act.get("action", "")
            selector = act.get("selector", "")
            reason = act.get("reason", "")
            log.info("[CU] turn=%d action=%r selector=%r reason=%s", turn + 1, action, selector, reason)

            if action == "done":
                log.info("[CU] Application submitted (Computer Use detected done).")
                return True

            if action == "failed":
                log.warning("[CU] Computer Use reports cannot complete: %s", reason)
                return False

            el = None
            if selector:
                try:
                    el = await page.query_selector(selector)
                    if el and not await el.is_visible():
                        el = None
                except Exception:
                    el = None

            if action == "click":
                if el:
                    await el.click()
                elif act.get("x") and act.get("y"):
                    await page.mouse.click(act["x"], act["y"])
                await asyncio.sleep(0.8)

            elif action == "type":
                text = act.get("text", "")
                if el:
                    await el.click()
                    await el.fill(text)
                elif act.get("x") and act.get("y"):
                    await page.mouse.click(act["x"], act["y"])
                    await page.keyboard.type(text, delay=30)
                await asyncio.sleep(0.4)

            elif action == "press":
                key = act.get("key", "Enter")
                if el:
                    await el.press(key)
                else:
                    await page.keyboard.press(key)
                await asyncio.sleep(0.8)

            elif action == "scroll":
                delta_y = act.get("delta_y", 300)
                await page.mouse.wheel(0, delta_y)
                await asyncio.sleep(0.5)

            elif action == "select":
                value = act.get("value", "")
                if el:
                    for v in (value, value.title(), value.upper()):
                        try:
                            await el.select_option(label=v)
                            break
                        except Exception:
                            try:
                                await el.select_option(value=v)
                                break
                            except Exception:
                                continue
                await asyncio.sleep(0.4)

            elif action == "upload":
                # Upload resume to the first file input found via selector or generic
                target = el
                if not target:
                    target = await page.query_selector("input[type='file']")
                if target and resume_path and Path(resume_path).exists():
                    try:
                        await target.set_input_files(resume_path)
                        await asyncio.sleep(1.0)
                        log.info("[CU] Uploaded resume via Computer Use")
                    except Exception as e:
                        log.warning("[CU] Upload failed: %s", e)

            # Check for confirmation after each action
            if await _check_confirmation(page):
                log.info("[CU] Confirmation detected after action.")
                return True

        except json.JSONDecodeError as e:
            log.warning("[CU] Bad JSON from Claude at turn %d (raw=%r...): %s", turn + 1, raw[:120] if 'raw' in dir() else '?', e)
        except Exception as e:
            log.warning("[CU] Error at turn %d: %s", turn + 1, e)

    log.warning("[CU] Exhausted %d turns without completing.", max_turns)
    return False


def _telegram_send_photo(token: str, chat_id: str, photo_bytes: bytes, caption: str):
    """Send a photo with caption to Telegram (synchronous, used in thread)."""
    boundary = "----CUBoundary7MA4YWxkTrZu0gW"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\";"
            f" filename=\"screen.png\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode() + photo_bytes,
        f"--{boundary}--".encode(),
    ]
    body = b"\r\n".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    urllib.request.urlopen(req, timeout=15)


async def _notify_stuck_telegram(page: Page, candidate_info: dict):
    """Send a final Telegram notification when Computer Use also failed."""
    import os
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
        await asyncio.to_thread(
            _telegram_send_photo, token, chat_id, screenshot_bytes,
            f"⚠️ <b>Could not auto-apply — manual action needed</b>\n"
            f"<b>URL:</b> <code>{page.url[:120]}</code>\n\n"
            f"Open the link and complete the application manually."
        )
    except Exception as e:
        log.warning("[form] Failed to send stuck notification: %s", e)


async def _click_submit(page: Page) -> bool:
    """Click the submit/apply button."""
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "a:has-text('Submit Application')",
        "button:has-text('Sign Up')",
        "button:has-text('Create Account')",
        "button:has-text('Register')",
        "button:has-text('Sign In')",
        "button:has-text('Log In')",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                log.info("[form] Clicked button: %s", sel)
                return True
        except Exception:
            pass
    return False


async def _click_next(page: Page, _already_clicked: set[str] | None = None) -> bool:
    """
    Click Next/Continue/Submit button to advance the form.
    `_already_clicked` tracks which fallback button texts were already used this session
    so we don't keep clicking the same pre-form CTA (e.g. "Apply here") repeatedly.
    """
    selectors = [
        # Standard navigation
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Proceed')",
        "button:has-text('Next Step')",
        "button:has-text('Review')",
        "button:has-text('Review Application')",
        # Submit / apply
        "button:has-text('Submit Application')",
        "button:has-text('Submit application')",
        "input[type='submit'][value*='Submit']",
        "input[type='submit'][value*='Apply']",
        "button[type='submit']",
        # Greenhouse-specific
        "button[data-qa='btn-submit']",
        "input[data-qa='btn-submit']",
        # Lever-specific
        "button.template-btn-submit",
        "[data-qa='btn-submit']",
        # Ashby-specific
        "button[class*='submit']",
        # Pre-form CTAs — only used when no form-nav button is found
        "button:has-text('Apply Now')",
        "button:has-text('Apply now')",
        "button:has-text('Apply Here')",
        "button:has-text('Apply here')",
        "button:has-text('Apply for this job')",
        "button:has-text('Apply for this position')",
        "a:has-text('Next')",
        "a:has-text('Continue')",
        # Get started / begin
        "button:has-text('Get Started')",
        "button:has-text('Start Application')",
        "button:has-text('Begin Application')",
        "button:has-text('Start')",
        "button:has-text('Begin')",
        # ARIA
        "button[aria-label*='next' i]",
        "button[aria-label*='continue' i]",
        "button[aria-label*='submit' i]",
        "[role='button']:has-text('Next')",
        "[role='button']:has-text('Continue')",
        "[role='button']:has-text('Submit')",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible() and await el.is_enabled():
                btn_text = (await el.inner_text()).strip()
                await el.click()
                log.info("[form] Clicked nav button via selector %r: %r", sel, btn_text)
                if _already_clicked is not None:
                    _already_clicked.add(btn_text.lower())
                return True
        except Exception:
            pass

    # Last resort fallback — skip upload-related words AND any button already clicked this session
    skip_words = {
        "back", "cancel", "close", "dismiss", "no", "skip", "later",
        "upload", "file", "choose", "browse", "attach", "drag", "drop",
        "select file", "upload file", "upload resume", "upload cv",
        "add file", "change file",
    }
    already = _already_clicked or set()
    try:
        buttons = await page.query_selector_all("button:visible, [role='button']:visible")
        for btn in buttons:
            text = (await btn.inner_text()).strip().lower()
            if not text:
                continue
            if any(w in text for w in skip_words):
                continue
            if text in already:
                continue  # don't re-click a pre-form CTA that didn't advance the form
            if not await btn.is_enabled():
                continue
            await btn.click()
            log.info("[form] Clicked fallback button: %r", text)
            if _already_clicked is not None:
                _already_clicked.add(text)
            return True
    except Exception:
        pass

    return False


async def _check_confirmation(page: Page) -> bool:
    """Check if a confirmation/thank-you page appeared."""
    content = (await page.content()).lower()
    keywords = [
        "thank you", "application submitted", "you've applied",
        "successfully applied", "application received", "we'll be in touch",
        "your application has been", "application complete",
    ]
    return any(kw in content for kw in keywords)
