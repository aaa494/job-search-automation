# Job Search Automation

Semi-automated job search pipeline for DevOps / Platform / Cloud / SRE roles.

Runs every morning at 08:00, searches 4 job boards, scores each job with Claude AI, adapts your resume, generates a cover letter and PDF — then sends you a Telegram digest with everything ready. **You apply manually** using the prepared files.

---

## What It Does

1. **Searches** LinkedIn, Indeed, We Work Remotely, and Dice for remote US jobs posted in the last 3 days
2. **Filters** blacklisted companies (Jack Henry, SAP, Akuna Capital, Humana, Brooksource + subsidiaries) before any AI calls
3. **Filters** clearance / citizenship-required jobs automatically
4. **Scores** each job 0-100 with Claude AI — skips anything below your threshold (default 70)
5. **Adapts** your resume summary and bullets to match each job description (no fabrication)
6. **Generates** a PDF resume + cover letter per job
7. **Uploads** files to Google Drive automatically
8. **Syncs** all jobs to a Google Sheets spreadsheet (Applications + Settings + Blacklist tabs)
9. **Sends a Telegram digest** at end of run: job title, company, job URL, resume Drive link
10. **Deduplicates** — never prepares the same job or company twice

You get a Telegram message every morning listing what's ready to apply to with direct links.

---

## Requirements

- **Python 3.10+** (3.12 recommended)
- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)
- Works on **Mac** (visible browser) and **Ubuntu server** (headless)

---

## Setup (one-time, ~15 minutes)

### Step 1 — Clone and install

**On Ubuntu server — install system dependencies first:**
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip python3.12-venv
```

> If `python3.12-venv` fails (older Ubuntu), try `python3-venv` instead.

**Then on any OS:**
```bash
git clone https://github.com/aaa494/job-search-automation.git
cd job-search-automation
bash setup.sh
```

Creates a virtual environment, installs dependencies, downloads Chromium.

### Step 2 — Add your API key

```bash
cp .env.example .env
nano .env   # or: open .env (Mac)
```

Required:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### Step 3 — Fill in your resume

```bash
nano resume/base_resume.json
```

Edit with your real name, contact info, skills, experience, education, certifications.

### Step 4 — Log in to LinkedIn and Indeed (saves cookies)

> **This step requires a Mac or desktop with a display.** The browser must be visible for Google login. You cannot run `--login` on a headless server.

```bash
source .venv/bin/activate
python main.py --login
```

A browser window opens. Click **Sign in with Google**, log in, press ENTER.
Cookies are saved to `cookies/`. You only need to do this once (LinkedIn ~1 year, Indeed ~30 days).

**If running on a server:** run `--login` on your Mac first, then copy cookies:
```bash
scp cookies/linkedin.json cookies/indeed.json user@yourserver:/path/to/app/cookies/
```

### Step 5 — Set up Google Drive / Sheets (optional but recommended)

See the [Google Drive section](#optional-google-drive) below. After placing `credentials/google_credentials.json`, authorize both tokens:

```bash
source .venv/bin/activate
python auth_google_drive.py   # authorizes Drive → saves credentials/token.json
python main.py --dry-run      # authorizes Sheets on first run → saves credentials/sheets_token.json
```

> **If you already have an existing "Job Applications" Google Sheet:** save its ID to `credentials/sheets_id.txt` to prevent the script from creating a new one:
> ```bash
> # Get the ID from the sheet URL: /spreadsheets/d/<ID>/edit
> echo "your-sheet-id-here" > credentials/sheets_id.txt
> ```

### Step 6 — Enable headless mode (server only)

On a server there is no display, so the browser must run headlessly. Set this in `user_config.json`:
```bash
echo '{"browser": {"headless": true}}' > user_config.json
```

Or set it in the **Settings tab** of your Google Sheet: `headless = TRUE`.

> Note: Google Sheets settings override `user_config.json`. If your sheet has `headless = FALSE`, the server run will fail. Make sure it says `TRUE`.

### Step 7 — Test it

```bash
source .venv/bin/activate
python main.py --dry-run
```

Finds 1 job, scores it, adapts your resume, generates a PDF + cover letter.
No DB write, no Drive upload. Check `output/` to see the generated files.

### Step 8 — Run the scheduler

```bash
python scheduler.py
```

Runs the full pipeline every day at 08:00. Sends Telegram digest at end.

---

## Daily Usage

```bash
# Full prepare run (default) — find, score, generate, notify
python main.py

# Limit to one platform (faster for testing)
python main.py --linkedin
python main.py --indeed

