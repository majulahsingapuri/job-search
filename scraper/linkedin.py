"""
scraper/linkedin.py
Scrapes LinkedIn Jobs search results pages (no login required for basic listings).
Uses Playwright in headless mode to handle JS-rendered content.
"""

import asyncio
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console

console = Console()

LINKEDIN_BASE = "https://www.linkedin.com/jobs/search"


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


async def scrape_linkedin(keywords: list[str], location: str) -> list[dict]:
    """
    Returns a list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        for keyword in keywords:
            url = build_linkedin_url(keyword, location)
            console.log(f"[cyan]LinkedIn:[/cyan] Scraping '{keyword}' in '{location}'")

            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2500)  # Let JS render

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
    for job in jobs:
        if not job.get("url"):
            enriched.append(job)
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
    return enriched
