"""
Checks Yahoo Mail for responses from companies we applied to.
Classifies each email with Claude, updates Google Sheets, and sends Telegram alerts
for positive responses (interview invites, follow-up questions).
Rejections are written to Google Sheets only — no Telegram notification.

Setup:
  1. Yahoo Mail → Settings → Security → Generate app password
     (Account Security → "Generate app password" — use "Other app")
  2. Add to .env:
       YAHOO_EMAIL=aidarbek.a@yahoo.com
       YAHOO_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
       EMAIL_CHECK_ENABLED=true

Yahoo IMAP: imap.mail.yahoo.com, port 993, SSL
"""

import asyncio
import email as email_lib
import imaplib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

import anthropic

import telegram_notifier as tg
from config import AI_CONFIG
from google_sheets import update_email_response, is_enabled as sheets_enabled

log = logging.getLogger("jobsearch")

IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "imap.mail.yahoo.com")
IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))

# How far back to check (days)
LOOKBACK_DAYS = 30

POSITIVE_CATEGORIES = {"interview_invite", "follow_up_question", "positive_other"}

_client = anthropic.AsyncAnthropic()


def is_enabled() -> bool:
    return (
        os.getenv("EMAIL_CHECK_ENABLED", "").lower() == "true"
        and bool(os.getenv("YAHOO_EMAIL"))
        and bool(os.getenv("YAHOO_APP_PASSWORD"))
    )


