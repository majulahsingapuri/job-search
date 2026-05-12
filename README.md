# Job Agent — Automated ML/AI Job Pipeline

Scrapes job boards daily, scores each role against your profile using the
configured LLM provider, picks the right resume variant, drafts a LinkedIn
outreach note, and delivers everything to your inbox as a ranked digest, then
runs LinkedIn outreach.

---

## Architecture

```text
CRON (daily @ SCRAPE_TIME)
         │
         ▼
┌─────────────────────┐
│  Stage 1: Scrape    │  LinkedIn · Simplify.jobs · Greenhouse · HN Who's Hiring
│                     │  Playwright + source APIs + saved auth state
└────────┬────────────┘
         │ listing metadata → pre-enrichment dedupe → enrich new jobs only
         │ new jobs → SQLite (final dedupe by SHA-256 ID + source external ID)
         ▼
┌─────────────────────┐
│  Stage 2: Score     │  Configured LLM provider + model
│  (Routing Agent)    │  Fit score · Resume picker · Outreach draft
│                     │  LinkedIn search queries · Red flags
└────────┬────────────┘
         │ results written back to SQLite
         ▼
┌─────────────────────┐
│  Stage 3: Digest    │  HTML + plaintext email via Porkbun SMTP
│                     │  Jobs ranked by fit score, outreach ready to copy
└─────────────────────┘
         │ emailed jobs
         ▼
┌─────────────────────┐
│ Stage 4: Outreach   │  LinkedIn People search + connect + note
│                     │  Logs outreach + updates job status
└─────────────────────┘
```

---

## Project Structure

```dir
job-search/
├── main.py                  # Orchestrator + scheduler
├── inspect_db.py            # CLI to browse the database
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── scraper/
│   ├── linkedin.py          # Playwright scraper
│   ├── linkedin_auth.py     # LinkedIn login + session management
│   ├── simplify.py          # Simplify API scraper
│   ├── greenhouse.py        # Greenhouse API + job description enrichment
│   ├── greenhouse_auth.py   # MyGreenhouse security-code login
│   └── hn.py                # HN Algolia + Firebase item APIs
├── agent/
│   ├── routing_agent.py     # LLM scoring + outreach drafting
│   ├── pipeline.py          # Batch runner for routing agent
│   └── linkedin_outreach.py # LinkedIn People outreach automation
├── notifier/
│   └── digest.py            # HTML email builder + SMTP sender
├── config/
│   ├── settings.py          # Environment-driven runtime settings
│   └── resumes.py           # Your 3 resume variants (edit this)
└── db/
    └── database.py          # SQLite layer + deduplication
```

---