# Just score — no file generation
python main.py --search-only

# Test the scraper (no AI, ~30 seconds)
python main.py --test
python main.py --test --platform=linkedin --title="SRE"

# Show stats
python main.py --stats

# Generate and open HTML report
python main.py --report
```

---

## Google Sheets Control Panel

When `GOOGLE_SHEETS_ENABLED=true` in `.env`, the script creates and maintains **"Job Applications"** spreadsheet with three tabs:

| Tab | Purpose |
|-----|---------|
| **Applications** | All jobs: title, company, score, status, found date, Drive links |
| **Settings** | All config — edit values in the sheet, script reads them before each run |
| **Blacklist** | Company blacklist — add/remove groups and names |

**Settings tab** — editable keys:

| Key | Default | Description |
|-----|---------|-------------|
| `min_relevance_score` | 70 | Jobs below this score are skipped |
| `posted_within_days` | 3 | Look back N days when searching |
| `digest_lookback_days` | 7 | Days of prepared jobs shown in morning digest |
| `run_at` | 08:00 | Daily run time (HH:MM 24h) |
| `job_titles` | DevOps Engineer, ... | Comma-separated titles to search |
| `linkedin_enabled` | TRUE | Search LinkedIn |
| `linkedin_max_jobs` | 30 | Max LinkedIn jobs per run |
| *(same for indeed, dice, weworkremotely)* | | |
| `headless` | FALSE | **Must be TRUE on servers** (no display); FALSE = visible browser (Mac only) |

Config priority (highest → lowest): **Google Sheets → user_config.json → config.py defaults**

---

## Dashboard (local Settings UI)

Alternative to Google Sheets — local web UI:

```bash
python dashboard.py          # http://localhost:5050
python dashboard.py --port=8080
```

Or set `DASHBOARD_PORT=5050` in `.env` to make the port persistent.

| Tab | What you can do |
|-----|----------------|
| **Applications** | Live table from the database |
| **Settings** | Edit all config + blacklist — click Save |

---

## Running on Ubuntu Server (headless)

### Install

```bash
# System dependencies (including python3.12-venv — required on Ubuntu 22/24)
sudo apt update && sudo apt install -y python3 python3-pip python3.12-venv

# Clone and set up
git clone https://github.com/aaa494/job-search-automation.git
cd job-search-automation
bash setup.sh
```

`setup.sh` automatically installs Playwright and Chromium. If it fails on Playwright deps:
```bash
sudo .venv/bin/playwright install-deps chromium
```

### Enable headless mode

On a server there is no display. You must enable headless mode — pick one:

**Option A — `user_config.json` (simplest):**
```bash
echo '{"browser": {"headless": true}}' > user_config.json
```

**Option B — Google Sheets Settings tab:**
Set `headless = TRUE` in your sheet's Settings tab.

> Do not add `BROWSER_CONFIG_HEADLESS` to `.env` — that variable is not read by the code.

### Copy cookies from Mac

```bash
# Run --login on your Mac first (requires visible browser):
source .venv/bin/activate && python main.py --login

# Then copy to server:
scp cookies/linkedin.json user@yourserver:/path/to/app/cookies/
scp cookies/indeed.json   user@yourserver:/path/to/app/cookies/
```

### Run as a systemd service

Replace `/path/to/app` and `youruser` with your actual path and user (e.g. `/root/job-search-aidar` and `root`).

```bash
sudo nano /etc/systemd/system/jobsearch.service
```

```ini
[Unit]
Description=Job Search Automation Scheduler
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/app
ExecStart=/path/to/app/.venv/bin/python scheduler.py
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable jobsearch
sudo systemctl start jobsearch
sudo systemctl status jobsearch

# View logs
journalctl -u jobsearch -f
# Or:
tail -f logs/scheduler.log
```

> Before enabling the service, make sure `--dry-run` works and headless mode is set (see above). The scheduler runs `main.py --prepare` daily at the time set in your Google Sheet (`run_at` key, default `09:00`).

### When cookies expire (Telegram will notify you)

When LinkedIn or Indeed session expires, you'll receive a Telegram message like:
> 🔐 **LinkedIn session expired** — refresh cookies from your Mac

Fix:
1. On your Mac: `python main.py --login`
2. Copy updated cookie: `scp cookies/linkedin.json user@server:/path/to/app/cookies/`

LinkedIn cookies last ~1 year. Indeed ~30 days.

---

## Running Multiple Independent Instances

Two instances for two different job seekers (or different configs) on the same server:

```bash
# Clone twice
git clone ... ~/job-search/alice
git clone ... ~/job-search/bob

