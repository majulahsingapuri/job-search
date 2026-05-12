"""
scraper/simplify.py
Fetches Simplify.jobs listings via their Typesense-backed API (no browser).
"""

import asyncio
import html
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

from console_utils import console, progress_bar
from config.settings import get_settings
from db.database import filter_new_jobs

settings = get_settings()

SIMPLIFY_SEARCH_URL = "https://js-ha.simplify.jobs/multi_search"
SIMPLIFY_LOCATION_URL = "https://simplify.jobs/api/location"
SIMPLIFY_TYPESENSE_API_KEY = (
    "SWF1ODFZbzBkcVlVdnVwT2FqUE5EZ3JpSk5hVmdpUHg1SklXWEdGbHZVRT1POHJie"
    "yJleGNsdWRlX2ZpZWxkcyI6ImNvbXBhbnlfdXJsLGNhdGVnb3JpZXMsYWRkaXRpb2"
    "5hbF9yZXF1aXJlbWVudHMsY291bnRyaWVzLGRlZ3JlZXMsZ2VvbG9jYXRpb25zLG"
    "luZHVzdHJpZXMsaXNfc2ltcGxlX2FwcGxpY2F0aW9uLGpvYl9saXN0cyxsZWFkZX"
    "JzaGlwX3R5cGUsc2VjdXJpdHlfY2xlYXJhbmNlLHNraWxscyx1cmwifQ=="
)
SIMPLIFY_COUNTRY_LAYERS = {"country", "dependency"}
SIMPLIFY_REMOTE_LOCATIONS = {"remote", "anywhere", "worldwide"}
SIMPLIFY_DEFAULT_REMOTE_COUNTRY = "USA"
SIMPLIFY_REMOTE_COUNTRY_ALIASES = {
    "australia": "Australia",
    "canada": "Canada",
    "france": "France",
    "germany": "Germany",
    "india": "India",
    "ireland": "Ireland",
    "italy": "Italy",
    "spain": "Spain",
    "united arab emirates": "United Arab Emirates",
    "united kingdom": "UK",
    "united states": "USA",
    "united states of america": "USA",
    "uae": "United Arab Emirates",
    "uk": "UK",
    "u.k": "UK",
    "u.k.": "UK",
    "us": "USA",
    "usa": "USA",
}
SIMPLIFY_COUNTRY_ALIASES = {
    "united states": "United States",
    "united states of america": "United States",
    "us": "United States",
    "usa": "United States",
}

SIMPLIFY_DETAIL_URL = (
    "https://api.simplify.jobs/v2/job-posting/:id/{posting_id}/company"
)


def _typesense_value(value: str) -> str:
    return value.replace("`", "").strip()


