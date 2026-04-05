# Job Search Automation — Claude Context

This file helps Claude understand the project instantly in a new session.

## What This Project Does

Semi-automated job search pipeline for a DevOps engineer:
1. Scrapes LinkedIn, Indeed, We Work Remotely, Dice for remote US jobs (last 3 days)
2. Filters blacklisted companies before scoring (no API calls wasted)
3. Scores each job 0-100 using Claude (`claude-opus-4-6` with adaptive thinking)
4. Adapts the resume to match each job description (no fabrication — only expands on skills listed)
5. Generates a PDF resume + cover letter per job
6. Uploads files to Google Drive
7. Syncs to Google Sheets (Applications + Settings + Blacklist tabs)
8. Sends a Telegram morning digest with all prepared jobs + Drive links
9. **No auto-apply** — user applies manually using the generated files

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point. Modes: prepare (default), dry_run, search_only |
| `config.py` | All config + `COMPANY_BLACKLIST` + `is_blacklisted()` + user_config.json merging |
| `user_config.json` | User overrides written by dashboard — not in git |
| `dashboard.py` | Local HTTP server (port from `DASHBOARD_PORT` env var, default 5050) |
| `database.py` | SQLite via `Database` class. `Job` dataclass is the central data object |
| `reporter.py` | HTML report generator — 2 tabs: Applications and Settings snapshot |
| `ai/job_matcher.py` | Scores jobs 0-100 with Claude |
| `ai/resume_adapter.py` | Rewrites summary/bullets to match job. Uses `SKILL_IMPLICATIONS` dict |
| `ai/cover_letter.py` | Streams cover letter generation with strict language rules |
| `pdf_generator.py` | Renders `resume/template.html` (Jinja2) → Playwright PDF |
| `scrapers/base_scraper.py` | Playwright base: human delays, cookie persistence, anti-bot evasion |
| `scrapers/linkedin.py` | LinkedIn search (no apply) |
| `scrapers/indeed.py` | Indeed search (no apply) |
| `scrapers/weworkremotely.py` | We Work Remotely (no login required) |
| `scrapers/dice.py` | Dice.com scraper (no apply) |
| `google_sheets.py` | Syncs jobs to Google Sheets; reads Settings + Blacklist tabs at startup |
| `email_checker.py` | Checks inbox for job response emails |
| `scheduler.py` | Runs `main.py --prepare` daily via `schedule` library |
| `google_drive.py` | Uploads PDFs, cover letters, reports to Google Drive |
| `telegram_notifier.py` | Sends Telegram messages — morning digest, login alerts, errors |
| `resume/base_resume.json` | Candidate's real resume data (Aidarbek A., DevOps Engineer, Chicago IL) |
| `resume/template.html` | Jinja2 HTML template → PDF |

## Candidate Profile (do not change without asking)

- **Name**: Aidarbek A.
- **Role**: DevOps / Platform / Cloud / SRE / Infrastructure Engineer
- **Location**: Chicago, IL — Green Card holder, open to remote US positions
- **Key skills**: Terraform, Kubernetes (CKA/CKAD certified), AWS (SA Associate), Azure, Ansible, Docker, Jenkins, GitLab CI, Datadog, Prometheus/Grafana, Python, Bash, PostgreSQL, Azure SQL, MongoDB, Liquibase
- **Language style**: Simple, intermediate English. No buzzwords. Use "built", "set up", "managed", "deployed", not "leveraged", "spearheaded", "orchestrated"

## Company Blacklist

Defined in `config.py` as `COMPANY_BLACKLIST: dict[str, list[str]]`.
The `is_blacklisted(company)` function returns `(bool, group_name)`.
Checked at the very start of `process_job()` — before any AI calls.

Default blacklisted groups: Jack Henry & Associates, SAP (+ subsidiaries), Akuna Capital, Humana (+ subsidiaries), Brooksource (+ Eight Eleven Group / Medasource / Genuent).

## User Config Override System

- `config.py` defines defaults in `SEARCH_CONFIG`, `PLATFORMS`, `SCHEDULER_CONFIG`, `BROWSER_CONFIG`
- At the bottom of `config.py`, `_load_user_config()` reads `user_config.json` and merges overrides
- `dashboard.py` writes `user_config.json` when the user clicks Save
- Config priority (highest → lowest): Google Sheets Settings tab → user_config.json → config.py defaults

## AI Usage (all in `ai/`)

All three AI modules use `claude-opus-4-6` with `thinking: {type: "adaptive"}`.

