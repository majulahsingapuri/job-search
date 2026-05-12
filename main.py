"""
main.py
Full pipeline: scrape → deduplicate → score (Claude) → email digest.

Usage:
    python main.py                                    # Start scheduler (daily at SCRAPE_TIME)
    python main.py --now                              # Run full pipeline once, then exit
    python main.py --score-only                       # Skip scraping, score unscored jobs, send digest
    python main.py --digest-only                      # Skip scraping + scoring, just send digest
    python main.py --outreach-only --headful          # Outreach with visible browser
    python main.py --outreach-only --outreach-date 2026-03-25
    python main.py --linkedin                         # Run only LinkedIn scraper
    python main.py --simplify                         # Run only Simplify scraper
    python main.py --greenhouse                       # Run only Greenhouse scraper
    python main.py --hn                               # Run only HN Hiring scraper
"""

import asyncio
import argparse
import sys
import schedule
import time
from datetime import datetime
from rich.table import Table

from console_utils import console
from config.settings import get_settings

from db.database import (
    init_db,
    insert_job,
    log_scrape_run,
    get_jobs_scraped_on,
    get_jobs_missing_descriptions,
    update_job_description,
)
from scraper.linkedin import scrape_linkedin
from scraper.linkedin import enrich_linkedin_descriptions
from scraper.simplify import scrape_simplify
from scraper.greenhouse import scrape_greenhouse
from scraper.greenhouse import enrich_greenhouse_jobs
from scraper.hn import scrape_hn
from agent.pipeline import run_routing_pipeline
from agent.linkedin_outreach import run_linkedin_outreach
from notifier.digest import send_digest

settings = get_settings()

KEYWORDS = settings.job_keywords
LOCATION = settings.job_location
SCRAPE_TIME = settings.scrape_time

ALLOWED_STAGES = ["scrape", "score", "digest", "outreach"]
ALLOWED_SCRAPE_SOURCES = ["linkedin", "simplify", "greenhouse", "hn"]
ENV_SCRAPE_SOURCES = [
    s for s in settings.scrape_sources if s in ALLOWED_SCRAPE_SOURCES
] or ALLOWED_SCRAPE_SOURCES.copy()


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


def _parse_selected_scrapers(args: argparse.Namespace) -> list[str]:
    selected = []
    if args.linkedin:
        selected.append("linkedin")
    if args.simplify:
        selected.append("simplify")
    if args.greenhouse:
        selected.append("greenhouse")
    if args.hn:
        selected.append("hn")
    if not selected:
        return ENV_SCRAPE_SOURCES.copy()
    filtered = [s for s in selected if s in ENV_SCRAPE_SOURCES]
    if not filtered:
        console.log(
            "[yellow]Requested scrapers are disabled by SCRAPE_SOURCES; "
            "falling back to allowed sources.[/yellow]"
        )
        return ENV_SCRAPE_SOURCES.copy()
    if len(filtered) != len(selected):
        console.log(
            "[yellow]Some requested scrapers are disabled by SCRAPE_SOURCES; "
            "running allowed sources only.[/yellow]"
        )
    return filtered


def _parse_stage_list(value: str | None, label: str) -> list[str]:
    if not value or not value.strip():
        return ALLOWED_STAGES.copy()
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    unknown = sorted({p for p in parts if p not in ALLOWED_STAGES})
    if unknown:
        allowed = ", ".join(ALLOWED_STAGES)
        invalid = ", ".join(unknown)
        raise ValueError(
            f"{label} contains invalid stage(s): {invalid}. Allowed: {allowed}."
        )
    stages: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if p not in seen:
            stages.append(p)
            seen.add(p)
    return stages or ALLOWED_STAGES.copy()


def _parse_outreach_date_arg(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "outreach date must be in YYYY-MM-DD format"
        ) from exc
    return value


async def _scrape_stage(selected: list[str], headful: bool = False) -> int:
    tasks = []
    ordered = []
    if "linkedin" in selected:
        ordered.append("linkedin")
        tasks.append(
            _run_scraper(
                "linkedin", scrape_linkedin(KEYWORDS, LOCATION, headless=not headful)
            )
        )
    if "simplify" in selected:
        ordered.append("simplify")
        tasks.append(_run_scraper("simplify", scrape_simplify(KEYWORDS)))
    if "greenhouse" in selected:
        ordered.append("greenhouse")
        tasks.append(
            _run_scraper("greenhouse", scrape_greenhouse(KEYWORDS, LOCATION))
        )
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
    if "greenhouse" in selected:
        gf, gn = by_source.get("greenhouse", (0, 0))
        t.add_row("Greenhouse", str(gf), str(gn))
        total_found += gf
        total_new += gn
    if "hn" in selected:
        hf, hn = by_source.get("hn", (0, 0))
        t.add_row("HN Hiring", str(hf), str(hn))
        total_found += hf
        total_new += hn
    t.add_row("[bold]Total[/bold]", str(total_found), f"[bold]{total_new}[/bold]")
    console.print(t)
    return total_new


