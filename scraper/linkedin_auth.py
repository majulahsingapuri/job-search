"""
scraper/linkedin_auth.py
Handles LinkedIn authentication and session management.
"""

import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from rich.console import Console

from utils import (
    DEFAULT_LINKEDIN_STORAGE_STATE,
    LINKEDIN_USER_AGENT,
    get_linkedin_storage_state_path,
    is_linkedin_login_page,
)
from config.settings import get_settings

console = Console()
settings = get_settings()

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"


async def _create_context(browser, storage_state: Path | None) -> tuple:
    if storage_state and storage_state.exists():
        console.log(f"[cyan]LinkedIn:[/cyan] Using saved session at {storage_state}")
        context = await browser.new_context(
            user_agent=LINKEDIN_USER_AGENT, storage_state=str(storage_state)
        )
        return context, True

    if storage_state:
        console.log(
            "[yellow]LinkedIn session not found; continuing without login.[/yellow]"
        )
    context = await browser.new_context(user_agent=LINKEDIN_USER_AGENT)
    return context, False


async def _is_two_factor_page(page) -> bool:
    url = page.url or ""
    if "/checkpoint/" in url or "two-step" in url or "challenge" in url:
        return True
    twofa_el = await page.query_selector(
        "input[name='pin'], input[name='otp'], input[name='verificationCode'], "
        "input#input__phone_verification_pin, input#input__email_verification_pin"
    )
    return twofa_el is not None


async def _wait_for_login_success(page, timeout_ms: int = 180_000) -> bool:
    start = time.monotonic()
    warned_2fa = False
    while (time.monotonic() - start) * 1000 < timeout_ms:
        if await _is_two_factor_page(page):
            if not warned_2fa:
                console.log(
                    "[yellow]LinkedIn:[/yellow] 2FA detected. Approve the login, "
                    "then wait for the page to finish redirecting."
                )
                warned_2fa = True
        elif await is_linkedin_login_page(page):
            pass
        else:
            return True
        await page.wait_for_timeout(1000)
    return False


async def _goto_with_login_fallback(
    page,
    url: str,
    browser,
    context,
    used_state: bool,
    *,
    headless: bool = True,
    allow_public_fallback: bool = True,
    allow_login_attempt: bool = True,
    storage_state: Path | None = None,
) -> tuple:
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)  # Let JS render

    if not await is_linkedin_login_page(page):
        return page, context, used_state, False, False

    if allow_login_attempt:
        if used_state:
            console.log(
                "[yellow]LinkedIn session expired; attempting login.[/yellow]"
            )
        else:
            console.log(
                "[yellow]LinkedIn unauthenticated; attempting login.[/yellow]"
            )
        if storage_state is None:
            storage_state = get_linkedin_storage_state_path(
                default_path=DEFAULT_LINKEDIN_STORAGE_STATE
            )
        login_ok = await login_linkedin(headless=headless)
        if login_ok and storage_state and storage_state.exists():
            await context.close()
            context, used_state = await _create_context(browser, storage_state)
            page = await context.new_page()
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            if not await is_linkedin_login_page(page):
                return page, context, used_state, False, False

    if allow_public_fallback:
        console.log(
            "[yellow]LinkedIn login failed; using public listings.[/yellow]"
        )
        await context.close()
        context, used_state = await _create_context(browser, None)
        page = await context.new_page()
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        return page, context, used_state, False, True

    console.log(
        "[red]LinkedIn login failed and public fallback is disabled.[/red]"
    )
    return page, context, used_state, True, False


async def login_linkedin(headless: bool = True) -> bool:
    storage_state = get_linkedin_storage_state_path(
        default_path=DEFAULT_LINKEDIN_STORAGE_STATE
    )
    storage_state.parent.mkdir(parents=True, exist_ok=True)
    username = settings.linkedin_username.strip()
    password = settings.linkedin_password.strip()

    if headless and (not username or not password):
        console.log(
            "[red]LinkedIn login: headless mode requires LINKEDIN_USERNAME and "
            "LINKEDIN_PASSWORD. Re-run with --headful for interactive login.[/red]"
        )
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(user_agent=LINKEDIN_USER_AGENT)
        page = await context.new_page()

        console.log("[cyan]LinkedIn:[/cyan] Opening login page...")
        await page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded")
        if headless:
            await page.fill(
                "input[name='session_key'], input#username", username, timeout=10000
            )
            await page.fill(
                "input[name='session_password'], input#password",
                password,
                timeout=10000,
            )
            await page.click("button[type='submit']", timeout=10000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            except PWTimeout:
                pass
            await page.wait_for_timeout(2000)
            if await _is_two_factor_page(page):
                console.log(
                    "[red]LinkedIn login requires 2FA. "
                    "Use --headful to complete the login interactively.[/red]"
                )
                await browser.close()
                return False
            if await is_linkedin_login_page(page):
                console.log(
                    "[red]LinkedIn login failed in headless mode. "
                    "If you have MFA, use --headful to login interactively.[/red]"
                )
                await browser.close()
                return False
        else:
            console.log(
                "[cyan]LinkedIn:[/cyan] Complete login in the browser, then press Enter here."
            )
            input()
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except PWTimeout:
                pass
            if await is_linkedin_login_page(page) or await _is_two_factor_page(page):
                console.log(
                    "[yellow]LinkedIn:[/yellow] Waiting for login to complete..."
                )
                if not await _wait_for_login_success(page):
                    console.log(
                        "[red]LinkedIn login timed out. "
                        "Try again and ensure the login completes before pressing Enter.[/red]"
                    )
                    await browser.close()
                    return False

        await context.storage_state(path=str(storage_state))
        await browser.close()

    console.log(f"[green]LinkedIn:[/green] Session saved to {storage_state}")
    return True


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Login to LinkedIn and save a session state file."
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Use a visible browser for interactive login.",
    )
    args = parser.parse_args()

    asyncio.run(login_linkedin(headless=not args.headful))
