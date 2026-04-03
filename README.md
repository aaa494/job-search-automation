# Job Search Automation

Automated job search and application pipeline for DevOps / Platform / Cloud / SRE roles.

Searches LinkedIn, Indeed, We Work Remotely, and Dice every day — scores each job with Claude AI, adapts your resume to match the job description, generates a cover letter, and applies automatically. Sends Telegram notifications and backs up everything to Google Drive.

**Designed to run on your Mac laptop.** Scraping requires a real browser with your logged-in sessions and a residential IP address.

---

## What It Does

1. **Searches** 4 job boards for remote US positions posted in the last 24 hours
2. **Filters** blacklisted companies (Jack Henry, SAP, Akuna Capital + subsidiaries) before scoring
3. **Scores** each job 0-100 with Claude AI — skips anything below your threshold
4. **Adapts** your resume summary and bullets to match each job description
5. **Generates** a PDF resume + personalized cover letter per job
6. **Deduplicates** — never applies to the same job or company twice
7. **Applies** via LinkedIn Easy Apply, Indeed Quick Apply, or generic form filler
8. **Notifies** you on Telegram: matches, successful applications, errors, run summaries
9. **Uploads** all files to Google Drive automatically
10. **Google Sheets control panel** — Applications, Settings, and Blacklist tabs all in one spreadsheet
    - Edit settings directly in Sheets — script reads them before every run
    - Yahoo email responses auto-update the Applications tab
11. **Dashboard** — local web UI as an alternative to Sheets for editing settings
12. **Auto-starts** on Mac boot — if the laptop was off during the scheduled run, it catches up immediately

---

## Requirements

- **Mac laptop** (macOS 12+) — required for browser automation with visible login
- **Python 3.10+** — check with `python3 --version`
- **Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com)

---

## Setup (one-time, ~15 minutes)

### Step 1 — Clone and install

```bash
git clone https://github.com/aaa494/job-search-automation.git
cd job-search-automation
bash setup.sh
```

This creates a Python virtual environment, installs all dependencies, and downloads the Chromium browser.

### Step 2 — Add your API key

Copy the env template and open it:
```bash
cp .env.example .env
open .env
```

