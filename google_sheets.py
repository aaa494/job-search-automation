"""
Google Sheets tracker — single spreadsheet with three tabs:

  Applications  — live job tracker (title, company, platform, score, status, files, email response)
  Settings      — all config editable in the sheet (script reads these before each run)
  Blacklist     — company blacklist editable in the sheet

Setup (one-time, same credentials as Drive):
  - Uses credentials/google_credentials.json (same file as Drive)
  - Separate token file: credentials/sheets_token.json
  - Needs Google Sheets API enabled in the same GCP project as Drive API
  - On first run, browser will open to authorize — one time only

Add to .env:
  GOOGLE_SHEETS_ENABLED=true

On first run the script creates the spreadsheet and populates Settings + Blacklist
with the current defaults from config.py. Edit the cells in Google Sheets — the
script will read them before every run.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
CREDS_DIR   = Path("credentials")
CREDS_FILE  = CREDS_DIR / "google_credentials.json"
TOKEN_FILE  = CREDS_DIR / "sheets_token.json"
SHEET_NAME  = "Job Applications"
DRIVE_FOLDER_NAME = "Job Search Automation"   # same folder used by google_drive.py

# ── Sheet names ────────────────────────────────────────────────────────────────
TAB_APPLICATIONS = "Applications"
TAB_SETTINGS     = "Settings"
TAB_BLACKLIST    = "Blacklist"

# ── Applications tab columns ───────────────────────────────────────────────────
APP_HEADERS = [
    "Job Title", "Company", "Platform", "Score", "Status",
    "Applied Date", "Resume Link", "Cover Letter Link",
    "Email Response", "Job URL", "Last Updated", "Found At",
    "_key",   # hidden lookup column — platform:job_id (column M)
]

# ── Settings tab structure ─────────────────────────────────────────────────────
# Each row: [Setting key, Value, Description]
SETTINGS_ROWS = [
    ["min_relevance_score",       "70",            "Jobs below this score (0-100) are skipped"],
    ["max_applications_per_run",  "20",            "Max jobs to apply per run"],
    ["posted_within_days",        "3",             "Search jobs posted in last N days (3 = last 72h)"],
    ["require_review",            "FALSE",         "TRUE = ask before each submission; FALSE = fully automatic"],
    ["skip_duplicate_companies",  "TRUE",          "Skip companies already applied to"],
    ["remote_only",               "TRUE",          "Remote positions only"],
    ["location",                  "United States", "Location to search"],
    ["job_titles",                "DevOps Engineer, Platform Engineer, Cloud Engineer, Infrastructure Engineer, Site Reliability Engineer, SRE, Terraform Engineer, Automation Engineer",
                                                   "Comma-separated list of job titles to search"],
    ["run_at",                    "08:00",         "Daily run time in HH:MM (24h local time) — Telegram digest sent at end of run"],
    ["digest_lookback_days",      "7",             "Days of prepared jobs to include in the morning Telegram digest"],
    ["headless",                  "FALSE",         "TRUE = headless browser (no window); FALSE = visible (default)"],
    ["linkedin_enabled",          "TRUE",          "Search LinkedIn"],
    ["linkedin_max_jobs",         "30",            "Max LinkedIn jobs per run"],
    ["indeed_enabled",            "TRUE",          "Search Indeed"],
    ["indeed_max_jobs",           "30",            "Max Indeed jobs per run"],
    ["weworkremotely_enabled",    "TRUE",          "Search We Work Remotely"],
    ["weworkremotely_max_jobs",   "20",            "Max We Work Remotely jobs per run"],
    ["dice_enabled",              "TRUE",          "Search Dice"],
    ["dice_max_jobs",             "20",            "Max Dice jobs per run"],
    ["job_title_blacklist",       "clearance, cleared, secret, top secret, ts/sci, polygraph",
                                                   "Comma-separated words — skip job if TITLE contains any of these"],
    ["job_description_blacklist", "security clearance, clearance required, active clearance, secret clearance, top secret, ts/sci, polygraph, us citizen only, us citizenship required, must be a us citizen, public trust clearance, dod clearance",
                                                   "Comma-separated phrases — skip job if DESCRIPTION contains any of these"],
]
SETTINGS_HEADERS = ["Setting", "Value", "Description"]

# ── Blacklist tab structure ────────────────────────────────────────────────────
# Each row: [Group name, Comma-separated names]
BLACKLIST_HEADERS = ["Group", "Names (comma-separated)"]


def is_enabled() -> bool:
    return os.getenv("GOOGLE_SHEETS_ENABLED", "").lower() == "true"


# ── Auth ───────────────────────────────────────────────────────────────────────

def _authenticate():
    """Returns (sheets_service, drive_service) tuple, or (None, None) on failure."""
    if not _GOOGLE_AVAILABLE:
        print("[Sheets] google-api-python-client not installed.")
        return None, None
    if not CREDS_FILE.exists():
        print(f"[Sheets] {CREDS_FILE} not found — see Drive setup instructions.")
        return None, None

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_FILE), SCOPES,
                redirect_uri="urn:ietf:wg:oauth:2.0:oob",
            )
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\n" + "=" * 60)
            print("[Sheets] Open this URL in your browser to authorize:")
            print(f"\n  {auth_url}\n")
            print("Paste the code Google gives you:")
            print("=" * 60)
            code = input("  Paste code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

        CREDS_DIR.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
        print("[Sheets] Token saved.")

    sheets_svc = build("sheets", "v4", credentials=creds)
    drive_svc  = build("drive",  "v3", credentials=creds)
    return sheets_svc, drive_svc


def _get_or_create_drive_folder(drive_svc, name: str) -> str | None:
    """Find or create a Drive folder by name. Returns folder ID or None."""
    try:
        q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
             f"and trashed=false")
        res = drive_svc.files().list(q=q, fields="files(id)").execute()
        files = res.get("files", [])
        if files:
            return files[0]["id"]
        # Create it
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        folder = drive_svc.files().create(body=meta, fields="id").execute()
        return folder["id"]
    except Exception as e:
        print(f"[Sheets] Drive folder lookup failed: {e}")
        return None


def _move_to_folder(drive_svc, file_id: str, folder_id: str):
    """Move a Drive file into a folder (removes from all other parents)."""
    try:
        # Get current parents
        f = drive_svc.files().get(fileId=file_id, fields="parents").execute()
        prev_parents = ",".join(f.get("parents", []))
        drive_svc.files().update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=prev_parents,
            fields="id, parents",
        ).execute()
    except Exception as e:
        print(f"[Sheets] Could not move spreadsheet to Drive folder: {e}")


# ── Sheet creation + lookup ────────────────────────────────────────────────────

def _get_sheet_id(service, spreadsheet_id: str, tab_name: str) -> int | None:
    """Return the numeric sheetId for a tab name."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    return None