## Setup

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` (make sure to add your LLM provider API key):

#### LLM + API

| Variable                        | What to set                                                 |
|---------------------------------|-------------------------------------------------------------|
| `*_API_KEY`                     | your model provider api key                                 |
| `LLM_PROVIDER`                  | your model provider (default: `anthropic`)                  |
| `LLM_MODEL`                     | LLM model name (default: `claude-haiku-4-5`)                |
| `LLM_TIMEOUT_SECONDS`           | Per-job LLM timeout in seconds (default: `180`)             |
| `AGENT_BATCH_SIZE`              | Concurrent LLM requests for scoring/analysis (default: `3`) |
| `ANTHROPIC_PROMPT_CACHE_TTL`    | Anthropic prompt cache TTL (default: `5m`)                  |
| `OPENAI_PROMPT_CACHE_RETENTION` | OpenAI prompt cache retention (default: `24h`)              |
| `OPENAI_PROMPT_CACHE_KEY`       | OpenAI prompt cache key (optional)                          |

#### Jobs + Scoring

| Variable        | What to set                                     |
|-----------------|-------------------------------------------------|
| `JOB_KEYWORDS`  | Comma-separated search terms                    |
| `JOB_LOCATION`  | e.g. `Boston, MA`                               |
| `MIN_FIT_SCORE` | Minimum score to include in digest (default: 6) |

#### Scheduling + Pipeline

| Variable                   | What to set                                                                         |
|----------------------------|-------------------------------------------------------------------------------------|
| `SCRAPE_TIME`              | Daily run time in 24h format (default: `08:00`)                                     |
| `PIPELINE_STAGES_NOW`      | Comma-separated stages for `--now` (default: `scrape,score,digest,outreach`)        |
| `PIPELINE_STAGES_SCHEDULE` | Comma-separated stages for scheduled runs (default: `scrape,score,digest,outreach`) |
| `SCRAPE_SOURCES`           | Comma-separated scrapers to enable (default: `linkedin,hn,simplify,greenhouse`)     |

#### Storage

| Variable  | What to set                                     |
|-----------|-------------------------------------------------|
| `DB_PATH` | SQLite DB path (default: `/app/db/jobs.sqlite`) |

#### Email Notifications

| Variable    | What to set                                      |
|-------------|--------------------------------------------------|
| `SMTP_HOST` | SMTP server host (default: `smtp.porkbun.com`)   |
| `SMTP_PORT` | SMTP server port (default: `587`)                |
| `SMTP_USER` | SMTP account username/email                      |
| `SMTP_PASS` | SMTP account password                            |
| `NOTIFY_TO` | Where to send digests (can be same as SMTP_USER) |

#### LinkedIn: Session + Scrape

| Variable                   | What to set                                                           |
|----------------------------|-----------------------------------------------------------------------|
| `LINKEDIN_STORAGE_STATE`   | Path to saved LinkedIn session (default: `.auth/linkedin_state.json`) |
| `LINKEDIN_USERNAME`        | LinkedIn login email/username (required for headless login)           |
| `LINKEDIN_PASSWORD`        | LinkedIn login password (required for headless login)                 |
| `LINKEDIN_MAX_PAGES`       | Max LinkedIn pages to scrape per keyword (default: all pages)         |
| `LINKEDIN_PUBLIC_FALLBACK` | Fall back to public listings when login fails (default: `true`)       |

#### LinkedIn: Enrichment Throttling

| Variable                      | What to set                                                      |
|-------------------------------|------------------------------------------------------------------|
| `LINKEDIN_ENRICH_CONCURRENCY` | Concurrent detail pages during enrichment (default: `5`)         |
| `LINKEDIN_ENRICH_DELAY_MS`    | Base delay (ms) before each enrichment request (default: `1000`) |
| `LINKEDIN_ENRICH_JITTER_MS`   | Extra random delay (ms) added to base delay (default: `500`)     |

#### Simplify: Scrape

| Variable             | What to set                                                         |
|----------------------|---------------------------------------------------------------------|
| `SIMPLIFY_MAX_PAGES` | Max Simplify pages to scrape per keyword (default: `3`)             |
| `SIMPLIFY_PER_PAGE`  | Simplify results per page (default: `50`)                           |
| `JOB_LOCATION`       | Countries use country filtering; cities use Simplify geocoding; `Remote` defaults to remote USA. |

#### Greenhouse: Session + Scrape

| Variable                     | What to set                                                                 |
|------------------------------|-----------------------------------------------------------------------------|
| `GREENHOUSE_STORAGE_STATE`   | Path to saved MyGreenhouse session (default: `.auth/greenhouse_state.json`) |
| `GREENHOUSE_EMAIL`           | Email address that receives the MyGreenhouse security code                  |
| `GREENHOUSE_MAX_PAGES`       | Max Greenhouse pages to scrape per keyword (default: `3`)                   |
| `GREENHOUSE_INERTIA_VERSION` | Optional Greenhouse Inertia version header override                         |

#### Outreach

| Variable           | What to set                                                            |
|--------------------|------------------------------------------------------------------------|
| `OUTREACH_TARGETS` | Comma-separated list: `recruiter,hiring_manager,alumni` (default: all) |

### 2. Update your resume variants

Open `config/resumes.py` and update the `"file"` field in each variant to match
your actual PDF filenames:

```python
"ml_engineer": {
    "file": "Bhargav_Resume_MLEng.pdf",   # ← your actual filename
    ...
}
```

### 3. Build and start

```bash
docker-compose up --build -d
```

The container runs 24/7 and fires the pipeline once a day at `SCRAPE_TIME`.

### 4. Open the job UI

Once the stack is up, visit:

```text
http://localhost:8080
```

Use the filters to slice the jobs table and update the `status` field.

---

## LinkedIn login (recommended)

LinkedIn scraping can work without login (public listings only). LinkedIn People
search (outreach) requires login. Save a session once and reuse it:

```bash
# Headless by default (uses LINKEDIN_USERNAME / LINKEDIN_PASSWORD)
python -m scraper.linkedin_auth

