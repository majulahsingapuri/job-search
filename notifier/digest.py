"""
notifier/digest.py
Builds and sends the daily digest email via Porkbun SMTP.

Each email contains:
  - Jobs ranked by fit score (above MIN_FIT_SCORE threshold)
  - Per job: role, company, score, which resume to use, outreach draft, LinkedIn queries
  - HTML email with plain-text fallback
  - After sending, marks all included jobs as notified in the DB
"""

import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from rich.console import Console

from db.database import get_unnotified_jobs, mark_notified
from config.resumes import RESUME_VARIANTS
from utils import LINKEDIN_PEOPLE_SEARCH_URL
from config.settings import get_settings

console = Console()
settings = get_settings()


def _score_colour(score: float) -> str:
    if score >= 8:
        return "#22c55e"
    if score >= 6:
        return "#eab308"
    if score >= 4:
        return "#f97316"
    return "#ef4444"


def _score_label(score: float) -> str:
    if score >= 8:
        return "Strong fit"
    if score >= 6:
        return "Good fit"
    if score >= 4:
        return "Partial fit"
    return "Weak fit"


def _resume_badge(variant: str) -> str:
    colours = {
        "ml_engineer": "#6366f1",
        "data_scientist": "#0ea5e9",
        "ai_researcher": "#8b5cf6",
    }
    labels = {
        "ml_engineer": "ML Engineer",
        "data_scientist": "Data Scientist",
        "ai_researcher": "AI Researcher",
    }
    c = colours.get(variant, "#6b7280")
    l = labels.get(variant, variant)
    return (
        f'<span style="background:{c};color:white;padding:2px 8px;border-radius:4px;'
        f'font-size:12px;font-weight:600;">{l}</span>'
    )


def _build_html(jobs: list[dict], date_str: str) -> str:
    total = len(jobs)
    strong = sum(1 for j in jobs if (j["fit_score"] or 0) >= 7)

    cards = ""
    for job in jobs:
        score = job["fit_score"] or 0
        sc = _score_colour(score)
        sl = _score_label(score)
        variant = job.get("resume_variant") or ""
        resume_fn = RESUME_VARIANTS.get(variant, {}).get("file", "—")

        try:
            queries = json.loads(job.get("people_to_reach") or "[]")
        except Exception:
            queries = []

        query_links = "".join(
            f'<li style="margin:4px 0;"><a href="{LINKEDIN_PEOPLE_SEARCH_URL}'
            f'?keywords={q.replace(" ", "%20")}" style="color:#6366f1;font-size:13px;">{q}</a></li>'
            for q in queries
        )

        red_flags = ""
        if job.get("red_flags"):
            red_flags = (
                f'<div style="margin-top:12px;padding:10px 14px;background:#fef2f2;'
                f'border-left:3px solid #ef4444;border-radius:4px;">'
                f'<b style="font-size:12px;color:#dc2626;">⚠ Red Flags</b>'
                f'<p style="margin:4px 0 0;font-size:13px;color:#374151;">{job["red_flags"]}</p></div>'
            )

        outreach_len = len(job.get("outreach_draft") or "")
        queries_block = ""
        if query_links:
            queries_block = (
                f'<div style="margin-top:14px;">'
                f'<p style="margin:0 0 6px;font-size:12px;font-weight:600;color:#374151;">'
                f"LinkedIn Searches → Find People</p>"
                f'<ul style="margin:0;padding-left:18px;">{query_links}</ul></div>'
            )

        cards += f"""
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;
            padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06);">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
    <div>
      <h2 style="margin:0;font-size:17px;color:#111827;">
        <a href="{job.get('url','#')}" style="color:#111827;text-decoration:none;">{job['title']}</a>
      </h2>
      <p style="margin:3px 0 0;font-size:14px;color:#6b7280;">
        {job['company']} &nbsp;·&nbsp; {job.get('location','') or 'N/A'}
        &nbsp;·&nbsp; <span style="text-transform:capitalize;">{job.get('source','')}</span>
      </p>
    </div>
    <div style="text-align:right;">
      <div style="font-size:26px;font-weight:700;color:{sc};line-height:1;">{score:.1f}</div>
      <div style="font-size:11px;color:{sc};font-weight:600;">{sl}</div>
    </div>
  </div>
  <div style="margin-top:14px;">
    {_resume_badge(variant)}
    <span style="margin-left:8px;font-size:12px;color:#6b7280;">Send: <code>{resume_fn}</code></span>
  </div>
  <p style="margin:12px 0 0;font-size:13px;color:#374151;line-height:1.6;">
    {job.get('fit_reasoning','') or ''}
  </p>
  {red_flags}
  <div style="margin-top:14px;padding:12px 16px;background:#f0fdf4;
              border-left:3px solid #22c55e;border-radius:4px;">
    <p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#15803d;">
      LinkedIn Note Draft &nbsp;
      <span style="font-weight:400;color:#6b7280;">({outreach_len} chars)</span>
    </p>
    <p style="margin:0;font-size:13px;color:#374151;font-style:italic;">
      &ldquo;{job.get('outreach_draft','')}&rdquo;
    </p>
  </div>
  {queries_block}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Digest {date_str}</title></head>
<body style="margin:0;padding:0;background:#f9fafb;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">

  <div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:12px;
              padding:24px 28px;margin-bottom:24px;color:white;">
    <h1 style="margin:0;font-size:22px;font-weight:700;">&#127919; Daily Job Digest</h1>
    <p style="margin:6px 0 0;opacity:.85;font-size:14px;">{date_str}</p>
    <div style="margin-top:14px;display:flex;gap:28px;">
      <div><div style="font-size:28px;font-weight:700;">{total}</div>
           <div style="font-size:12px;opacity:.8;">Roles Today</div></div>
      <div><div style="font-size:28px;font-weight:700;">{strong}</div>
           <div style="font-size:12px;opacity:.8;">Strong Fits (7+)</div></div>
    </div>
  </div>

  {cards or '<p style="color:#6b7280;text-align:center;">No new roles above your threshold today.</p>'}

  <p style="text-align:center;font-size:12px;color:#9ca3af;margin-top:8px;">
    Job Agent &nbsp;·&nbsp; bhargav.io
  </p>
</div>
</body></html>"""


