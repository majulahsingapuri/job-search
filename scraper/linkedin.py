"""
scraper/linkedin.py
Scrapes LinkedIn Jobs search results pages (no login required for basic listings).
Uses Playwright in headless mode to handle JS-rendered content.
"""

import asyncio

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console

from scraper.linkedin_auth import _create_context, _goto_with_login_fallback
from utils import DEFAULT_LINKEDIN_STORAGE_STATE, get_linkedin_storage_state_path
console = Console()

LINKEDIN_BASE = "https://www.linkedin.com/jobs/search"
async def _scroll_job_results(
    page,
    container_selector: str,
    auth_card_selector: str,
    public_card_selector: str,
    idle_rounds: int = 3,
    wait_ms: int = 1000,
    step_px: int = 700,
    max_rounds: int = 60,
) -> bool:
    try:
        await page.wait_for_selector(container_selector, timeout=5000)
    except PWTimeout:
        return False

    container = await page.query_selector(container_selector)
    if not container:
        return False

    previous_count = -1
    previous_scroll_height = -1
    stable_rounds = 0
    rounds = 0

    while True:
        scroll_top = await container.evaluate("(el) => el.scrollTop")
        scroll_height = await container.evaluate("(el) => el.scrollHeight")
        next_scroll_top = min(scroll_top + step_px, scroll_height)
        await container.evaluate(
            "(el, value) => { el.scrollTop = value; }", next_scroll_top
        )
        await page.wait_for_timeout(wait_ms)

        auth_cards = await page.query_selector_all(auth_card_selector)
        public_cards = await page.query_selector_all(public_card_selector)
        total_cards = max(len(auth_cards), len(public_cards))
        new_scroll_height = await container.evaluate("(el) => el.scrollHeight")

        at_bottom = next_scroll_top >= new_scroll_height
        if total_cards == previous_count and new_scroll_height == previous_scroll_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = total_cards
            previous_scroll_height = new_scroll_height

        rounds += 1
        if (stable_rounds >= idle_rounds and at_bottom) or rounds >= max_rounds:
            break

    return True


# Maps our keyword to LinkedIn's URL-encoded equivalent
def build_linkedin_url(keyword: str, location: str, start: int | None = None) -> str:
    import urllib.parse

    params = {
        "keywords": keyword,
        "location": location,
        "f_TPR": "r86400",  # Posted in last 24 hours
        "f_JT": "I",  # Internship — change to "F" for full-time or remove
        "sortBy": "DD",  # Most recent first
    }
    if start is not None:
        params["start"] = start
    return f"{LINKEDIN_BASE}?{urllib.parse.urlencode(params)}"


async def scrape_linkedin(
    keywords: list[str], location: str, headless: bool = True
) -> list[dict]:
    """
    Returns a list of job dicts:
    {title, company, location, url, source, description}
    """
    jobs = []
    seen_urls: set[str] = set()
    auth_selectors = {
        "card": "div.job-card-container.job-card-list",
        "title": "a.job-card-list__title--link",
        "company": "div.artdeco-entity-lockup__subtitle span",
        "location": (
            "div.artdeco-entity-lockup__caption "
            "ul.job-card-container__metadata-wrapper li span"
        ),
        "link": "a.job-card-list__title--link",
        "label": "authenticated",
    }
    public_selectors = {
        "card": "div.job-search-card",
        "title": "h3.base-search-card__title",
        "company": "h4.base-search-card__subtitle",
        "location": "span.job-search-card__location",
        "link": "a.base-card__full-link",
        "label": "public",
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        storage_state = get_linkedin_storage_state_path(
            default_path=DEFAULT_LINKEDIN_STORAGE_STATE
        )
        context, used_state = await _create_context(browser, storage_state)
        page = await context.new_page()

        for keyword in keywords:
            console.log(f"[cyan]LinkedIn:[/cyan] Scraping '{keyword}' in '{location}'")
            start = 0

            while True:
                url = build_linkedin_url(keyword, location, start=start)

                try:
                    page, context, used_state = await _goto_with_login_fallback(
                        page, url, browser, context, used_state
                    )

                    # Scroll the results list to load all cards
                    scroll_container_selector = (
                        "#main > div > div.scaffold-layout__list-detail-inner."
                        "scaffold-layout__list-detail-inner--grow > div.scaffold-layout__list > div"
                    )
                    scrolled = await _scroll_job_results(
                        page,
                        scroll_container_selector,
                        auth_selectors["card"],
                        public_selectors["card"],
                    )
                    if not scrolled:
                        for _ in range(3):
                            await page.keyboard.press("End")
                            await page.wait_for_timeout(1000)

                    if used_state:
                        auth_cards = await page.query_selector_all(
                            auth_selectors["card"]
                        )
                        if auth_cards:
                            selectors = auth_selectors
                            cards = auth_cards
                        else:
                            cards = await page.query_selector_all(
                                public_selectors["card"]
                            )
                            selectors = public_selectors
                    else:
                        public_cards = await page.query_selector_all(
                            public_selectors["card"]
                        )
                        if public_cards:
                            selectors = public_selectors
                            cards = public_cards
                        else:
                            cards = await page.query_selector_all(
                                auth_selectors["card"]
                            )
                            selectors = auth_selectors

                    if not cards:
                        console.log(
                            f"  Reached end of listings for '{keyword}' "
                            f"(start={start})."
                        )
                        break

                    console.log(
                        f"  Found {len(cards)} cards (start={start}, "
                        f"{selectors['label']} selectors)"
                    )

                    for card in cards:
                        try:
                            title_el = await card.query_selector(selectors["title"])
                            company_el = await card.query_selector(
                                selectors["company"]
                            )
                            location_el = await card.query_selector(
                                selectors["location"]
                            )
                            link_el = await card.query_selector(selectors["link"])

                            title = (
                                (await title_el.inner_text()).strip()
                                if title_el
                                else ""
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
                            job_url = (
                                await link_el.get_attribute("href") if link_el else ""
                            )

                            # Clean tracking params from URL
                            job_url = job_url.split("?")[0] if job_url else ""
                            if job_url.startswith("/"):
                                job_url = f"https://www.linkedin.com{job_url}"

                            if job_url and job_url in seen_urls:
                                continue
                            if job_url:
                                seen_urls.add(job_url)

                            if title and company:
                                # Skip aggregator postings
                                if company in ["Lensa", "WayUp", "Jobs via Dice"]:
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

                    start += 25
                    # Rate limit courtesy pause between pages
                    await asyncio.sleep(3)

                except PWTimeout:
                    console.log(
                        f"  [red]Timeout scraping LinkedIn for '{keyword}'[/red]"
                    )
                    break
                except Exception as e:
                    console.log(f"  [red]Error: {e}[/red]")
                    break

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
