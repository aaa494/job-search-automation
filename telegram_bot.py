"""
Telegram bot — listens for commands and responds.

Run once to register commands with BotFather (shows dropdown in Telegram):
  python telegram_bot.py --register

Run as a background process alongside the scheduler:
  python telegram_bot.py

Available commands (shown as dropdown when user types /):
  /helpjob  — list all commands
  /stats    — application statistics
  /run      — start a job search now (auto mode)
  /stop     — stop an in-progress run
  /report   — latest run report
  /status   — is the scheduler running?
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = "jobs.db"

# Commands shown in Telegram's / dropdown
COMMANDS = [
    ("helpjob", "List all commands"),
    ("applied", "Mark a job as applied — /applied CompanyName"),
    ("test",    "Find 1 real job and apply now (test mode)"),
    ("run",     "Start full job search for today"),
    ("stop",    "Stop the current run"),
    ("stats",   "Application stats and recent jobs"),
    ("report",  "Latest run summary"),
    ("status",  "Is the scheduler running?"),
]

_run_process: subprocess.Popen | None = None


# ── Telegram API helpers ────────────────────────────────────────────────────

def _api(method: str, payload: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[Bot] API error ({method}): {e}")
        return {}


def send(text: str) -> None:
    _api("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def get_updates(offset: int = 0, timeout: int = 30) -> list:
    result = _api("getUpdates", {"offset": offset, "timeout": timeout})
    return result.get("result", [])


def register_commands() -> None:
    """Register commands with BotFather — makes the / dropdown appear in Telegram."""
    commands = [{"command": cmd, "description": desc} for cmd, desc in COMMANDS]
    result = _api("setMyCommands", {"commands": commands})
    if result.get("result"):
        print("[Bot] Commands registered. Open Telegram and type / in your bot chat to see them.")
    else:
        print(f"[Bot] Failed to register commands: {result}")


# ── Command handlers ────────────────────────────────────────────────────────

def handle_applied(args: str) -> str:
    """
    Mark a prepared job as applied.
    Usage: /applied CompanyName
    Searches prepared jobs for a company name match (case-insensitive, partial).
    Updates DB status → 'applied' and syncs to Google Sheets.
    """
    company_query = args.strip()
    if not company_query:
        return (
            "Usage: <code>/applied CompanyName</code>\n"
            "Example: <code>/applied DraftKings</code>\n\n"
            "This marks the job as applied in the database and Google Sheets."
        )

    if not Path(DB_PATH).exists():
        return "No database found yet."

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT platform, job_id, title, company, url
                FROM jobs
                WHERE status = 'prepared'
                  AND LOWER(company) LIKE ?
                ORDER BY created_at DESC
            """, (f"%{company_query.lower()}%",)).fetchall()

        if not rows:
            # Show recent prepared jobs to help user
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                recent = conn.execute("""
                    SELECT company, title FROM jobs
                    WHERE status = 'prepared'
                    ORDER BY created_at DESC LIMIT 10
                """).fetchall()
            if recent:
                names = "\n".join(f"• {r['company']} — {r['title']}" for r in recent)
                return f"No prepared job found matching <b>{company_query}</b>.\n\nPrepared jobs:\n{names}"
            return f"No prepared jobs found matching <b>{company_query}</b>."

        if len(rows) > 1:
            names = "\n".join(f"• {r['company']} — {r['title']}" for r in rows)
            return (
                f"Found {len(rows)} matches for <b>{company_query}</b>. Be more specific:\n{names}"
            )

        job = rows[0]
        applied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                UPDATE jobs SET status = 'applied', applied_at = ?
                WHERE platform = ? AND job_id = ?
            """, (applied_at, job["platform"], job["job_id"]))
            conn.commit()

        # Sync to Google Sheets if enabled
        sheets_msg = ""
        try:
            from google_sheets import update_job_status, is_enabled as sheets_enabled
            if sheets_enabled():
                update_job_status(job["platform"], job["job_id"], "applied", applied_at)
                sheets_msg = "\n✅ Google Sheets updated."
        except Exception as e:
            sheets_msg = f"\n⚠️ Sheets sync failed: {e}"

        return (
            f"✅ Marked as <b>applied</b>:\n"
            f"<b>{job['title']}</b> @ {job['company']}\n"
            f"Applied: {applied_at[:10]}"
            f"{sheets_msg}\n\n"
            f"<a href=\"{job['url']}\">Job posting</a>"
        )

    except Exception as e:
        return f"Error: {e}"


def handle_helpjob() -> str:
    lines = ["<b>Available commands:</b>\n"]
    for cmd, desc in COMMANDS:
        lines.append(f"/{cmd} — {desc}")
    return "\n".join(lines)


def handle_stats() -> str:
    if not Path(DB_PATH).exists():
        return "No database found yet. Run /run first."
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            stats = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
            ).fetchall()
            stats_dict = {r["status"]: r["cnt"] for r in stats}

            avg = conn.execute(
                "SELECT AVG(relevance_score) FROM jobs WHERE status='applied'"
            ).fetchone()[0]

            recent = conn.execute("""
                SELECT title, company, relevance_score, applied_at
                FROM jobs WHERE status='applied'
                ORDER BY applied_at DESC LIMIT 5
            """).fetchall()

        lines = [
            "<b>Application Statistics</b>\n",
            f"✅ Applied:  <b>{stats_dict.get('applied', 0)}</b>",
            f"🔍 Found:    {stats_dict.get('found', 0)}",
            f"⏭ Skipped:  {stats_dict.get('skipped', 0)}",
            f"❌ Rejected: {stats_dict.get('rejected', 0)}",
            f"⚠️ Errors:   {stats_dict.get('error', 0)}",
            f"📊 Avg score (applied): {f'{avg:.0f}' if avg else '—'}",
        ]
        if recent:
            lines.append("\n<b>Last 5 applications:</b>")
            for r in recent:
                date = (r["applied_at"] or "")[:10]
                lines.append(f"• {r['title']} @ {r['company']}  [{r['relevance_score']:.0f}]  {date}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading stats: {e}"


def _start_run(args: list[str], label: str) -> str:
    global _run_process
    if _run_process and _run_process.poll() is None:
        return "⚠️ A run is already in progress. Use /stop to cancel it first."
    try:
        venv_python = Path(".venv/bin/python")
        python = str(venv_python) if venv_python.exists() else sys.executable
        _run_process = subprocess.Popen(
            [python, "main.py"] + args,
            cwd=Path(__file__).parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return (
            f"🚀 <b>{label}</b>\n"
            "You'll get a notification for each match and application.\n"
            "Use /stop to cancel."
        )
    except Exception as e:
        return f"❌ Failed to start: {e}"


def handle_test() -> str:
    return _start_run(
        ["--auto", "--limit=1"],
        "Test run started — finding and applying to 1 job.",
    )


def handle_run() -> str:
    return _start_run(
        ["--auto"],
        "Full job search started — up to 8 applications.",
    )


def handle_stop() -> str:
    global _run_process
    if _run_process is None or _run_process.poll() is not None:
        return "No run is currently in progress."
    try:
        _run_process.terminate()
        _run_process = None
        return "🛑 Job search stopped."
    except Exception as e:
        return f"Error stopping: {e}"


def handle_report() -> str:
    if not Path(DB_PATH).exists():
        return "No data yet. Use /run to start a job search."
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0]
            last = conn.execute(
                "SELECT applied_at FROM jobs WHERE status='applied' ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()

        last_date = (last["applied_at"] or "never")[:16] if last else "never"
        reports = sorted(Path("reports").glob("report_*.html"), reverse=True) if Path("reports").exists() else []
        report_info = f"\nLatest local report: <code>{reports[0].name}</code>" if reports else ""

        return (
            f"<b>Latest Report</b>\n"
            f"Total jobs tracked: {total}\n"
            f"Total applied: <b>{applied}</b>\n"
            f"Last application: {last_date}"
            f"{report_info}"
        )
    except Exception as e:
        return f"Error: {e}"


def handle_status() -> str:
    global _run_process
    run_status = "▶️ Running" if (_run_process and _run_process.poll() is None) else "⏸ Idle"

    scheduler_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "scheduler.py"], capture_output=True)
        scheduler_running = result.returncode == 0
    except Exception:
        pass

    return (
        f"<b>System Status</b>\n"
        f"Job search: {run_status}\n"
        f"Scheduler: {'✅ Running' if scheduler_running else '⏸ Not running'}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


# ── Dispatch ────────────────────────────────────────────────────────────────

HANDLERS = {
    "/helpjob": (handle_helpjob, False),
    "/start":   (handle_helpjob, False),
    "/help":    (handle_helpjob, False),
    "/applied": (handle_applied, True),   # True = passes remaining text as args
    "/test":    (handle_test,    False),
    "/run":     (handle_run,     False),
    "/stop":    (handle_stop,    False),
    "/stats":   (handle_stats,   False),
    "/report":  (handle_report,  False),
    "/status":  (handle_status,  False),
}


def dispatch(text: str) -> str:
    text = text.strip()
    # Strip bot username suffix (e.g. /stats@MyJobBot → /stats)
    parts = text.split(" ", 1)
    cmd = parts[0].split("@")[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    entry = HANDLERS.get(cmd)
    if not entry:
        return f"Unknown command: {cmd}\nType /helpjob to see available commands."
    handler, takes_args = entry
    return handler(args) if takes_args else handler()


# ── Main polling loop ───────────────────────────────────────────────────────

def main():
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        sys.exit(1)

    if "--register" in sys.argv:
        register_commands()
        return

    # Register commands on start so the dropdown is always up to date
    register_commands()

    print(f"[Bot] Polling for commands... (CHAT_ID={CHAT_ID})")
    send("🤖 <b>Job Search Bot online.</b>\nType /helpjob to see commands.")

    offset = 0
    while True:
        try:
            updates = get_updates(offset=offset, timeout=30)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                # Only respond to the configured chat
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != CHAT_ID:
                    continue

                text = msg.get("text", "")
                if not text.startswith("/"):
                    continue

                print(f"[Bot] Command: {text}")
                reply = dispatch(text)
                send(reply)

        except KeyboardInterrupt:
            print("\n[Bot] Stopped.")
            break
        except Exception as e:
            print(f"[Bot] Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