def _build_plaintext(jobs: list[dict], date_str: str) -> str:
    lines = [
        f"DAILY JOB DIGEST — {date_str}",
        f"{len(jobs)} roles | {sum(1 for j in jobs if (j['fit_score'] or 0) >= 7)} strong fits (7+)",
        "=" * 60,
        "",
    ]
    for i, job in enumerate(jobs, 1):
        score = job["fit_score"] or 0
        variant = job.get("resume_variant") or "—"
        resume = RESUME_VARIANTS.get(variant, {}).get("file", "—")
        try:
            queries = json.loads(job.get("people_to_reach") or "[]")
        except Exception:
            queries = []

        lines += [
            f"{i}. {job['title']} @ {job['company']}",
            f"   Score   : {score:.1f}/10  ({_score_label(score)})",
            f"   Source  : {job.get('source','')}  |  {job.get('location','')}",
            f"   Resume  : {resume}",
            f"   URL     : {job.get('url','')}",
            f"   Reason  : {job.get('fit_reasoning','') or '—'}",
        ]
        if job.get("red_flags"):
            lines.append(f"   Flags   : {job['red_flags']}")
        lines += [
            f"   Outreach: \"{job.get('outreach_draft','') or '—'}\"",
        ]
        if queries:
            lines.append("   People  :")
            for q in queries:
                lines.append(f"     • {q}")
        lines += ["", "-" * 60, ""]
    return "\n".join(lines)


def send_digest() -> dict:
    """Send digest email. Returns {sent, job_count, error}."""
    min_score = settings.min_fit_score
    smtp_host = settings.smtp_host
    smtp_port = settings.smtp_port
    smtp_user = settings.smtp_user
    smtp_pass = settings.smtp_pass
    notify_to = settings.notify_to or smtp_user

    if not smtp_user or not smtp_pass:
        console.log("[red]Notifier: SMTP_USER or SMTP_PASS not set — skipping.[/red]")
        return {
            "sent": False,
            "job_count": 0,
            "error": "SMTP credentials missing",
            "jobs": [],
        }

    jobs = get_unnotified_jobs(min_fit_score=min_score)
    if not jobs:
        console.log("[dim]Notifier: nothing new above threshold.[/dim]")
        return {"sent": False, "job_count": 0, "error": None, "jobs": []}

    date_str = datetime.now().strftime("%A, %B %d %Y")
    strong = sum(1 for j in jobs if (j["fit_score"] or 0) >= 7)
    subject = (
        f"Job Digest {datetime.now().strftime('%b %d')} — "
        f"{len(jobs)} roles, {strong} strong fits"
    )

    console.log(f"[cyan]Notifier:[/cyan] sending digest ({len(jobs)} jobs)...")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = notify_to
    msg.attach(MIMEText(_build_plaintext(jobs, date_str), "plain"))
    msg.attach(MIMEText(_build_html(jobs, date_str), "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_user, [notify_to], msg.as_string())

        mark_notified([j["id"] for j in jobs])
        console.log(f"[green]✓ Digest sent to {notify_to}[/green]")
        return {"sent": True, "job_count": len(jobs), "error": None, "jobs": jobs}

    except smtplib.SMTPAuthenticationError:
        err = "SMTP auth failed — double-check SMTP_USER / SMTP_PASS"
        console.log(f"[red]{err}[/red]")
        return {"sent": False, "job_count": 0, "error": err, "jobs": []}
    except Exception as e:
        console.log(f"[red]Notifier error: {e}[/red]")
        return {"sent": False, "job_count": len(jobs), "error": str(e)}
