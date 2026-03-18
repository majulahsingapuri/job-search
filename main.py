"""
main.py
Full pipeline: scrape → deduplicate → score (Claude) → email digest.

Usage:
    python main.py               # Start scheduler (daily at SCRAPE_TIME)
    python main.py --now         # Run full pipeline once, then exit
    python main.py --score-only  # Skip scraping, score unscored jobs, send digest
    python main.py --digest-only # Skip scraping + scoring, just send digest
    python main.py --linkedin    # Run only LinkedIn scraper
    python main.py --simplify    # Run only Simplify scraper
    python main.py --hn          # Run only HN Hiring scraper
"""

import asyncio
import sys
import os
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

from db.database import init_db, insert_job, log_scrape_run
from scraper.linkedin import scrape_linkedin
from scraper.simplify import scrape_simplify
from scraper.hn import scrape_hn
from agent.pipeline import run_routing_pipeline
from notifier.digest import send_digest

console = Console()

KEYWORDS = [
    k.strip() for k in os.getenv("JOB_KEYWORDS", "machine learning engineer").split(",")
]
LOCATION = os.getenv("JOB_LOCATION", "Boston, MA")
SCRAPE_TIME = os.getenv("SCRAPE_TIME", "08:00")


async def _run_scraper(source: str, coro) -> tuple[int, int]:
    try:
        jobs = await coro
        new_count = sum(insert_job(j) for j in jobs)
        log_scrape_run(source, len(jobs), new_count)
        return len(jobs), new_count
    except Exception as e:
        console.log(f"[red]{source} error: {e}[/red]")
        log_scrape_run(source, 0, 0, error=str(e))
        return 0, 0


def _parse_selected_scrapers(argv: list[str]) -> list[str]:
    selected = []
    if "--linkedin" in argv:
        selected.append("linkedin")
    if "--simplify" in argv:
        selected.append("simplify")
    if "--hn" in argv:
        selected.append("hn")
    return selected or ["linkedin", "simplify", "hn"]


async def _scrape_stage(selected: list[str]) -> int:
    tasks = []
    ordered = []
    if "linkedin" in selected:
        ordered.append("linkedin")
        tasks.append(_run_scraper("linkedin", scrape_linkedin(KEYWORDS, LOCATION)))
    if "simplify" in selected:
        ordered.append("simplify")
        tasks.append(_run_scraper("simplify", scrape_simplify(KEYWORDS)))
    if "hn" in selected:
        ordered.append("hn")
        tasks.append(_run_scraper("hn", scrape_hn(KEYWORDS)))

    results = await asyncio.gather(*tasks) if tasks else []
    by_source = {src: res for src, res in zip(ordered, results)}

    t = Table(title="Scrape Results", header_style="bold magenta")
    t.add_column("Source", style="cyan")
    t.add_column("Found", justify="right")
    t.add_column("New", justify="right", style="green")
    total_found = 0
    total_new = 0
    if "linkedin" in selected:
        lf, ln = by_source.get("linkedin", (0, 0))
        t.add_row("LinkedIn", str(lf), str(ln))
        total_found += lf
        total_new += ln
    if "simplify" in selected:
        sf, sn = by_source.get("simplify", (0, 0))
        t.add_row("Simplify", str(sf), str(sn))
        total_found += sf
        total_new += sn
    if "hn" in selected:
        hf, hn = by_source.get("hn", (0, 0))
        t.add_row("HN Hiring", str(hf), str(hn))
        total_found += hf
        total_new += hn
    t.add_row("[bold]Total[/bold]", str(total_found), f"[bold]{total_new}[/bold]")
    console.print(t)
    return total_new


async def run_pipeline(
    skip_scrape: bool = False, skip_score: bool = False, selected: list[str] | None = None
):
    console.rule(f"[bold blue]Job Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ── Stage 1: Scrape ───────────────────────────────────────────────────────
    if not skip_scrape:
        total_new = await _scrape_stage(selected or ["linkedin", "simplify", "hn"])
        if total_new == 0 and not skip_score:
            console.log("[yellow]No new jobs scraped.[/yellow]")
            # Still fall through to digest in case there are unnotified scored jobs

    # ── Stage 2: Score ────────────────────────────────────────────────────────
    if not skip_score:
        await run_routing_pipeline()

    # ── Stage 3: Digest ───────────────────────────────────────────────────────
    result = send_digest()
    if result["error"]:
        console.log(f"[red]Digest error: {result['error']}[/red]")

    console.rule("[dim]Done[/dim]")


def run_pipeline_sync(selected: list[str]):
    asyncio.run(run_pipeline(selected=selected))


if __name__ == "__main__":
    init_db()
    SELECTED_SCRAPERS = _parse_selected_scrapers(sys.argv)
    console.log("[bold green]Job Agent started[/bold green]")
    console.log(f"  Keywords : {KEYWORDS}")
    console.log(f"  Location : {LOCATION}")
    console.log(f"  Schedule : daily at {SCRAPE_TIME}")
    console.log(f"  Scrapers : {', '.join(SELECTED_SCRAPERS)}")

    if "--digest-only" in sys.argv:
        console.log(
            "[yellow]--digest-only: sending digest of already-scored jobs[/yellow]"
        )
        asyncio.run(run_pipeline(skip_scrape=True, skip_score=True, selected=SELECTED_SCRAPERS))
        sys.exit(0)

    if "--score-only" in sys.argv:
        console.log(
            "[yellow]--score-only: scoring unscored jobs then sending digest[/yellow]"
        )
        asyncio.run(run_pipeline(skip_scrape=True, selected=SELECTED_SCRAPERS))
        sys.exit(0)

    if "--now" in sys.argv:
        console.log("[yellow]--now: running full pipeline immediately[/yellow]")
        asyncio.run(run_pipeline(selected=SELECTED_SCRAPERS))
        sys.exit(0)

    schedule.every().day.at(SCRAPE_TIME).do(run_pipeline_sync, SELECTED_SCRAPERS)
    console.log(f"[green]Scheduler running. Next run at {SCRAPE_TIME}[/green]")
    while True:
        schedule.run_pending()
        time.sleep(30)
