import json
import os
import sqlite3
import time
from urllib.parse import urlencode, quote
from flask import Flask, render_template, request, redirect

app = Flask(__name__)

DB_PATH = os.getenv("DB_PATH", "/app/db/jobs.sqlite")
ALLOWED_STATUSES = [
    "new",
    "applied",
    "outreach",
    "interview",
    "offer",
    "accepted",
    "rejected",
]
ALLOWED_SORTS = {
    "date": "date_found",
    "company": "company",
    "title": "title",
    "location": "location",
    "source": "source",
    "fit": "fit_score",
    "status": "status",
}
PAGE_SIZE = 50
LOCATIONS_CACHE_TTL_SECONDS = 60
_cities_cache: dict[str, object] = {"data": [], "expires_at": 0.0}
_states_cache: dict[str, object] = {"data": [], "expires_at": 0.0}
_countries_cache: dict[str, object] = {"data": [], "expires_at": 0.0}
_modes_cache: dict[str, object] = {"data": [], "expires_at": 0.0}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        pass
    return conn


def _safe_next_url(next_url: str | None) -> str:
    if not next_url:
        return "/"
    if not next_url.startswith("/"):
        return "/"
    return next_url


def _parse_filters(args) -> dict:
    statuses = [s for s in args.getlist("status") if s and s in ALLOWED_STATUSES]
    sources = [s for s in args.getlist("source") if s]
    cities = [s for s in args.getlist("city") if s]
    states = [s for s in args.getlist("state") if s]
    countries = [s for s in args.getlist("country") if s]
    modes = [s for s in args.getlist("mode") if s]

    q = (args.get("q") or "").strip()

    min_fit_score_raw = (args.get("min_fit_score") or "").strip()
    min_fit_score = None
    if min_fit_score_raw:
        try:
            min_fit_score = float(min_fit_score_raw)
        except ValueError:
            min_fit_score_raw = ""

    date_from = (args.get("date_from") or "").strip()
    date_to = (args.get("date_to") or "").strip()

    sort = (args.get("sort") or "").strip()
    if sort not in ALLOWED_SORTS:
        sort = ""

    sort_dir = (args.get("dir") or "").strip().lower()
    if sort_dir not in {"asc", "desc"}:
        sort_dir = ""

    page_raw = (args.get("page") or "").strip()
    try:
        page = int(page_raw) if page_raw else 1
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    return {
        "statuses": statuses,
        "sources": sources,
        "cities": cities,
        "states": states,
        "countries": countries,
        "modes": modes,
        "q": q,
        "min_fit_score": min_fit_score,
        "min_fit_score_raw": min_fit_score_raw,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "sort_dir": sort_dir,
        "page": page,
    }


def _get_sources() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT source
            FROM jobs
            WHERE source IS NOT NULL AND source <> ''
            ORDER BY source
            """
        ).fetchall()
        return [r[0] for r in rows]


def _get_location_cities() -> list[str]:
    now = time.time()
    if _cities_cache["expires_at"] > now:
        return list(_cities_cache["data"])
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT location_city
            FROM job_locations
            WHERE location_city IS NOT NULL AND location_city <> ''
            ORDER BY location_city
            """
        ).fetchall()
        cities = [r[0] for r in rows]
    _cities_cache["data"] = cities
    _cities_cache["expires_at"] = now + LOCATIONS_CACHE_TTL_SECONDS
    return cities


def _get_location_states() -> list[str]:
    now = time.time()
    if _states_cache["expires_at"] > now:
        return list(_states_cache["data"])
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT location_state
            FROM job_locations
            WHERE location_state IS NOT NULL AND location_state <> ''
            ORDER BY location_state
            """
        ).fetchall()
        states = [r[0] for r in rows]
    _states_cache["data"] = states
    _states_cache["expires_at"] = now + LOCATIONS_CACHE_TTL_SECONDS
    return states


def _get_location_countries() -> list[str]:
    now = time.time()
    if _countries_cache["expires_at"] > now:
        return list(_countries_cache["data"])
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT location_country
            FROM job_locations
            WHERE location_country IS NOT NULL AND location_country <> ''
            ORDER BY location_country
            """
        ).fetchall()
        countries = [r[0] for r in rows]
    _countries_cache["data"] = countries
    _countries_cache["expires_at"] = now + LOCATIONS_CACHE_TTL_SECONDS
    return countries


