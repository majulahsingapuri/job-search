"""
scraper/hn.py
Parses Hacker News "Who is Hiring?" monthly thread via the official HN Algolia API.
No browser needed — pure HTTP. Very reliable.
"""

import aiohttp
import asyncio
from datetime import datetime, timezone
from rich.console import Console

console = Console()

HN_SEARCH_API = "https://hn.algolia.com/api/v1/search"
HN_ITEMS_API = "https://hacker-news.firebaseio.com/v0/item/{}.json"


async def get_latest_hiring_thread_id() -> int | None:
    now = datetime.now()
    query = f"Ask HN: Who is hiring? ({now.strftime('%B %Y')})"
    params = {"query": query, "tags": "ask_hn", "hitsPerPage": 5}
    async with aiohttp.ClientSession() as session:
        async with session.get(HN_SEARCH_API, params=params) as resp:
            data = await resp.json()
            hits = data.get("hits", [])
            if not hits:
                return None
            return int(hits[0]["objectID"])


async def get_thread_comments(thread_id: int) -> list[dict]:
    """Fetch top-level comments (job posts) from the thread."""
    url = HN_ITEMS_API.format(thread_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            thread = await resp.json()

        kids = thread.get("kids", [])[:200]  # Cap at 200 comments
        console.log(
            f"[cyan]HN:[/cyan] Fetching {len(kids)} comments from thread {thread_id}"
        )

        # Fetch comments concurrently in batches of 20
        comments = []
        for i in range(0, len(kids), 20):
            batch = kids[i : i + 20]
            tasks = [session.get(HN_ITEMS_API.format(kid)) for kid in batch]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for r in responses:
                if isinstance(r, Exception):
                    continue
                try:
                    async with r as resp:
                        comment = await resp.json()
                        if (
                            comment
                            and not comment.get("dead")
                            and not comment.get("deleted")
                        ):
                            comments.append(comment)
                except Exception:
                    continue
            await asyncio.sleep(0.5)  # Polite rate limiting

    return comments


def parse_comment_to_job(comment: dict, ml_keywords: list[str]) -> dict | None:
    """
    Parse a raw HN comment into a job dict.
    Only returns jobs matching ML/AI/Data keywords.
    """
    posted_at = comment.get("time", 0)
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    if posted_at < cutoff:
        return None
    text = comment.get("text", "") or ""
    if not text:
        return None

    # Strip HTML tags for keyword matching
    import re

    clean_text = re.sub(r"<[^>]+>", " ", text)
    clean_lower = clean_text.lower()

    # Must match at least one of our keywords
    if not any(kw.lower() in clean_lower for kw in ml_keywords):
        return None

    # Skip if clearly not a job post (too short)
    if len(clean_text) < 100:
        return None

    # Try to extract company name — usually first line or before "|"
    lines = [l.strip() for l in clean_text.split("\n") if l.strip()]
    company = lines[0][:80] if lines else "Unknown"

    # Try to extract a title from common patterns
    title_match = re.search(
        r"(machine learning|ML|AI|data scientist?|research engineer|software engineer)[^|\n]{0,60}",
        clean_text,
        re.IGNORECASE,
    )
    title = title_match.group(0).strip()[:100] if title_match else "ML/AI Role"

    hn_url = f"https://news.ycombinator.com/item?id={comment['id']}"

    return {
        "title": title,
        "company": company,
        "location": "Remote/Various",  # HN posts vary widely
        "url": hn_url,
        "source": "hn",
        "description": clean_text[:3000],
    }


async def scrape_hn(keywords: list[str]) -> list[dict]:
    """
    Returns list of ML/AI job dicts from the latest HN hiring thread.
    """
    ml_keywords = keywords + [
        "machine learning",
        "deep learning",
        "LLM",
        "NLP",
        "computer vision",
        "data science",
        "AI engineer",
        "ML engineer",
        "research engineer",
    ]

    try:
        thread_id = await get_latest_hiring_thread_id()
        if not thread_id:
            console.log("[yellow]HN: Could not find hiring thread[/yellow]")
            return []

        comments = await get_thread_comments(thread_id)
        jobs = []
        for comment in comments:
            job = parse_comment_to_job(comment, ml_keywords)
            if job:
                jobs.append(job)

        console.log(
            f"[green]HN:[/green] {len(jobs)} relevant jobs found in thread {thread_id}"
        )
        return jobs

    except Exception as e:
        console.log(f"[red]HN scraper error: {e}[/red]")
        return []