# Interactive login with visible browser
python -m scraper.linkedin_auth --headful
```

Headless login requires `LINKEDIN_USERNAME` and `LINKEDIN_PASSWORD` in your
`.env`. Interactive mode will open a browser; complete the login, then return
to the terminal and press Enter. A session file will be saved to
`.auth/linkedin_state.json` (or the path set by `LINKEDIN_STORAGE_STATE`).

If you log in locally and want to copy the auth state into the running
container, you can use:

```bash
docker cp ./.auth/linkedin_state.json job-agent:/app/.auth/linkedin_state.json
```

## Greenhouse login

MyGreenhouse search requires a session for the Inertia JSON API. Save a session
with the email security-code flow:

```bash
python -m scraper.greenhouse_auth --email jobs@bhargav.io
```

Enter the code from your inbox when prompted. The session is saved to
`.auth/greenhouse_state.json` by default and used automatically by the
Greenhouse scraper. If the session expires, rerun the same command and enter the
new code.

For Docker, run the login command with an interactive terminal and bind-mount
`.auth` so the saved session persists locally:

```bash
docker-compose run --rm -it -v "$PWD/.auth:/app/.auth" job-agent python -m scraper.greenhouse_auth --email jobs@bhargav.io
```

If the scheduler container is already running, copy the auth state into it:

```bash
docker cp ./.auth/greenhouse_state.json job-agent:/app/.auth/greenhouse_state.json
```

The Greenhouse scraper uses two data paths:

- Search results come from `my.greenhouse.io/jobs`, which requires the saved
  MyGreenhouse session.
- Full descriptions come from Greenhouse-rendered job pages. Direct
  `job-boards.greenhouse.io/.../jobs/<id>` URLs are fetched directly. External
  company career URLs with `gh_jid` are enriched through Greenhouse's embedded
  iframe endpoint:
  `job-boards.greenhouse.io/embed/job_app?for=<board_token>&token=<job_id>`.

The iframe URL does not require scraping the external company site, which avoids
company-specific Cloudflare/VPN blocks. If the inferred `for=<board_token>` is
wrong and the iframe returns `404`, the scraper keeps the search API metadata
and continues.

---

## Commands (by phase)

### Phase 1: Scrape + Score + Digest

```bash
# Run full pipeline right now (scrape + score + email)
docker-compose run --rm job-agent python main.py --now

# Run the configured stages using only one scraper source
docker-compose run --rm job-agent python main.py --now --greenhouse
docker-compose run --rm job-agent python main.py --now --simplify

# Scraper source flags can be combined
docker-compose run --rm job-agent python main.py --now --linkedin --greenhouse

# Score unscored jobs only, then send digest (useful after first run)
docker-compose run --rm job-agent python main.py --score-only

# Just send the digest (jobs already scored)
docker-compose run --rm job-agent python main.py --digest-only
```

### Phase 2: Outreach

```bash
# Run outreach only for today's scraped jobs
docker-compose run --rm job-agent python main.py --outreach-only

# Run outreach for a specific date (YYYY-MM-DD)
docker-compose run --rm job-agent python main.py --outreach-only --outreach-date 2026-03-25

