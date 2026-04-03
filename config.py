"""
Central configuration. Edit SEARCH_CONFIG and PLATFORMS to customize your search.
Overrides can also be saved via the dashboard (python dashboard.py) to user_config.json
without touching this file.
"""

import json
from pathlib import Path


def _load_user_config() -> dict:
    """Load user_config.json overrides saved from the dashboard."""
    p = Path("user_config.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Company blacklist ──────────────────────────────────────────────────────────
# Jobs from these companies (or any known subsidiary / parent) are skipped.
# Keys are the group label shown in logs; values are substrings matched
# case-insensitively against the scraped company name.
COMPANY_BLACKLIST: dict[str, list[str]] = {
    "Jack Henry & Associates": [
        "Jack Henry",
        "Banno",
        "ProfitStars",
        "Profitstars",
        "iPay Technologies",
        "Goldleaf",
        "Pemco Technology",
    ],
    "SAP": [
        "SAP SE",
        "SAP Labs",
        "SAP America",
        "SAP AG",
        " SAP ",          # standalone word (spaces prevent matching "esap", "asap")
        "SuccessFactors",
        "Concur Technologies",
        "Qualtrics",
        "Ariba",
        "Hybris",
        "Signavio",
        "LeanIX",
        "WalkMe",
        "Callidus",
        "BusinessObjects",
        "Sybase",
    ],
    "Akuna Capital": [
        "Akuna Capital",
    ],
}


def is_blacklisted(company: str) -> tuple[bool, str]:
    """
    Returns (True, group_name) if the company matches any blacklist entry,
    (False, "") otherwise.
    """
    c = company.lower()
    for group, names in COMPANY_BLACKLIST.items():
        for name in names:
            if name.lower() in c:
                return True, group
    return False, ""


# ── Job / position blacklist ───────────────────────────────────────────────────
# If the job TITLE contains any of these substrings (case-insensitive), skip it.
JOB_TITLE_BLACKLIST: list[str] = [
    "clearance",
    "cleared",
    "secret",
    "top secret",
    "ts/sci",
    "polygraph",
]

# If the job DESCRIPTION contains any of these phrases (case-insensitive), skip it.
# These phrases reliably indicate roles requiring security clearance or citizenship.
JOB_DESCRIPTION_BLACKLIST: list[str] = [
    "security clearance",
    "clearance required",
    "active clearance",
    "secret clearance",
    "top secret",
    "ts/sci",
    "must hold a clearance",
    "must have clearance",
    "clearance is required",
    "polygraph",
    "us citizen only",
    "us citizenship required",
    "must be a us citizen",
    "united states citizen only",
    "citizens only",
    "public trust clearance",
    "dod clearance",
    "dhs clearance",
    "central intelligence agency",   # avoid matching "cia" inside words like "proficiency"
    "national security agency",       # avoid matching "nsa" inside words
    "department of defense",
    "must be eligible for security clearance",
    "ability to obtain a clearance",
    "clearance eligible",
]


def is_job_blacklisted(title: str, description: str) -> tuple[bool, str]:
    """
    Returns (True, reason) if the job title or description matches the blacklist,
    (False, "") otherwise.
    """
    title_lower = title.lower()
    for phrase in JOB_TITLE_BLACKLIST:
        if phrase.lower() in title_lower:
            return True, f"title contains '{phrase}'"

    desc_lower = description.lower()
    for phrase in JOB_DESCRIPTION_BLACKLIST:
        if phrase.lower() in desc_lower:
            return True, f"description contains '{phrase}'"

    return False, ""


SEARCH_CONFIG = {
    # All roles to search for across platforms
    "job_titles": [
        "DevOps Engineer",
        "Platform Engineer",
        "Cloud Engineer",
        "Infrastructure Engineer",
        "Site Reliability Engineer",
        "SRE",
        "Terraform Engineer",
        "Automation Engineer",
    ],
    "location": "United States",
    "remote_only": True,
    "experience_level": "mid-senior",

    # Jobs below this score (0-100) are skipped automatically
    "min_relevance_score": 70,

    # Require manual confirmation before each submission
    # Set to False for fully automatic (use carefully!)
    "require_review": True,

    # Max applications to submit per run
    # In a typical day there are 5-15 good new remote DevOps jobs across all platforms.
    # Set to a high number if you want to apply to everything above the score threshold.
    "max_applications_per_run": 20,

    # Skip companies we already applied to (cross-platform dedup)
    "skip_duplicate_companies": True,

    # How many days back to look for jobs (1 = last 24 hours, matches daily schedule)
    "posted_within_days": 1,
}

PLATFORMS = {
    "linkedin":        {"enabled": True,  "max_jobs_to_scrape": 30},
    "indeed":          {"enabled": True,  "max_jobs_to_scrape": 30},
    "weworkremotely":  {"enabled": True,  "max_jobs_to_scrape": 20},
    "dice":            {"enabled": True,  "max_jobs_to_scrape": 20},
    "wellfound":       {"enabled": False, "max_jobs_to_scrape": 15},
}

AI_CONFIG = {
    "model": "claude-opus-4-6",
    "use_thinking": True,
}

PATHS = {
    "database":        "jobs.db",
    "cookies_dir":     "cookies/",
    "output_dir":      "output/",
    "report_dir":      "reports/",
    "resume_template": "resume/template.html",
    "base_resume":     "resume/base_resume.json",
}

BROWSER_CONFIG = {
    # False = visible browser window (easier to debug and handle CAPTCHAs)
    # True  = headless (no display — required on servers, good for scheduled runs)
    "headless": False,
    "slow_mo": 80,
    "viewport": {"width": 1280, "height": 900},
}

SCHEDULER_CONFIG = {
    # Time to run daily (24h format)
    "run_at": "09:00",
    # Send email report after each run (requires SMTP config in .env)
    "email_report": False,
}

GOOGLE_DRIVE_CONFIG = {
    # Set GOOGLE_DRIVE_ENABLED=true in .env to activate
    # Folder name that will be created in your Google Drive root
    "root_folder": "Job Search Automation",
    # Subfolders inside the root folder
    "applications_subfolder": "Applications",  # PDFs + cover letters
    "reports_subfolder": "Reports",            # HTML reports
}

# ── Apply user_config.json overrides (written by dashboard.py) ────────────────
_overrides = _load_user_config()
if _overrides.get("search"):
    SEARCH_CONFIG.update(_overrides["search"])
if _overrides.get("platforms"):
    for pname, pvals in _overrides["platforms"].items():
        if pname in PLATFORMS:
            PLATFORMS[pname].update(pvals)
if _overrides.get("scheduler"):
    SCHEDULER_CONFIG.update(_overrides["scheduler"])
if _overrides.get("browser"):
    BROWSER_CONFIG.update(_overrides["browser"])
if _overrides.get("blacklist"):
    # Merge additional blacklist entries from user config
    for group, names in _overrides["blacklist"].items():
        if group in COMPANY_BLACKLIST:
            existing = set(COMPANY_BLACKLIST[group])
            COMPANY_BLACKLIST[group] = list(existing | set(names))
        else:
            COMPANY_BLACKLIST[group] = names
