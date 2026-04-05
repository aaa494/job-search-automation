"""
Job Search Automation — main entry point.

Flags:
  (none)             Prepare mode: find jobs, score, generate resume+cover letter,
                     upload to Drive, sync Sheets — NO auto-apply (default)
  --prepare          Same as (none) — explicit prepare mode
  --dry-run          Search + score + generate ONE resume/PDF/cover-letter, no submitting
  --dry-run-apply    Like --dry-run but applies 1 job automatically
  --search-only      Search + score all jobs, no applying, no file generation
  --auto             Fully automatic apply (no review prompts) — legacy scheduler use
  --review           Full run with manual review before each application
  --report           Generate HTML report and open it
  --stats            Print stats table to terminal
  --login            Open browser to log in to platforms and save cookies (run once)
  --test             Quick scraper test: 1 platform, 1 title, 1 job, ~30 sec, no AI
                     Options: --platform=linkedin|indeed|dice|weworkremotely  --title="SRE"
  --test-form=URL    Test just the form filler against any URL (opens browser, fills form)
                     Example: python main.py --test-form=https://jobs.example.com/apply/123
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
from rich.prompt import Prompt
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

from config import AI_CONFIG, PATHS, PLATFORMS, SEARCH_CONFIG, is_blacklisted, is_job_blacklisted
from database import Database, Job
from pdf_generator import generate_pdf, save_cover_letter
from ai.job_matcher import score_job
from ai.resume_adapter import adapt_resume
from ai.cover_letter import generate_cover_letter
from scrapers.linkedin import LinkedInScraper
from scrapers.indeed import IndeedScraper
from scrapers.weworkremotely import WeWorkRemotelyScraper
from scrapers.dice import DiceScraper
from scrapers.employer_site import fill_employer_form
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
        color = {"applied": "green", "skipped": "dim", "rejected": "red", "error": "yellow"}.get(status, "")
        table.add_row(f"[{color}]{status}[/{color}]" if color else status, str(cnt))
    console.print(table)

    recent = db.get_recent_applied(10)
    if recent:
        t2 = Table(title="Recent Applications (last 10)")
        t2.add_column("Platform")
        t2.add_column("Title")
        t2.add_column("Company")
        t2.add_column("Score", justify="right")
        t2.add_column("Applied At")
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
    mode: str,          # "prepare" | "review" | "auto" | "dry_run" | "dry_run_apply" | "search_only"
    scraper_cls,
    job_index: int,
    total: int,
) -> bool:
    non_interactive = mode in ("auto", "dry_run_apply")
    """Score → adapt → cover letter → PDF → (optionally) apply. Returns True if applied."""

    console.rule(f"[bold cyan]Job {job_index}/{total} — {job.platform}[/bold cyan]")
    log.info("Processing job %d/%d: %s @ %s (%s)", job_index, total, job.title, job.company, job.platform)

    # ── Company blacklist ─────────────────────────────────────────────────────
    bl_hit, bl_group = is_blacklisted(job.company)
    if bl_hit:
        console.print(
            f"[dim]↳ Skipped — {job.company} is in company blacklist ({bl_group})[/dim]\n"
        )
        log.info("Company blacklisted: %s @ %s (group: %s)", job.title, job.company, bl_group)
        if mode not in ("dry_run", "dry_run_apply"):
            db.save_job(job)
            db.update_status(job, "skipped", notes=f"blacklisted:company:{bl_group}")
        return False

    # ── Job / position blacklist ──────────────────────────────────────────────
    jb_hit, jb_reason = is_job_blacklisted(job.title, job.description)
    if jb_hit:
        console.print(
            f"[dim]↳ Skipped — {job.title} ({jb_reason})[/dim]\n"
        )
        log.info("Job blacklisted: %s @ %s — %s", job.title, job.company, jb_reason)
        if mode not in ("dry_run", "dry_run_apply"):
            db.save_job(job)
            db.update_status(job, "skipped", notes=f"blacklisted:job:{jb_reason}")
        return False

    # ── Score ────────────────────────────────────────────────────────────────
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
    if mode not in ("dry_run", "dry_run_apply"):
        db.save_job(job)   # dry_run*: don't persist yet so the job stays fresh for real runs
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
        db.update_status(job, "skipped")
        return False

    # Notify Telegram about a promising match (skip in prepare mode — digest sent at end)
    if mode != "prepare":
        await tg.notify_match_found(job.title, job.company, job.platform, score, reason, job.url)

    if mode == "search_only":
        return False

    # ── Duplicate company check ───────────────────────────────────────────────
    if SEARCH_CONFIG.get("skip_duplicate_companies") and db.company_applied(job.company):
        console.print(f"  [dim]↳ Skipped — already applied to {job.company}[/dim]\n")
        db.update_status(job, "skipped")
        return False

    # ── Probe: check if application can be automated BEFORE generating files ──
    # Skip probe in dry_run, review, and prepare (no auto-apply in those modes)
    probe = {"can_automate": True, "needs_cover_letter": True, "reason": "skipped"}
    if mode in ("auto", "dry_run_apply"):
        with console.status("Checking if application can be automated..."):
            try:
                async with scraper_cls() as scraper:
                    probe = await scraper.probe_apply(job)
            except Exception as e:
                log.warning("Probe failed for %s @ %s: %s", job.title, job.company, e)
                probe = {"can_automate": True, "needs_cover_letter": True, "reason": f"probe_error:{e}"}
        log.info("Probe result for %s @ %s: %s", job.title, job.company, probe)

        if not probe.get("can_automate", True):
            # Only skip for things we genuinely can't automate: interactive CAPTCHA, SMS/MFA
            reason = probe.get("reason", "unknown")
            console.print(
                f"  [yellow]↳ Cannot auto-apply (CAPTCHA/MFA): {reason}[/yellow]\n"
                f"  [dim]Apply manually: {job.url}[/dim]\n"
            )
            db.save_job(job)
            db.update_status(job, "skipped", notes=f"cannot_automate:{reason[:100]}")
            await tg.notify_manual_needed(job.title, job.company, job.url)
            return False

    needs_cover_letter = probe.get("needs_cover_letter", True)

    # ── Adapt resume ─────────────────────────────────────────────────────────
    with console.status("Adapting resume with Claude..."):
        try:
            adapted = await adapt_resume(resume, job)
        except Exception as e:
            console.print(f"[red]Resume adaptation failed: {e}[/red]")
            return False

    console.print("\n[bold]Customized Summary:[/bold]")
    console.print(Panel(adapted["summary"], border_style="blue", padding=(0, 1)))

    # ── Cover letter (only if the form actually needs one) ────────────────────
    cover_letter = ""
    cl_path = None
    if needs_cover_letter:
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
    else:
        console.print("  [dim]Cover letter not needed — skipping.[/dim]")

    # ── Generate PDF ─────────────────────────────────────────────────────────
    out_dir = Path(PATHS["output_dir"])
    out_dir.mkdir(exist_ok=True)
    fname = safe_filename(f"{job.company}_{job.title}")
    ts = datetime.now().strftime("%m%d%y")   # e.g. 040226
    pdf_path = str(out_dir / f"{fname}_{ts}.pdf")
    if needs_cover_letter:
        cl_path = str(out_dir / f"{fname}_{ts}_cover_letter.txt")

    with console.status("Generating PDF..."):
        try:
            await generate_pdf(adapted, pdf_path)
            if cl_path and cover_letter:
                save_cover_letter(cover_letter, cl_path)
        except Exception as e:
            console.print(f"[red]PDF generation failed: {e}[/red]")
            return False

    console.print(f"[green]✓[/green] Resume: [dim]{pdf_path}[/dim]")
    if cl_path:
        console.print(f"[green]✓[/green] Cover:  [dim]{cl_path}[/dim]")

    # ── Upload to Google Drive ────────────────────────────────────────────────
    pdf_link = cl_link = None
    if drive_enabled():
        with console.status("Uploading to Google Drive..."):
            pdf_link, cl_link = upload_files_for_job(pdf_path, cl_path or "")
        if pdf_link:
            console.print(f"[green]✓[/green] Drive resume: [dim]{pdf_link}[/dim]")
        if cl_link:
            console.print(f"[green]✓[/green] Drive cover:  [dim]{cl_link}[/dim]")
        if sheets_enabled() and (pdf_link or cl_link):
            update_job_links(job.platform, job.job_id, pdf_link, cl_link)

    # ── Prepare mode: files ready, mark job and stop — user applies manually ──
    if mode == "prepare":
        db.update_status(job, "prepared")
        if drive_enabled() and (pdf_link or cl_link):
            db.save_drive_links(job, pdf_link or "", cl_link or "")
        console.print(f"  [green]✓ Prepared[/green] — resume + cover letter ready, apply manually\n")
        return True

    # ── Dry run stops here ────────────────────────────────────────────────────
    if mode == "dry_run":
        console.print("\n[yellow][DRY RUN] Files generated. No application submitted.[/yellow]\n")
        return False

    # ── Dry-run-apply: save job, then fall through to apply automatically ────
    if mode == "dry_run_apply":
        console.print(
            "\n[yellow][DRY RUN APPLY][/yellow] Applying automatically...\n"
            f"  Job   : [bold]{job.title}[/bold] @ [cyan]{job.company}[/cyan]\n"
            f"  Score : {job.relevance_score:.0f}/100\n"
            f"  URL   : [dim]{job.url}[/dim]"
        )
        db.save_job(job)
        # fall through to apply section below

    # ── Review gate (unless auto mode) ───────────────────────────────────────
    if mode == "review":
        db.update_status(job, "reviewing")
        answer = Prompt.ask(
            "\n  Submit application?",
            choices=["y", "n", "s"],
            default="y",
        )
        if answer == "s":
            console.print("  [dim]Skipped.[/dim]\n")
            db.update_status(job, "skipped")
            return False
        if answer == "n":
            console.print("  [dim]Rejected.[/dim]\n")
            db.update_status(job, "rejected")
            return False

    # ── Apply ─────────────────────────────────────────────────────────────────
    console.print(f"\n[cyan]Applying via {job.platform}...[/cyan]")
    success = False

    async with scraper_cls() as scraper:
        try:
            success = await scraper.apply(
                job, pdf_path, cover_letter,
                resume_data=adapted,
                non_interactive=non_interactive,
                cover_letter_path=cl_path,
            )

            # Fallback: generic form filler on job URL — only for non-LinkedIn platforms
            # (LinkedIn's apply() already handles both Easy Apply and external links)
            if not success and job.url and job.platform != "linkedin":
                console.print("  [dim]Trying generic employer form filler...[/dim]")
                page = await scraper.new_page()
                try:
                    await page.goto(job.url, wait_until="domcontentloaded")
                    success = await fill_employer_form(
                        page, adapted, cover_letter, pdf_path,
                        cover_letter_path=cl_path,
                        non_interactive=non_interactive,
                    )
                finally:
                    await page.close()

        except Exception as e:
            console.print(f"[red]Apply error: {e}[/red]")
            db.update_status(job, "error", notes=str(e)[:200])
            return False

    if success:
        db.mark_applied(job, pdf_path, cl_path)
        console.print(f"[bold green]✓ Applied successfully![/bold green]\n")
        await tg.notify_applied(job.title, job.company, job.platform, pdf_link if drive_enabled() else None)
    else:
        console.print("[yellow]Could not auto-apply. Files saved for manual submission.[/yellow]\n")
        db.update_status(job, "skipped", notes="auto_apply_failed")
        await tg.notify_manual_needed(job.title, job.company, job.url)

    return success


async def run(
    mode: str = "prepare",
    dry_run_limit: int = 1,
    max_apply_override: int = None,
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
    all_jobs: list[tuple[Job, type]] = []
    seen_this_run: set[str] = set()   # dedup within this run (same job across titles)
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
                            all_jobs.append((job, scraper_cls))
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
    applied = 0
    max_apply = max_apply_override if max_apply_override is not None else SEARCH_CONFIG["max_applications_per_run"]

    limit = dry_run_limit if mode in ("dry_run", "dry_run_apply") else len(all_jobs)

    for i, (job, scraper_cls) in enumerate(all_jobs[:limit], 1):
        if mode != "dry_run" and applied >= max_apply:
            console.print(f"\n[yellow]Reached daily limit ({max_apply} applications). Done.[/yellow]")
            break

        ok = await process_job(job, resume, db, mode, scraper_cls, i, min(limit, len(all_jobs)))
        if ok:
            applied += 1

    # ── Final summary ─────────────────────────────────────────────────────────
    console.rule("[bold]Session Complete[/bold]")
    if mode == "prepare":
        console.print(f"Prepared: [bold green]{applied}[/bold green] jobs ready for manual apply")
    elif mode not in ("search_only", "dry_run"):
        console.print(f"Applied: [bold green]{applied}[/bold green] jobs this session")
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
        if mode != "auto":
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

    # Final Telegram summary
    stats = db.get_stats()
    if mode == "prepare":
        # Send daily digest: all prepared jobs pending manual apply
        try:
            prepared_jobs = db.get_prepared_jobs(days=7)
            await tg.notify_daily_digest(prepared_jobs)
            log.info("Daily digest sent: %d prepared jobs", len(prepared_jobs))
        except Exception as e:
            console.print(f"[dim]Digest error: {e}[/dim]")
            log.exception("Daily digest failed")
    else:
        avg_row = None
        try:
            import sqlite3
            with sqlite3.connect(PATHS["database"]) as conn:
                row = conn.execute("SELECT AVG(relevance_score) FROM jobs WHERE status='applied'").fetchone()
                avg_row = f"{row[0]:.0f}" if row and row[0] else "—"
        except Exception:
            avg_row = "—"
        await tg.notify_run_complete(
            applied=applied,
            found=stats.get("found", 0),
            skipped=stats.get("skipped", 0),
            avg_score=avg_row,
            report_link=report_drive_link,
        )


async def test_scraper(platform: str = "indeed", title: str = "DevOps Engineer"):
    """
    Quick scraper test: search 1 title on 1 platform, fetch 1 job detail, print result.
    No AI, no DB writes, no files. Completes in ~30 seconds.

    Usage:
      python main.py --test
      python main.py --test --platform=linkedin --title="SRE"
      python main.py --test --platform=dice
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


