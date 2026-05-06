"""Centralized environment configuration via Pydantic settings."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JOB_KEYWORDS = ["machine learning engineer"]
DEFAULT_PIPELINE_STAGES = "scrape,score,digest,outreach"
DEFAULT_OUTREACH_TARGETS = ["recruiter", "hiring_manager", "alumni"]
ALLOWED_SCRAPE_SOURCES = ("linkedin", "hn", "simplify", "greenhouse")
DEFAULT_STRING_FIELDS = {
    "job_location": "Boston, MA",
    "scrape_time": "08:00",
    "pipeline_stages_now": DEFAULT_PIPELINE_STAGES,
    "pipeline_stages_schedule": DEFAULT_PIPELINE_STAGES,
    "llm_provider": "anthropic",
    "llm_model": "claude-haiku-4-5",
    "db_path": "/app/db/jobs.sqlite",
    "smtp_host": "smtp.porkbun.com",
    "linkedin_storage_state": ".auth/linkedin_state.json",
    "greenhouse_storage_state": ".auth/greenhouse_state.json",
}


def _select_env_file() -> str | None:
    env = (os.getenv("APP_ENV") or os.getenv("ENV") or "dev").lower()
    if env in {"prod", "production"}:
        return None
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_select_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM configuration
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-4-5"
    llm_timeout_seconds: int = 180
    agent_batch_size: int = 3
    anthropic_prompt_cache_ttl: str = "5m"
    openai_prompt_cache_retention: str = "24h"
    openai_prompt_cache_key: str | None = None

    # Job search + scoring
    job_keywords: list[str] | str = Field(
        default_factory=lambda: DEFAULT_JOB_KEYWORDS.copy()
    )
    job_location: str = "Boston, MA"
    min_fit_score: float = 6.0
    scrape_sources: list[str] | str = Field(
        default_factory=lambda: list(ALLOWED_SCRAPE_SOURCES)
    )

    # Scheduling
    scrape_time: str = "08:00"
    pipeline_stages_now: str = DEFAULT_PIPELINE_STAGES
    pipeline_stages_schedule: str = DEFAULT_PIPELINE_STAGES

    # Storage
    db_path: str = "/app/db/jobs.sqlite"

    # Email notifications
    smtp_host: str = "smtp.porkbun.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    notify_to: str | None = None

    # LinkedIn
    linkedin_storage_state: str = ".auth/linkedin_state.json"
    linkedin_username: str = ""
    linkedin_password: str = ""
    linkedin_max_pages: int | None = None
    linkedin_public_fallback: bool = True
    linkedin_enrich_concurrency: int = 5
    linkedin_enrich_delay_ms: int = 1000
    linkedin_enrich_jitter_ms: int = 500

    # Greenhouse
    greenhouse_max_pages: int = 3
    greenhouse_inertia_version: str = "debac7412270deb73a5f29804de3015747c87c56"
    greenhouse_storage_state: str = ".auth/greenhouse_state.json"
    greenhouse_email: str = ""

    # Outreach
    outreach_targets: list[str] | str = Field(
        default_factory=lambda: DEFAULT_OUTREACH_TARGETS.copy()
    )

    @field_validator("job_keywords", mode="before")
    def _parse_keywords(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            if not value.strip():
                return DEFAULT_JOB_KEYWORDS.copy()
            return [k.strip() for k in value.split(",") if k.strip()]
        if isinstance(value, list):
            return [str(k).strip() for k in value if str(k).strip()]
        return value

    @field_validator("outreach_targets", mode="before")
    def _parse_outreach_targets(cls, value: Any) -> list[str]:
        if value is None:
            return DEFAULT_OUTREACH_TARGETS.copy()
        if isinstance(value, str):
            if not value.strip():
                return DEFAULT_OUTREACH_TARGETS.copy()
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return value

    @field_validator("scrape_sources", mode="before")
    def _parse_scrape_sources(cls, value: Any) -> list[str]:
        if value is None:
            return list(ALLOWED_SCRAPE_SOURCES)
        if isinstance(value, str):
            if not value.strip():
                return list(ALLOWED_SCRAPE_SOURCES)
            parts = [v.strip().lower() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            parts = [str(v).strip().lower() for v in value if str(v).strip()]
        else:
            return value
        filtered = [v for v in parts if v in ALLOWED_SCRAPE_SOURCES]
        return filtered or list(ALLOWED_SCRAPE_SOURCES)

    @field_validator(
        "job_location",
        "scrape_time",
        "pipeline_stages_now",
        "pipeline_stages_schedule",
        "llm_provider",
        "llm_model",
        "db_path",
        "smtp_host",
        "linkedin_storage_state",
        "greenhouse_storage_state",
        mode="before",
    )
    def _default_if_blank(cls, value: Any, info):
        if isinstance(value, str) and not value.strip():
            return DEFAULT_STRING_FIELDS.get(info.field_name, value)
        return value

    @field_validator("llm_provider", mode="after")
    def _lower_provider(cls, value: str) -> str:
        return value.lower()

    @field_validator("anthropic_prompt_cache_ttl", mode="after")
    def _validate_anthropic_ttl(cls, value: str) -> str:
        return value if value in {"5m", "1h"} else "5m"

    @field_validator("openai_prompt_cache_retention", mode="after")
    def _validate_openai_retention(cls, value: str) -> str:
        return value if value in {"in_memory", "24h"} else "24h"

    @field_validator(
        "agent_batch_size",
        "llm_timeout_seconds",
        "smtp_port",
        "linkedin_enrich_concurrency",
        "greenhouse_max_pages",
        mode="after",
    )
    def _ensure_positive(cls, value: int) -> int:
        return value if value >= 1 else 1

    @field_validator(
        "linkedin_enrich_delay_ms",
        "linkedin_enrich_jitter_ms",
        mode="after",
    )
    def _ensure_non_negative(cls, value: int) -> int:
        return value if value >= 0 else 0

    @field_validator("linkedin_max_pages", mode="before")
    def _parse_optional_int(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @model_validator(mode="after")
    def _fill_notify_to(self) -> "Settings":
        if not self.notify_to:
            self.notify_to = self.smtp_user
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