Fill in your Anthropic API key (required):
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Get a key at [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key.

### Step 3 — Fill in your resume

```bash
open resume/base_resume.json
```

Edit with your real name, contact info, skills, experience, education, certifications.

### Step 4 — Test it (no applications submitted)

```bash
source .venv/bin/activate
python main.py --dry-run
```

A browser window will open. Log in to LinkedIn via Google, press ENTER in the terminal.
The script finds 1 job, scores it, adapts your resume, generates a PDF + cover letter.
Nothing is submitted. Check `output/` to see the generated files.

### Step 5 — Enable auto-start on Mac boot (recommended)

```bash
bash macos_autostart.sh install
```

Done. The job search now runs automatically every day at 09:00.

### Step 6 (optional) — Set up Telegram and Google Drive

See the sections below for Telegram notifications and Google Drive backup.

---

## First Run

```bash
source .venv/bin/activate
python main.py --dry-run
```

Searches for 1 real job, adapts your resume, generates a PDF + cover letter — does **not** submit anything.

**What happens with login:**
A browser window opens. Click **"Sign in with Google"** and log in to LinkedIn/Indeed normally. Come back to the terminal and press **ENTER**. Your session is saved — subsequent runs log in automatically.

---

## Daily Usage

```bash
# Full run — review each job before applying
python main.py

# Automatic — no prompts (for scheduler)
python main.py --auto

# Fully automatic end-to-end test: 1 job → apply → report
python main.py --dry-run-apply

# Limit to one platform (faster)
python main.py --dry-run-apply --linkedin
python main.py --dry-run-apply --indeed
python main.py --dry-run-apply --dice
python main.py --dry-run-apply --weworkremotely

# Search only — see scores, no applying
python main.py --search-only

# Show stats in terminal
python main.py --stats

# Generate + open HTML report
python main.py --report
```

---

## Dry Run Apply (1 job, fully automatic)

`--dry-run-apply` runs the full pipeline on exactly 1 job and applies automatically — no prompts.

```bash
# Search all platforms, pick the first matching job
python main.py --dry-run-apply

# Search only LinkedIn (much faster — no need to wait for all platforms)
python main.py --dry-run-apply --linkedin
```

What happens:
1. Searches the chosen platform(s)
2. Finds 1 job above the score threshold
3. Adapts resume, generates PDF + cover letter
4. Applies automatically (Easy Apply / Quick Apply / form filler)
5. Opens the HTML report showing the result

Use this to verify the full pipeline works before enabling the daily scheduler.

---

## Google Sheets Control Panel

When `GOOGLE_SHEETS_ENABLED=true`, the script creates and maintains a spreadsheet:
**"Job Applications"** with three tabs:

| Tab | Purpose |
|-----|---------|
| **Applications** | All jobs: title, company, score, status, files, email response |
| **Settings** | All config — edit values in the sheet, script reads them before each run |
| **Blacklist** | Company blacklist — add/remove groups and names |

**Settings tab** — editable columns:

| Setting | Default | Description |
|---------|---------|-------------|
| min_relevance_score | 70 | Jobs below this score are skipped |
| max_applications_per_run | 20 | Max jobs to apply per run |
| posted_within_days | 1 | Look back N days (1 = last 24h) |
| require_review | FALSE | TRUE = ask before each submission |
| job_titles | DevOps Engineer, ... | Comma-separated job titles |
| run_at | 09:00 | Daily run time |
| linkedin_enabled | TRUE | Search LinkedIn |
| linkedin_max_jobs | 30 | Max LinkedIn jobs per run |
| ... | ... | (same for indeed, dice, weworkremotely) |

**Yahoo email → Sheets auto-update:**
When `EMAIL_CHECK_ENABLED=true`, the script checks your Yahoo inbox after each run, classifies any job response emails with Claude AI, and writes the result into the **Email Response** column in the Applications tab. No manual action needed.

---

## Dashboard (local Settings UI)

As an alternative to Google Sheets, run the local web dashboard:

```bash
python dashboard.py          # opens http://localhost:5050
python dashboard.py --port=8080
```

| Tab | What you can do |
|-----|----------------|
| **Applications** | Live table from the database |
| **Settings** | Edit all config + blacklist — click Save |

Settings are saved to `user_config.json`. If Google Sheets is also enabled, Sheets settings take priority (they're applied last).

---

## Company Blacklist

Companies in the blacklist are skipped before any AI calls — no API costs, no files generated.

**Default blacklisted groups:**

| Group | Skipped names |
|-------|--------------|
| Jack Henry & Associates | Jack Henry, Banno, ProfitStars, iPay Technologies, Goldleaf, Pemco Technology |
| SAP | SAP SE/Labs/America/AG, SuccessFactors, Concur, Qualtrics, Ariba, Hybris, Signavio, LeanIX, WalkMe, Callidus, BusinessObjects, Sybase |
| Akuna Capital | Akuna Capital |

To add more: edit the **Blacklist tab** in Google Sheets, or the dashboard Settings tab, or `COMPANY_BLACKLIST` in `config.py`.

---

## Auto-Start on Mac Boot (recommended)

```bash
bash macos_autostart.sh install
bash macos_autostart.sh status     # check if running
bash macos_autostart.sh uninstall  # remove auto-start
```

- Mac boots → scheduler starts in background
- Every day at 09:00 (configurable) → job search runs automatically
- If your Mac was off during the scheduled time → on next boot, detects missed run, sends Telegram message, starts immediately

To change the run time, either edit `config.py` or use the dashboard Settings tab.

---

## Optional: Telegram Notifications

**Setup (2 minutes):**
1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow prompts → copy the **token** (looks like `123456:ABCdef...`)
3. Send any message to your new bot
4. Open in browser: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id": <NUMBER>}` — that's your **Chat ID**
5. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABCdef...
   TELEGRAM_CHAT_ID=123456789
   ```

**Telegram bot commands:**

```bash
python telegram_bot.py --register   # register commands (first time)
python telegram_bot.py              # start listening for commands
```

| Command | Description |
|---------|-------------|
| `/helpjob` | List all commands |
| `/stats` | Application count, scores, recent jobs |
| `/run` | Start a job search right now |
| `/stop` | Stop current run |
| `/report` | Latest run summary |
| `/status` | Is scheduler running? |

---

## Optional: Google Drive + Google Sheets

Both use the same credentials file. Enable either or both independently.

**Setup (5 minutes):**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → **APIs & Services** → enable **Google Drive API** and **Google Sheets API**
3. **Credentials** → **Create OAuth 2.0 Client ID** → Application type: **Desktop app**
4. Download the JSON → save as `credentials/google_credentials.json`
5. In `.env`:
   ```
   GOOGLE_DRIVE_ENABLED=true
   GOOGLE_SHEETS_ENABLED=true
   ```
6. Run once to authorize Drive: `python auth_google_drive.py`
7. On the next run, Sheets will authorize in the terminal (paste the code it shows you)

**Drive** — files organized automatically:
```
Job Search Automation/
  ├── Applications/     ← PDF resumes + cover letters
  └── Reports/          ← HTML reports
```

**Sheets** — one spreadsheet "Job Applications":
```
Applications tab  ← all jobs with links to Drive files + email response column
Settings tab      ← edit config in-sheet, script reads before every run
Blacklist tab     ← edit company blacklist in-sheet
```

---

## Duplicate Prevention

Jobs are tracked in `jobs.db` (SQLite). The system never applies twice to:
- The same job posting (matched by platform + job ID)
- The same company (if you've already applied there from any platform)

This persists across all runs — restoring your laptop or reinstalling doesn't reset it as long as you keep `jobs.db`.

---

## File Structure

```
job-search-automation/
├── main.py                    # Entry point — all run modes
├── config.py                  # Defaults + COMPANY_BLACKLIST
├── user_config.json           # Your overrides (written by dashboard) — not in git
├── dashboard.py               # Local web UI: Applications + Settings tabs
├── database.py                # SQLite job tracking
├── reporter.py                # HTML report generator (2 tabs)
├── scheduler.py               # Daily auto-run with missed-run detection
├── macos_autostart.sh         # Install/remove macOS LaunchAgent
├── telegram_bot.py            # Telegram command handler
├── telegram_notifier.py       # Telegram outgoing notifications
├── google_drive.py            # Google Drive uploader
├── google_sheets.py           # Google Sheets tracker (optional)
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
│   ├── linkedin.py            # LinkedIn + Easy Apply
│   ├── indeed.py              # Indeed + Quick Apply
│   ├── weworkremotely.py      # We Work Remotely
│   ├── dice.py                # Dice.com
│   └── employer_site.py       # Generic form filler (Claude Vision)
│
├── resume/
│   ├── base_resume.json       # Your resume data — edit this
│   └── template.html          # PDF template (Arial, monochrome)
│
├── credentials/               # OAuth tokens — not committed to git
├── cookies/                   # Browser sessions — not committed to git
├── output/                    # Generated PDFs and cover letters
├── reports/                   # HTML reports
├── logs/                      # Run logs
├── jobs.db                    # SQLite database
│
├── .env                       # Your secrets — not committed to git
├── .env.example               # Template with setup instructions
├── requirements.txt
├── setup.sh
├── CLAUDE.md                  # Context for Claude AI in new sessions
└── README.md
```

---

## How Resume Adaptation Works

The AI expands on skills you listed but **never invents** new ones.

If your resume lists "Kubernetes", the adapter knows that implies: pods, deployments, namespaces, RBAC, resource limits, health checks, horizontal scaling, kubectl. It can use these details to match a job description — but only because Kubernetes is already on your resume.

Banned words in all AI output: *leveraged, spearheaded, orchestrated, synergies, cutting-edge, innovative, passionate, driven, robust, seamlessly*

---

## Troubleshooting

**`ANTHROPIC_API_KEY not set`**
→ Open `.env` and add your key

**Browser doesn't open on login**
→ Make sure `BROWSER_CONFIG["headless"] = False` in `config.py` (default)

**Cloudflare blocks scraping**
→ This only happens on remote servers — run on your Mac, not a VPS

**PDF not generated**
→ Run `playwright install chromium`

**Google Drive: credentials not found**
→ File must be at `credentials/google_credentials.json`

**Telegram: no messages received**
→ Send at least one message to your bot first; check token and chat ID in `.env`

**All jobs skipped**
→ Lower `min_relevance_score` in the dashboard or `config.py` (default: 70)

**Auto-start not working**
→ Run `bash macos_autostart.sh status` to check; check `logs/scheduler_error.log`

**Dashboard won't open**
→ Make sure you're in the project folder with the venv active: `source .venv/bin/activate && python dashboard.py`

---

## Security

- `.env` is in `.gitignore` — never committed
- `credentials/` OAuth tokens are in `.gitignore`
- `cookies/` browser sessions are in `.gitignore`
- `user_config.json` is in `.gitignore` — settings stay local
- No passwords stored anywhere — only session cookies