async def run_pipeline(
    skip_scrape: bool = False,
    skip_score: bool = False,
    selected: list[str] | None = None,
    headful: bool = False,
    outreach_targets: list[str] | None = None,
    stages: list[str] | None = None,
):
    stages = stages or ALLOWED_STAGES.copy()
    do_scrape = "scrape" in stages and not skip_scrape
    do_score = "score" in stages and not skip_score
    do_digest = "digest" in stages
    do_outreach = "outreach" in stages

    console.rule(f"[bold blue]Job Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    console.log(f"[dim]Stages: {', '.join(stages)}[/dim]")

    # ── Stage 1: Scrape ───────────────────────────────────────────────────────
    if do_scrape:
        total_new = await _scrape_stage(
            selected or ["linkedin", "simplify", "greenhouse", "hn"],
            headful=headful,
        )
        if total_new == 0 and do_score:
            console.log("[yellow]No new jobs scraped.[/yellow]")
            # Still fall through to digest in case there are unnotified scored jobs

    # ── Stage 2: Score ────────────────────────────────────────────────────────
    if do_score:
        await run_routing_pipeline()

    # ── Stage 3: Digest ───────────────────────────────────────────────────────
    result = None
    if do_digest:
        result = send_digest()
        if result["error"]:
            console.log(f"[red]Digest error: {result['error']}[/red]")

    # ── Stage 4: Outreach ─────────────────────────────────────────────────────
    if do_outreach:
        if not do_digest:
            console.log(
                "[yellow]Outreach skipped because digest stage is disabled.[/yellow]"
            )
        elif result and result.get("sent") and result.get("jobs"):
            console.log("[cyan]LinkedIn:[/cyan] Starting outreach stage...")
            await run_linkedin_outreach(
                result["jobs"], headless=not headful, targets=outreach_targets
            )

    console.rule("[dim]Done[/dim]")


def run_pipeline_sync(
    selected: list[str],
    headful: bool = False,
    outreach_targets: list[str] | None = None,
    stages: list[str] | None = None,
):
    asyncio.run(
        run_pipeline(
            selected=selected,
            headful=headful,
            outreach_targets=outreach_targets,
            stages=stages,
        )
    )


async def run_outreach_for_date(
    outreach_date: str,
    headful: bool = False,
    outreach_targets: list[str] | None = None,
) -> None:
    jobs = get_jobs_scraped_on(outreach_date)
    if not jobs:
        console.log(
            f"[yellow]No scored jobs found for {outreach_date}.[/yellow]"
        )
        return
    console.log(
        f"[cyan]LinkedIn:[/cyan] Running outreach for {len(jobs)} jobs from {outreach_date}"
    )
    await run_linkedin_outreach(
        jobs, headless=not headful, targets=outreach_targets
    )


async def run_enrich_missing_descriptions(
    source: str,
    limit: int | None = None,
    headful: bool = False,
) -> None:
    jobs = get_jobs_missing_descriptions(source=source, limit=limit)
    total = len(jobs)
    if total == 0:
        console.log("[green]No jobs with missing descriptions found.[/green]")
        return

    if source not in {"linkedin", "greenhouse"}:
        console.log(
            f"[red]Enrichment for source '{source}' is not supported yet.[/red]"
        )
        return

    if source == "linkedin":
        console.log(
            f"[cyan]LinkedIn:[/cyan] Enriching descriptions for {total} jobs"
        )
        enriched = await enrich_linkedin_descriptions(
            jobs,
            headless=not headful,
            concurrency=settings.linkedin_enrich_concurrency,
        )
    else:
        console.log(
            f"[cyan]Greenhouse:[/cyan] Enriching descriptions for {total} jobs"
        )
        enriched = await enrich_greenhouse_jobs(jobs)

    updated = 0
    skipped = 0
    for job in enriched:
        description = (job.get("description") or "").strip()
        if description:
            update_job_description(job["id"], description)
            updated += 1
        else:
            skipped += 1

    console.log(
        f"[green]Enrichment complete: {updated} updated, {skipped} still empty.[/green]"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Job Agent — scrape, score, digest, and run LinkedIn outreach."
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run full pipeline once, then exit.",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Skip scraping, score unscored jobs, then send digest.",
    )
    parser.add_argument(
        "--digest-only",
        action="store_true",
        help="Skip scraping + scoring, just send digest.",
    )
    parser.add_argument(
        "--outreach-only",
        action="store_true",
        help="Run LinkedIn outreach for a specific date (default: today).",
    )
    parser.add_argument(
        "--outreach-date",
        type=_parse_outreach_date_arg,
        help="Date for --outreach-only in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--enrich-missing",
        action="store_true",
        help="Enrich missing job descriptions from their source.",
    )
    parser.add_argument(
        "--enrich-source",
        choices=["linkedin", "greenhouse"],
        default="linkedin",
        help="Source to enrich descriptions for (default: linkedin).",
    )
    parser.add_argument(
        "--enrich-limit",
        type=int,
        help="Limit number of jobs to enrich (default: all).",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Show the browser for stages that use Playwright.",
    )
    parser.add_argument(
        "--linkedin",
        action="store_true",
        help="Run LinkedIn scraper only (can be combined with other scrapers).",
    )
    parser.add_argument(
        "--simplify",
        action="store_true",
        help="Run Simplify scraper only (can be combined with other scrapers).",
    )
    parser.add_argument(
        "--greenhouse",
        action="store_true",
        help="Run Greenhouse scraper only (can be combined with other scrapers).",
    )
    parser.add_argument(
        "--hn",
        action="store_true",
        help="Run HN Hiring scraper only (can be combined with other scrapers).",
    )
    return parser


if __name__ == "__main__":
    init_db()
    parser = _build_parser()
    args = parser.parse_args()

    if args.outreach_date and not args.outreach_only:
        parser.error("--outreach-date requires --outreach-only")
    if args.enrich_missing and (
        args.now or args.score_only or args.digest_only or args.outreach_only
    ):
        parser.error(
            "--enrich-missing cannot be combined with other run modes."
        )

    SELECTED_SCRAPERS = _parse_selected_scrapers(args)
    try:
        STAGES_NOW = _parse_stage_list(
            settings.pipeline_stages_now, "PIPELINE_STAGES_NOW"
        )
        STAGES_SCHEDULE = _parse_stage_list(
            settings.pipeline_stages_schedule, "PIPELINE_STAGES_SCHEDULE"
        )
    except ValueError as exc:
        console.log(f"[red]{exc}[/red]")
        sys.exit(1)

    if args.score_only and "score" not in STAGES_NOW:
        console.log(
            "[red]--score-only requires 'score' in PIPELINE_STAGES_NOW.[/red]"
        )
        sys.exit(1)
    if args.digest_only and "digest" not in STAGES_NOW:
        console.log(
            "[red]--digest-only requires 'digest' in PIPELINE_STAGES_NOW.[/red]"
        )
        sys.exit(1)
    console.log("[bold green]Job Agent started[/bold green]")
    console.log(f"  Keywords : {KEYWORDS}")
    console.log(f"  Location : {LOCATION}")
    console.log(f"  Schedule : daily at {SCRAPE_TIME}")
    console.log(f"  Scrapers : {', '.join(SELECTED_SCRAPERS)}")
    console.log(f"  Stages (now) : {', '.join(STAGES_NOW)}")
    console.log(f"  Stages (schedule) : {', '.join(STAGES_SCHEDULE)}")

    headful = args.headful

    if args.digest_only:
        console.log(
            "[yellow]--digest-only: sending digest of already-scored jobs[/yellow]"
        )
        asyncio.run(
            run_pipeline(
                skip_scrape=True,
                skip_score=True,
                selected=SELECTED_SCRAPERS,
                headful=headful,
                stages=STAGES_NOW,
            )
        )
        sys.exit(0)

    if args.outreach_only:
        outreach_date = args.outreach_date or datetime.now().strftime("%Y-%m-%d")
        console.log(
            "[yellow]--outreach-only: running LinkedIn outreach[/yellow]"
        )
        asyncio.run(
            run_outreach_for_date(
                outreach_date, headful=headful
            )
        )
        sys.exit(0)

    if args.enrich_missing:
        console.log(
            "[yellow]--enrich-missing: enriching job descriptions[/yellow]"
        )
        asyncio.run(
            run_enrich_missing_descriptions(
                source=args.enrich_source,
                limit=args.enrich_limit,
                headful=headful,
            )
        )
        sys.exit(0)

    if args.score_only:
        console.log(
            "[yellow]--score-only: scoring unscored jobs then sending digest[/yellow]"
        )
        asyncio.run(
            run_pipeline(
                skip_scrape=True,
                selected=SELECTED_SCRAPERS,
                headful=headful,
                stages=STAGES_NOW,
            )
        )
        sys.exit(0)

    if args.now:
        console.log("[yellow]--now: running pipeline immediately[/yellow]")
        asyncio.run(
            run_pipeline(
                selected=SELECTED_SCRAPERS, headful=headful, stages=STAGES_NOW
            )
        )
        sys.exit(0)

    schedule.every().day.at(SCRAPE_TIME).do(
        run_pipeline_sync, SELECTED_SCRAPERS, headful, None, STAGES_SCHEDULE
    )
    console.log(f"[green]Scheduler running. Next run at {SCRAPE_TIME}[/green]")
    while True:
        schedule.run_pending()
        time.sleep(30)
