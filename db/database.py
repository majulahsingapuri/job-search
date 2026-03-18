"""
db/database.py
All SQLite operations: schema, insertion, deduplication, Stage 2 updates.
"""

import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

import os

DB_PATH = Path(os.getenv("DB_PATH", "./jobs.db"))


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id               TEXT PRIMARY KEY,
                title            TEXT NOT NULL,
                company          TEXT NOT NULL,
                location         TEXT,
                url              TEXT,
                source           TEXT,
                description      TEXT,
                date_found       TEXT NOT NULL,
                fit_score        REAL,
                fit_reasoning    TEXT,
                resume_variant   TEXT,
                resume_reasoning TEXT,
                outreach_draft   TEXT,
                people_to_reach  TEXT,
                red_flags        TEXT,
                status           TEXT DEFAULT 'new',
                notified         INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at     TEXT NOT NULL,
                source     TEXT NOT NULL,
                jobs_found INTEGER DEFAULT 0,
                jobs_new   INTEGER DEFAULT 0,
                error      TEXT
            );
        """
        )
    _migrate()


def _migrate():
    new_cols = [
        ("fit_reasoning", "TEXT"),
        ("resume_reasoning", "TEXT"),
        ("people_to_reach", "TEXT"),
        ("red_flags", "TEXT"),
    ]
    with get_connection() as conn:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col, col_type in new_cols:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")


def make_job_id(title: str, company: str, url: str) -> str:
    raw = f"{title.lower().strip()}{company.lower().strip()}{url.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_duplicate(job_id: str) -> bool:
    with get_connection() as conn:
        return (
            conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            is not None
        )


def insert_job(job: dict) -> bool:
    job_id = make_job_id(job["title"], job["company"], job.get("url", ""))
    if is_duplicate(job_id):
        return False
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, title, company, location, url, source, description, date_found)
            VALUES (:id, :title, :company, :location, :url, :source, :description, :date_found)
        """,
            {
                "id": job_id,
                "title": job["title"],
                "company": job["company"],
                "location": job.get("location", ""),
                "url": job.get("url", ""),
                "source": job.get("source", ""),
                "description": job.get("description", ""),
                "date_found": datetime.utcnow().isoformat(),
            },
        )
    return True


def log_scrape_run(source: str, jobs_found: int, jobs_new: int, error: str = None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO scrape_runs (run_at, source, jobs_found, jobs_new, error)
            VALUES (?, ?, ?, ?, ?)
        """,
            (datetime.utcnow().isoformat(), source, jobs_found, jobs_new, error),
        )


def get_unscored_jobs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs WHERE fit_score IS NULL ORDER BY date_found DESC
        """
        ).fetchall()
        return [dict(r) for r in rows]


def update_job_agent_results(
    job_id: str,
    fit_score: float,
    fit_reasoning: str,
    resume_variant: str,
    resume_reasoning: str,
    outreach_draft: str,
    people_to_reach: str,
    red_flags: str,
):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs SET
                fit_score=?, fit_reasoning=?, resume_variant=?,
                resume_reasoning=?, outreach_draft=?, people_to_reach=?, red_flags=?
            WHERE id=?
        """,
            (
                fit_score,
                fit_reasoning,
                resume_variant,
                resume_reasoning,
                outreach_draft,
                people_to_reach,
                red_flags,
                job_id,
            ),
        )


def get_unnotified_jobs(min_fit_score: float = 0.0) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified=0 AND fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, date_found DESC
        """,
            (min_fit_score,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notified(job_ids: list[str]):
    with get_connection() as conn:
        conn.executemany(
            "UPDATE jobs SET notified=1 WHERE id=?", [(j,) for j in job_ids]
        )