def _decode_header_value(value: str) -> str:
    parts = decode_header(value or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _get_email_body(msg) -> str:
    """Extract plain text body from email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    return body[:3000]


def _fetch_inbox_emails(since_days: int = LOOKBACK_DAYS) -> list[dict]:
    """Connect to Yahoo IMAP and fetch recent emails."""
    yahoo_email = os.getenv("YAHOO_EMAIL", "")
    app_password = os.getenv("YAHOO_APP_PASSWORD", "")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(yahoo_email, app_password)
        mail.select("INBOX")

        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        _, msg_ids = mail.search(None, f'(SINCE "{since_date}")')

        emails = []
        for msg_id in msg_ids[0].split():
            try:
                _, data = mail.fetch(msg_id, "(RFC822)")
                raw = data[0][1]
                msg = email_lib.message_from_bytes(raw)

                sender = _decode_header_value(msg.get("From", ""))
                subject = _decode_header_value(msg.get("Subject", ""))
                date_str = msg.get("Date", "")
                body = _get_email_body(msg)

                emails.append({
                    "id": msg_id.decode(),
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "body": body,
                })
            except Exception as e:
                log.warning("Failed to parse email id %s: %s", msg_id, e)

        mail.logout()
        log.info("[Email] Fetched %d emails from inbox (last %d days)", len(emails), since_days)
        return emails

    except Exception as e:
        log.error("[Email] IMAP connection failed: %s", e)
        return []


def _get_applied_companies(db_path: str = "jobs.db") -> list[dict]:
    """Returns all applied jobs with company name, platform, job_id."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT platform, job_id, title, company, url, applied_at
                FROM jobs
                WHERE status = 'applied'
                ORDER BY applied_at DESC
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error("[Email] DB read error: %s", e)
        return []


def _email_likely_from_company(email_sender: str, company_name: str) -> bool:
    """Rough check: does the sender domain match the company name?"""
    # Extract domain from sender
    domain_match = re.search(r"@([\w.-]+)", email_sender.lower())
    if not domain_match:
        return False
    domain = domain_match.group(1)

    # Normalize company name to simple words
    company_words = re.findall(r"\w+", company_name.lower())
    significant = [w for w in company_words if len(w) > 3 and w not in
                   {"inc", "corp", "llc", "ltd", "the", "and", "for", "with"}]

    return any(word in domain for word in significant)


async def _classify_email(subject: str, body: str, company: str, job_title: str) -> dict:
    """
    Use Claude to classify the email response.
    Returns: {category, summary, is_positive}
    """
    prompt = f"""You are reviewing a job application email response.

Company: {company}
Job title: {job_title}
Email subject: {subject}
Email body (excerpt):
{body}

Classify this email into ONE of these categories:
- interview_invite: They want to schedule an interview or call
- follow_up_question: They're asking for more info, test, or documents
- positive_other: Positive response that doesn't fit above (interest, moving forward, etc.)
- rejection: They're declining/rejecting the application
- auto_reply: Automated acknowledgement, no human review yet
- unrelated: Not related to this job application

Respond with JSON only:
{{
  "category": "one of the categories above",
  "summary": "one sentence describing what this email says",
  "confidence": "high|medium|low"
}}"""

    try:
        response = await _client.messages.create(
            model=AI_CONFIG["model"],
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        data["is_positive"] = data.get("category") in POSITIVE_CATEGORIES
        return data
    except Exception as e:
        log.error("[Email] Classification failed: %s", e)
        return {"category": "unknown", "summary": subject, "is_positive": False, "confidence": "low"}


async def check_emails(db_path: str = "jobs.db") -> int:
    """
    Main entry point. Fetches emails, matches to applied jobs,
    classifies responses, updates Sheets, sends Telegram for positives.
    Returns number of matches found.
    """
    if not is_enabled():
        log.info("[Email] Email check disabled or not configured.")
        return 0

    log.info("[Email] Starting email check...")
    emails = _fetch_inbox_emails()
    if not emails:
        log.info("[Email] No emails to process.")
        return 0

    applied_jobs = _get_applied_companies(db_path)
    if not applied_jobs:
        log.info("[Email] No applied jobs in DB yet.")
        return 0

    matches = 0
    already_seen = _load_seen_email_ids()

    for em in emails:
        if em["id"] in already_seen:
            continue

        for job in applied_jobs:
            if not _email_likely_from_company(em["sender"], job["company"]):
                continue

            log.info("[Email] Potential match: '%s' from '%s' for %s @ %s",
                     em["subject"], em["sender"], job["title"], job["company"])

            result = await _classify_email(
                em["subject"], em["body"], job["company"], job["title"]
            )

            log.info("[Email] Classification: %s — %s", result["category"], result["summary"])

            summary_text = f"{result['category'].replace('_', ' ').title()}: {result['summary']}"

            # Update Google Sheets
            if sheets_enabled():
                update_email_response(job["platform"], job["job_id"], summary_text)

            # Update DB notes
            _update_db_email_response(db_path, job["platform"], job["job_id"], summary_text)

            # Telegram alert only for positive responses; rejections → Sheets only
            if result["is_positive"]:
                category_label = {
                    "interview_invite": "Interview invite! 🗓",
                    "follow_up_question": "Follow-up question",
                    "positive_other": "Positive response",
                }.get(result["category"], "Positive response")

                await tg.send(
                    f"🎉 <b>{category_label}</b>\n"
                    f"<b>{job['title']}</b> @ {job['company']}\n"
                    f"Subject: {em['subject']}\n"
                    f"{result['summary']}\n"
                    f"<a href=\"{job['url']}\">Job posting</a> — check Gmail for details."
                )
                log.info("[Email] Telegram alert sent for positive response from %s", job["company"])
            else:
                log.info("[Email] %s from %s — written to Sheets, no Telegram alert",
                         result["category"], job["company"])

            matches += 1
            _save_seen_email_id(em["id"])
            break  # matched this email to one job, move on

    log.info("[Email] Email check complete. %d matches processed.", matches)
    return matches


def _load_seen_email_ids() -> set:
    path = Path("logs/seen_emails.txt")
    if path.exists():
        return set(path.read_text().splitlines())
    return set()


def _save_seen_email_id(email_id: str):
    path = Path("logs/seen_emails.txt")
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as f:
        f.write(email_id + "\n")


def _update_db_email_response(db_path: str, platform: str, job_id: str, summary: str):
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE jobs SET notes = ? WHERE platform = ? AND job_id = ?",
                (summary, platform, job_id),
            )
            conn.commit()
    except Exception as e:
        log.error("[Email] DB update error: %s", e)
