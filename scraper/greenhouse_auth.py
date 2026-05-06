"""
scraper/greenhouse_auth.py
Handles MyGreenhouse magic-code authentication and session persistence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
from rich.console import Console
from yarl import URL

from config.settings import get_settings
from utils import (
    DEFAULT_GREENHOUSE_STORAGE_STATE,
    get_greenhouse_storage_state_path,
)

console = Console()
settings = get_settings()

GREENHOUSE_ORIGIN = "https://my.greenhouse.io"
GREENHOUSE_ORIGIN_URL = URL(GREENHOUSE_ORIGIN)
GREENHOUSE_LOGIN_URL = f"{GREENHOUSE_ORIGIN}/users/sign_in"
GREENHOUSE_SUBMIT_CODE_URL = f"{GREENHOUSE_ORIGIN}/users/submit_code"
GREENHOUSE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_INERTIA_VERSION = "5c6cae2464495aa21a802a787ab8d8993195eebb"


def _base_headers() -> dict[str, str]:
    return {
        "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
        "user-agent": GREENHOUSE_USER_AGENT,
    }


def _inertia_headers(csrf_token: str, inertia_version: str) -> dict[str, str]:
    headers = _base_headers()
    headers.update(
        {
            "accept": "text/html, application/xhtml+xml",
            "content-type": "application/json",
            "origin": GREENHOUSE_ORIGIN,
            "referer": GREENHOUSE_LOGIN_URL,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-csrf-token": csrf_token,
            "x-inertia": "true",
            "x-inertia-version": inertia_version,
            "x-requested-with": "XMLHttpRequest",
        }
    )
    return headers


def _extract_data_page(raw_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    app = soup.select_one("#app[data-page]")
    if not app:
        return {}
    try:
        return json.loads(app["data-page"])
    except (KeyError, json.JSONDecodeError):
        return {}


def _csrf_from_cookie_jar(cookie_jar: aiohttp.CookieJar) -> str:
    cookies = cookie_jar.filter_cookies(GREENHOUSE_ORIGIN_URL)
    morsel = cookies.get("MYGREENHOUSE-XSRF-TOKEN")
    return morsel.value if morsel else ""


def _cookies_from_jar(cookie_jar: aiohttp.CookieJar) -> list[dict[str, str]]:
    cookies = cookie_jar.filter_cookies(GREENHOUSE_ORIGIN_URL)
    return [
        {
            "name": name,
            "value": morsel.value,
            "domain": "my.greenhouse.io",
            "path": "/",
        }
        for name, morsel in cookies.items()
    ]


def _cookie_header_from_state(state: dict[str, Any]) -> str:
    cookies = state.get("cookies") or []
    return "; ".join(
        f"{cookie['name']}={cookie['value']}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value")
    )


def load_greenhouse_state(
    path: Path | None = None,
) -> dict[str, Any] | None:
    state_path = path or get_greenhouse_storage_state_path()
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def greenhouse_cookie_header_from_state(
    path: Path | None = None,
) -> str:
    state = load_greenhouse_state(path)
    if not state:
        return ""
    return _cookie_header_from_state(state)


def greenhouse_inertia_version_from_state(
    path: Path | None = None,
) -> str:
    state = load_greenhouse_state(path)
    if not state:
        return ""
    return str(state.get("inertia_version") or "")


def save_greenhouse_state(
    cookie_jar: aiohttp.CookieJar,
    inertia_version: str,
    email: str,
    path: Path | None = None,
) -> Path:
    state_path = path or get_greenhouse_storage_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "origin": GREENHOUSE_ORIGIN,
        "email": email,
        "inertia_version": inertia_version,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "cookies": _cookies_from_jar(cookie_jar),
    }
    state_path.write_text(json.dumps(state, indent=2))
    return state_path


async def _load_login_page(session: aiohttp.ClientSession) -> tuple[str, str]:
    headers = _base_headers()
    headers.update(
        {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
        }
    )
    async with session.get(GREENHOUSE_LOGIN_URL, headers=headers) as resp:
        raw_html = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"Login page HTTP {resp.status}")

    data_page = _extract_data_page(raw_html)
    inertia_version = str(data_page.get("version") or DEFAULT_INERTIA_VERSION)
    csrf_token = _csrf_from_cookie_jar(session.cookie_jar)
    if not csrf_token:
        raise RuntimeError("Greenhouse login did not return an XSRF token.")
    return inertia_version, csrf_token


async def login_greenhouse(email: str | None = None) -> bool:
    email = (email or settings.greenhouse_email or settings.smtp_user or "").strip()
    if not email:
        console.log(
            "[red]Greenhouse login requires an email. Set GREENHOUSE_EMAIL "
            "or pass --email.[/red]"
        )
        return False

    storage_state = get_greenhouse_storage_state_path(
        default_path=DEFAULT_GREENHOUSE_STORAGE_STATE
    )
    cookie_jar = aiohttp.CookieJar()
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        cookie_jar=cookie_jar, timeout=timeout
    ) as session:
        console.log("[cyan]Greenhouse:[/cyan] Loading login page...")
        inertia_version, csrf_token = await _load_login_page(session)

        console.log(f"[cyan]Greenhouse:[/cyan] Sending login code to {email}")
        async with session.post(
            GREENHOUSE_LOGIN_URL,
            headers=_inertia_headers(csrf_token, inertia_version),
            json={"email": email, "job_board": None},
            allow_redirects=True,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                console.log(f"[red]Greenhouse email submit HTTP {resp.status}[/red]")
                return False
            inertia_version = str(data.get("version") or inertia_version)

        csrf_token = _csrf_from_cookie_jar(cookie_jar)
        code = input("Enter the Greenhouse security code: ").strip()
        if not code:
            console.log("[red]Greenhouse login cancelled: no code entered.[/red]")
            return False

        async with session.post(
            GREENHOUSE_SUBMIT_CODE_URL,
            headers=_inertia_headers(csrf_token, inertia_version),
            json={"code": code, "email": email},
            allow_redirects=True,
        ) as resp:
            data = await resp.json(content_type=None)
            inertia_version = str(data.get("version") or inertia_version)
            errors = (data.get("props") or {}).get("errors") or {}
            current_user_id = (data.get("props") or {}).get("currentUserId")
            if errors:
                console.log(f"[red]Greenhouse login failed: {errors}[/red]")
                return False
            if not current_user_id:
                console.log(
                    "[red]Greenhouse login did not return an authenticated user.[/red]"
                )
                return False

        state_path = save_greenhouse_state(cookie_jar, inertia_version, email)

    console.log(f"[green]Greenhouse:[/green] Session saved to {state_path}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Login to MyGreenhouse and save a session state file."
    )
    parser.add_argument(
        "--email",
        help="Email address that receives the Greenhouse security code.",
    )
    args = parser.parse_args()

    asyncio.run(login_greenhouse(email=args.email))
