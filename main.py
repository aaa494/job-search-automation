"""
Job Search Automation — main entry point.

Flags:
  (none)          Prepare mode (default): find jobs, score, generate resume +
                  cover letter, upload to Drive, sync Sheets, send Telegram digest
  --prepare       Same as (none) — explicit prepare mode
  --dry-run       Full pipeline for 1 job but no DB write and no Drive upload
                  (safe for testing the AI + PDF generation)
  --search-only   Search + score all jobs, no file generation
  --report        Generate HTML report and open it
  --stats         Print stats table to terminal
  --login         Open browser to log in to platforms and save cookies (run once)
  --test          Quick scraper test: 1 platform, 1 job, ~30 sec, no AI
                  Options: --platform=linkedin|indeed|dice|weworkremotely  --title="SRE"

Platform filter flags (add to any mode):
  --linkedin  --indeed  --dice  --weworkremotely
"""

import asyncio
import json
import logging
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

# ── File logging ──────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
_log_path = Path("logs") / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
    ],
)
log = logging.getLogger("jobsearch")

from config import PATHS, PLATFORMS, SEARCH_CONFIG, is_blacklisted, is_job_blacklisted
from database import Database, Job
from pdf_generator import generate_pdf, save_cover_letter
from ai.job_matcher import score_job
from ai.resume_adapter import adapt_resume
from ai.cover_letter import generate_cover_letter
from scrapers.linkedin import LinkedInScraper
from scrapers.indeed import IndeedScraper
from scrapers.weworkremotely import WeWorkRemotelyScraper
from scrapers.dice import DiceScraper
from reporter import generate_report
from google_drive import upload_files_for_job, upload_report, is_enabled as drive_enabled
from google_sheets import (
    sync_all_jobs,
    update_job_links,
    is_enabled as sheets_enabled,
    apply_sheets_config,
)
from email_checker import check_emails
import telegram_notifier as tg

console = Console()

SCRAPER_MAP = {
    "linkedin":       LinkedInScraper,
    "indeed":         IndeedScraper,
    "weworkremotely": WeWorkRemotelyScraper,
    "dice":           DiceScraper,
}


def load_resume() -> dict:
    path = Path(PATHS["base_resume"])
    if not path.exists():
        console.print(f"[red]Resume not found: {path}[/red]")
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "_instructions" in data:
        console.print("[yellow]⚠ base_resume.json still contains template instructions. Fill in your real data first.[/yellow]")
    return data


def show_stats(db: Database):
    stats = db.get_stats()
    table = Table(title="Application Statistics")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    for status, cnt in sorted(stats.items()):
        color = {"applied": "green", "prepared": "magenta", "skipped": "dim",
                 "rejected": "red", "error": "yellow"}.get(status, "")
        table.add_row(f"[{color}]{status}[/{color}]" if color else status, str(cnt))
    console.print(table)

    recent = db.get_recent_applied(10)
    if recent:
        t2 = Table(title="Recent (last 10 prepared/applied)")
        t2.add_column("Platform")
        t2.add_column("Title")
        t2.add_column("Company")
        t2.add_column("Score", justify="right")
        t2.add_column("Found At")
        for r in recent:
            t2.add_row(
                r["platform"], r["title"], r["company"],
                f"{r['relevance_score']:.0f}",
                (r["applied_at"] or "")[:16],
            )
        console.print(t2)


def safe_filename(s: str, max_len: int = 60) -> str:
    return re.sub(r"[^\w\-]", "_", s)[:max_len]


