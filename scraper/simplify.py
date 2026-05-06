"""
scraper/simplify.py
Fetches Simplify.jobs listings via their Typesense-backed API (no browser).
"""

import asyncio
import html
import re
from typing import Any

import aiohttp
from rich.console import Console

console = Console()

SIMPLIFY_SEARCH_URL = "https://js-ha.simplify.jobs/multi_search"
SIMPLIFY_TYPESENSE_API_KEY = (
    "SWF1ODFZbzBkcVlVdnVwT2FqUE5EZ3JpSk5hVmdpUHg1SklXWEdGbHZVRT1POHJie"
    "yJleGNsdWRlX2ZpZWxkcyI6ImNvbXBhbnlfdXJsLGNhdGVnb3JpZXMsYWRkaXRpb2"
    "5hbF9yZXF1aXJlbWVudHMsY291bnRyaWVzLGRlZ3JlZXMsZ2VvbG9jYXRpb25zLG"
    "luZHVzdHJpZXMsaXNfc2ltcGxlX2FwcGxpY2F0aW9uLGpvYl9saXN0cyxsZWFkZX"
    "JzaGlwX3R5cGUsc2VjdXJpdHlfY2xlYXJhbmNlLHNraWxscyx1cmwifQ=="
)
SIMPLIFY_FILTER_BY = "countries:=[`United States`]"
SIMPLIFY_PER_PAGE = 50
SIMPLIFY_MAX_PAGES = 3

SIMPLIFY_DETAIL_URL = (
    "https://api.simplify.jobs/v2/job-posting/:id/{posting_id}/company"
)


def _build_search_payload(keyword: str, page: int) -> dict[str, Any]:
    return {
        "searches": [
            {
                "query_by": "title,company_name,functions,locations",
                "per_page": SIMPLIFY_PER_PAGE,
                "sort_by": "_text_match:desc,start_date:desc",
                "highlight_full_fields": "title,company_name,functions,locations",
                "collection": "jobs",
                "q": keyword,
                "filter_by": SIMPLIFY_FILTER_BY,
                "max_facet_values": 50,
                "page": page,
            }
        ]
    }


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
        for keyword in keywords:
            console.log(f"[cyan]Simplify:[/cyan] Scraping '{keyword}'")
            total_hits = 0

            for page in range(1, SIMPLIFY_MAX_PAGES + 1):
                payload = _build_search_payload(keyword, page)
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
                    location = _pick_location(doc.get("locations", []) or [])

                    if not title or not company:
                        continue

                    jobs.append(
                        {
                            "title": title,
                            "company": company,
                            "location": location,
                            "url": "",
                            "source": "simplify",
                            "description": "",
                            "posting_id": posting_id,
                        }
                    )

                await asyncio.sleep(0.3)

            console.log(f"  Found {total_hits} hits")
            await asyncio.sleep(1)

        jobs = await enrich_job_descriptions(jobs, session)

    console.log(f"[green]Simplify:[/green] {len(jobs)} total jobs collected")
    return jobs


async def enrich_job_descriptions(
    jobs: list[dict], session: aiohttp.ClientSession
) -> list[dict]:
    """Enrich a list of jobs by fetching detail JSON from Simplify API."""
    enriched: list[dict] = []
    total = len(jobs)
    processed = 0
    console.log(f"  Enriching 0/{total}")

    sem = asyncio.Semaphore(5)

    async def _enrich_job(job: dict) -> dict:
        posting_id = job.get("posting_id")
        if not posting_id:
            return job

        url = SIMPLIFY_DETAIL_URL.format(posting_id=posting_id)
        async with sem:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        console.log(
                            f"  [yellow]Detail HTTP {resp.status} for {posting_id}[/yellow]"
                        )
                        return job
                    data = await resp.json()
            except Exception as e:
                console.log(
                    f"  [yellow]Enrich failed for {job.get('title','')} @ {job.get('company','')}: {e}[/yellow]"
                )
                return job

        description_html = data.get("description", "") or ""
        job["description"] = _html_to_text(description_html)[:3000]
        job["url"] = data.get("url") or f"https://simplify.jobs/jobs/click/{posting_id}"
        return job

    tasks = [_enrich_job(job) for job in jobs]
    for coro in asyncio.as_completed(tasks):
        job = await coro
        enriched.append(job)
        processed += 1
        if processed % 10 == 0 or processed == total:
            console.log(f"  Enriched {processed}/{total}")

    return enriched
