"""
Telegram notifications for job search events.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Message your new bot once (any text)
  3. Open https://api.telegram.org/bot<TOKEN>/getUpdates
     → find "chat":{"id": <YOUR_CHAT_ID>}
  4. Add to .env:
       TELEGRAM_BOT_TOKEN=123456:ABCdef...
       TELEGRAM_CHAT_ID=123456789
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime


def _is_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID"))


def _post(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[Telegram] Failed to send notification: {e}")


async def send(text: str) -> None:
    """Send a Telegram message. Silently skips if not configured."""
    if not _is_configured():
        return
    await asyncio.to_thread(_post, text)


# ── Convenience helpers ────────────────────────────────────────────────────────

async def notify_run_started(platforms: list[str], titles: list[str]) -> None:
    await send(
        f"🚀 <b>Job search started</b>\n"
        f"Platforms: {', '.join(platforms)}\n"
        f"Roles: {', '.join(titles[:4])}"
        + (" +more" if len(titles) > 4 else "")
    )


async def notify_match_found(title: str, company: str, platform: str, score: float, reason: str, url: str) -> None:
    await send(
        f"✅ <b>Match found ({score:.0f}/100)</b>\n"
        f"<b>{title}</b> @ {company}\n"
        f"Platform: {platform}\n"
        f"{reason}\n"
        f"<a href=\"{url}\">{url}</a>"
    )


async def notify_applied(title: str, company: str, platform: str, pdf_link: str = None) -> None:
    msg = (
        f"📨 <b>Application submitted!</b>\n"
        f"<b>{title}</b> @ {company}\n"
        f"Platform: {platform}"
    )
    if pdf_link:
        msg += f"\n📄 <a href=\"{pdf_link}\">Resume on Drive</a>"
    await send(msg)


async def notify_manual_needed(title: str, company: str, url: str) -> None:
    await send(
        f"⚠️ <b>Needs manual apply</b>\n"
        f"<b>{title}</b> @ {company}\n"
        f"Auto-apply failed — files generated, submit manually:\n"
        f"<a href=\"{url}\">{url}</a>"
    )


async def notify_run_complete(applied: int, found: int, skipped: int, avg_score: str = "—", report_link: str = None) -> None:
    msg = (
        f"📊 <b>Run complete</b> — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"✅ Applied: <b>{applied}</b>\n"
        f"🔍 Found: {found}   ⏭ Skipped: {skipped}\n"
        f"Avg score (applied): {avg_score}"
    )
    if report_link:
        msg += f"\n📋 <a href=\"{report_link}\">Open report</a>"
    await send(msg)


async def notify_error(context: str, error: str) -> None:
    await send(
        f"❌ <b>Error</b> in {context}\n"
        f"<code>{error[:300]}</code>"
    )


async def notify_daily_digest(jobs: list[dict]) -> None:
    """
    Send the morning digest: all prepared jobs waiting for manual apply.
    Each job dict: title, company, url, relevance_score, resume_drive_link,
                   cover_letter_drive_link, created_at.
    """
    if not jobs:
        await send(
            f"☀️ <b>Morning digest — {datetime.now().strftime('%Y-%m-%d')}</b>\n"
            "No new jobs ready for manual apply today."
        )
        return

    lines = [f"☀️ <b>Morning digest — {datetime.now().strftime('%Y-%m-%d')}</b>\n"
             f"{len(jobs)} job(s) ready for you to apply:\n"]

    for i, job in enumerate(jobs, 1):
        score = f"{job['relevance_score']:.0f}" if job.get("relevance_score") else "—"
        line = f"<b>{i}. {job['title']}</b> @ {job['company']}  [{score}/100]\n"
        if job.get("url"):
            line += f"   🔗 <a href=\"{job['url']}\">Job posting</a>\n"
        if job.get("resume_drive_link"):
            line += f"   📄 <a href=\"{job['resume_drive_link']}\">Resume (Drive)</a>\n"
        if job.get("cover_letter_drive_link"):
            line += f"   ✉️ <a href=\"{job['cover_letter_drive_link']}\">Cover letter</a>\n"
        lines.append(line)

    # Telegram message limit is 4096 chars — split if needed
    full_msg = "\n".join(lines)
    if len(full_msg) <= 4096:
        await send(full_msg)
    else:
        # Send header + first chunk, then remaining jobs
        await send(lines[0])
        chunk: list[str] = []
        for line in lines[1:]:
            chunk.append(line)
            if sum(len(l) for l in chunk) > 3500:
                await send("\n".join(chunk))
                chunk = []
        if chunk:
            await send("\n".join(chunk))