async def process_job(
    job: Job,
    resume: dict,
    db: Database,
    mode: str,          # "prepare" | "dry_run" | "search_only"
    job_index: int,
    total: int,
) -> bool:
    """Score → adapt → cover letter → PDF → Drive upload → mark prepared. Returns True if prepared."""

    console.rule(f"[bold cyan]Job {job_index}/{total} — {job.platform}[/bold cyan]")
    log.info("Processing job %d/%d: %s @ %s (%s)", job_index, total, job.title, job.company, job.platform)

    # ── Company blacklist ─────────────────────────────────────────────────────
    bl_hit, bl_group = is_blacklisted(job.company)
    if bl_hit:
        console.print(f"[dim]↳ Skipped — {job.company} is in company blacklist ({bl_group})[/dim]\n")
        log.info("Company blacklisted: %s @ %s (group: %s)", job.title, job.company, bl_group)
        if mode != "dry_run":
            db.save_job(job)
            db.update_status(job, "skipped", notes=f"blacklisted:company:{bl_group}")
        return False

    # ── Job / position blacklist ──────────────────────────────────────────────
    jb_hit, jb_reason = is_job_blacklisted(job.title, job.description)
    if jb_hit:
        console.print(f"[dim]↳ Skipped — {job.title} ({jb_reason})[/dim]\n")
        log.info("Job blacklisted: %s @ %s — %s", job.title, job.company, jb_reason)
        if mode != "dry_run":
            db.save_job(job)
            db.update_status(job, "skipped", notes=f"blacklisted:job:{jb_reason}")
        return False

    # ── Score ─────────────────────────────────────────────────────────────────
    log.info("Scoring job with Claude...")
    with console.status("Scoring with Claude..."):
        try:
            score, reason = await score_job(job, resume)
        except Exception as e:
            console.print(f"[red]Scoring failed: {e}[/red]")
            log.exception("Scoring failed for %s @ %s", job.title, job.company)
            return False

    job.relevance_score = score
    job.relevance_reason = reason
    if mode != "dry_run":
        db.save_job(job)
    log.info("Score: %.0f/100 — %s", score, reason)

    color = "green" if score >= 80 else "yellow" if score >= 65 else "red"
    console.print(
        f"[bold]{job.title}[/bold] @ [cyan]{job.company}[/cyan]  "
        f"[{color}]{score:.0f}/100[/{color}]\n"
        f"  {reason}\n"
        f"  [dim]{job.url}[/dim]"
    )

    threshold = SEARCH_CONFIG["min_relevance_score"]
    if score < threshold:
        console.print(f"  [dim]↳ Skipped (below {threshold})[/dim]\n")
        if mode != "dry_run":
            db.update_status(job, "skipped")
        return False

    if mode == "search_only":
        return False

    # ── Duplicate company check ───────────────────────────────────────────────
    if mode != "dry_run" and SEARCH_CONFIG.get("skip_duplicate_companies") and db.company_applied(job.company):
        console.print(f"  [dim]↳ Skipped — already applied to {job.company}[/dim]\n")
        db.update_status(job, "skipped")
        return False

    # ── Adapt resume ──────────────────────────────────────────────────────────
    with console.status("Adapting resume with Claude..."):
        try:
            adapted = await adapt_resume(resume, job)
        except Exception as e:
            console.print(f"[red]Resume adaptation failed: {e}[/red]")
            return False

    console.print("\n[bold]Customized Summary:[/bold]")
    console.print(Panel(adapted["summary"], border_style="blue", padding=(0, 1)))

    # ── Cover letter ──────────────────────────────────────────────────────────
    console.print("\n[bold]Cover Letter:[/bold]")
    cl_chunks: list[str] = []

    def on_chunk(chunk: str):
        console.print(chunk, end="")
        cl_chunks.append(chunk)

    try:
        cover_letter = await generate_cover_letter(job, adapted, stream_callback=on_chunk)
    except Exception as e:
        console.print(f"\n[red]Cover letter failed: {e}[/red]")
        return False
    console.print()

    # ── Generate PDF ──────────────────────────────────────────────────────────
    out_dir = Path(PATHS["output_dir"])
    out_dir.mkdir(exist_ok=True)
    fname = safe_filename(f"{job.company}_{job.title}")
    ts = datetime.now().strftime("%m%d%y")
    pdf_path = str(out_dir / f"{fname}_{ts}.pdf")
    cl_path = str(out_dir / f"{fname}_{ts}_cover_letter.txt")

    with console.status("Generating PDF..."):
        try:
            await generate_pdf(adapted, pdf_path)
            save_cover_letter(cover_letter, cl_path)
        except Exception as e:
            console.print(f"[red]PDF generation failed: {e}[/red]")
            return False

    console.print(f"[green]✓[/green] Resume: [dim]{pdf_path}[/dim]")
    console.print(f"[green]✓[/green] Cover:  [dim]{cl_path}[/dim]")

    # ── Dry run stops here ────────────────────────────────────────────────────
    if mode == "dry_run":
        console.print("\n[yellow][DRY RUN] Files generated — no DB write, no Drive upload.[/yellow]\n")
        return False

    # ── Upload to Google Drive ────────────────────────────────────────────────
    pdf_link = cl_link = None
    if drive_enabled():
        with console.status("Uploading to Google Drive..."):
            pdf_link, cl_link = upload_files_for_job(pdf_path, cl_path)
        if pdf_link:
            console.print(f"[green]✓[/green] Drive resume: [dim]{pdf_link}[/dim]")
        if cl_link:
            console.print(f"[green]✓[/green] Drive cover:  [dim]{cl_link}[/dim]")

    # ── Mark as prepared ──────────────────────────────────────────────────────
    db.update_status(job, "prepared",
                     resume_path=pdf_path,
                     cover_letter_path=cl_path)
    if drive_enabled() and (pdf_link or cl_link):
        db.save_drive_links(job, pdf_link or "", cl_link or "")
    if sheets_enabled() and (pdf_link or cl_link):
        update_job_links(job.platform, job.job_id, pdf_link, cl_link)

    console.print(f"  [green]✓ Prepared[/green] — apply manually at: [dim]{job.url}[/dim]\n")
    return True