def _get_or_create_spreadsheet(service, drive_svc) -> str:
    """
    Returns the spreadsheet ID, creating all three tabs if it doesn't exist.
    Always ensures the spreadsheet lives inside the 'Job Search Automation' Drive folder.
    """
    id_file = CREDS_DIR / "sheets_id.txt"
    folder_id = _get_or_create_drive_folder(drive_svc, DRIVE_FOLDER_NAME) if drive_svc else None

    if id_file.exists():
        sid = id_file.read_text().strip()
        try:
            service.spreadsheets().get(spreadsheetId=sid).execute()
            # Move to folder if we have a drive service (idempotent)
            if drive_svc and folder_id:
                _move_to_folder(drive_svc, sid, folder_id)
            return sid
        except Exception:
            pass  # Deleted or lost access — recreate

    # Create spreadsheet with three tabs at once
    body = {
        "properties": {"title": SHEET_NAME},
        "sheets": [
            {"properties": {"title": TAB_APPLICATIONS}},
            {"properties": {"title": TAB_SETTINGS}},
            {"properties": {"title": TAB_BLACKLIST}},
        ],
    }
    sheet = service.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    sid = sheet["spreadsheetId"]
    CREDS_DIR.mkdir(exist_ok=True)
    id_file.write_text(sid)

    _init_applications_tab(service, sid)
    _init_settings_tab(service, sid)
    _init_blacklist_tab(service, sid)

    # Move into Drive folder
    if drive_svc and folder_id:
        _move_to_folder(drive_svc, sid, folder_id)

    print(f"[Sheets] Created: https://docs.google.com/spreadsheets/d/{sid}")
    return sid


def _init_applications_tab(service, sid: str):
    # Write header
    service.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{TAB_APPLICATIONS}!A1",
        valueInputOption="RAW",
        body={"values": [APP_HEADERS]},
    ).execute()
    sheet_id = _get_sheet_id(service, sid, TAB_APPLICATIONS)
    if sheet_id is not None:
        _bold_freeze_header(service, sid, sheet_id)
    # Hide the _key column (column M = index 12)
    _hide_column(service, sid, sheet_id, 12)


def _init_settings_tab(service, sid: str):
    rows = [SETTINGS_HEADERS] + SETTINGS_ROWS
    service.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{TAB_SETTINGS}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    sheet_id = _get_sheet_id(service, sid, TAB_SETTINGS)
    if sheet_id is not None:
        _bold_freeze_header(service, sid, sheet_id)
    print("[Sheets] Settings tab initialized with defaults.")


