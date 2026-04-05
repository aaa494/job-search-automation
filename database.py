"""SQLite database for tracking job applications."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Job:
    platform: str
    job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    salary: str = ""
    relevance_score: float = 0.0
    relevance_reason: str = ""
    status: str = "found"       # found | skipped | reviewing | applied | rejected | error
    applied_at: Optional[str] = None
    resume_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    notes: str = ""
    id: Optional[int] = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.job_id}"


class Database:
    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform                TEXT    NOT NULL,
                    job_id                  TEXT    NOT NULL,
                    title                   TEXT    NOT NULL,
                    company                 TEXT    NOT NULL,
                    location                TEXT,
                    url                     TEXT,
                    description             TEXT,
                    salary                  TEXT    DEFAULT '',
                    relevance_score         REAL    DEFAULT 0,
                    relevance_reason        TEXT    DEFAULT '',
                    status                  TEXT    DEFAULT 'found',
                    applied_at              TEXT,
                    resume_path             TEXT,
                    cover_letter_path       TEXT,
                    notes                   TEXT    DEFAULT '',
                    created_at              TEXT    DEFAULT CURRENT_TIMESTAMP,
                    resume_drive_link       TEXT    DEFAULT '',
                    cover_letter_drive_link TEXT    DEFAULT '',
                    UNIQUE(platform, job_id)
                )
            """)
            # Migrate existing tables that lack drive link columns
            for col, defn in [
                ("resume_drive_link",       "TEXT DEFAULT ''"),
                ("cover_letter_drive_link", "TEXT DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()

    def save_job(self, job: Job) -> Job:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO jobs
                    (platform, job_id, title, company, location, url, description,
                     salary, relevance_score, relevance_reason, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                    title             = excluded.title,
                    relevance_score   = excluded.relevance_score,
                    relevance_reason  = excluded.relevance_reason,
                    status            = CASE WHEN jobs.status = 'found' THEN excluded.status ELSE jobs.status END
            """, (
                job.platform, job.job_id, job.title, job.company, job.location,
                job.url, job.description, job.salary, job.relevance_score,
                job.relevance_reason, job.status, job.notes,
            ))
            conn.commit()
            job.id = cursor.lastrowid
            return job

    def update_status(self, job: Job, status: str, **kwargs):
        job.status = status
        fields = {"status": status}
        fields.update(kwargs)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job.platform, job.job_id]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE platform = ? AND job_id = ?",
                values,
            )
            conn.commit()

    def mark_applied(self, job: Job, resume_path: str, cover_letter_path: str):
        self.update_status(
            job, "applied",
            applied_at=datetime.now().isoformat(),
            resume_path=resume_path,
            cover_letter_path=cover_letter_path,
        )

    def is_seen(self, platform: str, job_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE platform = ? AND job_id = ?",
                (platform, job_id),
            ).fetchone()
            return row is not None

    def company_applied(self, company: str) -> bool:
        """Check if we already applied to this company (any platform)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE LOWER(company) = LOWER(?) AND status = 'applied'",
                (company,),
            ).fetchone()
            return row is not None

    def save_drive_links(self, job: "Job", resume_link: str, cl_link: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET resume_drive_link = ?, cover_letter_drive_link = ? "
                "WHERE platform = ? AND job_id = ?",
                (resume_link or "", cl_link or "", job.platform, job.job_id),
            )
            conn.commit()

    def get_prepared_jobs(self, days: int = 7) -> list[dict]:
        """Return jobs with status 'prepared' from the last N days, newest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT title, company, url, relevance_score,
                       resume_drive_link, cover_letter_drive_link, created_at
                FROM jobs
                WHERE status = 'prepared'
                  AND datetime(created_at) >= datetime('now', ?)
                ORDER BY relevance_score DESC
                """,
                (f"-{days} days",),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM jobs GROUP BY status"
            ).fetchall()
            return {row[0]: row[1] for row in rows}

    def get_recent_applied(self, limit: int = 10) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT platform, title, company, relevance_score, applied_at
                FROM jobs WHERE status = 'applied'
                ORDER BY applied_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