async def test_form(url: str):
    """
    Test the form filler against a specific URL.
    Opens a visible browser, navigates to the URL, and runs fill_employer_form.
    Uses base resume as-is — no AI calls, no PDF generation. Does NOT mark anything in the DB.

    Usage:
      python main.py --test-form=https://jobs.example.com/apply/123
    """
    from scrapers.base_scraper import BaseScraper
    from scrapers.employer_site import fill_employer_form

    console.print(f"\n[cyan]FORM TEST[/cyan] — {url}\n")
    log.info("test_form: url=%s", url)

    resume = load_resume()

    # Find any existing PDF in the output dir to reuse, or skip uploading
    out_dir = Path(PATHS["output_dir"])
    out_dir.mkdir(exist_ok=True)
    existing_pdfs = sorted(out_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing_pdfs:
        pdf_path = str(existing_pdfs[0])
        console.print(f"  Using existing PDF: {pdf_path}")
    else:
        pdf_path = None
        console.print("  No PDF found — form filler will skip file uploads")

    async with BaseScraper() as scraper:
        page = await scraper.new_page()
        console.print(f"  Navigating to {url} ...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            console.print(f"[red]Navigation failed: {e}[/red]")
            return

        console.print("  Running form filler — watch the browser window.\n")
        result = await fill_employer_form(
            page=page,
            resume_data=resume,
            cover_letter="",
            resume_pdf_path=pdf_path,
            cover_letter_path=None,
            max_steps=20,
            non_interactive=False,
        )

    if result:
        console.print("\n[green]✓ Form filler completed successfully![/green]")
    else:
        console.print("\n[yellow]Form filler returned False (did not submit).[/yellow]")
        console.print(f"  Check the log for details: {_log_path}")


async def do_login(platforms: list[str]):
    """Open a visible browser for each platform, let the user log in, save cookies."""
    from config import BROWSER_CONFIG
    import config as _cfg

    # Force visible browser for login regardless of config
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


_PLATFORM_FLAGS = {"--linkedin", "--indeed", "--dice", "--weworkremotely"}
_FLAG_TO_PLATFORM = {f"--{p}": p for p in ["linkedin", "indeed", "dice", "weworkremotely"]}


def _parse_platform_filter(args: set) -> list[str] | None:
    """Return list of platform names if any --platform flags are set, else None (= all)."""
    selected = [_FLAG_TO_PLATFORM[a] for a in args if a in _FLAG_TO_PLATFORM]
    return selected if selected else None


def main():
    args = set(sys.argv[1:])

    console.print(f"[dim]Log: {_log_path.absolute()}[/dim]")
    log.info("Run started: %s", " ".join(sys.argv))

    # Pull settings from Google Sheets if enabled — overrides config.py and user_config.json
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
        # Which platforms to log into (default: linkedin indeed)
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
        # Quick scraper test: 1 platform, 1 title, 1 job — no AI, no files, ~30 sec
        platform = next((a.split("=")[1] for a in args if a.startswith("--platform=")), "indeed")
        title = next((a.split("=")[1] for a in args if a.startswith("--title=")), "DevOps Engineer")
        asyncio.run(test_scraper(platform, title))
        return

    test_form_url = next((a.split("=", 1)[1] for a in args if a.startswith("--test-form=")), None)
    if test_form_url:
        asyncio.run(test_form(test_form_url))
        return

    if "--prepare" in args:
        pf = _parse_platform_filter(args)
        asyncio.run(run(mode="prepare", platforms_filter=pf))
        return

    if "--dry-run" in args:
        # Run full pipeline for 1 job, generate files, DO NOT submit
        console.print("[yellow]DRY RUN MODE — will process 1 job fully, no submission.[/yellow]")
        asyncio.run(run(mode="dry_run", dry_run_limit=1))
        return

    if "--dry-run-apply" in args:
        # Tries up to 10 candidates, stops as soon as 1 is successfully applied.
        # Uses --linkedin/--indeed/etc. to restrict which platform to search.
        pf = _parse_platform_filter(args)
        plat_str = f" [{', '.join(pf)}]" if pf else " [all platforms]"
        console.print(f"[yellow]DRY RUN APPLY — up to 3 candidates{plat_str}, stops after first apply.[/yellow]")
        asyncio.run(run(mode="dry_run_apply", dry_run_limit=3, max_apply_override=1, platforms_filter=pf))
        return

    if "--search-only" in args:
        asyncio.run(run(mode="search_only"))
        return

    if "--review" in args:
        asyncio.run(run(mode="review"))
        return

    if "--auto" in args:
        # Used by scheduler — no manual review prompts
        limit = None
        for a in args:
            if a.startswith("--limit="):
                try:
                    limit = int(a.split("=", 1)[1])
                except ValueError:
                    pass
        asyncio.run(run(mode="auto", max_apply_override=limit))
        return

    # Default: prepare mode — find jobs, generate files, send Telegram digest
    pf = _parse_platform_filter(args)
    asyncio.run(run(mode="prepare", platforms_filter=pf))


if __name__ == "__main__":
    main()