def _get_location_modes() -> list[str]:
    now = time.time()
    if _modes_cache["expires_at"] > now:
        return list(_modes_cache["data"])
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT location_mode
            FROM job_locations
            WHERE location_mode IS NOT NULL AND location_mode <> ''
            ORDER BY location_mode
            """
        ).fetchall()
        modes = [r[0] for r in rows]
    _modes_cache["data"] = modes
    _modes_cache["expires_at"] = now + LOCATIONS_CACHE_TTL_SECONDS
    return modes


def _query_jobs(
    filters: dict,
) -> tuple[list[sqlite3.Row], int, int, int, str | None]:
    where = []
    params: list[object] = []

    if filters["statuses"]:
        where.append(f"status IN ({','.join(['?'] * len(filters['statuses']))})")
        params.extend(filters["statuses"])

    if filters["sources"]:
        where.append(f"source IN ({','.join(['?'] * len(filters['sources']))})")
        params.extend(filters["sources"])

    if filters["cities"]:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM job_locations jl "
            "WHERE jl.job_id = jobs.id "
            f"AND jl.location_city IN ({','.join(['?'] * len(filters['cities']))})"
            ")"
        )
        params.extend(filters["cities"])

    if filters["states"]:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM job_locations jl "
            "WHERE jl.job_id = jobs.id "
            f"AND jl.location_state IN ({','.join(['?'] * len(filters['states']))})"
            ")"
        )
        params.extend(filters["states"])

    if filters["countries"]:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM job_locations jl "
            "WHERE jl.job_id = jobs.id "
            f"AND jl.location_country IN ({','.join(['?'] * len(filters['countries']))})"
            ")"
        )
        params.extend(filters["countries"])

    if filters["modes"]:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM job_locations jl "
            "WHERE jl.job_id = jobs.id "
            f"AND jl.location_mode IN ({','.join(['?'] * len(filters['modes']))})"
            ")"
        )
        params.extend(filters["modes"])

    if filters["q"]:
        q = f"%{filters['q'].lower()}%"
        where.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        params.extend([q, q])

    if filters["min_fit_score"] is not None:
        where.append("fit_score >= ?")
        params.append(filters["min_fit_score"])

    if filters["date_from"]:
        where.append("date_found >= ?")
        params.append(filters["date_from"])

    if filters["date_to"]:
        where.append("date_found <= ?")
        params.append(f"{filters['date_to']}T23:59:59")

    try:
        with get_connection() as conn:
            base_sql = "FROM jobs"
            if where:
                base_sql += " WHERE " + " AND ".join(where)

            total = conn.execute(f"SELECT COUNT(1) {base_sql}", params).fetchone()[0]
            if total == 0:
                return [], 0, 1, 0, None

            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            page = min(filters["page"], total_pages)
            offset = (page - 1) * PAGE_SIZE

            data_sql = f"SELECT * {base_sql}"
            if filters["sort"]:
                column = ALLOWED_SORTS[filters["sort"]]
                direction = filters["sort_dir"].upper() or "ASC"
                data_sql += (
                    f" ORDER BY {column} IS NULL, {column} {direction}, date_found DESC"
                )
            else:
                data_sql += " ORDER BY date_found DESC"

            data_sql += " LIMIT ? OFFSET ?"
            rows = conn.execute(data_sql, params + [PAGE_SIZE, offset]).fetchall()
            return rows, total, page, total_pages, None
    except sqlite3.OperationalError as exc:
        return [], 0, 1, 0, str(exc)


def _parse_outreach_filters(args) -> dict:
    job_title = (args.get("job_title") or "").strip()
    company = (args.get("company") or "").strip()
    query_type = (args.get("query_type") or "").strip()
    status = (args.get("status") or "").strip()

    page_raw = (args.get("page") or "").strip()
    try:
        page = int(page_raw) if page_raw else 1
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    return {
        "job_title": job_title,
        "company": company,
        "query_type": query_type,
        "status": status,
        "page": page,
    }


def _get_outreach_query_types() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT query_type
            FROM outreach_log
            WHERE query_type IS NOT NULL AND query_type <> ''
            ORDER BY query_type
            """
        ).fetchall()
        return [r[0] for r in rows]