# Each has its own setup
cd ~/job-search/alice && bash setup.sh
cd ~/job-search/bob   && bash setup.sh
```

Each instance has completely separate:
- `.env` — different API keys, Telegram bots, Drive folders
- `cookies/` — different platform accounts  
- `credentials/` — different Google OAuth tokens
- `jobs.db` — separate history
- `user_config.json` — different filters, score thresholds, job titles

**Dashboard ports** — set different ports in each `.env`:
```
# alice/.env
DASHBOARD_PORT=5050

# bob/.env
DASHBOARD_PORT=5051
```

**Systemd services** — one per instance:
```bash
sudo cp /etc/systemd/system/jobsearch.service /etc/systemd/system/jobsearch-alice.service
sudo cp /etc/systemd/system/jobsearch.service /etc/systemd/system/jobsearch-bob.service
# Edit WorkingDirectory in each file
sudo systemctl enable jobsearch-alice jobsearch-bob
sudo systemctl start  jobsearch-alice jobsearch-bob
```

**Stagger run times** to avoid running both at the same time (each Playwright browser uses ~400 MB RAM):
```
# alice/user_config.json
{"scheduler": {"run_at": "08:00"}}

# bob/user_config.json
{"scheduler": {"run_at": "08:30"}}
```

---

## Optional: Telegram Notifications

**Setup (2 minutes):**
1. Telegram → **@BotFather** → `/newbot`
2. Copy the **token** (e.g. `123456:ABCdef...`)
3. Send any message to your new bot
4. Open: `https://api.telegram.org/bot<TOKEN>/getUpdates` → find `"chat":{"id": <NUMBER>}`
5. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABCdef...
   TELEGRAM_CHAT_ID=123456789
   ```

**What you receive:**
- 🚀 Run started
- ☀️ Morning digest — list of prepared jobs with links
- 🔐 Session expired — cookie refresh instructions

---

## Optional: Google Drive

**Setup:**
1. [console.cloud.google.com](https://console.cloud.google.com) → Create project
2. Enable **Google Drive API** and **Google Sheets API**
3. Credentials → Create OAuth 2.0 Client ID → Desktop app → Download JSON
4. Save as `credentials/google_credentials.json`
5. In `.env`:
   ```
   GOOGLE_DRIVE_ENABLED=true
   GOOGLE_SHEETS_ENABLED=true
   ```
6. Authorize (two separate tokens, both one-time only):
   ```bash
   source .venv/bin/activate
   python auth_google_drive.py   # → saves credentials/token.json
   python main.py --dry-run      # → saves credentials/sheets_token.json on first run
   ```

> The script uses **two separate OAuth tokens**: `credentials/token.json` (Drive) and `credentials/sheets_token.json` (Sheets). Both must be authorized before the full pipeline runs unattended.

> **Existing spreadsheet:** If you already have a "Job Applications" sheet, paste its ID (from the URL) into `credentials/sheets_id.txt` — otherwise the script creates a new one and reads settings from there instead of yours.

Drive folder structure:
```
Job Search Automation/
  ├── Applications/   ← PDF resumes + cover letters
  └── Reports/        ← HTML reports
