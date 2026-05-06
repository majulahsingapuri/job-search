"""
inspect_db.py  —  Browse the jobs database from the terminal.
"""

import argparse
import json
from db.database import get_connection, init_db
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()


def _score_style(score):
    if score is None: return "dim"
    if score >= 8:    return "bold green"
    if score >= 6:    return "yellow"
    if score >= 4:    return "orange3"
    return "red"


def show_jobs_table(rows, title="Jobs"):
    if not rows:
        console.print(f"[yellow]No jobs found.[/yellow]")
        return

    table = Table(title=title, header_style="bold magenta", show_lines=True)
    table.add_column("ID",       width=10, style="dim")
    table.add_column("Score",    width=7)
    table.add_column("Title",    width=30)
    table.add_column("Company",  width=20)
    table.add_column("Location", width=16)
    table.add_column("Mode",     width=7)
    table.add_column("Source",   width=9)
    table.add_column("Resume",   width=13)
    table.add_column("Date",     width=11)

    for row in rows:
        score = row["fit_score"]
        style = _score_style(score)
        table.add_row(
            row["id"],
            f"[{style}]{score:.1f}[/{style}]" if score is not None else "—",
            row["title"][:28],
            row["company"][:18],
            (row["location"] or "")[:14],
            (row.get("location_mode") or "—")[:7],
            row["source"] or "",
            row["resume_variant"] or "—",
            (row["date_found"] or "")[:10],
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} jobs[/dim]\n")


def show_job_detail(job_id: str):
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        location_rows = conn.execute(
            """
            SELECT *
            FROM job_locations
            WHERE job_id=?
            ORDER BY position
            """,
            (job_id,),
        ).fetchall()

    if not row:
        console.print(f"[red]Job '{job_id}' not found.[/red]")
        return

    row = dict(row)
    score = row["fit_score"]
    style = _score_style(score)

    # Header panel
    header = Text()
    header.append(f"{row['title']}\n", style="bold white")
    header.append(f"{row['company']}  •  {row.get('location','')}\n", style="cyan")
    header.append(f"Source: {row['source']}  |  Found: {row['date_found'][:10]}\n", style="dim")
    if location_rows:
        rendered_locations = []
        for loc in location_rows:
            loc_bits = []
            if loc["location_city"]:
                loc_bits.append(loc["location_city"])
            if loc["location_state"]:
                loc_bits.append(loc["location_state"])
            if loc["location_country"]:
                loc_bits.append(loc["location_country"])
            if loc["location_mode"]:
                loc_bits.append(loc["location_mode"])
            if loc_bits:
                rendered_locations.append(", ".join(loc_bits))
        if rendered_locations:
            header.append(f"Parsed: {'; '.join(rendered_locations)}\n", style="dim")
    if row.get("url"):
        header.append(row["url"], style="link")
    console.print(Panel(header, title=f"[{style}]Fit Score: {score}[/{style}]", expand=False))

    # Fit reasoning
    if row.get("fit_reasoning"):
        console.print(Panel(row["fit_reasoning"], title="Fit Reasoning", border_style="blue"))

    # Resume
    if row.get("resume_variant"):
        console.print(f"\n[bold]Resume Variant:[/bold] [green]{row['resume_variant']}[/green]")
        if row.get("resume_reasoning"):
            console.print(f"  {row['resume_reasoning']}")

    # Red flags
    if row.get("red_flags"):
        console.print(Panel(row["red_flags"], title="⚠ Red Flags", border_style="red"))

    # Outreach draft
    if row.get("outreach_draft"):
        console.print(Panel(
            row["outreach_draft"],
            title=f"LinkedIn Outreach Draft  [{len(row['outreach_draft'])} chars]",
            border_style="green",
        ))

    # People to reach
    if row.get("people_to_reach"):
        try:
            queries = json.loads(row["people_to_reach"])
            console.print("\n[bold]LinkedIn Search Queries to Find People:[/bold]")
            for i, q in enumerate(queries, 1):
                console.print(f"  {i}. {q}")
        except Exception:
            console.print(f"\n[bold]People to reach:[/bold] {row['people_to_reach']}")

    # Description preview
    if row.get("description"):
        preview = row["description"][:400] + ("..." if len(row["description"]) > 400 else "")
        console.print(Panel(preview, title="Description Preview", border_style="dim"))


