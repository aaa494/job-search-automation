"""
Pipeline test — skips scraping, uses a sample job to test everything else:
  Claude scoring → resume adaptation → cover letter → PDF → Drive upload → Telegram

Run:
  python test_pipeline.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel

console = Console()

SAMPLE_JOB_DESCRIPTION = """
We are looking for a Senior DevOps / Platform Engineer to join our fully remote team.

Requirements:
- 4+ years of DevOps experience
- Strong Terraform and Infrastructure as Code experience
- Kubernetes administration (CKA preferred)
- AWS or Azure cloud platforms
- CI/CD pipelines (Jenkins, GitLab CI, or similar)
- Monitoring with Datadog, Prometheus, or Grafana
- Python or Bash scripting
- Experience with PostgreSQL or other relational databases

Nice to have:
- HashiCorp certifications
- Experience with Ansible
- MongoDB experience

We offer:
- Fully remote position (US candidates only, work authorization required)
- $130,000 – $160,000 / year
- Health, dental, vision insurance
- Flexible hours

This is a full-time role. You must be authorized to work in the United States.
"""


async def main():
    console.print(Panel.fit(
        "[bold cyan]Pipeline Test[/bold cyan]\n"
        "Skips scraping — tests Claude + PDF + Drive + Telegram",
        border_style="cyan"
    ))

    # ── Check API key ─────────────────────────────────────────────────────────
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY not set in .env[/red]")
        sys.exit(1)

    # ── Load resume ───────────────────────────────────────────────────────────
    resume_path = Path("resume/base_resume.json")
    if not resume_path.exists():
        console.print("[red]resume/base_resume.json not found[/red]")
        sys.exit(1)
    resume = json.loads(resume_path.read_text(encoding="utf-8"))
    console.print(f"[green]✓[/green] Loaded resume for: {resume['personal']['name']}")

    # ── Create sample job ─────────────────────────────────────────────────────
    from database import Job
    job = Job(
        platform="test",
        job_id="test-001",
        title="Senior DevOps Engineer",
        company="Acme Corp",
        location="Remote (US)",
        url="https://example.com/jobs/devops-001",
        description=SAMPLE_JOB_DESCRIPTION,
        salary="$130,000 – $160,000",
    )
    console.print(f"[green]✓[/green] Sample job: {job.title} @ {job.company}")

    # ── Step 1: Score ─────────────────────────────────────────────────────────
    console.print("\n[bold]Step 1: Scoring with Claude...[/bold]")
    from ai.job_matcher import score_job
    try:
        score, reason = await score_job(job, resume)
        console.print(f"  Score: [bold]{score:.0f}/100[/bold]")
        console.print(f"  Reason: {reason}")
    except Exception as e:
        console.print(f"  [red]Scoring failed: {e}[/red]")
        sys.exit(1)

    # ── Step 2: Adapt resume ──────────────────────────────────────────────────
    console.print("\n[bold]Step 2: Adapting resume...[/bold]")
    from ai.resume_adapter import adapt_resume
    try:
        adapted = await adapt_resume(resume, job)
        console.print(Panel(adapted["summary"], title="New Summary", border_style="blue", padding=(0, 1)))
    except Exception as e:
        console.print(f"  [red]Resume adaptation failed: {e}[/red]")
        sys.exit(1)

    # ── Step 3: Cover letter ──────────────────────────────────────────────────
    console.print("\n[bold]Step 3: Generating cover letter...[/bold]")
    from ai.cover_letter import generate_cover_letter
    cl_chunks: list[str] = []
    try:
        def on_chunk(chunk: str):
            console.print(chunk, end="")
            cl_chunks.append(chunk)

        cover_letter = await generate_cover_letter(job, adapted, stream_callback=on_chunk)
        console.print()
    except Exception as e:
        console.print(f"  [red]Cover letter failed: {e}[/red]")
        sys.exit(1)

    # ── Step 4: Generate PDF ──────────────────────────────────────────────────
    console.print("\n[bold]Step 4: Generating PDF...[/bold]")
    from pdf_generator import generate_pdf, save_cover_letter
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = str(output_dir / f"test_Acme_Corp_{ts}.pdf")
    cl_path  = str(output_dir / f"test_Acme_Corp_{ts}_cover_letter.txt")
    try:
        await generate_pdf(adapted, pdf_path)
        save_cover_letter(cover_letter, cl_path)
        console.print(f"  [green]✓[/green] PDF:   {pdf_path}")
        console.print(f"  [green]✓[/green] Cover: {cl_path}")
    except Exception as e:
        console.print(f"  [red]PDF generation failed: {e}[/red]")
        sys.exit(1)

    # ── Step 5: Google Drive ──────────────────────────────────────────────────
    from google_drive import is_enabled as drive_enabled, upload_files_for_job
    if drive_enabled():
        console.print("\n[bold]Step 5: Uploading to Google Drive...[/bold]")
        try:
            pdf_link, cl_link = upload_files_for_job(pdf_path, cl_path)
            if pdf_link:
                console.print(f"  [green]✓[/green] Resume: {pdf_link}")
            if cl_link:
                console.print(f"  [green]✓[/green] Cover:  {cl_link}")
        except Exception as e:
            console.print(f"  [yellow]Drive upload failed: {e}[/yellow]")
    else:
        console.print("\n[dim]Step 5: Google Drive disabled (set GOOGLE_DRIVE_ENABLED=true to enable)[/dim]")

    # ── Step 6: Telegram ──────────────────────────────────────────────────────
    import telegram_notifier as tg
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        console.print("\n[bold]Step 6: Sending Telegram notification...[/bold]")
        try:
            await tg.notify_match_found(
                job.title, job.company, job.platform,
                score, reason, job.url
            )
            await tg.notify_applied(job.title, job.company, "test")
            console.print("  [green]✓[/green] Telegram messages sent — check your phone!")
        except Exception as e:
            console.print(f"  [yellow]Telegram failed: {e}[/yellow]")
    else:
        console.print("\n[dim]Step 6: Telegram not configured (add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to .env)[/dim]")

    # ── Done ──────────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold green]All steps passed![/bold green]\n"
        "The pipeline is working correctly.\n\n"
        "Next: run [bold]python main.py[/bold] on your local machine\n"
        "where you can log into LinkedIn/Indeed via the browser.",
        border_style="green"
    ))


if __name__ == "__main__":
    asyncio.run(main())