# Show the browser for any stage that uses Playwright
docker-compose run --rm job-agent python main.py --now --headful
```

### Pipeline stage selection (optional)

You can control which stages run for `--now` and scheduled runs using:

- `PIPELINE_STAGES_NOW`
- `PIPELINE_STAGES_SCHEDULE`

Each is a comma-separated list of: `scrape,score,digest,outreach` (default: all).

Example (scrape + score only):

```bash
PIPELINE_STAGES_NOW="scrape,score"
PIPELINE_STAGES_SCHEDULE="scrape,score"
```

Flags never enable stages that are disabled in `PIPELINE_STAGES_NOW`. For example,
`--digest-only` will error if `digest` is not listed.

### Phase 3: Inspect + Debug

```bash
# Browse unscored jobs
docker-compose run --rm job-agent python inspect_db.py

# Browse all scored jobs ranked by fit
docker-compose run --rm job-agent python inspect_db.py --scored

# Full detail for one job (outreach draft, search queries, red flags)
docker-compose run --rm job-agent python inspect_db.py --job <id>

# View scrape run history + DB stats
docker-compose run --rm job-agent python inspect_db.py --stats

# Tail live logs
docker-compose logs -f job-agent
```

### Phase 4: Enrich missing descriptions

```bash
# Fill missing LinkedIn descriptions in the DB
docker-compose run --rm job-agent python main.py --enrich-missing

# Fill missing Greenhouse descriptions in the DB
docker-compose run --rm job-agent python main.py --enrich-missing --enrich-source greenhouse

# Limit to 50 jobs
docker-compose run --rm job-agent python main.py --enrich-missing --enrich-source greenhouse --enrich-limit 50
```

---

## What the digest email contains

For each role above your `MIN_FIT_SCORE`:

- **Fit score** (0-10) with LLM reasoning
- **Which resume to attach** — ML Engineer / Data Scientist / AI Researcher
- **LinkedIn outreach note** — personalised, ≤300 chars, ready to copy-paste
- **3 clickable LinkedIn search queries** to find a recruiter, team lead, or alum
- **Red flags** — seniority mismatch, visa issues, domain drift

---

## Outreach logging

Outreach attempts are stored in the `outreach_log` table in SQLite and shown in
the UI under each job. Each LinkedIn profile is only contacted once across all
roles.

---

## Porkbun SMTP settings

If you're not using Porkbun, set `SMTP_HOST` and `SMTP_PORT` in `.env` for your
provider and ignore this section.

| Setting  | Value                   |
|----------|-------------------------|
| Host     | `smtp.porkbun.com`      |
| Port     | `587`                   |
| Security | STARTTLS                |
| Username | your full email address |
| Password | your email password     |

If Porkbun requires an app-specific password, generate one in your Porkbun
email dashboard and use that as `SMTP_PASS`.

---

## Notes

- LinkedIn scraping works without login (public listings only). If LinkedIn
  updates their markup, update CSS selectors in `scraper/linkedin.py`.
- LinkedIn outreach uses People search filters and requires a logged-in session.
- Simplify.jobs scraping uses the Typesense API (no browser/DOM selectors).
- Greenhouse scraping uses the my.greenhouse.io Inertia JSON endpoint with a
  saved MyGreenhouse session. Full descriptions are extracted from direct
  Greenhouse job pages or Greenhouse embed iframes for external career pages.
- LinkedIn, Simplify, and Greenhouse skip description enrichment for jobs that
  are already in SQLite. The scraper filters listing metadata first, then the DB
  layer performs a final insert-time dedupe using the canonical SHA-256 job ID
  and source external IDs such as Simplify `posting_id` and Greenhouse job IDs.
- HN scraping uses the Algolia search API and Hacker News Firebase item API,
  no browser needed.
- LLM API calls are batched (default 3 concurrent) to stay within rate limits.
  Increase `AGENT_BATCH_SIZE` in `.env` if you have a higher-tier API plan.