def show_stats():
    init_db()
    with get_connection() as conn:
        runs   = conn.execute("SELECT * FROM scrape_runs ORDER BY run_at DESC LIMIT 30").fetchall()
        totals = conn.execute("SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source").fetchall()
        scored = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
        total  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    console.rule("[bold]Scrape Run History[/bold]")
    t = Table(header_style="bold cyan")
    t.add_column("Run At",  width=20)
    t.add_column("Source",  width=10)
    t.add_column("Found",   width=7)
    t.add_column("New",     width=7)
    t.add_column("Error",   width=35)
    for r in runs:
        t.add_row(r["run_at"][:19], r["source"], str(r["jobs_found"]),
                  str(r["jobs_new"]), (r["error"] or "")[:33])
    console.print(t)

    console.rule("[bold]Database Summary[/bold]")
    console.print(f"  Total jobs : {total}")
    console.print(f"  Scored     : {scored}")
    for row in totals:
        console.print(f"  {row['source']:12} {row['cnt']} jobs")


def _add_filters(where: list[str], params: list[object], args: argparse.Namespace) -> None:
    if args.source:
        where.append("source = ?")
        params.append(args.source)
    if args.city:
        where.append(
            "EXISTS (SELECT 1 FROM job_locations jl WHERE jl.job_id = jobs.id AND jl.location_city = ?)"
        )
        params.append(args.city)
    if args.state:
        where.append(
            "EXISTS (SELECT 1 FROM job_locations jl WHERE jl.job_id = jobs.id AND jl.location_state = ?)"
        )
        params.append(args.state)
    if args.country:
        where.append(
            "EXISTS (SELECT 1 FROM job_locations jl WHERE jl.job_id = jobs.id AND jl.location_country = ?)"
        )
        params.append(args.country)
    if args.mode:
        where.append(
            "EXISTS (SELECT 1 FROM job_locations jl WHERE jl.job_id = jobs.id AND jl.location_mode = ?)"
        )
        params.append(args.mode)


def _list_jobs(args: argparse.Namespace) -> None:
    where: list[str] = []
    params: list[object] = []

    if args.scored:
        where.append("fit_score IS NOT NULL")
        order_by = "fit_score DESC, date_found DESC"
        title = "Scored Jobs (ranked by fit)"
    elif args.all:
        order_by = "date_found DESC"
        title = "All Jobs"
    else:
        where.append("fit_score IS NULL")
        order_by = "date_found DESC"
        title = "Unscored Jobs"

    _add_filters(where, params, args)

    query = "SELECT * FROM jobs"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += f" ORDER BY {order_by}"
    if args.limit:
        query += " LIMIT ?"
        params.append(args.limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    show_jobs_table([dict(r) for r in rows], title=title)


if __name__ == "__main__":
    init_db()
    parser = argparse.ArgumentParser(
        description="Browse the jobs database from the terminal."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--stats", action="store_true", help="Scrape run history")
    mode_group.add_argument("--job", metavar="ID", help="Full detail for one job")
    mode_group.add_argument(
        "--scored", action="store_true", help="Scored jobs, ranked by fit"
    )
    mode_group.add_argument("--all", action="store_true", help="Everything")

    parser.add_argument("--source", help="Filter by source (linkedin/simplify/hn)")
    parser.add_argument("--city", help="Filter by parsed city")
    parser.add_argument("--state", help="Filter by parsed state")
    parser.add_argument("--country", help="Filter by parsed country")
    parser.add_argument(
        "--mode", choices=["remote", "hybrid", "onsite"], help="Filter by work mode"
    )
    parser.add_argument("--limit", type=int, help="Limit number of rows")

    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.job:
        show_job_detail(args.job)
    else:
        _list_jobs(args)
