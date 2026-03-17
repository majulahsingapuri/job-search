"""
main.py
Full pipeline: scrape → deduplicate → score (Claude) → email digest.

Usage:
    python main.py               # Start scheduler (daily at SCRAPE_TIME)
    python main.py --now         # Run full pipeline once, then exit
    python main.py --score-only  # Skip scraping, score unscored jobs, send digest
    python main.py --digest-only # Skip scraping + scoring, just send digest
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

KEYWORDS    = [k.strip() for k in os.getenv("JOB_KEYWORDS", "machine learning engineer").split(",")]
LOCATION    = os.getenv("JOB_LOCATION", "Boston, MA")
SCRAPE_TIME = os.getenv("SCRAPE_TIME", "08:00")


async def _run_scraper(source: str, coro) -> tuple[int, int]:
    try:
        jobs      = await coro
        new_count = sum(insert_job(j) for j in jobs)
        log_scrape_run(source, len(jobs), new_count)
        return len(jobs), new_count
    except Exception as e:
        console.log(f"[red]{source} error: {e}[/red]")
        log_scrape_run(source, 0, 0, error=str(e))
        return 0, 0


async def _scrape_stage() -> int:
    (lf, ln), (sf, sn), (hf, hn) = await asyncio.gather(
        _run_scraper("linkedin", scrape_linkedin(KEYWORDS, LOCATION)),
        _run_scraper("simplify", scrape_simplify(KEYWORDS)),
        _run_scraper("hn",       scrape_hn(KEYWORDS)),
    )
    t = Table(title="Scrape Results", header_style="bold magenta")
    t.add_column("Source", style="cyan"); t.add_column("Found", justify="right"); t.add_column("New", justify="right", style="green")
    t.add_row("LinkedIn",  str(lf), str(ln))
    t.add_row("Simplify",  str(sf), str(sn))
    t.add_row("HN Hiring", str(hf), str(hn))
    total_new = ln + sn + hn
    t.add_row("[bold]Total[/bold]", str(lf+sf+hf), f"[bold]{total_new}[/bold]")
    console.print(t)
    return total_new


async def run_pipeline(skip_scrape: bool = False, skip_score: bool = False):
    console.rule(f"[bold blue]Job Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ── Stage 1: Scrape ───────────────────────────────────────────────────────
    if not skip_scrape:
        total_new = await _scrape_stage()
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


def run_pipeline_sync():
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    init_db()
    console.log("[bold green]Job Agent started[/bold green]")
    console.log(f"  Keywords : {KEYWORDS}")
    console.log(f"  Location : {LOCATION}")
    console.log(f"  Schedule : daily at {SCRAPE_TIME}")

    if "--digest-only" in sys.argv:
        console.log("[yellow]--digest-only: sending digest of already-scored jobs[/yellow]")
        asyncio.run(run_pipeline(skip_scrape=True, skip_score=True))
        sys.exit(0)

    if "--score-only" in sys.argv:
        console.log("[yellow]--score-only: scoring unscored jobs then sending digest[/yellow]")
        asyncio.run(run_pipeline(skip_scrape=True))
        sys.exit(0)

    if "--now" in sys.argv:
        console.log("[yellow]--now: running full pipeline immediately[/yellow]")
        asyncio.run(run_pipeline())
        sys.exit(0)

    schedule.every().day.at(SCRAPE_TIME).do(run_pipeline_sync)
    console.log(f"[green]Scheduler running. Next run at {SCRAPE_TIME}[/green]")
    while True:
        schedule.run_pending()
        time.sleep(30)
