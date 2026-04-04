"""
db/database.py
All SQLite operations: schema, insertion, deduplication, Stage 2 updates.
"""

import sqlite3
import hashlib
import re
from datetime import datetime
from pathlib import Path

from config.settings import get_settings

settings = get_settings()
DB_PATH = Path(settings.db_path)

US_STATE_ABBR = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}
US_STATE_ABBR_TO_NAME = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}
US_STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
    "District of Columbia",
}
US_STATE_NAMES_LOWER = {name.lower(): name for name in US_STATE_NAMES}
COUNTRY_ALIASES = {
    "united states": "United States",
    "united states of america": "United States",
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
}
KNOWN_COUNTRIES = {
    "United States",
    "India",
    "Singapore",
}


def _normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().strip(".")
    if not raw:
        return None
    key = raw.lower()
    normalized = COUNTRY_ALIASES.get(key)
    if normalized:
        return normalized
    for country in KNOWN_COUNTRIES:
        if key == country.lower():
            return country
    return None


def _normalize_state(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    compact = raw.replace(".", "").upper()
    if compact in US_STATE_ABBR_TO_NAME:
        return US_STATE_ABBR_TO_NAME[compact]
    name = US_STATE_NAMES_LOWER.get(raw.lower())
    return name or raw


def _is_us_state(value: str | None) -> bool:
    if not value:
        return False
    compact = value.strip().replace(".", "").upper()
    if compact in US_STATE_ABBR_TO_NAME:
        return True
    return value.strip().lower() in US_STATE_NAMES_LOWER


def _normalize_state_for_country(value: str | None, country: str | None) -> str | None:
    if not value:
        return None
    if country and country != "United States":
        raw = value.strip()
        return raw or None
    return _normalize_state(value)


def _extract_work_mode(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if "remote" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if "on-site" in lowered or "onsite" in lowered or "on site" in lowered:
        return "onsite"
    return None


def parse_location(raw_location: str | None) -> dict[str, str | None]:
    if not raw_location:
        return {
            "city": None,
            "state": None,
            "country": None,
            "work_mode": None,
        }

    raw = raw_location.strip()
    if not raw:
        return {
            "city": None,
            "state": None,
            "country": None,
            "work_mode": None,
        }

    work_mode = None
    base = raw
    match = re.search(r"\(([^)]*)\)\s*$", raw)
    if match:
        work_mode = _extract_work_mode(match.group(1))
        base = raw[: match.start()].strip()
    else:
        work_mode = _extract_work_mode(raw)

    if base.lower().startswith("remote"):
        work_mode = work_mode or "remote"
        base = re.sub(r"(?i)^remote\\b", "", base).strip(" -/")
        base = re.sub(r"(?i)^(in|within)\\s+", "", base).strip(" -/")

    if base.lower() in {"various", "multiple", "anywhere"}:
        base = ""

    if not base:
        return {
            "city": None,
            "state": None,
            "country": None,
            "work_mode": work_mode,
        }

    parts = [p.strip() for p in base.split(",") if p.strip()]

    city = None
    state = None
    country = None

    if len(parts) >= 3:
        city = parts[0]
        country = _normalize_country(", ".join(parts[2:])) or ", ".join(parts[2:])
        state = _normalize_state_for_country(parts[1], country)
    elif len(parts) == 2:
        first, second = parts
        country = _normalize_country(second)
        if country:
            if _is_us_state(first) and country == "United States":
                state = _normalize_state(first)
            else:
                city = first
        else:
            normalized_state = _normalize_state(second)
            if _is_us_state(normalized_state):
                city = first
                state = normalized_state
                country = "United States"
            else:
                city = first
                country = _normalize_country(second) or second
    else:
        token = parts[0]
        normalized_country = _normalize_country(token)
        if normalized_country:
            country = normalized_country
        else:
            normalized_state = _normalize_state(token)
            if _is_us_state(normalized_state):
                state = normalized_state
                country = "United States"
            else:
                city = token

    return {
        "city": city,
        "state": state,
        "country": country,
        "work_mode": work_mode,
    }


def _backfill_location_fields(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, location
        FROM jobs
        WHERE location IS NOT NULL
            AND TRIM(location) <> ''
            AND location_city IS NULL
            AND location_state IS NULL
            AND location_country IS NULL
            AND location_mode IS NULL
    """
    ).fetchall()
    if not rows:
        return
    for row in rows:
        parsed = parse_location(row["location"])
        conn.execute(
            """
            UPDATE jobs
            SET location_city=?,
                location_state=?,
                location_country=?,
                location_mode=?
            WHERE id=?
        """,
            (
                parsed["city"],
                parsed["state"],
                parsed["country"],
                parsed["work_mode"],
                row["id"],
            ),
        )


def _normalize_state_abbreviations(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, location_state, location_country
        FROM jobs
        WHERE location_state IS NOT NULL
            AND TRIM(location_state) <> ''
    """
    ).fetchall()
    if not rows:
        return
    for row in rows:
        raw_state = row["location_state"]
        country = row["location_country"]
        if country and country != "United States":
            continue
        compact = raw_state.strip().replace(".", "").upper()
        normalized = US_STATE_ABBR_TO_NAME.get(compact)
        if normalized and normalized != raw_state:
            conn.execute(
                "UPDATE jobs SET location_state=? WHERE id=?",
                (normalized, row["id"]),
            )


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
                location_city    TEXT,
                location_state   TEXT,
                location_country TEXT,
                location_mode    TEXT,
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
        ("location_city", "TEXT"),
        ("location_state", "TEXT"),
        ("location_country", "TEXT"),
        ("location_mode", "TEXT"),
    ]
    with get_connection() as conn:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col, col_type in new_cols:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
        existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if {
            "location_city",
            "location_state",
            "location_country",
            "location_mode",
        }.issubset(existing):
            _backfill_location_fields(conn)
            _normalize_state_abbreviations(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_location_city ON jobs(location_city)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_location_state ON jobs(location_state)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_location_country ON jobs(location_country)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_location_mode ON jobs(location_mode)"
            )
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
    parsed_location = parse_location(job.get("location", ""))
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id,
                title,
                company,
                location,
                location_city,
                location_state,
                location_country,
                location_mode,
                url,
                source,
                description,
                date_found
            )
            VALUES (
                :id,
                :title,
                :company,
                :location,
                :location_city,
                :location_state,
                :location_country,
                :location_mode,
                :url,
                :source,
                :description,
                :date_found
            )
        """,
            {
                "id": job_id,
                "title": job["title"],
                "company": job["company"],
                "location": job.get("location", ""),
                "location_city": parsed_location["city"],
                "location_state": parsed_location["state"],
                "location_country": parsed_location["country"],
                "location_mode": parsed_location["work_mode"],
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
            WHERE date_found >= ? 
                AND date_found <= ? 
                AND fit_score >= 9 
                AND status = 'applied'
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