def _init_blacklist_tab(service, sid: str):
    from config import COMPANY_BLACKLIST
    rows = [BLACKLIST_HEADERS]
    for group, names in COMPANY_BLACKLIST.items():
        rows.append([group, ", ".join(names)])
    service.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{TAB_BLACKLIST}!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    sheet_id = _get_sheet_id(service, sid, TAB_BLACKLIST)
    if sheet_id is not None:
        _bold_freeze_header(service, sid, sheet_id)
    print("[Sheets] Blacklist tab initialized.")


def _bold_freeze_header(service, sid: str, sheet_id: int):
    service.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }},
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]},
    ).execute()


def _hide_column(service, sid: str, sheet_id: int | None, col_index: int):
    if sheet_id is None:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [{
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_index,
                    "endIndex": col_index + 1,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }]},
    ).execute()


# ── Read config from Settings tab ─────────────────────────────────────────────

def read_settings_from_sheet(service, sid: str) -> dict:
    """
    Reads the Settings tab and returns a config dict in the same format
    as user_config.json (compatible with config.py's merge logic).
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_SETTINGS}!A2:B"
    ).execute()
    rows = result.get("values", [])
    raw = {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2}

    def _bool(v: str) -> bool:
        return v.upper() in ("TRUE", "YES", "1", "ON")

    cfg: dict = {"search": {}, "platforms": {}, "scheduler": {}, "browser": {}}

    # Search
    if "min_relevance_score" in raw:
        cfg["search"]["min_relevance_score"] = int(raw["min_relevance_score"])
    if "max_applications_per_run" in raw:
        cfg["search"]["max_applications_per_run"] = int(raw["max_applications_per_run"])
    if "posted_within_days" in raw:
        cfg["search"]["posted_within_days"] = int(raw["posted_within_days"])
    if "require_review" in raw:
        cfg["search"]["require_review"] = _bool(raw["require_review"])
    if "skip_duplicate_companies" in raw:
        cfg["search"]["skip_duplicate_companies"] = _bool(raw["skip_duplicate_companies"])
    if "remote_only" in raw:
        cfg["search"]["remote_only"] = _bool(raw["remote_only"])
    if "location" in raw:
        cfg["search"]["location"] = raw["location"]
    if "job_titles" in raw:
        cfg["search"]["job_titles"] = [t.strip() for t in raw["job_titles"].split(",") if t.strip()]
    if "digest_lookback_days" in raw:
        cfg["search"]["digest_lookback_days"] = int(raw["digest_lookback_days"])

    # Platforms
    for p in ["linkedin", "indeed", "weworkremotely", "dice"]:
        enabled_key = f"{p}_enabled"
        max_key = f"{p}_max_jobs"
        if enabled_key in raw or max_key in raw:
            cfg["platforms"].setdefault(p, {})
            if enabled_key in raw:
                cfg["platforms"][p]["enabled"] = _bool(raw[enabled_key])
            if max_key in raw:
                cfg["platforms"][p]["max_jobs_to_scrape"] = int(raw[max_key])

    # Scheduler
    if "run_at" in raw:
        cfg["scheduler"]["run_at"] = raw["run_at"]

    # Browser
    if "headless" in raw:
        cfg["browser"]["headless"] = _bool(raw["headless"])

    # Job blacklists
    if "job_title_blacklist" in raw:
        cfg["job_title_blacklist"] = [t.strip() for t in raw["job_title_blacklist"].split(",") if t.strip()]
    if "job_description_blacklist" in raw:
        cfg["job_description_blacklist"] = [t.strip() for t in raw["job_description_blacklist"].split(",") if t.strip()]

    return cfg


def read_blacklist_from_sheet(service, sid: str) -> dict[str, list[str]]:
    """Read the Blacklist tab and return {group: [name, ...]}."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_BLACKLIST}!A2:B"
    ).execute()
    rows = result.get("values", [])
    bl: dict[str, list[str]] = {}
    for row in rows:
        if len(row) >= 2 and row[0].strip():
            group = row[0].strip()
            names = [n.strip() for n in row[1].split(",") if n.strip()]
            bl[group] = names
    return bl


