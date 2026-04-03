# Job Search Automation — Claude Context

This file helps Claude understand the project instantly in a new session.

## What This Project Does

Automated job search pipeline for a DevOps engineer:
1. Scrapes LinkedIn, Indeed, We Work Remotely, Dice for remote US jobs
2. Filters blacklisted companies before scoring (no API calls wasted)
3. Scores each job 0-100 using Claude (`claude-opus-4-6` with adaptive thinking)
4. Adapts the resume to match each job description (no fabrication — only expands on skills listed)
5. Generates a PDF resume + cover letter per application
6. Fills application forms (platform Easy Apply / Quick Apply, or generic Claude Vision form filler)
7. Uploads files to Google Drive, sends Telegram notifications
8. Generates HTML reports (2 tabs: Applications + Settings), stores everything in SQLite
9. Provides a local web dashboard (`dashboard.py`) for editing settings without touching code

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point. Modes: review (default), auto, dry_run, dry_run_apply, search_only |
| `config.py` | All config + `COMPANY_BLACKLIST` + `is_blacklisted()` + user_config.json merging |
| `user_config.json` | User overrides written by dashboard — not in git, not in config.py |
| `dashboard.py` | Local HTTP server (port 5050) — Applications table + editable Settings |
| `database.py` | SQLite via `Database` class. `Job` dataclass is the central data object |
| `reporter.py` | HTML report generator — 2 tabs: Applications and Settings snapshot |
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
| `google_sheets.py` | Syncs applications to Google Sheets (optional) |
| `email_checker.py` | Checks inbox for job response emails |
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

## Company Blacklist

Defined in `config.py` as `COMPANY_BLACKLIST: dict[str, list[str]]`.
The `is_blacklisted(company)` function returns `(bool, group_name)`.
Checked at the very start of `process_job()` — before any AI calls.
Users can add entries via the dashboard or by editing `COMPANY_BLACKLIST` in config.py.

Default blacklisted groups: Jack Henry & Associates, SAP (+ subsidiaries), Akuna Capital.

## User Config Override System

- `config.py` defines defaults in `SEARCH_CONFIG`, `PLATFORMS`, `SCHEDULER_CONFIG`, `BROWSER_CONFIG`
- At the bottom of `config.py`, `_load_user_config()` reads `user_config.json` and merges overrides
- `dashboard.py` writes `user_config.json` when the user clicks Save
- This means users never need to edit `config.py` directly — the dashboard handles it
- `user_config.json` is in `.gitignore` (personal settings, not committed)

## AI Usage (all in `ai/`)

All three AI modules use `claude-opus-4-6` with `thinking: {type: "adaptive"}`.

- **job_matcher.py**: Returns `(score: float, reason: str)`. Scoring guide built into prompt.
- **resume_adapter.py**: Returns modified resume dict. `SKILL_IMPLICATIONS` maps skills to implied knowledge (e.g. Kubernetes → pods/deployments/RBAC/scaling). NEVER invents new jobs, companies, or certs.
- **cover_letter.py**: Streams text. 3 paragraphs, ~250 words. Banned word list enforced in prompt.

## Run Modes

| Mode | Flag | Description |
|------|------|-------------|
| `review` | (none) | Full run — prompts y/n before each application |
| `auto` | `--auto` | No prompts — for scheduler use |
| `dry_run` | `--dry-run` | 1 job, generate files, no submission, no DB write |
| `dry_run_apply` | `--dry-run-apply` | 1 job, fully automatic: find → score → adapt → PDF → apply → report |
| `search_only` | `--search-only` | Score all jobs, no file generation, no applying |

Platform filter flags (combinable with any mode): `--linkedin`, `--indeed`, `--dice`, `--weworkremotely`
Example: `python main.py --dry-run-apply --linkedin` — search only LinkedIn, apply 1 job automatically.

## Browser Automation

- Playwright (async), `headless=False` by default (easier to handle CAPTCHAs)
- Login: Google OAuth only — browser opens, user clicks "Sign in with Google", cookies saved to `cookies/`
- Anti-bot: custom user-agent, `--disable-blink-features=AutomationControlled`, human delays, slow typing
- Cookie persistence: per-platform JSON files in `cookies/`

## Data Flow

```
search_jobs() → blacklist_check() → score_job() → adapt_resume()
             → generate_cover_letter() → generate_pdf()
             → [review / dry_run_apply gate]
             → apply() → mark_applied()
             → upload to Drive → send Telegram → generate_report()
```

## Dashboard (dashboard.py)