def _get_outreach_statuses() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT status
            FROM outreach_log
            WHERE status IS NOT NULL AND status <> ''
            ORDER BY status
            """
        ).fetchall()
        return [r[0] for r in rows]


def _query_outreach(
    filters: dict,
) -> tuple[list[sqlite3.Row], int, int, int, str | None]:
    where = []
    params: list[object] = []

    if filters["job_title"]:
        where.append("LOWER(job_title) LIKE ?")
        params.append(f"%{filters['job_title'].lower()}%")

    if filters["company"]:
        where.append("LOWER(company) LIKE ?")
        params.append(f"%{filters['company'].lower()}%")

    if filters["query_type"]:
        where.append("query_type = ?")
        params.append(filters["query_type"])

    if filters["status"]:
        where.append("status = ?")
        params.append(filters["status"])

    try:
        with get_connection() as conn:
            base_sql = "FROM outreach_log"
            if where:
                base_sql += " WHERE " + " AND ".join(where)

            total = conn.execute(f"SELECT COUNT(1) {base_sql}", params).fetchone()[0]
            if total == 0:
                return [], 0, 1, 0, None

            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            page = min(filters["page"], total_pages)
            offset = (page - 1) * PAGE_SIZE

            data_sql = f"SELECT * {base_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?"
            rows = conn.execute(data_sql, params + [PAGE_SIZE, offset]).fetchall()
            return rows, total, page, total_pages, None
    except sqlite3.OperationalError as exc:
        return [], 0, 1, 0, str(exc)


def _clean_params(args: dict) -> dict:
    cleaned: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(value, list):
            values = [v for v in value if v not in (None, "")]
            if values:
                cleaned[key] = values
        else:
            if value not in (None, ""):
                cleaned[key] = value
    return cleaned


def _build_sort_links(
    args: dict, current_sort: str, current_dir: str, base_path: str = "/"
) -> dict:
    base = _clean_params(args)
    base.pop("page", None)
    links: dict[str, dict[str, str]] = {}
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"
    for key in ALLOWED_SORTS:
        if current_sort == key:
            if current_dir == "asc":
                next_dir = "desc"
            elif current_dir == "desc":
                next_dir = ""
            else:
                next_dir = "asc"
        else:
            next_dir = "asc"

        params = dict(base)
        if next_dir:
            params["sort"] = key
            params["dir"] = next_dir
        else:
            params.pop("sort", None)
            params.pop("dir", None)

        url = base_path
        if params:
            url += "?" + urlencode(params, doseq=True)

        state = "none"
        if current_sort == key and current_dir in {"asc", "desc"}:
            state = current_dir

        links[key] = {"url": url, "state": state}
    return links


def _build_page_links(
    args: dict, page: int, total_pages: int, base_path: str = "/"
) -> dict:
    base = _clean_params(args)
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"

    def _url_for(target_page: int) -> str:
        params = dict(base)
        if target_page <= 1:
            params.pop("page", None)
        else:
            params["page"] = str(target_page)
        if params:
            return f"{base_path}?" + urlencode(params, doseq=True)
        return base_path

    prev_url = _url_for(page - 1) if page > 1 else ""
    next_url = _url_for(page + 1) if page < total_pages else ""
    return {"prev": prev_url, "next": next_url}


def _get_outreach_logs(job_ids: list[str]) -> dict[str, list[dict]]:
    if not job_ids:
        return {}
    placeholders = ",".join(["?"] * len(job_ids))
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM outreach_log
                WHERE job_id IN ({placeholders})
                ORDER BY created_at DESC
                """,
                job_ids,
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d.get("job_id") or "", []).append(d)
    return out


def _format_parsed_location(row: sqlite3.Row) -> str:
    parts = []
    if row["location_city"]:
        parts.append(row["location_city"])
    if row["location_state"]:
        parts.append(row["location_state"])
    if row["location_country"]:
        parts.append(row["location_country"])
    value = ", ".join(parts)
    if row["location_mode"]:
        if value:
            value = f"{value} ({row['location_mode']})"
        else:
            value = row["location_mode"]
    return value