def apply_sheets_config() -> None:
    """
    Called by main.py at startup when sheets are enabled.
    Reads Settings + Blacklist tabs and mutates the config module dicts in-place
    so the updated values are used for the rest of the run.
    """
    if not is_enabled():
        return
    service, drive_svc = _authenticate()
    if not service:
        return
    try:
        sid = _get_or_create_spreadsheet(service, drive_svc)

        # Read settings
        settings = read_settings_from_sheet(service, sid)
        import config as _cfg
        if settings.get("search"):
            _cfg.SEARCH_CONFIG.update(settings["search"])
        if settings.get("platforms"):
            for pname, pvals in settings["platforms"].items():
                if pname in _cfg.PLATFORMS:
                    _cfg.PLATFORMS[pname].update(pvals)
        if settings.get("scheduler"):
            _cfg.SCHEDULER_CONFIG.update(settings["scheduler"])
        if settings.get("browser"):
            _cfg.BROWSER_CONFIG.update(settings["browser"])

        # Read company blacklist
        bl = read_blacklist_from_sheet(service, sid)
        if bl:
            _cfg.COMPANY_BLACKLIST.clear()
            _cfg.COMPANY_BLACKLIST.update(bl)

        # Read job/position blacklists
        if settings.get("job_title_blacklist"):
            _cfg.JOB_TITLE_BLACKLIST[:] = settings["job_title_blacklist"]
        if settings.get("job_description_blacklist"):
            _cfg.JOB_DESCRIPTION_BLACKLIST[:] = settings["job_description_blacklist"]

        print("[Sheets] Config loaded from Google Sheets.")
    except Exception as e:
        print(f"[Sheets] apply_sheets_config error: {e}")


# ── Applications tab sync ──────────────────────────────────────────────────────

def _find_row_by_key(service, sid: str, platform: str, job_id: str) -> int | None:
    """Returns the 1-based row index of an existing job entry, or None."""
    key_result = service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_APPLICATIONS}!M:M"
    ).execute()
    key_rows = key_result.get("values", [])
    target = f"{platform}:{job_id}"
    for i, row in enumerate(key_rows):
        if row and row[0] == target:
            return i + 1  # 1-based
    return None


def sync_all_jobs(db_path: str = "jobs.db") -> None:
    """
    Reads all tracked jobs from SQLite and syncs them to the Applications tab.
    Creates new rows for new jobs, updates existing rows.
    Also ensures Settings and Blacklist tabs exist (creates them if not).
    """
    if not is_enabled():
        return

    service, drive_svc = _authenticate()
    if not service:
        return

    try:
        sid = _get_or_create_spreadsheet(service, drive_svc)

        # Ensure Settings and Blacklist tabs exist (they may not if spreadsheet was pre-existing)
        _ensure_extra_tabs(service, sid)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT platform, job_id, title, company, relevance_score,
                       status, applied_at, resume_path, cover_letter_path,
                       url, notes, created_at
                FROM jobs
                WHERE status IN ('applied', 'reviewing', 'skipped', 'rejected', 'error', 'found', 'prepared')
                ORDER BY applied_at DESC NULLS LAST
            """).fetchall()

        # Get existing keys (column M now that Found At is column L)
        key_result = service.spreadsheets().values().get(
            spreadsheetId=sid, range=f"{TAB_APPLICATIONS}!M:M"
        ).execute()
        key_rows = key_result.get("values", [])
        existing_keys = {row[0]: idx + 1 for idx, row in enumerate(key_rows) if row}

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        updates = []
        appends = []

        for job in rows:
            key = f"{job['platform']}:{job['job_id']}"
            score = f"{job['relevance_score']:.0f}" if job['relevance_score'] else ""
            applied = (job['applied_at'] or "")[:16]
            found_at = (job['created_at'] or "")[:16]

            row_data = [
                job['title'] or "",           # A - Job Title
                job['company'] or "",         # B - Company
                job['platform'] or "",        # C - Platform
                score,                        # D - Score
                job['status'] or "",          # E - Status
                applied,                      # F - Applied Date
                "",                           # G - Resume Link (filled by update_job_links)
                "",                           # H - Cover Letter Link (filled by update_job_links)
                job['notes'] or "",           # I - Email Response / notes
                job['url'] or "",             # J - Job URL
                now,                          # K - Last Updated
                found_at,                     # L - Found At
                key,                          # M - _key (hidden)
            ]

            if key in existing_keys:
                row_num = existing_keys[key]
                # Update A-F (title→applied_date) and I-M (notes→_key).
                # Skip G-H (Resume Link / Cover Letter Link) — written by update_job_links().
                updates.append({
                    "range": f"{TAB_APPLICATIONS}!A{row_num}:F{row_num}",
                    "values": [row_data[:6]],
                })
                updates.append({
                    "range": f"{TAB_APPLICATIONS}!I{row_num}:M{row_num}",
                    "values": [row_data[8:]],
                })
            else:
                appends.append(row_data)

        if updates:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": updates},
            ).execute()

        if appends:
            service.spreadsheets().values().append(
                spreadsheetId=sid,
                range=f"{TAB_APPLICATIONS}!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": appends},
            ).execute()

        total = len(updates) + len(appends)
        print(f"[Sheets] Synced {total} jobs ({len(updates)} updated, {len(appends)} added)")
        print(f"[Sheets] https://docs.google.com/spreadsheets/d/{sid}")

    except Exception as e:
        err = str(e)
        if "SERVICE_DISABLED" in err or "sheets.googleapis.com" in err:
            print(
                "\n[Sheets] Google Sheets API is not enabled.\n"
                "  → Go to: https://console.developers.google.com/apis/api/sheets.googleapis.com\n"
                "  → Click 'Enable', wait 1 minute, then run again.\n"
                "  → Set GOOGLE_SHEETS_ENABLED=false in .env to skip."
            )
        else:
            print(f"[Sheets] Sync error: {e}")


def _ensure_extra_tabs(service, sid: str):
    """Create Settings and Blacklist tabs if they don't exist yet."""
    meta = service.spreadsheets().get(spreadsheetId=sid).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}

    add_requests = []
    for tab in (TAB_SETTINGS, TAB_BLACKLIST):
        if tab not in existing:
            add_requests.append({"addSheet": {"properties": {"title": tab}}})

    if add_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": add_requests}
        ).execute()

    if TAB_SETTINGS not in existing:
        _init_settings_tab(service, sid)
    if TAB_BLACKLIST not in existing:
        _init_blacklist_tab(service, sid)


