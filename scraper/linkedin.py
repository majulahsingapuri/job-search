"""
scraper/linkedin.py
Scrapes LinkedIn Jobs search results pages (no login required for basic listings).
Uses Playwright in headless mode to handle JS-rendered content.
"""

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console

console = Console()

LINKEDIN_BASE = "https://www.linkedin.com/jobs/search"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
DEFAULT_STORAGE_STATE = ".auth/linkedin_state.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _get_storage_state_path() -> Path:
    raw = os.getenv("LINKEDIN_STORAGE_STATE", DEFAULT_STORAGE_STATE)
    return Path(raw).expanduser()


async def _create_context(browser, storage_state: Path | None) -> tuple:
    if storage_state and storage_state.exists():
        console.log(f"[cyan]LinkedIn:[/cyan] Using saved session at {storage_state}")
        context = await browser.new_context(
            user_agent=USER_AGENT, storage_state=str(storage_state)
        )
        return context, True

    if storage_state:
        console.log(
            "[yellow]LinkedIn session not found; continuing without login.[/yellow]"
        )
    context = await browser.new_context(user_agent=USER_AGENT)
    return context, False


async def _is_login_page(page) -> bool:
    url = page.url or ""
    if "/login" in url:
        return True
    login_el = await page.query_selector(
        "input[name='session_key'], input#username, form[action*='login']"
    )
    return login_el is not None


async def _goto_with_login_fallback(
    page,
    url: str,
    browser,
    context,
    used_state: bool,
) -> tuple:
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)  # Let JS render

    if used_state and await _is_login_page(page):
        console.log(
            "[yellow]LinkedIn session expired; falling back to public listings.[/yellow]"
        )
        await context.close()
        context, used_state = await _create_context(browser, None)
        page = await context.new_page()
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

    return page, context, used_state


async def login_linkedin() -> None:
    storage_state = _get_storage_state_path()
    storage_state.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        console.log("[cyan]LinkedIn:[/cyan] Opening login page...")
        await page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded")
        console.log(
            "[cyan]LinkedIn:[/cyan] Complete login in the browser, then press Enter here."
        )
        input()

        await context.storage_state(path=str(storage_state))
        await browser.close()

    console.log(f"[green]LinkedIn:[/green] Session saved to {storage_state}")


# Maps our keyword to LinkedIn's URL-encoded equivalent
def build_linkedin_url(keyword: str, location: str) -> str:
    import urllib.parse

    params = {
        "keywords": keyword,
        "location": location,
        "f_TPR": "r86400",  # Posted in last 24 hours
        "f_JT": "I",  # Internship — change to "F" for full-time or remove
        "sortBy": "DD",  # Most recent first
    }
    return f"{LINKEDIN_BASE}?{urllib.parse.urlencode(params)}"


async def scrape_linkedin(
    keywords: list[str], location: str, headless: bool = True
) -> list[dict]:
    """
    Returns a list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        storage_state = _get_storage_state_path()
        context, used_state = await _create_context(browser, storage_state)
        page = await context.new_page()

        for keyword in keywords:
            url = build_linkedin_url(keyword, location)
            console.log(f"[cyan]LinkedIn:[/cyan] Scraping '{keyword}' in '{location}'")

            try:
                page, context, used_state = await _goto_with_login_fallback(
                    page, url, browser, context, used_state
                )

                # Scroll to load more results
                for _ in range(3):
                    await page.keyboard.press("End")
                    await page.wait_for_timeout(1000)

                cards = await page.query_selector_all("div.job-search-card")
                console.log(f"  Found {len(cards)} cards")

                for card in cards:
                    try:
                        title_el = await card.query_selector(
                            "h3.base-search-card__title"
                        )
                        company_el = await card.query_selector(
                            "h4.base-search-card__subtitle"
                        )
                        location_el = await card.query_selector(
                            "span.job-search-card__location"
                        )
                        link_el = await card.query_selector("a.base-card__full-link")

                        title = (
                            (await title_el.inner_text()).strip() if title_el else ""
                        )
                        company = (
                            (await company_el.inner_text()).strip()
                            if company_el
                            else ""
                        )
                        loc = (
                            (await location_el.inner_text()).strip()
                            if location_el
                            else ""
                        )
                        job_url = await link_el.get_attribute("href") if link_el else ""

                        # Clean tracking params from URL
                        job_url = job_url.split("?")[0] if job_url else ""

                        if title and company:
                            # Skip aggregator postings
                            if company in ["Lensa", "WayUp"]:
                                continue
                            jobs.append(
                                {
                                    "title": title,
                                    "company": company,
                                    "location": loc,
                                    "url": job_url,
                                    "source": "linkedin",
                                    "description": "",  # Fetched lazily in enrichment step
                                }
                            )
                    except Exception as e:
                        console.log(f"  [yellow]Card parse error: {e}[/yellow]")
                        continue

                # Rate limit courtesy pause between keywords
                await asyncio.sleep(3)

            except PWTimeout:
                console.log(f"  [red]Timeout scraping LinkedIn for '{keyword}'[/red]")
            except Exception as e:
                console.log(f"  [red]Error: {e}[/red]")

        jobs = await enrich_job_descriptions(jobs, context)
        await browser.close()

    console.log(f"[green]LinkedIn:[/green] {len(jobs)} total jobs collected")
    return jobs


async def enrich_job_descriptions(jobs: list[dict], context) -> list[dict]:
    """Enrich a list of jobs reusing an existing browser context."""
    enriched = []
    total = len(jobs)
    processed = 0
    console.log(f"  Enriching 0/{total}")
    for job in jobs:
        if not job.get("url"):
            enriched.append(job)
            processed += 1
            if processed % 10 == 0 or processed == total:
                console.log(f"  Enriched {processed}/{total}")
            continue
        page = await context.new_page()
        try:
            await page.goto(job["url"], timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            desc_el = await page.query_selector("div.show-more-less-html__markup")
            if desc_el:
                job["description"] = (await desc_el.inner_text()).strip()[:3000]
        except Exception as e:
            console.log(
                f"  [yellow]Enrich failed for {job['title']} @ {job['company']}: {e}[/yellow]"
            )
        finally:
            await page.close()
        await asyncio.sleep(1)
        enriched.append(job)
        processed += 1
        if processed % 10 == 0 or processed == total:
            console.log(f"  Enriched {processed}/{total}")
    return enriched


if __name__ == "__main__":
    if "--login" in sys.argv:
        asyncio.run(login_linkedin())
