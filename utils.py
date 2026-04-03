"""
Shared helpers for LinkedIn automation.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import get_settings

DEFAULT_LINKEDIN_STORAGE_STATE = ".auth/linkedin_state.json"
LINKEDIN_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
LINKEDIN_PEOPLE_SEARCH_URL = "https://www.linkedin.com/search/results/people/"


def get_linkedin_storage_state_path(
    env_var: str = "LINKEDIN_STORAGE_STATE",
    default_path: str = DEFAULT_LINKEDIN_STORAGE_STATE,
) -> Path:
    settings = get_settings()
    raw = settings.linkedin_storage_state or default_path
    return Path(raw).expanduser()


async def is_linkedin_login_page(page) -> bool:
    url = page.url or ""
    if "/login" in url:
        return True
    login_el = await page.query_selector(
        "input[name='session_key'], input#username, form[action*='login']"
    )
    return login_el is not None
