"""
Daily scheduler — runs the job search automatically every day.

Usage:
  python scheduler.py             # schedule daily runs at time set in config.py
  python scheduler.py --now       # run immediately, then schedule
  python scheduler.py --startup   # used by macOS auto-start:
                                  #   checks if today's run was missed while laptop
                                  #   was off → runs immediately if so, then schedules

The scheduler saves a timestamp after each run to last_run.txt.
On --startup it reads that file to detect missed runs.
"""

import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path

import schedule
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

from config import SCHEDULER_CONFIG, PATHS
from reporter import generate_report
import telegram_notifier as tg
import asyncio

console = Console()

LAST_RUN_FILE = Path("last_run.txt")


def save_last_run():
    LAST_RUN_FILE.write_text(datetime.now().isoformat())


def was_missed_today() -> bool:
    """Returns True if the scheduled run didn't happen yet today."""
    run_time = SCHEDULER_CONFIG.get("run_at", "09:00")
    hour, minute = map(int, run_time.split(":"))
    scheduled_today = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Not yet time today — nothing missed
    if datetime.now() < scheduled_today:
        return False

    # Check last recorded run
    if not LAST_RUN_FILE.exists():
        return True  # Never ran before

    try:
        last = datetime.fromisoformat(LAST_RUN_FILE.read_text().strip())
        return last.date() < date.today()
    except Exception:
        return True


def run_job_search(reason: str = "scheduled"):
    console.rule(f"[bold cyan]Job Search Run — {datetime.now().strftime('%Y-%m-%d %H:%M')} ({reason})[/bold cyan]")

    result = subprocess.run(
        [sys.executable, "main.py", "--auto"],
        capture_output=False,
    )

    save_last_run()

    try:
        generate_report()
    except Exception as e:
        console.print(f"[red]Report error: {e}[/red]")

    run_time = SCHEDULER_CONFIG.get("run_at", "09:00")
    console.print(f"[dim]Next run: {run_time} tomorrow[/dim]\n")


def main():
    run_time = SCHEDULER_CONFIG.get("run_at", "09:00")
    startup_mode = "--startup" in sys.argv

    console.print(f"[cyan]Job Search Scheduler — daily at {run_time}[/cyan]")

    if startup_mode:
        # macOS just booted (or app just started). Check if we missed today's run.
        asyncio.run(tg.send(
            "💻 <b>Laptop is online.</b>\n"
            f"Scheduler started — daily run at {run_time}."
        ))

        if was_missed_today():
            console.print("[yellow]Missed run detected — starting now.[/yellow]")
            asyncio.run(tg.send(
                f"⏰ <b>Missed run detected.</b>\n"
                f"The {run_time} job search did not run (laptop was off).\n"
                "Starting now..."
            ))
            run_job_search(reason="missed run — laptop was off")
        else:
            console.print("[green]Today's run already completed. Waiting for tomorrow.[/green]")

    elif "--now" in sys.argv:
        run_job_search(reason="manual --now")

    schedule.every().day.at(run_time).do(run_job_search)

    console.print(f"[dim]Waiting for next run at {run_time}...[/dim]\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
