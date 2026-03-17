"""
scraper/simplify.py
Scrapes Simplify.jobs for internship/new grad listings.
Simplify renders via JS — we use Playwright and target their search page.
"""

import asyncio
import urllib.parse
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console

console = Console()

SIMPLIFY_BASE = "https://simplify.jobs/jobs"


def build_simplify_url(keyword: str) -> str:
    params = {"search": keyword, "experience": "Internship,Entry Level"}
    return f"{SIMPLIFY_BASE}?{urllib.parse.urlencode(params)}"


async def scrape_simplify(keywords: list[str]) -> list[dict]:
    """
    Returns list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
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
            url = build_simplify_url(keyword)
            console.log(f"[cyan]Simplify:[/cyan] Scraping '{keyword}'")

            try:
                await page.goto(url, timeout=30000, wait_until="networkidle")
                await page.wait_for_timeout(3000)

                # Scroll to trigger lazy loading
                for _ in range(4):
                    await page.keyboard.press("End")
                    await page.wait_for_timeout(1200)

                # Simplify job cards — selectors may need updating if they change markup
                cards = await page.query_selector_all("a[data-testid='job-card'], div[class*='JobCard'], li[class*='job-item']")

                # Fallback: grab all job-like links
                if not cards:
                    cards = await page.query_selector_all("a[href*='/jobs/']")

                console.log(f"  Found {len(cards)} cards")

                seen_urls = set()
                for card in cards:
                    try:
                        # Try multiple selector strategies
                        title = ""
                        company = ""
                        location = ""
                        job_url = ""

                        # Title
                        for sel in ["h3", "h2", "[class*='title']", "[class*='Title']"]:
                            el = await card.query_selector(sel)
                            if el:
                                title = (await el.inner_text()).strip()
                                break

                        # Company
                        for sel in ["[class*='company']", "[class*='Company']", "p"]:
                            el = await card.query_selector(sel)
                            if el:
                                text = (await el.inner_text()).strip()
                                if text and text != title:
                                    company = text
                                    break

                        # URL
                        href = await card.get_attribute("href")
                        if href:
                            if href.startswith("http"):
                                job_url = href.split("?")[0]
                            else:
                                job_url = f"https://simplify.jobs{href.split('?')[0]}"

                        if title and job_url and job_url not in seen_urls:
                            seen_urls.add(job_url)
                            jobs.append({
                                "title": title,
                                "company": company or "Unknown",
                                "location": location,
                                "url": job_url,
                                "source": "simplify",
                                "description": "",
                            })
                    except Exception as e:
                        continue

                await asyncio.sleep(3)

            except PWTimeout:
                console.log(f"  [red]Timeout on Simplify for '{keyword}'[/red]")
            except Exception as e:
                console.log(f"  [red]Error: {e}[/red]")

        await browser.close()

    console.log(f"[green]Simplify:[/green] {len(jobs)} total jobs collected")
    return jobs
