"""
Job Search Automation — main entry point.

Flags:
  (none)            Full run with manual review before each application
  --dry-run         Search + score + generate ONE resume/PDF/cover-letter, no submitting
  --search-only     Search + score all jobs, no applying, no file generation
  --auto            Fully automatic (no review prompts) — for scheduler use
  --report          Generate HTML report and open it
  --stats           Print stats table to terminal
"""

import asyncio
import json
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

from config import AI_CONFIG, PATHS, PLATFORMS, SEARCH_CONFIG
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
    mode: str,          # "review" | "auto" | "dry_run" | "search_only"
    scraper_cls,
    job_index: int,
    total: int,
) -> bool:
    """Score → adapt → cover letter → PDF → (optionally) apply. Returns True if applied."""

    console.rule(f"[bold cyan]Job {job_index}/{total} — {job.platform}[/bold cyan]")

    # ── Score ────────────────────────────────────────────────────────────────
    with console.status("Scoring with Claude..."):
        try:
            score, reason = await score_job(job, resume)
        except Exception as e:
            console.print(f"[red]Scoring failed: {e}[/red]")
            return False

    job.relevance_score = score
    job.relevance_reason = reason
    db.save_job(job)

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

    # Notify Telegram about a promising match
    await tg.notify_match_found(job.title, job.company, job.platform, score, reason, job.url)

    if mode == "search_only":
        return False

    # ── Duplicate company check ───────────────────────────────────────────────
    if SEARCH_CONFIG.get("skip_duplicate_companies") and db.company_applied(job.company):
        console.print(f"  [dim]↳ Skipped — already applied to {job.company}[/dim]\n")
        db.update_status(job, "skipped")
        return False

    # ── Adapt resume ─────────────────────────────────────────────────────────
    with console.status("Adapting resume with Claude..."):
        try:
            adapted = await adapt_resume(resume, job)
        except Exception as e:
            console.print(f"[red]Resume adaptation failed: {e}[/red]")
            return False

    console.print("\n[bold]Customized Summary:[/bold]")
    console.print(Panel(adapted["summary"], border_style="blue", padding=(0, 1)))

    # ── Cover letter ─────────────────────────────────────────────────────────
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

    # ── Generate PDF ─────────────────────────────────────────────────────────
    out_dir = Path(PATHS["output_dir"])
    out_dir.mkdir(exist_ok=True)
    fname = safe_filename(f"{job.company}_{job.title}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = str(out_dir / f"{fname}_{ts}.pdf")
    cl_path  = str(out_dir / f"{fname}_{ts}_cover_letter.txt")

    with console.status("Generating PDF..."):
        try:
            await generate_pdf(adapted, pdf_path)
            save_cover_letter(cover_letter, cl_path)
        except Exception as e:
            console.print(f"[red]PDF generation failed: {e}[/red]")
            return False

    console.print(f"[green]✓[/green] Resume: [dim]{pdf_path}[/dim]")
    console.print(f"[green]✓[/green] Cover:  [dim]{cl_path}[/dim]")

    # ── Upload to Google Drive ────────────────────────────────────────────────
    pdf_link = cl_link = None
    if drive_enabled():
        with console.status("Uploading to Google Drive..."):
            pdf_link, cl_link = upload_files_for_job(pdf_path, cl_path)
        if pdf_link:
            console.print(f"[green]✓[/green] Drive resume: [dim]{pdf_link}[/dim]")
        if cl_link:
            console.print(f"[green]✓[/green] Drive cover:  [dim]{cl_link}[/dim]")

    # ── Dry run stops here ────────────────────────────────────────────────────
    if mode == "dry_run":
        console.print("\n[yellow][DRY RUN] Files generated. No application submitted.[/yellow]\n")
        return False

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
            # Platform-specific apply
            success = await scraper.apply(job, pdf_path, cover_letter)

            # If platform apply returned False, try generic employer form filler
            if not success and job.url:
                console.print("  [dim]Trying generic employer form filler...[/dim]")
                page = await scraper.new_page()
                try:
                    await page.goto(job.url, wait_until="domcontentloaded")
                    success = await fill_employer_form(page, adapted, cover_letter, pdf_path)
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


async def run(mode: str = "review", dry_run_limit: int = 1, max_apply_override: int = None):
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
    enabled = {p: c for p, c in PLATFORMS.items() if c["enabled"] and p in SCRAPER_MAP}
    console.print(f"Platforms: {', '.join(enabled.keys())}\n")

    await tg.notify_run_started(list(enabled.keys()), SEARCH_CONFIG["job_titles"])

    for platform, cfg in enabled.items():
        scraper_cls = SCRAPER_MAP[platform]
        console.print(f"[cyan]Searching {platform}...[/cyan]", end=" ")
        found = new_count = 0

        async with scraper_cls() as scraper:
            titles = SEARCH_CONFIG["job_titles"]
            per_title = max(1, cfg["max_jobs_to_scrape"] // len(titles))
            for title in titles:
                try:
                    async for job in scraper.search_jobs(title, per_title):
                        found += 1
                        if not db.is_seen(job.platform, job.job_id):
                            all_jobs.append((job, scraper_cls))
                            new_count += 1
                except Exception as e:
                    console.print(f"\n  [red]{platform} search error: {e}[/red]")

        console.print(f"Found {found}, [green]{new_count} new[/green]")

    if not all_jobs:
        console.print("\n[yellow]No new jobs found. Try again later.[/yellow]")
        return

    console.print(f"\n[bold]Total new jobs to evaluate: {len(all_jobs)}[/bold]\n")

    # ── Process jobs ──────────────────────────────────────────────────────────
    applied = 0
    max_apply = max_apply_override if max_apply_override is not None else SEARCH_CONFIG["max_applications_per_run"]

    limit = dry_run_limit if mode == "dry_run" else len(all_jobs)

    for i, (job, scraper_cls) in enumerate(all_jobs[:limit], 1):
        if mode != "dry_run" and applied >= max_apply:
            console.print(f"\n[yellow]Reached daily limit ({max_apply} applications). Done.[/yellow]")
            break

        ok = await process_job(job, resume, db, mode, scraper_cls, i, min(limit, len(all_jobs)))
        if ok:
            applied += 1

    # ── Final summary ─────────────────────────────────────────────────────────
    console.rule("[bold]Session Complete[/bold]")
    if mode not in ("search_only", "dry_run"):
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

    # Final Telegram summary
    stats = db.get_stats()
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


def main():
    args = set(sys.argv[1:])

    if "--stats" in args:
        db = Database(PATHS["database"])
        show_stats(db)
        return

    if "--report" in args:
        path = generate_report()
        console.print(f"[green]Report generated:[/green] {path}")
        webbrowser.open(f"file://{Path(path).absolute()}")
        return

    if "--dry-run" in args:
        # Run full pipeline for 1 job, generate files, DO NOT submit
        console.print("[yellow]DRY RUN MODE — will process 1 job fully, no submission.[/yellow]")
        asyncio.run(run(mode="dry_run", dry_run_limit=1))
        return

    if "--search-only" in args:
        asyncio.run(run(mode="search_only"))
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

    # Default: full run with review before each application
    asyncio.run(run(mode="review"))


if __name__ == "__main__":
    main()