```

---

## Company Blacklist

Blacklisted companies are skipped before any AI calls (no API cost).

Default groups:

| Group | Includes |
|-------|---------|
| Jack Henry & Associates | Jack Henry, Banno, ProfitStars, iPay Technologies, Goldleaf, Pemco Technology |
| SAP | SAP SE/Labs/America/AG, SuccessFactors, Concur, Qualtrics, Ariba, Hybris, Signavio, LeanIX, WalkMe, Callidus, BusinessObjects, Sybase |
| Akuna Capital | Akuna Capital |
| Humana | Humana, Conviva Care, CenterWell, Kindred Healthcare, LifeSynch, Humana Military |
| Brooksource | Brooksource, Eight Eleven Group, Medasource, Genuent |

To add more: edit the **Blacklist tab** in Google Sheets, or the dashboard Settings tab, or `COMPANY_BLACKLIST` in `config.py`.

---

## File Structure

```
job-search-automation/
├── main.py                    # Entry point — all run modes
├── config.py                  # Defaults + COMPANY_BLACKLIST
├── user_config.json           # Your overrides (written by dashboard) — not in git
├── dashboard.py               # Local web UI: Applications + Settings
├── database.py                # SQLite job tracking
├── reporter.py                # HTML report generator
├── scheduler.py               # Daily auto-run with missed-run detection
├── telegram_notifier.py       # Telegram outgoing notifications
├── google_drive.py            # Google Drive uploader
├── google_sheets.py           # Google Sheets tracker + config reader
├── pdf_generator.py           # Resume → PDF via Playwright
├── email_checker.py           # Check inbox for job responses
├── auth_google_drive.py       # One-time Google Drive authorization
├── test_pipeline.py           # Test AI pipeline without scraping
│
├── ai/
│   ├── job_matcher.py         # Score jobs 0-100 with Claude
│   ├── resume_adapter.py      # Adapt resume to job description
│   └── cover_letter.py        # Generate cover letter (streaming)
│
├── scrapers/
│   ├── base_scraper.py        # Playwright base: human delays, cookies
│   ├── linkedin.py            # LinkedIn search
│   ├── indeed.py              # Indeed search
│   ├── weworkremotely.py      # We Work Remotely
│   └── dice.py                # Dice.com
│
├── resume/
│   ├── base_resume.json       # Your resume data — edit this
│   └── template.html          # PDF template
│
├── credentials/               # OAuth tokens — not committed to git
├── cookies/                   # Browser sessions — not committed to git
├── output/                    # Generated PDFs and cover letters
├── reports/                   # HTML reports
├── logs/                      # Run logs
├── jobs.db                    # SQLite database
│
├── .env                       # Your secrets — not committed to git
├── .env.example               # Template
├── requirements.txt
├── setup.sh
└── README.md
```

---

## How Resume Adaptation Works

The AI expands on skills you listed but **never invents** new ones.

If your resume lists "Kubernetes", the adapter knows that implies: pods, deployments, namespaces, RBAC, resource limits, health checks, horizontal scaling, kubectl. It uses these to match a job description — but only because Kubernetes is already on your resume.

Banned words: *leveraged, spearheaded, orchestrated, synergies, cutting-edge, innovative, passionate, driven, robust, seamlessly*

---

## Troubleshooting

**`setup.sh` fails: `ensurepip is not available`**  
→ `sudo apt install python3.12-venv` then re-run `bash setup.sh`

**`ANTHROPIC_API_KEY not set`**  
→ Open `.env` and add your key

**Server: `Looks like you launched a headed browser without having a XServer`**  
→ Headless mode is not enabled. Set it in `user_config.json`:
```bash
echo '{"browser": {"headless": true}}' > user_config.json
```
→ Or set `headless = TRUE` in your Google Sheet's Settings tab. Note: Google Sheets overrides `user_config.json`, so check the sheet if the problem persists.

**Script creates a new Google Sheet instead of using your existing one**  
→ Save the existing sheet's ID to `credentials/sheets_id.txt`:
```bash
echo "your-sheet-id-here" > credentials/sheets_id.txt
```
Get the ID from the URL: `docs.google.com/spreadsheets/d/<ID>/edit`

**Google Drive: `EOF when reading a line` / authorization loop**  
→ Run `python auth_google_drive.py` interactively (not in background) to complete OAuth and save `credentials/token.json`

**🔐 LinkedIn/Indeed session expired (Telegram message)**  
→ `python main.py --login` on your Mac (needs a display), then copy cookies to server:
```bash
scp cookies/linkedin.json cookies/indeed.json user@server:/path/to/app/cookies/
```

**PDF not generated**  
→ `source .venv/bin/activate && playwright install chromium`

**On server: Playwright deps missing**  
→ `sudo .venv/bin/playwright install-deps chromium`

**Google Drive / Sheets: `credentials/google_credentials.json not found`**  
→ Download OAuth 2.0 Desktop JSON from Google Cloud Console and save to that path

**Telegram: no messages received**  
→ Send at least one message to your bot first; check token and chat ID in `.env`

**All jobs skipped**  
→ Lower `min_relevance_score` in Google Sheets Settings tab or `user_config.json` (default: 70)

**Dashboard won't open**  
→ `source .venv/bin/activate && python dashboard.py`; check `DASHBOARD_PORT` in `.env`

**Two instances conflict**  
→ Set different `DASHBOARD_PORT` in each instance's `.env`; stagger `run_at` times by 30+ minutes

---

## Security

- `.env` — in `.gitignore`, never committed
- `credentials/` — OAuth tokens, in `.gitignore`
- `cookies/` — browser sessions, in `.gitignore`
- `user_config.json` — personal settings, in `.gitignore`
- No passwords stored — only session cookies and OAuth tokens
