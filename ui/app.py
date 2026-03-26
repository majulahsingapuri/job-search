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
    "outreach",
    "applied",
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
_locations_cache: dict[str, object] = {"data": [], "expires_at": 0.0}


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
    statuses = [
        s for s in args.getlist("status") if s and s in ALLOWED_STATUSES
    ]
    sources = [s for s in args.getlist("source") if s]
    locations = [s for s in args.getlist("location") if s]

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
        "locations": locations,
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

def _get_locations() -> list[str]:
    now = time.time()
    if _locations_cache["expires_at"] > now:
        return list(_locations_cache["data"])

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT location
            FROM jobs
            WHERE location IS NOT NULL AND location <> ''
            ORDER BY location
            """
        ).fetchall()
        locations = [r[0] for r in rows]

    _locations_cache["data"] = locations
    _locations_cache["expires_at"] = now + LOCATIONS_CACHE_TTL_SECONDS
    return locations


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

    if filters["locations"]:
        where.append(f"location IN ({','.join(['?'] * len(filters['locations']))})")
        params.extend(filters["locations"])

    if filters["q"]:
        q = f"%{filters['q'].lower()}%"
        where.append(
            "(LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(location) LIKE ?)"
        )
        params.extend([q, q, q])

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


def _build_sort_links(args: dict, current_sort: str, current_dir: str) -> dict:
    base = _clean_params(args)
    base.pop("page", None)
    links: dict[str, dict[str, str]] = {}
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

        url = "/"
        if params:
            url += "?" + urlencode(params, doseq=True)

        state = "none"
        if current_sort == key and current_dir in {"asc", "desc"}:
            state = current_dir

        links[key] = {"url": url, "state": state}
    return links


def _build_page_links(args: dict, page: int, total_pages: int) -> dict:
    base = _clean_params(args)

    def _url_for(target_page: int) -> str:
        params = dict(base)
        if target_page <= 1:
            params.pop("page", None)
        else:
            params["page"] = str(target_page)
        if params:
            return "/?" + urlencode(params, doseq=True)
        return "/"

    prev_url = _url_for(page - 1) if page > 1 else ""
    next_url = _url_for(page + 1) if page < total_pages else ""
    return {"prev": prev_url, "next": next_url}


@app.get("/")
def index():
    filters = _parse_filters(request.args)
    jobs, total, page, total_pages, error = _query_jobs(filters)
    sources = _get_sources() if not error else []
    locations = _get_locations() if not error else []
    sort_links = _build_sort_links(
        request.args.to_dict(flat=False), filters["sort"], filters["sort_dir"]
    )
    page_links = _build_page_links(
        request.args.to_dict(flat=False), page, total_pages
    )

    jobs_out = []
    for row in jobs:
        data = dict(row)
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
        locations=locations,
        filters=filters,
        statuses=ALLOWED_STATUSES,
        next_url=next_url,
        error=error,
        total=total,
        page=page,
        total_pages=total_pages,
        sort_links=sort_links,
        page_links=page_links,
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
