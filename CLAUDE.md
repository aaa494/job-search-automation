# Job Search Automation — Claude Context

This file helps Claude understand the project instantly in a new session.

## What This Project Does

Automated job search pipeline for a DevOps engineer:
1. Scrapes LinkedIn, Indeed, We Work Remotely, Dice for remote US jobs
2. Scores each job 0-100 using Claude (`claude-opus-4-6` with adaptive thinking)
3. Adapts the resume to match each job description (no fabrication — only expands on skills listed)
4. Generates a PDF resume + cover letter per application
5. Fills application forms (platform Easy Apply / Quick Apply, or generic Claude Vision form filler)
6. Uploads files to Google Drive, sends Telegram notifications
7. Generates HTML reports, stores everything in SQLite

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point. Modes: review (default), auto, dry_run, search_only |
| `config.py` | All config: job titles, platforms, score threshold, paths, browser settings |
| `database.py` | SQLite via `Database` class. `Job` dataclass is the central data object |
| `ai/job_matcher.py` | Scores jobs 0-100 with Claude |
| `ai/resume_adapter.py` | Rewrites summary/bullets to match job. Uses `SKILL_IMPLICATIONS` dict |
| `ai/cover_letter.py` | Streams cover letter generation with strict language rules |
| `pdf_generator.py` | Renders `resume/template.html` (Jinja2) → Playwright PDF |
| `scrapers/base_scraper.py` | Playwright base: human delays, cookie persistence, anti-bot evasion |
| `scrapers/linkedin.py` | LinkedIn search + Easy Apply |
| `scrapers/indeed.py` | Indeed search + Quick Apply |
| `scrapers/weworkremotely.py` | We Work Remotely (no login required) |
| `scrapers/dice.py` | Dice.com scraper |
| `scrapers/employer_site.py` | Generic form filler using Claude Vision (screenshots → field analysis) |
| `reporter.py` | Generates HTML report from SQLite data |
| `scheduler.py` | Runs `main.py --auto` daily via `schedule` library |
| `google_drive.py` | Uploads PDFs, cover letters, reports to Google Drive |
| `telegram_notifier.py` | Sends Telegram messages for matches, applications, run summaries |
| `resume/base_resume.json` | Candidate's real resume data (Aidarbek A., DevOps Engineer, Chicago IL) |
| `resume/template.html` | Jinja2 HTML template → PDF |

## Candidate Profile (do not change without asking)

- **Name**: Aidarbek A.
- **Role**: DevOps / Platform / Cloud / SRE / Infrastructure Engineer
- **Location**: Chicago, IL — Green Card holder, open to remote US positions
- **Key skills**: Terraform, Kubernetes (CKA/CKAD certified), AWS (SA Associate), Azure, Ansible, Docker, Jenkins, GitLab CI, Datadog, Prometheus/Grafana, Python, Bash, PostgreSQL, Azure SQL, MongoDB, Liquibase
- **Language style**: Simple, intermediate English. No buzzwords. Use "built", "set up", "managed", "deployed", not "leveraged", "spearheaded", "orchestrated"

## AI Usage (all in `ai/`)

All three AI modules use `claude-opus-4-6` with `thinking: {type: "adaptive"}`.

- **job_matcher.py**: Returns `(score: float, reason: str)`. Scoring guide built into prompt.
- **resume_adapter.py**: Returns modified resume dict. `SKILL_IMPLICATIONS` maps skills to implied knowledge (e.g. Kubernetes → pods/deployments/RBAC/scaling). NEVER invents new jobs, companies, or certs.
- **cover_letter.py**: Streams text. 3 paragraphs, ~250 words. Banned word list enforced in prompt.

## Browser Automation

- Playwright (async), `headless=False` by default (easier to handle CAPTCHAs)
- Login: Google OAuth only — browser opens, user clicks "Sign in with Google", cookies saved to `cookies/`
- Anti-bot: custom user-agent, `--disable-blink-features=AutomationControlled`, human delays, slow typing
- Cookie persistence: per-platform JSON files in `cookies/`

## Data Flow

```
search_jobs() → Job → score_job() → adapt_resume() → generate_cover_letter()
             → generate_pdf() → [review gate] → apply() → mark_applied()
             → upload to Drive → send Telegram → generate_report()
```

## Configuration (config.py)

Key settings to adjust:
- `SEARCH_CONFIG["min_relevance_score"]` — default 70. Jobs below this are skipped.
- `SEARCH_CONFIG["require_review"]` — default True. Set False (or use `--auto`) for fully automatic.
- `SEARCH_CONFIG["max_applications_per_run"]` — default 8. Safety cap.
- `PLATFORMS` — enable/disable each platform, set `max_jobs_to_scrape`

## Environment Variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...           # Required
GOOGLE_DRIVE_ENABLED=true/false        # Optional
GOOGLE_DRIVE_FOLDER=Job Search Auto    # Drive folder name
TELEGRAM_BOT_TOKEN=123456:ABCdef...    # Optional
TELEGRAM_CHAT_ID=123456789             # Optional
```

## Running

```bash
source .venv/bin/activate
python main.py --dry-run      # Safe test: 1 job, no submission
python main.py                # Full run with review before each apply
python main.py --auto         # No review prompts (for scheduler)
python main.py --stats        # Print stats table
python main.py --report       # Generate + open HTML report
python scheduler.py           # Daily auto-run at 09:00
python scheduler.py --now     # Run immediately, then schedule daily
python scheduler.py --startup # macOS boot mode: detect missed run, notify Telegram
python test_pipeline.py       # Test AI pipeline with a sample job (no scraping needed)
python telegram_bot.py        # Start Telegram command listener
bash macos_autostart.sh install    # Install macOS LaunchAgent (auto-start on boot)
bash macos_autostart.sh uninstall  # Remove
bash macos_autostart.sh status     # Check
```

## Deduplication

Jobs are never applied to twice. Two layers:
1. `db.is_seen(platform, job_id)` — checked before scoring, skips already-seen jobs
2. `db.company_applied(company)` — skips companies already applied to from any platform
Both use SQLite `jobs.db` which persists across all runs.

## Missed Run Detection (scheduler.py)

On `--startup`, scheduler reads `last_run.txt`:
- If today's scheduled time has passed and last_run is not today → missed run detected
- Sends Telegram notification, runs immediately, then schedules next daily run
- `save_last_run()` writes current timestamp to `last_run.txt` after each completed run

## macOS Auto-Start (macos_autostart.sh)

Creates `~/Library/LaunchAgents/com.jobsearch.automation.plist` which:
- Runs `scheduler.py --startup` on every Mac login
- Keeps the process alive (KeepAlive: true)
- Writes logs to `logs/scheduler.log`

## Common Issues

- **`ANTHROPIC_API_KEY` not set**: Add to `.env`
- **Login wall on LinkedIn/Indeed**: Browser opens — click "Sign in with Google", press ENTER in terminal
- **Google Drive auth**: First run opens browser to authorize — one-time, token saved to `credentials/token.json`
- **Telegram not notifying**: Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`; make sure you messaged the bot at least once
- **PDF broken**: Playwright Chromium not installed — run `playwright install chromium`

## What NOT to Change Without Asking

- Resume data in `base_resume.json` — this is Aidarbek's real data
- The `SKILL_IMPLICATIONS` dict — carefully curated to avoid fabrication
- Language rules in `cover_letter.py` — user specifically requested simple English
- The `require_review: True` default — safety gate, don't disable unless user asks
