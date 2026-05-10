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
    "canada": "Canada",
    "united states": "United States",
    "united states of america": "United States",
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
}
KNOWN_COUNTRIES = {
    "Canada",
    "United States",
    "India",
    "Singapore",
    "United Kingdom",
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
    if (
        "on-site" in lowered
        or "onsite" in lowered
        or "on site" in lowered
        or "in person" in lowered
    ):
        return "onsite"
    return None


def _clean_location_token(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", value)
    cleaned = re.sub(r"(?i)\bpreferred\b", "", cleaned)
    cleaned = re.sub(r"(?i)\s+-\s+nyc$", "", cleaned)
    cleaned = re.sub(r"(?i)\bin\s+person\b", "", cleaned)
    cleaned = re.sub(r"(?i)\bremote[- ]first\b", "remote", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ,-/")


def _normalize_location_alias(raw: str) -> str:
    value = raw.strip()
    lowered = value.lower()
    if lowered == "nyc global hq":
        return "New York City, NY"
    if lowered.startswith("bellevue office"):
        return "Bellevue, WA"
    sandstone_match = re.search(
        r"(?i)sandstone care\s*-\s*([^()]+colorado)\b",
        value,
    )
    if sandstone_match:
        return sandstone_match.group(1).strip(" -")
    return value


def _looks_like_location_list(value: str) -> bool:
    if not value:
        return False
    if re.search(r"\b[A-Z]{2}\b", value):
        return True
    if any(state in value.lower() for state in US_STATE_NAMES_LOWER):
        return True
    if _normalize_country(value):
        return True
    return "," in value


def _strip_mode_parenthetical(raw: str) -> tuple[str, str | None]:
    work_mode = _extract_work_mode(raw)
    base = raw
    match = re.search(r"\(([^)]*)\)\s*$", raw)
    if match:
        inside = match.group(1).strip()
        inside_mode = _extract_work_mode(inside)
        if inside_mode and not _looks_like_location_list(inside):
            work_mode = work_mode or inside_mode
            base = raw[: match.start()].strip()
        elif raw.lower().startswith("remote") and _looks_like_location_list(inside):
            work_mode = work_mode or "remote"
            base = inside
    return base, work_mode


def _location_text_candidates(raw_location: str | None) -> tuple[list[str], str | None]:
    if not raw_location:
        return [], None
    raw = raw_location.strip()
    if not raw:
        return [], None

    raw = _normalize_location_alias(raw)
    base, work_mode = _strip_mode_parenthetical(raw)

    parenthetical_locations = [
        m.group(1).strip()
        for m in re.finditer(r"\(([^)]*)\)", raw)
        if _looks_like_location_list(m.group(1))
        and not _extract_work_mode(m.group(1))
        and not raw.lower().startswith("remote")
    ]
    if parenthetical_locations:
        base = "; ".join(parenthetical_locations)

    if base.lower().startswith("remote"):
        work_mode = work_mode or "remote"
        base = re.sub(r"(?i)^remote\b", "", base).strip(" -/,")
        base = re.sub(r"(?i)^(in|within)\s+", "", base).strip(" -/,")

    base = re.sub(r"(?i)\bhybrid\s+if\s+local\s+to\b", "", base)
    base = re.sub(r"(?i)\bany\s+office\b", "", base)
    base = re.sub(r"(?i)^onsite\b", "", base).strip(" -/,")
    base = re.sub(r"(?i)\bon[- ]site\b", "", base).strip(" -/,")
    base = re.sub(r"(?i)\bhybrid\b.*$", "", base).strip(" -/,")

    if base.lower() in {"various", "multiple", "anywhere"}:
        base = ""

    if not base:
        return [], work_mode

    candidates: list[str] = []
    for semi_part in re.split(r"\s*;\s*", base):
        part = _clean_location_token(semi_part)
        if not part:
            continue
        part = re.sub(r"(?i)\s+or\s+", ";", part)
        subparts = [_clean_location_token(p) for p in part.split(";") if p.strip()]
        for subpart in subparts:
            if subpart.lower() in {"remote", "hybrid", "onsite", "on-site"}:
                continue
            comma_parts = [p.strip() for p in subpart.split(",") if p.strip()]
            comma_parts = [
                p
                for p in comma_parts
                if p.lower() not in {"remote", "hybrid", "onsite", "on-site"}
            ]
            if len(comma_parts) >= 4:
                for idx in range(0, len(comma_parts) - 1, 2):
                    city = comma_parts[idx]
                    state = comma_parts[idx + 1]
                    if _is_us_state(state):
                        candidates.append(f"{city}, {state}")
                continue
            if len(comma_parts) == 3 and _is_us_state(comma_parts[1]):
                candidates.append(", ".join(comma_parts))
                continue
            candidates.append(subpart)

    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = _clean_location_token(candidate)
        if candidate and candidate.lower() not in seen:
            seen.add(candidate.lower())
            cleaned.append(candidate)
    return cleaned, work_mode


def _parse_single_location(raw_location: str, work_mode: str | None = None) -> dict[str, str | None]:
    token = _clean_location_token(_normalize_location_alias(raw_location))
    token = re.sub(
        r"\b([A-Z]{2})\s+(United States|USA|US|Canada|United Kingdom|UK)\b",
        r"\1, \2",
        token,
    )
    address_match = re.search(
        r"([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3}),?\s+([A-Z]{2})$",
        token,
    )
    if address_match and not token.split(",", 1)[0].strip().istitle():
        token = f"{address_match.group(1)}, {address_match.group(2)}"

    compact_city_state = re.fullmatch(
        r"([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})\s+([A-Z]{2})",
        token,
    )
    if compact_city_state:
        token = f"{compact_city_state.group(1)}, {compact_city_state.group(2)}"

    parts = [p.strip() for p in token.split(",") if p.strip()]
    parts = [
        p
        for p in parts
        if p.lower() not in {"remote", "hybrid", "onsite", "on-site"}
    ]

    city = None
    state = None
    country = None

    if len(parts) >= 3:
        if _is_us_state(parts[-1]):
            city = parts[-2]
            state = _normalize_state(parts[-1])
            country = "United States"
        else:
            city = parts[-3] if re.search(r"\d", parts[0]) else parts[0]
            country = _normalize_country(", ".join(parts[-1:])) or ", ".join(parts[-1:])
            state = _normalize_state_for_country(parts[-2], country)
            if _is_us_state(state) and _normalize_country(country) is None:
                country = "United States"
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
    elif parts:
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

    if city and re.search(r"\d", city):
        words = city.split()
        for idx in range(len(words) - 1, -1, -1):
            suffix = " ".join(words[idx:])
            if suffix and suffix[0].isupper():
                city = suffix
                break

    return {
        "raw_location": raw_location,
        "city": city,
        "state": state,
        "country": country,
        "work_mode": work_mode,
    }


def parse_locations(raw_locations: str | list[str] | tuple[str, ...] | None) -> list[dict[str, str | None]]:
    values: list[str]
    if isinstance(raw_locations, (list, tuple)):
        values = [str(v).strip() for v in raw_locations if str(v).strip()]
    elif raw_locations:
        values = [str(raw_locations).strip()]
    else:
        values = []

    parsed_locations: list[dict[str, str | None]] = []
    seen: dict[tuple[str | None, str | None, str | None], int] = {}
    fallback_work_mode = None

    for value in values:
        candidates, work_mode = _location_text_candidates(value)
        fallback_work_mode = fallback_work_mode or work_mode
        if not candidates:
            if work_mode:
                candidate = {
                    "raw_location": value,
                    "city": None,
                    "state": None,
                    "country": None,
                    "work_mode": work_mode,
                }
                key = (
                    candidate["city"],
                    candidate["state"],
                    candidate["country"],
                )
                if key not in seen:
                    seen[key] = len(parsed_locations)
                    parsed_locations.append(candidate)
            continue
        for candidate_text in candidates:
            parsed = _parse_single_location(candidate_text, work_mode)
            key = (
                parsed["city"],
                parsed["state"],
                parsed["country"],
            )
            if not any(key):
                continue
            if key in seen:
                existing = parsed_locations[seen[key]]
                if parsed["work_mode"] and not existing.get("work_mode"):
                    existing["work_mode"] = parsed["work_mode"]
                continue
            else:
                seen[key] = len(parsed_locations)
                parsed_locations.append(parsed)

    if not parsed_locations and fallback_work_mode:
        parsed_locations.append(
            {
                "raw_location": None,
                "city": None,
                "state": None,
                "country": None,
                "work_mode": fallback_work_mode,
            }
        )
    return parsed_locations


def parse_location(raw_location: str | None) -> dict[str, str | None]:
    parsed_locations = parse_locations(raw_location)
    if parsed_locations:
        first = parsed_locations[0]
        return {
            "city": first["city"],
            "state": first["state"],
            "country": first["country"],
            "work_mode": first["work_mode"],
        }
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


def _ensure_job_locations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_locations (
            job_id           TEXT NOT NULL,
            position         INTEGER NOT NULL,
            raw_location     TEXT,
            location_city    TEXT,
            location_state   TEXT,
            location_country TEXT,
            location_mode    TEXT,
            PRIMARY KEY (job_id, position),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_locations_job_id ON job_locations(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_locations_city ON job_locations(location_city)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_locations_state ON job_locations(location_state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_locations_country ON job_locations(location_country)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_locations_mode ON job_locations(location_mode)"
    )


def _location_inputs_for_row(row: sqlite3.Row) -> list[str]:
    values = []
    location = (row["location"] or "").strip()
    description = row["description"] or ""
    if location:
        values.append(location)
    if row["source"] == "greenhouse" and description:
        match = re.search(r"(?im)^Locations:\s*(.+)$", description)
        if match:
            values.append(match.group(1).strip())
    return values


def _replace_job_locations(
    conn: sqlite3.Connection, job_id: str, locations: list[dict[str, str | None]]
) -> None:
    conn.execute("DELETE FROM job_locations WHERE job_id=?", (job_id,))
    conn.executemany(
        """
        INSERT INTO job_locations (
            job_id,
            position,
            raw_location,
            location_city,
            location_state,
            location_country,
            location_mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                job_id,
                idx,
                loc.get("raw_location"),
                loc.get("city"),
                loc.get("state"),
                loc.get("country"),
                loc.get("work_mode"),
            )
            for idx, loc in enumerate(locations)
        ],
    )


def _sync_job_location_summary(
    conn: sqlite3.Connection, job_id: str, locations: list[dict[str, str | None]]
) -> None:
    first = locations[0] if locations else {}
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
            first.get("city"),
            first.get("state"),
            first.get("country"),
            first.get("work_mode"),
            job_id,
        ),
    )


def _rebuild_job_locations(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, source, location, description
        FROM jobs
        WHERE location IS NOT NULL AND TRIM(location) <> ''
        """
    ).fetchall()
    for row in rows:
        parsed = parse_locations(_location_inputs_for_row(row))
        _replace_job_locations(conn, row["id"], parsed)
        _sync_job_location_summary(conn, row["id"], parsed)


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
                source_external_id TEXT,
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
        _ensure_job_locations_table(conn)
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
        ("source_external_id", "TEXT"),
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
            _ensure_job_locations_table(conn)
            _rebuild_job_locations(conn)
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
            "CREATE INDEX IF NOT EXISTS idx_jobs_source_external_id "
            "ON jobs(source, source_external_id)"
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


def make_source_external_id(job: dict) -> str:
    source_external_id = str(job.get("source_external_id") or "").strip()
    if source_external_id:
        return source_external_id

    source = (job.get("source") or "").strip().lower()
    if source == "simplify":
        return str(job.get("posting_id") or "").strip()
    if source == "greenhouse":
        return str(job.get("greenhouse_id") or "").strip()
    return ""


def _candidate_job_id(job: dict) -> str:
    url = (job.get("url") or "").strip()
    if not url:
        return ""
    return make_job_id(job["title"], job["company"], url)


def is_duplicate(job_id: str) -> bool:
    with get_connection() as conn:
        return (
            conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            is not None
        )


def _set_source_external_id_if_missing(job_id: str, source_external_id: str) -> None:
    if not source_external_id:
        return
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET source_external_id = ?
            WHERE id = ?
              AND (source_external_id IS NULL OR TRIM(source_external_id) = '')
            """,
            (source_external_id, job_id),
        )


def is_existing_job(job: dict) -> bool:
    candidate_id = _candidate_job_id(job)
    source = (job.get("source") or "").strip()
    source_external_id = make_source_external_id(job)

    clauses = []
    params: list[str] = []
    if candidate_id:
        clauses.append("id = ?")
        params.append(candidate_id)
    if source and source_external_id:
        clauses.append("(source = ? AND source_external_id = ?)")
        params.extend([source, source_external_id])

    if not clauses:
        return False

    with get_connection() as conn:
        query = f"SELECT 1 FROM jobs WHERE {' OR '.join(clauses)} LIMIT 1"
        return conn.execute(query, params).fetchone() is not None


def filter_new_jobs(jobs: list[dict]) -> list[dict]:
    new_jobs: list[dict] = []
    seen_ids: set[str] = set()
    seen_external_ids: set[tuple[str, str]] = set()

    for job in jobs:
        candidate_id = _candidate_job_id(job)
        source = (job.get("source") or "").strip()
        source_external_id = make_source_external_id(job)
        external_key = (
            (source, source_external_id) if source and source_external_id else None
        )

        if candidate_id and candidate_id in seen_ids:
            continue
        if external_key and external_key in seen_external_ids:
            continue
        if is_existing_job(job):
            continue

        if candidate_id:
            seen_ids.add(candidate_id)
        if external_key:
            seen_external_ids.add(external_key)
        new_jobs.append(job)

    return new_jobs


def insert_job(job: dict) -> bool:
    job_id = make_job_id(job["title"], job["company"], job.get("url", ""))
    source_external_id = make_source_external_id(job) or None
    if is_duplicate(job_id):
        _set_source_external_id_if_missing(job_id, source_external_id or "")
        return False
    if source_external_id and is_existing_job(job):
        return False
    location_values = job.get("locations") or job.get("location", "")
    parsed_locations = parse_locations(location_values)
    parsed_location = (
        {
            "city": parsed_locations[0]["city"],
            "state": parsed_locations[0]["state"],
            "country": parsed_locations[0]["country"],
            "work_mode": parsed_locations[0]["work_mode"],
        }
        if parsed_locations
        else parse_location(job.get("location", ""))
    )
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
                source_external_id,
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
                :source_external_id,
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
                "source_external_id": source_external_id,
                "description": job.get("description", ""),
                "date_found": datetime.utcnow().isoformat(),
            },
        )
        _ensure_job_locations_table(conn)
        _replace_job_locations(conn, job_id, parsed_locations)
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
