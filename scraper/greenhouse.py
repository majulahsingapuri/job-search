"""
scraper/greenhouse.py
Fetches my.greenhouse.io search listings via Greenhouse's Inertia JSON endpoint.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
from bs4 import BeautifulSoup
from rich.console import Console

from config.settings import get_settings
from db.database import filter_new_jobs
from scraper.greenhouse_auth import (
    greenhouse_cookie_header_from_state,
    greenhouse_inertia_version_from_state,
)

console = Console()
settings = get_settings()

GREENHOUSE_SEARCH_URL = "https://my.greenhouse.io/jobs"
GREENHOUSE_EMBED_URL = "https://job-boards.greenhouse.io/embed/job_app"
GREENHOUSE_DEFAULT_INERTIA_VERSION = "debac7412270deb73a5f29804de3015747c87c56"
GREENHOUSE_DEFAULT_LAT = "39.71614"
GREENHOUSE_DEFAULT_LON = "-96.999246"
GREENHOUSE_PAGE_DELAY_SECONDS = 0.5
GREENHOUSE_ENRICH_CONCURRENCY = 5
GREENHOUSE_DESCRIPTION_LIMIT = 3000


def _build_params(keyword: str, location: str, page: int) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "query": keyword,
        "location": location,
        "page": page,
    }

    if location.strip().lower() in {
        "united states",
        "united states of america",
        "us",
        "usa",
    }:
        params.update(
            {
                "lat": GREENHOUSE_DEFAULT_LAT,
                "lon": GREENHOUSE_DEFAULT_LON,
                "location_type": "country",
                "country_short_name": "US",
            }
        )

    return params


def _build_headers() -> dict[str, str]:
    state_inertia_version = greenhouse_inertia_version_from_state()
    state_cookie = greenhouse_cookie_header_from_state()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "X-Inertia": "true",
        "X-Inertia-Version": (
            state_inertia_version
            or settings.greenhouse_inertia_version
            or GREENHOUSE_DEFAULT_INERTIA_VERSION
        ),
    }
    if state_cookie:
        headers["Cookie"] = state_cookie
    return headers


def _build_detail_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }


def _clean_locations(locations: list[str], work_type: str | None = None) -> list[str]:
    cleaned = []
    seen = set()
    suffix = f" ({work_type.replace('_', ' ')})" if work_type else ""
    for loc in locations:
        value = (loc or "").strip()
        if not value:
            continue
        if suffix:
            value = f"{value}{suffix}"
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


def _pick_location(locations: list[str], work_type: str | None) -> str:
    if not locations:
        return work_type or ""
    return _clean_locations(locations, work_type)[0]


def _build_description(post: dict[str, Any]) -> str:
    parts = []
    first_published = post.get("firstPublished")
    if first_published:
        try:
            published = datetime.fromisoformat(
                first_published.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except ValueError:
            published = first_published
        parts.append(f"First published: {published}")

    work_type = post.get("workType")
    if work_type:
        parts.append(f"Work type: {str(work_type).replace('_', ' ')}")

    pay_ranges = post.get("payRanges")
    if pay_ranges:
        parts.append(f"Pay range: {pay_ranges}")

    locations = post.get("locations") or []
    if locations:
        parts.append(f"Locations: {', '.join(locations)}")

    return "\n".join(parts)


def _html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_job_post(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        if isinstance(data.get("jobPost"), dict):
            return data["jobPost"]
        for value in data.values():
            found = _find_job_post(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_job_post(item)
            if found:
                return found
    return None


def _extract_remix_context(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        marker = "window.__remixContext = "
        if marker not in text:
            continue
        json_text = text.split(marker, 1)[1].strip()
        if json_text.endswith(";"):
            json_text = json_text[:-1]
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            return None
    return None


def _description_from_job_post(job_post: dict[str, Any]) -> str:
    sections = [
        job_post.get("introduction") or "",
        job_post.get("content") or "",
        job_post.get("conclusion") or "",
    ]
    text = "\n\n".join(_html_to_text(section) for section in sections if section)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_detail_html(raw_html: str) -> dict[str, str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    parsed: dict[str, str] = {}

    context = _extract_remix_context(soup)
    job_post = _find_job_post(context) if context else None
    if job_post:
        title = (job_post.get("title") or "").strip()
        company = (job_post.get("company_name") or "").strip()
        location = (job_post.get("job_post_location") or "").strip()
        public_url = (job_post.get("public_url") or "").strip()
        description = _description_from_job_post(job_post)

        if title:
            parsed["title"] = title
        if company:
            parsed["company"] = company
        if location:
            parsed["location"] = location
        if public_url:
            parsed["url"] = public_url
        if description:
            parsed["description"] = description[:GREENHOUSE_DESCRIPTION_LIMIT]
        return parsed

    title_el = soup.select_one(".job__title h1")
    company_meta = soup.select_one('meta[property="og:site_name"]')
    location_meta = soup.select_one('meta[property="og:description"]')
    url_meta = soup.select_one('meta[property="og:url"]')
    description_el = soup.select_one(".job__description")

    if title_el:
        parsed["title"] = title_el.get_text(" ", strip=True)
    if company_meta and company_meta.get("content"):
        parsed["company"] = company_meta["content"].strip()
    if location_meta and location_meta.get("content"):
        parsed["location"] = location_meta["content"].strip()
    if url_meta and url_meta.get("content"):
        parsed["url"] = url_meta["content"].strip()
    if description_el:
        parsed["description"] = _html_to_text(str(description_el))[
            :GREENHOUSE_DESCRIPTION_LIMIT
        ]

    return parsed


def _extract_greenhouse_job_id(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    gh_jid = query.get("gh_jid", [""])[0].strip()
    if gh_jid:
        return gh_jid
    match = re.search(r"/jobs/(\d+)", parsed.path)
    return match.group(1) if match else ""


def _board_token_candidates(company: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", "", company.lower())
    without_suffixes = re.sub(
        r"(incorporated|corporation|company|group|holdings|inc|llc|ltd|co)$",
        "",
        normalized,
    )
    candidates = [without_suffixes, normalized]
    seen: set[str] = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _build_embed_url(board_token: str, job_id: str) -> str:
    params = {"for": board_token, "token": job_id, "t": "my.greenhouse.search"}
    return f"{GREENHOUSE_EMBED_URL}?{urlencode(params)}"


def _detail_url_candidates(job: dict) -> list[str]:
    url = (job.get("url") or "").strip()
    job_id = str(job.get("greenhouse_id") or "").strip() or _extract_greenhouse_job_id(
        url
    )
    candidates: list[str] = []

    parsed = urlparse(url)
    if parsed.netloc.lower() == "job-boards.greenhouse.io":
        candidates.append(url)
        return candidates

    if job_id:
        for board_token in _board_token_candidates(job.get("company", "")):
            candidates.append(_build_embed_url(board_token, job_id))

    return candidates


async def scrape_greenhouse(keywords: list[str], location: str) -> list[dict]:
    """
    Returns list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs: list[dict] = []
    seen_ids: set[str] = set()
    max_pages = settings.greenhouse_max_pages
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        headers=_build_headers(), timeout=timeout
    ) as session:
        for keyword in keywords:
            console.log(
                f"[cyan]Greenhouse:[/cyan] Scraping '{keyword}' in '{location}'"
            )
            total_hits = 0

            for page in range(1, max_pages + 1):
                try:
                    async with session.get(
                        GREENHOUSE_SEARCH_URL,
                        params=_build_params(keyword, location, page),
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

                if data.get("component") == "login":
                    console.log(
                        "  [yellow]Greenhouse session required; "
                        "run python -m scraper.greenhouse_auth.[/yellow]"
                    )
                    break

                props = data.get("props", {})
                posts = props.get("jobPosts", []) or []
                if not posts:
                    break

                total_hits += len(posts)
                for post in posts:
                    job_id = str(post.get("id") or "")
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    title = (post.get("title") or "").strip()
                    company = (post.get("companyName") or "").strip()
                    public_url = (post.get("publicUrl") or "").strip()
                    if not title or not company or not public_url:
                        continue

                    locations = _clean_locations(
                        post.get("locations", []) or [],
                        post.get("workType"),
                    )
                    jobs.append(
                        {
                            "title": title,
                            "company": company,
                            "location": _pick_location(locations, None),
                            "locations": locations,
                            "url": public_url,
                            "source": "greenhouse",
                            "source_external_id": job_id,
                            "description": _build_description(post),
                            "greenhouse_id": job_id,
                        }
                    )

                if not props.get("moreResultsAvailable"):
                    break

                await asyncio.sleep(GREENHOUSE_PAGE_DELAY_SECONDS)

            console.log(f"  Found {total_hits} hits")
            await asyncio.sleep(1)

        found_count = len(jobs)
        jobs = filter_new_jobs(jobs)
        skipped_count = found_count - len(jobs)
        if skipped_count:
            console.log(
                f"  Skipped enrichment for {skipped_count} existing Greenhouse jobs"
            )

        jobs = await enrich_greenhouse_descriptions(jobs, session)

    console.log(f"[green]Greenhouse:[/green] {len(jobs)} total jobs collected")
    return jobs


async def enrich_greenhouse_descriptions(
    jobs: list[dict], session: aiohttp.ClientSession
) -> list[dict]:
    """Enrich jobs by fetching their public Greenhouse job-board pages."""
    enriched: list[dict] = []
    total = len(jobs)
    processed = 0
    console.log(f"  Enriching 0/{total}")

    sem = asyncio.Semaphore(GREENHOUSE_ENRICH_CONCURRENCY)

    async def _enrich_job(job: dict) -> dict:
        url = (job.get("url") or "").strip()
        if not url:
            return job
        detail_urls = _detail_url_candidates(job)
        if not detail_urls:
            return job

        parsed: dict[str, str] = {}
        async with sem:
            for detail_url in detail_urls:
                try:
                    async with session.get(
                        detail_url, headers=_build_detail_headers()
                    ) as resp:
                        if resp.status != 200:
                            console.log(
                                f"  [yellow]Detail HTTP {resp.status} for {detail_url}[/yellow]"
                            )
                            continue
                        raw_html = await resp.text()
                except Exception as e:
                    console.log(
                        f"  [yellow]Enrich failed for {job.get('title','')} @ "
                        f"{job.get('company','')}: {e}[/yellow]"
                    )
                    continue

                parsed = _parse_detail_html(raw_html)
                if parsed.get("description"):
                    break

        for field in ("title", "company", "location", "url", "description"):
            value = (parsed.get(field) or "").strip()
            if value:
                job[field] = value
                if field == "location":
                    job["locations"] = _clean_locations([value])
        return job

    tasks = [_enrich_job(job) for job in jobs]
    for coro in asyncio.as_completed(tasks):
        job = await coro
        enriched.append(job)
        processed += 1
        if processed % 10 == 0 or processed == total:
            console.log(f"  Enriched {processed}/{total}")

    return enriched


async def enrich_greenhouse_jobs(jobs: list[dict]) -> list[dict]:
    """Enrich Greenhouse jobs using a managed HTTP session."""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        headers=_build_headers(), timeout=timeout
    ) as session:
        return await enrich_greenhouse_descriptions(jobs, session)
