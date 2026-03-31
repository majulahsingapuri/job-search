"""
agent/pipeline.py
Processes all unscored jobs through the routing agent in controlled batches.
Called by main.py after scraping completes.
"""

import asyncio
import json
import os
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from db.database import get_unscored_jobs, update_job_agent_results
from agent.routing_agent import score_job_async

console = Console()

# Keep batches small — each call hits the Claude API
BATCH_SIZE = int(os.getenv("AGENT_BATCH_SIZE", "3"))


def _score_badge(score: float) -> str:
    if score >= 8:
        return "bold green"
    if score >= 6:
        return "yellow"
    if score >= 4:
        return "orange3"
    return "red"


async def run_routing_pipeline() -> dict:
    jobs = get_unscored_jobs()
    if not jobs:
        console.log("[dim]Routing agent: no unscored jobs.[/dim]")
        return {"processed": 0, "scored": 0, "errors": 0}

    console.rule(f"[bold blue]Routing Agent — scoring {len(jobs)} jobs")

    processed = scored = errors = 0
    results_log: list[tuple[dict, dict]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Scoring...", total=len(jobs))

        for i in range(0, len(jobs), BATCH_SIZE):
            batch = jobs[i : i + BATCH_SIZE]
            tasks = [asyncio.create_task(score_job_async(j)) for j in batch]
            for coro in asyncio.as_completed(tasks):
                try:
                    item = await coro
                except Exception:
                    processed += 1
                    errors += 1
                    progress.advance(task)
                    continue

                processed += 1
                progress.advance(task)

                job, result = item
                if result is None:
                    errors += 1
                    continue

                # Persist all fields to DB
                update_job_agent_results(
                    job_id=job["id"],
                    fit_score=result["fit_score"],
                    fit_reasoning=result.get("fit_reasoning", ""),
                    resume_variant=result["resume_variant"],
                    resume_reasoning=result.get("resume_reasoning", ""),
                    outreach_draft=result["outreach_draft"],
                    people_to_reach=json.dumps(
                        result.get("linkedin_search_queries", [])
                    ),
                    red_flags=result.get("red_flags", ""),
                )
                scored += 1
                results_log.append((job, result))

            if i + BATCH_SIZE < len(jobs):
                await asyncio.sleep(1.5)  # polite pause between batches

    # ── Summary table ─────────────────────────────────────────────────────────
    if results_log:
        results_log.sort(key=lambda x: x[1]["fit_score"], reverse=True)

        table = Table(
            title="Routing Results",
            header_style="bold magenta",
            show_lines=True,
        )
        table.add_column("Score", width=7)
        table.add_column("Title", width=28)
        table.add_column("Company", width=18)
        table.add_column("Resume", width=14)
        table.add_column("Reasoning", width=46)

        for job, result in results_log:
            score = result["fit_score"]
            colour = _score_badge(score)
            table.add_row(
                f"[{colour}]{score:.1f}[/{colour}]",
                job["title"][:26],
                job["company"][:16],
                result["resume_variant"],
                result.get("fit_reasoning", "")[:44],
            )

        console.print(table)

    console.log(
        f"[green]✓ Scored {scored}/{processed}[/green]"
        + (f" [red]({errors} errors)[/red]" if errors else "")
    )
    return {"processed": processed, "scored": scored, "errors": errors}
