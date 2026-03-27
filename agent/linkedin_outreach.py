"""
agent/linkedin_outreach.py
Post-digest LinkedIn People outreach automation using Playwright.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
)

from db.database import has_outreach_profile, insert_outreach_log, update_job_status

console = Console()

LINKEDIN_PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/"
DEFAULT_STORAGE_STATE = ".auth/linkedin_state.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
LOCATION_FILTER = "United States"
SCHOOLS = ["Northeastern University", "Nanyang Technological University Singapore"]


def _get_storage_state_path() -> Path:
    raw = DEFAULT_STORAGE_STATE
    return Path(raw).expanduser()


def _normalize_profile_url(url: str | None) -> str:
    if not url:
        return ""
    clean = url.split("?")[0].split("#")[0].strip()
    if clean.startswith("/in/"):
        clean = "https://www.linkedin.com" + clean
    if clean.startswith("https://linkedin.com"):
        clean = clean.replace("https://linkedin.com", "https://www.linkedin.com")
    if clean.startswith("http://"):
        clean = "https://" + clean[len("http://") :]
    clean = clean.rstrip("/")
    return clean.lower()


def _normalize_company_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip()).lower()


def _safe_float(value: object, default: float = -1.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date_found(raw: str | None) -> datetime:
    if not raw:
        return datetime.min
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def _select_top_jobs_by_company(jobs: list[dict]) -> list[dict]:
    """Pick the highest scoring job per company from a provided list."""
    best_by_company: dict[str, dict] = {}
    for idx, job in enumerate(jobs):
        company_key = _normalize_company_name(job.get("company"))
        if company_key:
            key = f"company:{company_key}"
        else:
            key = f"job:{job.get('id') or idx}"

        score = _safe_float(job.get("fit_score"))
        date_found = _parse_date_found(job.get("date_found"))

        current = best_by_company.get(key)
        if not current:
            best_by_company[key] = {
                "job": job,
                "score": score,
                "date_found": date_found,
                "idx": idx,
            }
            continue

        if (score, date_found, -idx) > (
            current["score"],
            current["date_found"],
            -current["idx"],
        ):
            best_by_company[key] = {
                "job": job,
                "score": score,
                "date_found": date_found,
                "idx": idx,
            }

    selected_entries = list(best_by_company.values())
    selected_entries.sort(key=lambda entry: entry["idx"])
    return [entry["job"] for entry in selected_entries]


async def _is_login_page(page) -> bool:
    url = page.url or ""
    if "/login" in url:
        return True
    login_el = await page.query_selector(
        "input[name='session_key'], input#username, form[action*='login']"
    )
    return login_el is not None


async def _open_all_filters(page):
    candidates = [
        page.locator("button:has(span:has-text('All filters'))"),
        page.locator("button:has-text('All filters')"),
    ]
    for loc in candidates:
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                popover = page.locator("aside[aria-hidden='false']").first
                await popover.wait_for(state="visible", timeout=5000)
                return popover
        except Exception:
            continue
    return None


async def _find_input(popover, hints: list[str]):
    for h in hints:
        loc = popover.locator(f"input[placeholder*='{h}'], input[aria-label*='{h}']")
        if await loc.count() > 0:
            return loc.first
    for h in hints:
        label = popover.locator(f"label:has-text('{h}')")
        if await label.count() > 0:
            input_loc = label.locator("xpath=following::input[1]")
            if await input_loc.count() > 0:
                return input_loc.first
    return None


async def _select_first_suggestion(page, input_loc) -> bool:
    list_id = await input_loc.get_attribute("aria-owns")
    if list_id:
        listbox = page.locator(f"#{list_id}")
        try:
            await listbox.wait_for(state="visible", timeout=2000)
        except Exception:
            listbox = None

        # Prefer keyboard selection to avoid pointer interception
        try:
            await input_loc.press("ArrowDown")
            await input_loc.press("Enter")
            if listbox is not None:
                try:
                    await listbox.wait_for(state="hidden", timeout=1200)
                except Exception:
                    pass
            return True
        except Exception:
            pass

        if listbox is not None:
            options = listbox.locator("[role='option'], li")
            if await options.count() > 0:
                await options.first.click()
                return True

    # Fallback: use any visible listbox
    listbox = page.locator("div[role='listbox']").first
    if await listbox.count() > 0:
        try:
            await input_loc.press("ArrowDown")
            await input_loc.press("Enter")
            return True
        except Exception:
            options = listbox.locator("[role='option'], li")
            if await options.count() > 0:
                await options.first.click()
                return True
    return False


async def _add_filter_value(page, input_loc, value: str) -> bool:
    if input_loc is None:
        return False
    try:
        await input_loc.click()
        await input_loc.fill(value)
        await page.wait_for_timeout(600)
        if await _select_first_suggestion(page, input_loc):
            await page.wait_for_timeout(200)
            # Close any open suggestions list to avoid intercepting clicks
            await page.keyboard.press("Escape")
            return True
        await input_loc.press("Enter")
        await page.keyboard.press("Escape")
        return True
    except Exception:
        return False


async def _apply_filters(
    page,
    popover,
    company: str,
    use_schools: bool,
) -> bool:
    ok = True
    loc_input = await _find_input(popover, ["Location", "location"])
    if loc_input is None:
        add_loc = popover.locator("button:has(span:has-text('Add a location'))")
        if await add_loc.count() > 0:
            await add_loc.first.click()
            await page.wait_for_timeout(300)
        loc_input = await _find_input(popover, ["Location", "location"])
    if not await _add_filter_value(page, loc_input, LOCATION_FILTER):
        ok = False

    comp_input = await _find_input(popover, ["Company", "company"])
    if comp_input is None:
        add_comp = popover.locator("button:has(span:has-text('Add a company'))")
        if await add_comp.count() > 0:
            await add_comp.first.click()
            await page.wait_for_timeout(300)
        comp_input = await _find_input(popover, ["Company", "company"])
    if not await _add_filter_value(page, comp_input, company):
        ok = False

    if use_schools:
        school_input = await _find_input(popover, ["School", "school"])
        if school_input is None:
            add_school = popover.locator("button:has(span:has-text('Add a school'))")
            if await add_school.count() > 0:
                await add_school.first.click()
                await page.wait_for_timeout(300)
            school_input = await _find_input(popover, ["School", "school"])
        for school in SCHOOLS:
            if not await _add_filter_value(page, school_input, school):
                ok = False
            await page.wait_for_timeout(250)

    # Ensure dropdown overlays are closed before clicking
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(150)

    btn = popover.locator("a:has(span:has-text('Show results'))")
    if await btn.count() > 0:
        await btn.first.click()
        await page.wait_for_timeout(1500)
        return ok
    btn_alt = popover.locator(
        "button:has-text('Show results'), button:has-text('Apply')"
    )
    if await btn_alt.count() > 0:
        await btn_alt.first.click()
        await page.wait_for_timeout(1500)
        return ok
    return False


async def _collect_top_people(page, limit: int = 5) -> list[dict]:
    await page.wait_for_timeout(1500)
    containers = page.locator("div[role='listitem']")
    if await containers.count() == 0:
        containers = page.locator("li.reusable-search__result-container")
    if await containers.count() == 0:
        containers = page.locator("div.entity-result")

    people = []
    seen = set()
    count = await containers.count()
    for i in range(count):
        if len(people) >= limit:
            break
        container = containers.nth(i)
        link_loc = container.locator("a[href*='/in/']")
        link_count = await link_loc.count()
        profile_url = ""
        for j in range(link_count):
            href = await link_loc.nth(j).get_attribute("href")
            if href and "/in/" in href:
                profile_url = _normalize_profile_url(href)
                break
        if not profile_url:
            continue
        if profile_url in seen:
            continue
        seen.add(profile_url)

        name = ""
        name_loc = container.locator("a[href*='/in/']")
        if await name_loc.count() > 0:
            try:
                name = (await name_loc.first.inner_text()).strip()
            except Exception:
                name = ""
        if not name:
            alt_img = container.locator("img[alt]").first
            if await alt_img.count() > 0:
                try:
                    name = (await alt_img.get_attribute("alt") or "").strip()
                except Exception:
                    name = ""

        people.append({"name": name, "profile_url": profile_url})
    return people


async def _search_people(
    context,
    query_text: str,
    company: str,
    use_schools: bool,
) -> tuple[list[dict], str | None]:
    page = await context.new_page()
    try:
        url = f"{LINKEDIN_PEOPLE_SEARCH}?keywords={quote(query_text)}"
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        if await _is_login_page(page):
            return [], "login_required"

        popover = await _open_all_filters(page)
        if not popover:
            console.log("[yellow]LinkedIn: could not open All filters[/yellow]")
        else:
            await _apply_filters(page, popover, company, use_schools)

        people = await _collect_top_people(page, limit=5)
        return people, None
    except PWTimeout:
        return [], "timeout"
    except Exception as e:
        return [], f"error:{e}"
    finally:
        await page.close()


async def _connect_and_send_note(page, note: str) -> tuple[bool, str | None]:
    try:
        # 1) Click Connect (direct invite link if present, else More → menu Connect)
        direct_invite = page.locator(
            "a[aria-label*='Invite'][href*='custom-invite'], a[aria-label*='Invite'][href*='search-custom-invite']"
        )
        if await direct_invite.count() > 0 and await direct_invite.first.is_visible():
            await direct_invite.first.evaluate("el => el.click()")
        else:
            more_btn = page.get_by_role("button", name=re.compile(r"^More$", re.I))
            if await more_btn.count() == 0:
                more_btn = page.locator("button:has-text('More')")
            if await more_btn.count() == 0:
                return False, "connect_not_available"
            await more_btn.first.evaluate("el => el.click()")
            await page.wait_for_timeout(500)

            menu_connect = page.locator(
                "a[role='menuitem'][href*='custom-invite'], a[role='menuitem']:has-text('Connect')"
            )
            if await menu_connect.count() > 0:
                await menu_connect.first.evaluate("el => el.click()")
            else:
                # Per your HTML: Connect is 3rd item (ArrowDown twice, Enter)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")

        # 2) Wait for modal, click "Add a note"
        modal = page.locator("div[data-test-modal][role='dialog']").first
        await modal.wait_for(state="visible", timeout=5000)
        add_note = modal.locator("button:has-text('Add a note')")
        if await add_note.count() == 0:
            return False, "add_note_missing"
        await add_note.first.evaluate("el => el.click()")

        # 3) Wait for textarea, fill note, send
        note_box = modal.locator("textarea[name='message'], textarea#custom-message")
        await note_box.first.wait_for(state="visible", timeout=5000)
        await note_box.first.fill(note)

        send_btn = modal.locator("button:has-text('Send')")
        await send_btn.first.wait_for(state="visible", timeout=5000)
        await send_btn.first.evaluate("el => el.click()")
        await page.wait_for_timeout(1200)
        return True, None
    except Exception as e:
        return False, f"error:{e}"


async def run_linkedin_outreach(jobs: list[dict], headless: bool = True) -> dict:
    if not jobs:
        return {"processed": 0, "sent": 0, "skipped": 0, "errors": 0}

    original_count = len(jobs)
    jobs = _select_top_jobs_by_company(jobs)
    if len(jobs) < original_count:
        console.log(
            f"[dim]LinkedIn outreach: targeting top-scoring role per company "
            f"({len(jobs)}/{original_count}).[/dim]"
        )

    storage_state = _get_storage_state_path()
    if not storage_state.exists():
        console.log(
            f"[red]LinkedIn outreach: session not found at {storage_state}[/red]"
        )
        return {"processed": 0, "sent": 0, "skipped": 0, "errors": 1}

    processed = sent = skipped = errors = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT, storage_state=str(storage_state)
        )

        # Validate login once
        page = await context.new_page()
        await page.goto(LINKEDIN_PEOPLE_SEARCH, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        if await _is_login_page(page):
            console.log(
                "[red]LinkedIn outreach: session expired. Run login and retry.[/red]"
            )
            await page.close()
            await browser.close()
            return {"processed": 0, "sent": 0, "skipped": 0, "errors": 1}
        await page.close()

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )

        with progress:
            jobs_task = progress.add_task("Outreach jobs", total=len(jobs))
            contacts_task = progress.add_task("Contacts attempted", total=None)

            for job in jobs:
                processed += 1
                job_sent = 0
                outreach_note = (job.get("outreach_draft") or "").strip()
                progress.update(
                    jobs_task,
                    description=f"Outreach jobs: {job.get('company','')} — {job.get('title','')[:28]}",
                )

                try:
                    raw_people = job.get("people_to_reach") or "[]"
                    queries = json.loads(raw_people)
                    if not isinstance(queries, list):
                        queries = []
                except Exception:
                    queries = []

                hiring_query = (
                    queries[1]
                    if len(queries) > 1 and queries[1]
                    else f"{job['title']} hiring manager"
                )
                alumni_query = (
                    queries[2] if len(queries) > 2 and queries[2] else "alumni"
                )

                query_set = [
                    ("recruiter", "early career talent recruiter university", False),
                    ("hiring_team", hiring_query, False),
                    ("alumni", alumni_query, True),
                ]

                for query_type, query_text, use_schools in query_set:
                    console.log(
                        f"[cyan]LinkedIn:[/cyan] {job['company']} — {query_type} search"
                    )
                    people, err = await _search_people(
                        context, query_text, job["company"], use_schools
                    )
                    if err:
                        errors += 1
                        console.log(
                            f"[yellow]LinkedIn outreach: search error ({err}) for {job['company']}[/yellow]"
                        )
                        continue
                    console.log(
                        f"[dim]Found {len(people)} people for '{query_text}'[/dim]"
                    )

                    for person in people:
                        progress.advance(contacts_task)
                        profile_url = person.get("profile_url") or ""
                        if not profile_url:
                            skipped += 1
                            continue

                        if has_outreach_profile(profile_url):
                            skipped += 1
                            continue

                        if not outreach_note:
                            insert_outreach_log(
                                job_id=job.get("id"),
                                job_title=job.get("title"),
                                company=job.get("company"),
                                query_type=query_type,
                                query_text=query_text,
                                person_name=person.get("name"),
                                profile_url=profile_url,
                                status="skipped",
                                reason="missing_outreach_note",
                                note_text="",
                                created_at=datetime.now().isoformat(),
                            )
                            skipped += 1
                            continue

                        profile_page = await context.new_page()
                        try:
                            await profile_page.goto(
                                profile_url,
                                timeout=30000,
                                wait_until="domcontentloaded",
                            )
                            await profile_page.wait_for_timeout(1500)
                            if await _is_login_page(profile_page):
                                insert_outreach_log(
                                    job_id=job.get("id"),
                                    job_title=job.get("title"),
                                    company=job.get("company"),
                                    query_type=query_type,
                                    query_text=query_text,
                                    person_name=person.get("name"),
                                    profile_url=profile_url,
                                    status="skipped",
                                    reason="login_required",
                                    note_text="",
                                    created_at=datetime.now().isoformat(),
                                )
                                skipped += 1
                                continue

                            ok, reason = await _connect_and_send_note(
                                profile_page, outreach_note
                            )
                            if ok:
                                insert_outreach_log(
                                    job_id=job.get("id"),
                                    job_title=job.get("title"),
                                    company=job.get("company"),
                                    query_type=query_type,
                                    query_text=query_text,
                                    person_name=person.get("name"),
                                    profile_url=profile_url,
                                    status="sent",
                                    reason="",
                                    note_text=outreach_note,
                                    created_at=datetime.now().isoformat(),
                                )
                                sent += 1
                                job_sent += 1
                                if job.get("id"):
                                    update_job_status(job["id"], "outreach")
                            else:
                                insert_outreach_log(
                                    job_id=job.get("id"),
                                    job_title=job.get("title"),
                                    company=job.get("company"),
                                    query_type=query_type,
                                    query_text=query_text,
                                    person_name=person.get("name"),
                                    profile_url=profile_url,
                                    status="skipped",
                                    reason=reason or "connect_failed",
                                    note_text="",
                                    created_at=datetime.now().isoformat(),
                                )
                                skipped += 1
                        except Exception as e:
                            insert_outreach_log(
                                job_id=job.get("id"),
                                job_title=job.get("title"),
                                company=job.get("company"),
                                query_type=query_type,
                                query_text=query_text,
                                person_name=person.get("name"),
                                profile_url=profile_url,
                                status="failed",
                                reason=str(e),
                                note_text="",
                                created_at=datetime.now().isoformat(),
                            )
                            errors += 1
                        finally:
                            await profile_page.close()
                            await asyncio.sleep(1.2)

                progress.advance(jobs_task)

        await browser.close()

    return {"processed": processed, "sent": sent, "skipped": skipped, "errors": errors}
