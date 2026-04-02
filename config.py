"""
Central configuration. Edit SEARCH_CONFIG and PLATFORMS to customize your search.
"""

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
    "headless": True,
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
