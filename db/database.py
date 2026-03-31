"""
db/database.py
All SQLite operations: schema, insertion, deduplication, Stage 2 updates.
"""

import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

import os

DB_PATH = Path(os.getenv("DB_PATH", "/app/db/jobs.sqlite"))


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

            CREATE TABLE IF NOT EXISTS outreach_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id       TEXT,
                job_title    TEXT,
                company      TEXT,
                query_type   TEXT,
                query_text   TEXT,
                person_name  TEXT,
                profile_url  TEXT NOT NULL UNIQUE,
                status       TEXT,
                reason       TEXT,
                note_text    TEXT,
                created_at   TEXT NOT NULL
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outreach_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id       TEXT,
                job_title    TEXT,
                company      TEXT,
                query_type   TEXT,
                query_text   TEXT,
                person_name  TEXT,
                profile_url  TEXT NOT NULL UNIQUE,
                status       TEXT,
                reason       TEXT,
                note_text    TEXT,
                created_at   TEXT NOT NULL
            )
        """
        )


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


def get_jobs_missing_descriptions(
    source: str | None = None, limit: int | None = None
) -> list[dict]:
    query = """
        SELECT *
        FROM jobs
        WHERE (description IS NULL OR TRIM(description) = '')
    """
    params: list[object] = []
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY date_found DESC"
    if limit is not None and limit > 0:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_job_description(job_id: str, description: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET description=? WHERE id=?",
            (description, job_id),
        )


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


def get_jobs_scraped_on(date_str: str) -> list[dict]:
    date_from = date_str
    date_to = f"{date_str}T23:59:59"
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE date_found >= ? AND date_found <= ? AND fit_score >= 8.5
            ORDER BY date_found DESC
            """,
            (date_from, date_to),
        ).fetchall()
        return [dict(r) for r in rows]


def update_job_status(job_id: str, status: str):
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))


def has_outreach_profile(profile_url: str) -> bool:
    if not profile_url:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM outreach_log WHERE profile_url=? AND status='sent'",
            (profile_url,),
        ).fetchone()
        return row is not None


def insert_outreach_log(
    job_id: str | None,
    job_title: str | None,
    company: str | None,
    query_type: str | None,
    query_text: str | None,
    person_name: str | None,
    profile_url: str,
    status: str | None,
    reason: str | None,
    note_text: str | None,
    created_at: str,
) -> bool:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO outreach_log (
                    job_id, job_title, company, query_type, query_text,
                    person_name, profile_url, status, reason, note_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_url) DO UPDATE SET
                    job_id=excluded.job_id,
                    job_title=excluded.job_title,
                    company=excluded.company,
                    query_type=excluded.query_type,
                    query_text=excluded.query_text,
                    person_name=excluded.person_name,
                    status=excluded.status,
                    reason=excluded.reason,
                    note_text=excluded.note_text,
                    created_at=excluded.created_at
            """,
                (
                    job_id,
                    job_title,
                    company,
                    query_type,
                    query_text,
                    person_name,
                    profile_url,
                    status,
                    reason,
                    note_text,
                    created_at,
                ),
            )
        return True
    except sqlite3.Error:
        return False