def _format_geo(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _build_geolocation_filter(bbox: list[Any]) -> str:
    min_lon, min_lat, max_lon, max_lat = [float(value) for value in bbox[:4]]
    points = (
        (max_lat, max_lon),
        (max_lat, min_lon),
        (min_lat, min_lon),
        (min_lat, max_lon),
    )
    coordinates = ", ".join(
        f"{_format_geo(lat)}, {_format_geo(lon)}" for lat, lon in points
    )
    return f"geolocations:({coordinates})"


def _is_remote_location(location: str) -> bool:
    normalized = _normalize_location(location)
    return (
        normalized in SIMPLIFY_REMOTE_LOCATIONS
        or normalized.startswith("remote ")
        or normalized.endswith(" remote")
    )


def _country_filter(country: str) -> str:
    return f"countries:=[`{_typesense_value(country)}`]"


def _normalize_location(location: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", location.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _remote_country(location: str) -> str:
    normalized = _normalize_location(location)
    for alias, country in sorted(
        SIMPLIFY_REMOTE_COUNTRY_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        alias_normalized = _normalize_location(alias)
        if re.search(rf"\b{re.escape(alias_normalized)}\b", normalized):
            return country
    return SIMPLIFY_DEFAULT_REMOTE_COUNTRY


def _remote_filter(location: str) -> str:
    country = _remote_country(location)
    remote_location = f"Remote in {country}"
    return (
        f"locations:=[`{_typesense_value(remote_location)}`] "
        "&& travel_requirements:[`Remote`]"
    )


async def _build_location_filter(
    session: aiohttp.ClientSession, location: str
) -> str:
    location = (location or "").strip()
    if not location:
        return ""

    country = SIMPLIFY_COUNTRY_ALIASES.get(location.lower())
    if country:
        console.log(f"[cyan]Simplify:[/cyan] Location filter: {country}")
        return _country_filter(country)

    if _is_remote_location(location):
        console.log(
            f"[cyan]Simplify:[/cyan] Location filter: Remote in "
            f"{_remote_country(location)}"
        )
        return _remote_filter(location)

    try:
        async with session.get(
            SIMPLIFY_LOCATION_URL,
            params={"text": location},
            headers={"Accept": "application/json"},
        ) as resp:
            if resp.status != 200:
                console.log(
                    f"[yellow]Simplify:[/yellow] Location lookup HTTP {resp.status}; "
                    "searching without a location filter."
                )
                return ""
            data = await resp.json()
    except Exception as e:
        console.log(
            f"[yellow]Simplify:[/yellow] Location lookup failed for "
            f"'{location}': {e}; searching without a location filter."
        )
        return ""

    features = data.get("data", {}).get("features", [])
    if not features:
        console.log(
            f"[yellow]Simplify:[/yellow] No location match for '{location}'; "
            "searching without a location filter."
        )
        return ""

    feature = features[0]
    properties = feature.get("properties", {})
    layer = (properties.get("layer") or "").strip().lower()
    label = (properties.get("label") or properties.get("name") or location).strip()

    if layer in SIMPLIFY_COUNTRY_LAYERS:
        country = (
            properties.get("country")
            or properties.get("name")
            or properties.get("label")
            or location
        )
        console.log(f"[cyan]Simplify:[/cyan] Location filter: {label}")
        return _country_filter(country)

    bbox = feature.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        try:
            filter_by = _build_geolocation_filter(bbox)
        except (TypeError, ValueError):
            filter_by = ""
        if filter_by:
            console.log(f"[cyan]Simplify:[/cyan] Location filter: {label}")
            return filter_by

    country = properties.get("country")
    if country:
        console.log(
            f"[yellow]Simplify:[/yellow] Location match for '{location}' has no "
            f"bounding box; falling back to country filter: {country}"
        )
        return _country_filter(country)

    console.log(
        f"[yellow]Simplify:[/yellow] Could not build a location filter for "
        f"'{location}'; searching without a location filter."
    )
    return ""


def _build_search_payload(keyword: str, page: int, filter_by: str) -> dict[str, Any]:
    search: dict[str, Any] = {
        "query_by": "title,company_name,functions,locations",
        "per_page": settings.simplify_per_page,
        "sort_by": "_text_match:desc,start_date:desc",
        "highlight_full_fields": "title,company_name,functions,locations",
        "collection": "jobs",
        "q": keyword,
        "max_facet_values": 50,
        "page": page,
    }
    if filter_by:
        search["filter_by"] = filter_by

    return {"searches": [search]}


def _clean_locations(locations: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for loc in locations:
        value = (loc or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


def _pick_location(locations: list[str]) -> str:
    if not locations:
        return ""
    for loc in locations:
        if loc and loc.strip() and loc.strip() != "United States":
            return loc.strip()
    return locations[0].strip()


def _html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_simplify_redirect_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.netloc.lower() == "simplify.jobs"
        and parsed.path.startswith("/jobs/click/")
    )


def _canonical_job_url(url: str) -> str:
    """Normalize external job-board URLs to the same shape as direct scrapers."""
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (url or "").strip()

    netloc = parsed.netloc.lower()
    query = parsed.query
    if netloc in {"job-boards.greenhouse.io", "boards.greenhouse.io"}:
        query = ""

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") or parsed.path,
            parsed.params,
            query,
            "",
        )
    )


async def _resolve_redirect_url(session: aiohttp.ClientSession, url: str) -> str:
    if not url:
        return ""

    async def _request(method: str) -> str:
        async with session.request(
            method,
            url,
            allow_redirects=True,
            max_redirects=10,
        ) as resp:
            if resp.status >= 400:
                return ""
            return str(resp.url)

    for method in ("HEAD", "GET"):
        try:
            resolved = await _request(method)
        except Exception:
            resolved = ""
        if resolved and not _is_simplify_redirect_url(resolved):
            return _canonical_job_url(resolved)

    return _canonical_job_url(url)


async def scrape_simplify(keywords: list[str]) -> list[dict]:
    """
    Returns list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs: list[dict] = []
    seen_posting_ids: set[str] = set()

    headers = {"Content-Type": "application/json"}
    params = {"x-typesense-api-key": SIMPLIFY_TYPESENSE_API_KEY}
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        filter_by = await _build_location_filter(session, settings.job_location)
        for keyword in keywords:
            console.log(
                f"[cyan]Simplify:[/cyan] Scraping '{keyword}' in "
                f"'{settings.job_location}'"
            )
            total_hits = 0

            for page in range(1, settings.simplify_max_pages + 1):
                payload = _build_search_payload(keyword, page, filter_by)
                try:
                    async with session.post(
                        SIMPLIFY_SEARCH_URL, params=params, json=payload
                    ) as resp:
                        if resp.status != 200:
                            console.log(
                                f"  [yellow]Search HTTP {resp.status} on page {page}[/yellow]"
                            )
                            break
                        data = await resp.json()
                except Exception as e:
                    console.log(f"  [red]Search error page {page}: {e}[/red]")
                    break

                results = data.get("results", [])
                if not results:
                    break
                hits = results[0].get("hits", [])
                if not hits:
                    break

                total_hits += len(hits)
                for hit in hits:
                    doc = hit.get("document", {})
                    posting_id = doc.get("posting_id")
                    if not posting_id or posting_id in seen_posting_ids:
                        continue
                    seen_posting_ids.add(posting_id)

                    title = (doc.get("title") or "").strip()
                    company = (doc.get("company_name") or "").strip()
                    locations = _clean_locations(doc.get("locations", []) or [])
                    location = _pick_location(locations)

                    if not title or not company:
                        continue

                    jobs.append(
                        {
                            "title": title,
                            "company": company,
                            "location": location,
                            "locations": locations,
                            "url": "",
                            "source": "simplify",
                            "source_external_id": posting_id,
                            "description": "",
                            "posting_id": posting_id,
                        }
                    )

                await asyncio.sleep(0.3)

            console.log(f"  Found {total_hits} hits")
            await asyncio.sleep(1)

        found_count = len(jobs)
        jobs = filter_new_jobs(jobs)
        skipped_count = found_count - len(jobs)
        if skipped_count:
            console.log(
                f"  Skipped enrichment for {skipped_count} existing Simplify jobs"
            )

        jobs = await enrich_job_descriptions(jobs, session)

    console.log(f"[green]Simplify:[/green] {len(jobs)} total jobs collected")
    return jobs


async def enrich_job_descriptions(
    jobs: list[dict], session: aiohttp.ClientSession
) -> list[dict]:
    """Enrich a list of jobs by fetching detail JSON from Simplify API."""
    if not jobs:
        return []

    enriched: list[dict] = []
    total = len(jobs)

    sem = asyncio.Semaphore(5)
    log = console.log

    async def _enrich_job(job: dict) -> dict:
        posting_id = job.get("posting_id")
        if not posting_id:
            return job

        url = SIMPLIFY_DETAIL_URL.format(posting_id=posting_id)
        async with sem:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log(
                            f"  [yellow]Detail HTTP {resp.status} for {posting_id}[/yellow]"
                        )
                        return job
                    data = await resp.json()
            except Exception as e:
                log(
                    f"  [yellow]Enrich failed for {job.get('title','')} @ {job.get('company','')}: {e}[/yellow]"
                )
                return job

        description_html = data.get("description", "") or ""
        job["description"] = _html_to_text(description_html)[:3000]
        detail_url = (
            data.get("url") or f"https://simplify.jobs/jobs/click/{posting_id}"
        ).strip()
        if _is_simplify_redirect_url(detail_url):
            detail_url = await _resolve_redirect_url(session, detail_url)
        job["url"] = _canonical_job_url(detail_url)
        return job

    tasks = [_enrich_job(job) for job in jobs]
    with progress_bar() as progress:
        log = progress.console.log
        task = progress.add_task("Enriching Simplify jobs...", total=total)
        for coro in asyncio.as_completed(tasks):
            job = await coro
            enriched.append(job)
            progress.advance(task)

    return enriched