Runs a minimal HTTP server (no external dependencies — uses Python's built-in `http.server`).

- `GET /` → renders the dashboard HTML with live SQLite data
- `GET /api/config` → returns `user_config.json` as JSON
- `POST /api/config` → saves posted JSON to `user_config.json`

The Settings tab in the dashboard lets users edit:
- Search config (score threshold, max apps, titles, location, etc.)
- Platform on/off + max jobs per platform
- Scheduler time
- Browser headless mode
- Company blacklist (add/remove groups and names)

## HTML Report (reporter.py)

Two-tab HTML file saved to `reports/report_YYYYMMDD_HHMMSS.html`:
- **Applications tab**: stats cards + sortable table of all jobs
- **Settings tab**: read-only snapshot of current config + link to open the dashboard

## Configuration (config.py key settings)

- `SEARCH_CONFIG["min_relevance_score"]` — default 70. Jobs below this are skipped.
- `SEARCH_CONFIG["require_review"]` — default True. Set False (or use `--auto`) for fully automatic.
- `SEARCH_CONFIG["max_applications_per_run"]` — default 20.
- `PLATFORMS` — enable/disable each platform, set `max_jobs_to_scrape`
- `COMPANY_BLACKLIST` — dict of group → list of substring names to skip

## Environment Variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...           # Required
GOOGLE_DRIVE_ENABLED=true/false        # Optional
GOOGLE_DRIVE_FOLDER=Job Search Auto    # Drive folder name
TELEGRAM_BOT_TOKEN=123456:ABCdef...    # Optional
TELEGRAM_CHAT_ID=123456789             # Optional
GOOGLE_SHEETS_ENABLED=true/false       # Optional
```

## Google Sheets Control Panel (google_sheets.py)

Three-tab spreadsheet "Job Applications":
- **Applications tab**: all jobs (title, company, platform, score, status, Drive links, email response, _key hidden)
- **Settings tab**: key/value rows — script reads these and mutates config dicts in-place at startup
- **Blacklist tab**: group + comma-separated names — replaces `COMPANY_BLACKLIST` in-place at startup

Key functions:
- `apply_sheets_config()` — called by `main.py` at startup; reads Settings+Blacklist, mutates `config` module dicts
- `sync_all_jobs(db_path)` — syncs SQLite → Applications tab (create/update rows)
- `update_job_links(platform, job_id, resume_link, cl_link)` — writes Drive links to Applications tab (cols G/H)
- `update_email_response(platform, job_id, summary)` — writes email classification result to col I
- `_ensure_extra_tabs(service, sid)` — creates Settings/Blacklist tabs on pre-existing spreadsheets

Config priority (highest to lowest): Google Sheets → user_config.json → config.py defaults.

## Running

```bash
source .venv/bin/activate
python main.py --dry-run                    # Safe test: 1 job, no submission
python main.py --dry-run-apply              # 1 job, apply automatically, open report
python main.py --dry-run-apply --linkedin   # Same but only search LinkedIn (faster)
python main.py --dry-run-apply --indeed     # Same but only search Indeed
python main.py                              # Full run with review before each apply
python main.py --auto                       # No review prompts (for scheduler)
python main.py --stats                      # Print stats table
python main.py --report                     # Generate + open HTML report
python dashboard.py                         # Open local web dashboard (settings + jobs)
python scheduler.py                         # Daily auto-run at 09:00
python scheduler.py --now                   # Run immediately, then schedule daily
python scheduler.py --startup               # macOS boot mode: detect missed run
python test_pipeline.py                     # Test AI pipeline with a sample job (no scraping)
python telegram_bot.py                      # Start Telegram command listener
bash macos_autostart.sh install             # Install macOS LaunchAgent (auto-start on boot)
bash macos_autostart.sh uninstall           # Remove
bash macos_autostart.sh status             # Check
```

## Deduplication

Jobs are never applied to twice. Three layers:
1. `is_blacklisted(company)` — checked first, skips entire company group
2. `db.is_seen(platform, job_id)` — checked before scoring, skips already-seen jobs
3. `db.company_applied(company)` — skips companies already applied to from any platform
Layers 2 and 3 use SQLite `jobs.db` which persists across all runs.

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
- **Dashboard won't start**: Make sure venv is active (`source .venv/bin/activate`), then `python dashboard.py`

## What NOT to Change Without Asking

- Resume data in `base_resume.json` — this is Aidarbek's real data
- The `SKILL_IMPLICATIONS` dict — carefully curated to avoid fabrication
- Language rules in `cover_letter.py` — user specifically requested simple English
- The `require_review: True` default — safety gate, don't disable unless user asks
- `COMPANY_BLACKLIST` entries — user specifically requested these companies be excluded