def _get_job_locations(job_ids: list[str]) -> dict[str, str]:
    if not job_ids:
        return {}
    placeholders = ",".join(["?"] * len(job_ids))
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM job_locations
                WHERE job_id IN ({placeholders})
                ORDER BY job_id, position
                """,
                job_ids,
            ).fetchall()
    except sqlite3.OperationalError:
        return {}

    grouped: dict[str, list[str]] = {}
    for row in rows:
        rendered = _format_parsed_location(row)
        if rendered:
            grouped.setdefault(row["job_id"], []).append(rendered)
    return {job_id: "; ".join(values) for job_id, values in grouped.items()}


@app.get("/")
def index():
    filters = _parse_filters(request.args)
    jobs, total, page, total_pages, error = _query_jobs(filters)
    sources = _get_sources() if not error else []
    cities = _get_location_cities() if not error else []
    states = _get_location_states() if not error else []
    countries = _get_location_countries() if not error else []
    modes = _get_location_modes() if not error else []
    sort_links = _build_sort_links(
        request.args.to_dict(flat=False),
        filters["sort"],
        filters["sort_dir"],
        base_path="/",
    )
    page_links = _build_page_links(
        request.args.to_dict(flat=False), page, total_pages, base_path="/"
    )

    job_ids = [row["id"] for row in jobs]
    outreach_map = _get_outreach_logs(job_ids)
    location_map = _get_job_locations(job_ids)

    jobs_out = []
    for row in jobs:
        data = dict(row)
        data["location_display"] = location_map.get(data.get("id") or "") or data.get(
            "location"
        )
        outreach_logs = outreach_map.get(data.get("id") or "", [])
        data["outreach_log"] = outreach_logs
        data["outreach_total"] = len(outreach_logs)
        data["outreach_sent"] = sum(
            1 for o in outreach_logs if o.get("status") == "sent"
        )
        people_links = []
        raw_people = data.get("people_to_reach") or ""
        if raw_people:
            try:
                queries = json.loads(raw_people)
            except Exception:
                queries = []
            if isinstance(queries, list):
                for q in queries:
                    if not q:
                        continue
                    people_links.append(
                        {
                            "query": q,
                            "url": "https://www.linkedin.com/search/results/people/?keywords="
                            + quote(q),
                        }
                    )
        data["people_links"] = people_links
        jobs_out.append(data)

    next_url = request.full_path or "/"
    if next_url.endswith("?"):
        next_url = next_url[:-1]

    return render_template(
        "index.html",
        jobs=jobs_out,
        sources=sources,
        cities=cities,
        states=states,
        countries=countries,
        modes=modes,
        filters=filters,
        statuses=ALLOWED_STATUSES,
        next_url=next_url,
        error=error,
        total=total,
        page=page,
        total_pages=total_pages,
        sort_links=sort_links,
        page_links=page_links,
        active_tab="jobs",
    )


@app.get("/outreach")
def outreach():
    filters = _parse_outreach_filters(request.args)
    entries, total, page, total_pages, error = _query_outreach(filters)
    query_types = _get_outreach_query_types() if not error else []
    outreach_statuses = _get_outreach_statuses() if not error else []
    page_links = _build_page_links(
        request.args.to_dict(flat=False), page, total_pages, base_path="/outreach"
    )

    outreach_rows = [dict(row) for row in entries]

    return render_template(
        "outreach.html",
        outreach=outreach_rows,
        filters=filters,
        query_types=query_types,
        outreach_statuses=outreach_statuses,
        error=error,
        total=total,
        page=page,
        total_pages=total_pages,
        page_links=page_links,
        active_tab="outreach",
    )


@app.post("/jobs/<job_id>/status")
def update_status(job_id: str):
    status = (request.form.get("status") or "").strip()
    next_url = _safe_next_url(request.form.get("next"))

    if status not in ALLOWED_STATUSES:
        return redirect(next_url)

    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))

    return redirect(next_url)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