async def run(
    mode: str = "prepare",
    dry_run_limit: int = 1,
    platforms_filter: list[str] | None = None,
):
    console.print(Panel.fit(
        f"[bold cyan]Job Search Automation[/bold cyan]\n"
        f"Roles: [white]{', '.join(SEARCH_CONFIG['job_titles'][:4])} +more[/white]\n"
        f"Mode: [yellow]{mode}[/yellow]  |  Min Score: {SEARCH_CONFIG['min_relevance_score']}/100",
        border_style="cyan",
    ))

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY not set. Create a .env file from .env.example.[/red]")
        sys.exit(1)

    resume = load_resume()
    db = Database(PATHS["database"])

    # ── Scrape all platforms ──────────────────────────────────────────────────
    all_jobs: list[Job] = []
    seen_this_run: set[str] = set()
    enabled = {
        p: c for p, c in PLATFORMS.items()
        if c["enabled"] and p in SCRAPER_MAP
        and (platforms_filter is None or p in platforms_filter)
    }
    console.print(f"Platforms: {', '.join(enabled.keys())}\n")

    await tg.notify_run_started(list(enabled.keys()), SEARCH_CONFIG["job_titles"])

    for platform, cfg in enabled.items():
        scraper_cls = SCRAPER_MAP[platform]
        console.print(f"[cyan]Searching {platform}...[/cyan]", end=" ")
        log.info("=== Platform: %s ===", platform)
        found = new_count = 0

        async with scraper_cls() as scraper:
            titles = SEARCH_CONFIG["job_titles"]
            per_title = max(1, cfg["max_jobs_to_scrape"] // len(titles))
            for title in titles:
                console.print(f"\n  [dim]→ {title}[/dim]", end=" ")
                log.info("Searching '%s' on %s (max %d)", title, platform, per_title)
                title_count = 0
                try:
                    async for job in scraper.search_jobs(title, per_title):
                        found += 1
                        title_count += 1
                        job_key = f"{job.platform}:{job.job_id}"
                        if job_key in seen_this_run or db.is_seen(job.platform, job.job_id):
                            console.print(f"[dim]·[/dim]", end=" ")
                            log.debug("  seen %s @ %s", job.title, job.company)
                        else:
                            seen_this_run.add(job_key)
                            all_jobs.append(job)
                            new_count += 1
                            console.print(f"[green]✓[/green]", end=" ")
                            log.info("  NEW  %s @ %s  [%s]  %s", job.title, job.company, job.location, job.url)
                except Exception as e:
                    console.print(f"\n  [red]{platform} search error: {e}[/red]")
                    log.exception("Search error on %s / %s", platform, title)
                log.info("  title='%s' found=%d", title, title_count)
                if title_count == 0:
                    console.print(f"[dim](0)[/dim]", end="")

        console.print(f"\nFound {found}, [green]{new_count} new[/green]")
        log.info("Platform %s done: found=%d new=%d", platform, found, new_count)

    if not all_jobs:
        console.print("\n[yellow]No new jobs found. Try again later.[/yellow]")
        return

    console.print(f"\n[bold]Total new jobs to evaluate: {len(all_jobs)}[/bold]\n")

    # ── Process jobs ──────────────────────────────────────────────────────────
    prepared = 0
    limit = dry_run_limit if mode == "dry_run" else len(all_jobs)

    for i, job in enumerate(all_jobs[:limit], 1):
        ok = await process_job(job, resume, db, mode, i, min(limit, len(all_jobs)))
        if ok:
            prepared += 1

    # ── Final summary ─────────────────────────────────────────────────────────
    console.rule("[bold]Session Complete[/bold]")
    if mode == "prepare":
        console.print(f"Prepared: [bold green]{prepared}[/bold green] jobs ready for manual apply")
    show_stats(db)

    # Generate report
    report_drive_link = None
    try:
        report_path = generate_report()
        console.print(f"\n[green]Report:[/green] {report_path}")
        if drive_enabled():
            report_drive_link = upload_report(report_path)
            if report_drive_link:
                console.print(f"[green]Drive report:[/green] {report_drive_link}")
        if mode != "prepare":
            webbrowser.open(f"file://{Path(report_path).absolute()}")
    except Exception as e:
        console.print(f"[dim]Report error: {e}[/dim]")

    # Sync Google Sheets
    if sheets_enabled():
        with console.status("Syncing Google Sheets..."):
            try:
                sync_all_jobs(PATHS["database"])
                log.info("Google Sheets synced.")
            except Exception as e:
                console.print(f"[dim]Sheets sync error: {e}[/dim]")
                log.exception("Sheets sync failed")

    # Check email for responses
    try:
        email_matches = await check_emails(PATHS["database"])
        if email_matches:
            console.print(f"[green]Email check:[/green] {email_matches} response(s) found")
        log.info("Email check done: %d matches", email_matches)
    except Exception as e:
        console.print(f"[dim]Email check error: {e}[/dim]")
        log.exception("Email check failed")

    # Telegram: send digest in prepare mode, run summary otherwise
    stats = db.get_stats()
    if mode == "prepare":
        try:
            lookback = SEARCH_CONFIG.get("digest_lookback_days", 7)
            prepared_jobs = db.get_prepared_jobs(days=lookback)
            await tg.notify_daily_digest(prepared_jobs)
            log.info("Daily digest sent: %d prepared jobs", len(prepared_jobs))
        except Exception as e:
            console.print(f"[dim]Digest error: {e}[/dim]")
            log.exception("Daily digest failed")
    else:
        avg_row = "—"
        try:
            import sqlite3
            with sqlite3.connect(PATHS["database"]) as conn:
                row = conn.execute("SELECT AVG(relevance_score) FROM jobs WHERE status='applied'").fetchone()
                avg_row = f"{row[0]:.0f}" if row and row[0] else "—"
        except Exception:
            pass
        await tg.notify_run_complete(
            applied=prepared,
            found=stats.get("found", 0),
            skipped=stats.get("skipped", 0),
            avg_score=avg_row,
            report_link=report_drive_link,
        )


async def test_scraper(platform: str = "indeed", title: str = "DevOps Engineer"):
    """
    Quick scraper test: search 1 title on 1 platform, fetch 1 job detail, print result.
    No AI, no DB writes, no files. Completes in ~30 seconds.
    """
    scraper_cls = SCRAPER_MAP.get(platform)
    if not scraper_cls:
        console.print(f"[red]Unknown platform: {platform}. Choose from: {', '.join(SCRAPER_MAP)}[/red]")
        return

    console.print(f"\n[cyan]TEST:[/cyan] {platform} / '{title}' — fetching 1 job\n")
    log.info("test_scraper: platform=%s title=%s", platform, title)

    found = None
    async with scraper_cls() as scraper:
        try:
            async for job in scraper.search_jobs(title, max_results=1):
                found = job
                break
        except Exception as e:
            console.print(f"[red]Search error: {e}[/red]")
            log.exception("test_scraper search failed")
            return

    if not found:
        console.print("[yellow]No jobs found. Check the log for details:[/yellow]")
        console.print(f"  tail -f {_log_path}")
        return

    console.print(f"[green]✓ Found:[/green] [bold]{found.title}[/bold] @ {found.company}")
    console.print(f"  Location : {found.location or '(none)'}")
    console.print(f"  URL      : {found.url}")
    console.print(f"  Desc len : {len(found.description)} chars")
    console.print(f"\n[dim]Description preview:[/dim]")
    console.print(found.description[:400])
    console.print(f"\n[green]Scraper working correctly for {platform}.[/green]")
    log.info("test_scraper OK: %s @ %s desc_len=%d", found.title, found.company, len(found.description))


async def do_login(platforms: list[str]):
    """Open a visible browser for each platform, let the user log in, save cookies."""
    from config import BROWSER_CONFIG
    import config as _cfg

    original_headless = BROWSER_CONFIG["headless"]
    _cfg.BROWSER_CONFIG["headless"] = False

    login_scrapers = {
        "linkedin": LinkedInScraper,
        "indeed":   IndeedScraper,
    }
    for name in platforms:
        cls = login_scrapers.get(name)
        if not cls:
            console.print(f"[dim]{name}: no login needed[/dim]")
            continue
        console.print(f"\n[cyan]Opening browser for {name}...[/cyan]")
        async with cls() as scraper:
            page = await scraper.new_page()
            if name == "linkedin":
                await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            elif name == "indeed":
                await page.goto("https://secure.indeed.com/auth", wait_until="domcontentloaded")
            console.print(f"  Log in to [bold]{name}[/bold] in the browser window.")
            input(f"  Press ENTER when done: ")
            await scraper._save_cookies()
            await page.close()
        console.print(f"  [green]✓[/green] Cookies saved for {name}")

    _cfg.BROWSER_CONFIG["headless"] = original_headless


_FLAG_TO_PLATFORM = {f"--{p}": p for p in ["linkedin", "indeed", "dice", "weworkremotely"]}


def _parse_platform_filter(args: set) -> list[str] | None:
    selected = [_FLAG_TO_PLATFORM[a] for a in args if a in _FLAG_TO_PLATFORM]
    return selected if selected else None


def main():
    args = set(sys.argv[1:])

    console.print(f"[dim]Log: {_log_path.absolute()}[/dim]")
    log.info("Run started: %s", " ".join(sys.argv))

    # Pull settings from Google Sheets if enabled
    if sheets_enabled():
        try:
            apply_sheets_config()
            log.info("Config loaded from Google Sheets.")
        except Exception as e:
            console.print(f"[dim]Sheets config load skipped: {e}[/dim]")

    if "--stats" in args:
        db = Database(PATHS["database"])
        show_stats(db)
        return

    if "--login" in args:
        platforms = [a for a in sys.argv[1:] if not a.startswith("--")]
        if not platforms:
            platforms = ["linkedin", "indeed"]
        asyncio.run(do_login(platforms))
        return

    if "--report" in args:
        path = generate_report()
        console.print(f"[green]Report generated:[/green] {path}")
        webbrowser.open(f"file://{Path(path).absolute()}")
        return

    if "--test" in args:
        platform = next((a.split("=")[1] for a in args if a.startswith("--platform=")), "indeed")
        title = next((a.split("=")[1] for a in args if a.startswith("--title=")), "DevOps Engineer")
        asyncio.run(test_scraper(platform, title))
        return

    if "--dry-run" in args:
        console.print("[yellow]DRY RUN MODE — 1 job, no DB write, no Drive upload.[/yellow]")
        asyncio.run(run(mode="dry_run", dry_run_limit=1))
        return

    if "--search-only" in args:
        asyncio.run(run(mode="search_only"))
        return

    # Default: prepare mode
    pf = _parse_platform_filter(args)
    asyncio.run(run(mode="prepare", platforms_filter=pf))


if __name__ == "__main__":
    main()