# ── Per-job link updates (called after Drive upload) ──────────────────────────

def update_job_links(platform: str, job_id: str, resume_link: str = None, cl_link: str = None) -> None:
    """Update the Drive links for a specific job row."""
    if not is_enabled():
        return
    service, drive_svc = _authenticate()
    if not service:
        return
    try:
        sid = _get_or_create_spreadsheet(service, drive_svc)
        row_num = _find_row_by_key(service, sid, platform, job_id)
        if not row_num:
            return
        updates = []
        if resume_link:
            updates.append({"range": f"{TAB_APPLICATIONS}!G{row_num}", "values": [[resume_link]]})
        if cl_link:
            updates.append({"range": f"{TAB_APPLICATIONS}!H{row_num}", "values": [[cl_link]]})
        if updates:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": updates},
            ).execute()
    except Exception as e:
        print(f"[Sheets] update_job_links error: {e}")


# ── Email response update (called by email_checker.py) ────────────────────────

def update_email_response(platform: str, job_id: str, response_summary: str) -> None:
    """Write the email response summary into the Email Response column (I) for this job."""
    if not is_enabled():
        return
    service, drive_svc = _authenticate()
    if not service:
        return
    try:
        sid = _get_or_create_spreadsheet(service, drive_svc)
        row_num = _find_row_by_key(service, sid, platform, job_id)
        if not row_num:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": [
                {"range": f"{TAB_APPLICATIONS}!I{row_num}", "values": [[response_summary]]},
                {"range": f"{TAB_APPLICATIONS}!K{row_num}", "values": [[now]]},
            ]},
        ).execute()
        print(f"[Sheets] Email response updated for row {row_num}: {response_summary[:60]}")
    except Exception as e:
        print(f"[Sheets] update_email_response error: {e}")


def update_job_status(platform: str, job_id: str, status: str, applied_at: str = "") -> None:
    """Update Status (E) and Applied Date (F) columns for a job — used by Telegram /applied command."""
    if not is_enabled():
        return
    service, drive_svc = _authenticate()
    if not service:
        return
    try:
        sid = _get_or_create_spreadsheet(service, drive_svc)
        row_num = _find_row_by_key(service, sid, platform, job_id)
        if not row_num:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        applied_date = applied_at[:10] if applied_at else now[:10]
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": [
                {"range": f"{TAB_APPLICATIONS}!E{row_num}", "values": [[status]]},
                {"range": f"{TAB_APPLICATIONS}!F{row_num}", "values": [[applied_date]]},
                {"range": f"{TAB_APPLICATIONS}!K{row_num}", "values": [[now]]},
            ]},
        ).execute()
        print(f"[Sheets] Status updated to '{status}' for row {row_num}")
    except Exception as e:
        print(f"[Sheets] update_job_status error: {e}")


def get_sheet_url() -> str | None:
    """Returns the URL of the spreadsheet, or None if not created yet."""
    id_file = CREDS_DIR / "sheets_id.txt"
    if id_file.exists():
        sid = id_file.read_text().strip()
        return f"https://docs.google.com/spreadsheets/d/{sid}"
    return None
