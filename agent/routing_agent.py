"""
agent/routing_agent.py
Claude-powered routing agent. For each unscored job it:
  1. Scores fit 0-10 against Bhargav's profile
  2. Picks the right resume variant (ml_engineer | data_scientist | ai_researcher)
  3. Drafts a personalised LinkedIn cold note (≤300 chars)
  4. Suggests 3 LinkedIn search queries to find people to contact
  5. Flags any red flags about the role

Returns structured JSON — all fields written to SQLite by pipeline.py.
"""

from enum import Enum
import json
import asyncio
from typing import Annotated, Tuple
from pydantic_ai import Agent
from pydantic import BaseModel, Field
from pydantic_ai.models.anthropic import AnthropicModelSettings
from rich.console import Console
from config.resumes import RESUME_VARIANTS

console = Console()

CANDIDATE_PROFILE = """
Name: Bhargav Singapuri
Degree: MS in Artificial Intelligence, Northeastern University (expected Dec 2026)
         BS in Data Science & AI, NTU Singapore (2023)
Availability: Seeking Summer 2026 internship — available May through August 2026
Location: Boston, MA (open to remote and relocation for the right role)

Industry experience:
- Biogen (ML Intern, Jul-Dec 2025): LLM summarisation pipeline (2 weeks → 2 min),
  LangSmith monitoring (34% cost reduction, 16% satisfaction uplift), Flask backend
  refactor (10% → 80% test coverage), internal AI dev playbook for 10+ engineers,
  semantic SOP deduplication via ML clustering
- UOB (Data Engineer, Jul 2023-Aug 2024): Splunk dashboard on 10M+ daily API records,
  incident diagnosis 10 hours → 10 seconds, database upgrades
- Vertex Holdings (Data Science Intern, Dec 2021-Apr 2023): Airflow ETL ingesting 1M+
  records into Snowflake, CRM with relationship graphs for investment managers

Active research (Jan 2026-present):
- Transliteration as jailbreak vector on Gemma 3; mitigation via linear probes + SAEs
- Cross-coder interpretability benchmarking on binary decision datasets

Skills: PyTorch, TensorFlow, HuggingFace Transformers, LangChain, TransformerLens,
        SAELens, OpenCV, scikit-learn, SQL (PostgreSQL, Snowflake), Pandas, NumPy,
        Airflow, Docker, LangSmith, Terraform, Flask, REST APIs, CI/CD,
        AWS, GCP, Azure, Python, Git, Linux
""".strip()

RESUME_SUMMARY = "\n\n".join(
    [
        f"VARIANT: {k}\n"
        f"Label: {v['label']}\n"
        f"Best for: {', '.join(v['target_roles'])}\n"
        f"Summary: {v['summary']}"
        for k, v in RESUME_VARIANTS.items()
    ]
)

SYSTEM_PROMPT = f"""You are a career advisor helping Bhargav Singapuri, an AI/ML grad student
at Northeastern University, identify and apply to Summer 2026 internships.

Analyse each job posting and return ONLY a valid JSON object.
No preamble. No markdown fences. No explanation outside the JSON.

## Candidate Profile
{CANDIDATE_PROFILE}

## Available Resume Variants
{RESUME_SUMMARY}

## Scoring rubric
10  = perfect alignment (title + stack + seniority all match)
7-9 = strong (2 of 3 align, minor gaps)
4-6 = partial (relevant skills but notable gaps)
1-3 = weak (different domain or too senior)
0   = not a fit (staff/principal/PhD required, unrelated domain)

## Outreach rules
- Open by naming the specific role and company
- Do not put a [Name] placeholder
- Reference exactly ONE concrete thing from Bhargav's background that is relevant
- Confident and peer-level tone — not grovelling
- End with a single low-friction ask (15 min chat, advice, referral)
- Hard limit: 300 characters including spaces
""".strip()


class ResumeVariant(str, Enum):
    ML_ENGINEER = "ml_engineer"
    DATA_SCIENTIST = "data_scientist"
    AI_RESEARCHER = "ai_researcher"


SearchQueryTuple = Tuple[
    Annotated[
        str, Field(description="query to find recruiter or hiring manager at company")
    ],
    Annotated[str, Field(description="query to find ML/AI team lead at company")],
    Annotated[
        str, Field(description="query to find Northeastern or NTU alum at company")
    ],
]


class LLMResponse(BaseModel):
    fit_score: float = Field(ge=0.0, le=10.0)
    fit_reasoning: str = Field(
        description="2-3 sentences: why this score, what aligns, what's missing"
    )
    resume_variant: ResumeVariant
    resume_reasoning: str = (Field(description="1 sentence: why this variant"),)
    outreach_draft: str = Field(
        description="LinkedIn cold note, max 300 chars, personalised to role + company"
    )
    linkedin_search_queries: SearchQueryTuple
    red_flags: str = Field(
        description="concerns about seniority mismatch, visa sponsorship, location, or domain fit — or empty string"
    )


async def score_job_async(job: dict) -> tuple[dict, dict | None]:
    """
    Score a single job. Returns (job, result_dict) or (job, None) on failure.
    Async so batches can run concurrently.
    """
    client = Agent(
        "anthropic:claude-4-sonnet-20250514",
        system_prompt=SYSTEM_PROMPT,
        output_type=LLMResponse,
        model_settings=AnthropicModelSettings(anthropic_cache_instructions=True),
    )

    job_text = (
        f"Title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Location: {job.get('location', 'Not specified')}\n"
        f"Source: {job.get('source', '')}\n"
        f"URL: {job.get('url', '')}\n\n"
        f"Description:\n{job.get('description', 'No description — base analysis on title and company name.')}"
    )

    try:
        # Run blocking SDK call in thread pool so we don't block the event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.run_sync(f"Analyse this job:\n\n{job_text}"),
        )

        return job, response.output.model_dump()

    except json.JSONDecodeError as e:
        console.log(
            f"  [red]JSON parse error — {job['title']} @ {job['company']}: {e}[/red]"
        )
        return job, None
    except Exception as e:
        console.log(
            f"  [red]Agent error — {job['title']} @ {job['company']}: {e}[/red]"
        )
        return job, None
