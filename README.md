# Job Search Automation

Automated job search and application pipeline for DevOps / Platform / Cloud / SRE roles.

Searches LinkedIn, Indeed, We Work Remotely, and Dice every day — scores each job with Claude AI, adapts your resume to match the job description, generates a cover letter, and applies automatically. Sends Telegram notifications and backs up everything to Google Drive.

**Designed to run on your Mac laptop.** Scraping requires a real browser with your logged-in sessions and a residential IP address.

---

## What It Does

1. **Searches** 4 job boards for remote US positions posted in the last 24 hours
2. **Scores** each job 0-100 with Claude AI — skips anything below your threshold
3. **Adapts** your resume summary and bullets to match each job description
4. **Generates** a PDF resume + personalized cover letter per job
5. **Deduplicates** — never applies to the same job or company twice
6. **Applies** via LinkedIn Easy Apply, Indeed Quick Apply, or generic form filler
7. **Notifies** you on Telegram: matches, successful applications, errors, run summaries
8. **Uploads** all files to Google Drive automatically
9. **Reports** a full HTML summary after each run
10. **Auto-starts** on Mac boot — if the laptop was off during the scheduled run, it catches up immediately and notifies you via Telegram

---

## Requirements

- **Mac laptop** (macOS 12+) — required for browser automation with visible login
- **Python 3.10+** — check with `python3 --version`
- **Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com)

---

## Setup (one-time, ~10 minutes)

### 1. Clone and install

```bash
git clone https://github.com/aaa494/job-search-automation.git
cd job-search-automation
bash setup.sh
source .venv/bin/activate
```

### 2. Add your Anthropic API key

Open `.env` and fill in:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Fill in your resume

Edit `resume/base_resume.json` with your real information.  
The file is fully commented — fill in personal info, skills, experience, education, certifications.

### 4. Customize job search (optional)

Edit `config.py`:
```python
SEARCH_CONFIG = {
    "job_titles": ["DevOps Engineer", "Platform Engineer", ...],
    "min_relevance_score": 70,    # skip jobs below this (0-100)
    "max_applications_per_run": 8,
    "require_review": True,       # ask before each submission
}
```

---

## First Run

```bash
source .venv/bin/activate
python main.py --dry-run
```

This searches for 1 real job, adapts your resume, generates a PDF + cover letter — but does **not** submit anything. Good for verifying everything works.

**What happens with login:**  
A browser window opens. Click **"Sign in with Google"** and log in to LinkedIn/Indeed normally. Come back to the terminal and press **ENTER**. Your session is saved — subsequent runs log in automatically.

---

## Daily Usage

```bash
# Full run — review each job before applying
python main.py

# Automatic — no prompts (for scheduler)
python main.py --auto

# Search only — see scores, no applying
python main.py --search-only

# Show stats in terminal
python main.py --stats

# Generate + open HTML report
python main.py --report
```

---

## Auto-Start on Mac Boot (recommended)

Install once — the scheduler starts automatically every time your Mac turns on.

```bash
bash macos_autostart.sh install
```

**How it works:**
- Mac boots → scheduler starts in the background
- Every day at 09:00 (configurable in `config.py`) → job search runs automatically
- **If your Mac was off** during the scheduled time → on next boot, scheduler detects the missed run, sends you a Telegram message, and starts the job search immediately
- You receive Telegram notifications for every match, application, and daily summary

```bash
bash macos_autostart.sh status     # check if running
bash macos_autostart.sh uninstall  # remove auto-start
```

Logs are saved to `logs/scheduler.log`.

To change the run time, edit `config.py`:
```python
SCHEDULER_CONFIG = {
    "run_at": "09:00",  # 24h format, your local time
}
```

---

## Optional: Telegram Notifications

Get notified on your phone for every match, application, and run summary.

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

**Telegram bot commands** (type `/` in your bot chat to see the dropdown):

Run the bot in a separate terminal:
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

## Optional: Google Drive

Store all resumes, cover letters, and reports in your Google Drive automatically.

**Setup (5 minutes):**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → **APIs & Services** → **Enable Google Drive API**
3. **Credentials** → **Create OAuth 2.0 Client ID** → Application type: **Desktop app**
4. Download the JSON → save as `credentials/google_credentials.json`
5. In `.env`, set `GOOGLE_DRIVE_ENABLED=true`
6. Run once to authorize: `python auth_google_drive.py`

Files are organized in Drive:
```
Job Search Automation/
  ├── Applications/     ← PDF resumes + cover letters
  └── Reports/          ← HTML reports
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
├── main.py                    # Entry point
├── config.py                  # All configuration — edit this
├── database.py                # SQLite job tracking
├── reporter.py                # HTML report generator
├── scheduler.py               # Daily auto-run with missed-run detection
├── macos_autostart.sh         # Install/remove macOS LaunchAgent
├── telegram_bot.py            # Telegram command handler
├── telegram_notifier.py       # Telegram outgoing notifications
├── google_drive.py            # Google Drive uploader
├── pdf_generator.py           # Resume → PDF via Playwright
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
├── logs/                      # Scheduler logs (created on first run)
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
→ Lower `min_relevance_score` in `config.py` (default: 70)

**Auto-start not working**  
→ Run `bash macos_autostart.sh status` to check; check `logs/scheduler_error.log`

---

## Security

- `.env` is in `.gitignore` — never committed
- `credentials/` OAuth tokens are in `.gitignore`
- `cookies/` browser sessions are in `.gitignore`
- No passwords stored anywhere — only session cookies
