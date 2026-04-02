"""
Generic employer website form filler using Claude Vision.

Flow:
  1. Navigate to the application URL
  2. Take a screenshot
  3. Claude Vision analyzes the form fields
  4. Fill fields programmatically
  5. Repeat for multi-step forms
  6. Submit when ready
"""

import asyncio
import base64
import json
import anthropic
from playwright.async_api import Page

from config import AI_CONFIG

_client = anthropic.AsyncAnthropic()


async def fill_employer_form(
    page: Page,
    resume_data: dict,
    cover_letter: str,
    resume_pdf_path: str,
    max_steps: int = 12,
) -> bool:
    """
    Returns True if the form was submitted (or user confirmed manual completion).
    """
    p = resume_data["personal"]

    # Pre-build common field answers so Claude can reference them
    candidate_info = {
        "full_name": p["name"],
        "first_name": p["name"].split()[0],
        "last_name": " ".join(p["name"].split()[1:]),
        "email": p["email"],
        "phone": p["phone"],
        "location": p["location"],
        "linkedin": p.get("linkedin", ""),
        "github": p.get("github", ""),
        "cover_letter_excerpt": cover_letter[:800],
        "years_experience": "7",
        "authorized_to_work": "Yes — Green Card Holder",
        "requires_sponsorship": "No",
        "salary_expectation": "120000-160000",
        "availability": "2 weeks notice",
        "preferred_work": "Remote",
    }

    for step in range(max_steps):
        await asyncio.sleep(1.5)

        screenshot_bytes = await page.screenshot(full_page=False)
        b64 = base64.standard_b64encode(screenshot_bytes).decode()

        # Ask Claude to analyze the current state
        analysis = await _analyze_form(b64, candidate_info)

        action = analysis.get("action", "unknown")

        if action == "already_applied":
            print("  [form] Already applied to this job.")
            return False

        if action == "login_required":
            print("  [form] Login required — please log in manually.")
            input("  Press ENTER after logging in: ")
            continue

        if action == "completed":
            print("  [form] Application submitted successfully!")
            return True

        # Fill detected fields
        fields = analysis.get("fields", [])
        for field in fields:
            await _fill_field(page, field, resume_pdf_path, cover_letter)
            await asyncio.sleep(0.4)

        # Handle file upload for resume
        if analysis.get("has_file_upload"):
            await _upload_resume(page, resume_pdf_path)

        # Submit or go next
        if action == "submit" and analysis.get("has_submit"):
            submitted = await _click_submit(page)
            if submitted:
                await asyncio.sleep(2)
                # Check for confirmation
                confirm = await _check_confirmation(page)
                if confirm:
                    return True
                # Take another screenshot to verify
                continue

        elif action in ("next", "continue", "fill_and_next"):
            clicked = await _click_next(page)
            if not clicked:
                # No next button found — might need manual help
                break

        else:
            # Unknown state — ask user
            print(f"\n  [form] Unclear state at step {step + 1}. Current URL: {page.url}")
            response = input("  Type 'done' if submitted, 'skip' to cancel, or ENTER to retry: ").strip().lower()
            if response == "done":
                return True
            if response == "skip":
                return False

    # Fallback: ask user to finish manually
    print(f"\n  [form] Could not fully automate this form. Please complete manually.")
    print(f"  URL: {page.url}")
    answer = input("  Type 'done' when submitted, or 'skip' to cancel: ").strip().lower()
    return answer == "done"


async def _analyze_form(screenshot_b64: str, candidate_info: dict) -> dict:
    """Call Claude Vision to analyze the current form state."""
    info_str = json.dumps(candidate_info, indent=2)

    prompt = f"""You are analyzing a job application form screenshot.

Candidate information to use for filling:
{info_str}

Analyze the screenshot and return JSON describing the current form state:
{{
  "action": "fill_and_next|submit|completed|login_required|already_applied|unknown",
  "has_submit": true/false,
  "has_file_upload": true/false,
  "page_description": "brief description of what you see",
  "fields": [
    {{
      "description": "human-readable description of the field",
      "how_to_find": "text_content|placeholder|label|aria_label",
      "find_value": "the text to find the element by",
      "element_type": "input_text|input_email|input_tel|textarea|select|checkbox|radio",
      "value_to_fill": "the value from candidate info that fits this field"
    }}
  ]
}}

Rules:
- Only include fields that are EMPTY and need to be filled
- For file upload fields, just set has_file_upload=true (handled separately)
- If you see a "Thank you" confirmation, set action to "completed"
- If you see a submit/apply button as the main CTA, set action to "submit"
- If multiple pages, set action to "fill_and_next"
- For textarea asking about motivation/cover letter, use the cover_letter_excerpt from candidate info
- For "authorized to work in US?": use authorized_to_work value
- For "require sponsorship?": use requires_sponsorship value

Return ONLY the JSON, no markdown."""

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

    text = next(b.text for b in response.content if b.type == "text")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"action": "unknown", "fields": [], "has_submit": False, "has_file_upload": False}


async def _fill_field(page: Page, field: dict, resume_path: str, cover_letter: str):
    """Attempt to fill a single form field."""
    how = field.get("how_to_find", "")
    val = field.get("find_value", "")
    elem_type = field.get("element_type", "input_text")
    fill_value = field.get("value_to_fill", "")

    if not val or not fill_value:
        return

    selectors = [
        f'[placeholder="{val}"]',
        f'[aria-label="{val}"]',
        f'[name="{val}"]',
        f'[id="{val}"]',
        f'label:has-text("{val}") + input',
        f'label:has-text("{val}") + textarea',
        f'label:has-text("{val}") ~ input',
        f'text="{val}"',
    ]

    el = None
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                break
        except Exception:
            continue

    if not el:
        return

    try:
        if elem_type in ("input_text", "input_email", "input_tel"):
            await el.fill(fill_value)
        elif elem_type == "textarea":
            await el.fill(fill_value)
        elif elem_type == "select":
            await el.select_option(label=fill_value)
        elif elem_type == "checkbox":
            if fill_value.lower() in ("yes", "true", "1", "checked"):
                await el.check()
        elif elem_type == "radio":
            await el.click()
    except Exception:
        pass


async def _upload_resume(page: Page, resume_path: str):
    """Find a file input and upload the resume PDF."""
    inputs = await page.query_selector_all("input[type='file']")
    for inp in inputs:
        accept = await inp.get_attribute("accept") or ""
        if "pdf" in accept.lower() or accept == "" or "resume" in (await inp.get_attribute("name") or "").lower():
            try:
                await inp.set_input_files(resume_path)
                await asyncio.sleep(1)
                return
            except Exception:
                pass


async def _click_submit(page: Page) -> bool:
    """Click the submit/apply button."""
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "a:has-text('Submit Application')",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                return True
        except Exception:
            pass
    return False


async def _click_next(page: Page) -> bool:
    """Click Next/Continue button."""
    selectors = [
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Proceed')",
        "a:has-text('Next')",
        "button[aria-label*='next']",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                return True
        except Exception:
            pass
    return False


async def _check_confirmation(page: Page) -> bool:
    """Check if a confirmation/thank-you page appeared."""
    content = (await page.content()).lower()
    keywords = ["thank you", "application submitted", "you've applied", "successfully applied",
                 "application received", "we'll be in touch"]
    return any(kw in content for kw in keywords)