- **job_matcher.py**: Returns `(score: float, reason: str)`. Scoring guide built into prompt.
- **resume_adapter.py**: Returns modified resume dict. `SKILL_IMPLICATIONS` maps skills to implied knowledge (e.g. Kubernetes → pods/deployments/RBAC/scaling). NEVER invents new jobs, companies, or certs.
- **cover_letter.py**: Streams text. 3 paragraphs, ~250 words. Banned word list enforced in prompt.

## Run Modes

| Mode | Flag | Description |
|------|------|-------------|
| `prepare` | (none) or `--prepare` | Default: find → score → adapt → PDF → Drive → Sheets → Telegram digest |
| `dry_run` | `--dry-run` | 1 job, generate files, no DB write, no Drive upload |
| `search_only` | `--search-only` | Score all jobs, no file generation |

Platform filter flags: `--linkedin`, `--indeed`, `--dice`, `--weworkremotely`

## Browser Automation

- Playwright (async), `headless=False` by default on Mac, `headless=True` required on servers
- Login: Google OAuth only — browser opens, user clicks "Sign in with Google", cookies saved to `cookies/`
- Anti-bot: custom user-agent, `--disable-blink-features=AutomationControlled`, human delays
- Cookie persistence: per-platform JSON files in `cookies/`
- **Session expiry**: when cookies expire, Telegram notification is sent with instructions to refresh

## Data Flow

```
search_jobs() → blacklist_check() → score_job() → adapt_resume()
             → generate_cover_letter() → generate_pdf()
             → upload to Drive → save_drive_links() → update Sheets
             → mark_prepared() → [end of run] → notify_daily_digest()
```

## Database (database.py)

Job statuses: `found` | `skipped` | `prepared` | `applied` (manual) | `rejected` | `error`

Key methods:
- `save_job(job)` — insert or update
- `update_status(job, status, **kwargs)` — update status + optional fields
- `save_drive_links(job, resume_link, cl_link)` — store Drive URLs in SQLite
- `get_prepared_jobs(days)` — return prepared jobs from last N days (for digest)
- `is_seen(platform, job_id)` — dedup check
- `company_applied(company)` — skip if already applied to this company

## Google Sheets Columns (Applications tab)

A=Job Title, B=Company, C=Platform, D=Score, E=Status, F=Applied Date,
G=Resume Link, H=Cover Letter Link, I=Email Response, J=Job URL,
K=Last Updated, L=Found At, M=_key (hidden)

## Dashboard (dashboard.py)

- Port: `DASHBOARD_PORT` env var (default 5050) — change per instance for multi-instance setups
- `GET /` → dashboard HTML with live SQLite data
- `GET /api/config` → returns `user_config.json`
- `POST /api/config` → saves to `user_config.json`

## Running

```bash
source .venv/bin/activate
python main.py --dry-run       # Safe test: 1 job, generate files, no DB write
python main.py                 # Full prepare run (default)
python main.py --linkedin      # Prepare, LinkedIn only
python main.py --search-only   # Score only, no files
python main.py --stats         # Print stats table
python main.py --report        # Generate + open HTML report
python main.py --login         # Log in to LinkedIn/Indeed, save cookies
python dashboard.py            # Open local web dashboard
python scheduler.py            # Daily auto-run at 08:00 (prepare mode)
python scheduler.py --now      # Run immediately, then schedule daily
python scheduler.py --startup  # Boot mode: detect missed run
```

## Multi-Instance Setup

Each instance lives in its own directory with its own `.env`, `cookies/`, `credentials/`,
`jobs.db`, and `user_config.json`. Set different `DASHBOARD_PORT` in each `.env` and
stagger `run_at` times by 30+ minutes.

## Common Issues

- **`ANTHROPIC_API_KEY` not set**: Add to `.env`
- **Session expired (Telegram message)**: `python main.py --login` on Mac, copy cookies to server
- **Google Drive auth**: First run opens browser — one-time, token saved to `credentials/token.json`
- **PDF broken**: `playwright install chromium`
- **Dashboard won't start**: `source .venv/bin/activate && python dashboard.py`
- **Port conflict**: Set `DASHBOARD_PORT=5051` in `.env`

## What NOT to Change Without Asking

- Resume data in `base_resume.json` — this is Aidarbek's real data
- The `SKILL_IMPLICATIONS` dict — carefully curated to avoid fabrication
- Language rules in `cover_letter.py` — user specifically requested simple English
- `COMPANY_BLACKLIST` entries — user specifically requested these companies be excluded
