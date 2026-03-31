# Job Agent — Automated ML/AI Job Pipeline

Scrapes job boards daily, scores each role against your profile using Claude,
picks the right resume variant, drafts a LinkedIn outreach note, and delivers
everything to your inbox as a ranked digest, then runs LinkedIn outreach.

---

## Architecture

```text
CRON (daily @ SCRAPE_TIME)
         │
         ▼
┌─────────────────────┐
│  Stage 1: Scrape    │  LinkedIn · Simplify.jobs · HN Who's Hiring
│                     │  Playwright (headless) + HN Algolia API
└────────┬────────────┘
         │ new jobs → SQLite (deduplicated by SHA-256 ID)
         ▼
┌─────────────────────┐
│  Stage 2: Score     │  Claude (claude-sonnet-4-20250514)
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
│   ├── simplify.py          # Playwright scraper
│   └── hn.py                # HN Algolia API (no browser)
├── agent/
│   ├── routing_agent.py     # Claude scoring + outreach drafting
│   └── pipeline.py          # Batch runner for routing agent
│   └── linkedin_outreach.py # LinkedIn People outreach automation
├── notifier/
│   └── digest.py            # HTML email builder + SMTP sender
├── config/
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

| Variable                   | What to set                                                                         |
|----------------------------|-------------------------------------------------------------------------------------|
| `ANTHROPIC_API_KEY`        | your anthropic api key                                                              |
| `LLM_MODEL`                | LLM model name (default: `claude-4-sonnet-20250514`)                                |
| `SMTP_HOST`                | SMTP server host (default: `smtp.porkbun.com`)                                      |
| `SMTP_PORT`                | SMTP server port (default: `587`)                                                   |
| `SMTP_USER`                | SMTP account username/email                                                         |
| `SMTP_PASS`                | SMTP account password                                                               |
| `NOTIFY_TO`                | Where to send digests (can be same as SMTP_USER)                                    |
| `JOB_KEYWORDS`             | Comma-separated search terms                                                        |
| `JOB_LOCATION`             | e.g. `Boston, MA`                                                                   |
| `MIN_FIT_SCORE`            | Minimum score to include in digest (default: 6)                                     |
| `SCRAPE_TIME`              | Daily run time in 24h format (default: `08:00`)                                     |
| `PIPELINE_STAGES_NOW`      | Comma-separated stages for `--now` (default: `scrape,score,digest,outreach`)        |
| `PIPELINE_STAGES_SCHEDULE` | Comma-separated stages for scheduled runs (default: `scrape,score,digest,outreach`) |
| `LINKEDIN_STORAGE_STATE`   | Path to saved LinkedIn session (default: `.auth/linkedin_state.json`)               |
| `LINKEDIN_USERNAME`        | LinkedIn login email/username (required for headless login)                         |
| `LINKEDIN_PASSWORD`        | LinkedIn login password (required for headless login)                               |
| `OUTREACH_TARGETS`         | Comma-separated list: `recruiter,hiring_manager,alumni` (default: all)              |

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

---

## Commands (by phase)

### Phase 1: Scrape + Score + Digest

```bash
# Run full pipeline right now (scrape + score + email)
docker-compose run --rm job-search python main.py --now

# Score unscored jobs only, then send digest (useful after first run)
docker-compose run --rm job-search python main.py --score-only

# Just send the digest (jobs already scored)
docker-compose run --rm job-search python main.py --digest-only
```

### Phase 2: Outreach

```bash
# Run outreach only for today's scraped jobs
docker-compose run --rm job-search python main.py --outreach-only

# Run outreach for a specific date (YYYY-MM-DD)
docker-compose run --rm job-search python main.py --outreach-only --outreach-date 2026-03-25

# Show the browser for any stage that uses Playwright
docker-compose run --rm job-search python main.py --now --headful
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

### Phase 3: Inspect + Debug

```bash
# Browse unscored jobs
docker-compose run --rm job-search python inspect_db.py

# Browse all scored jobs ranked by fit
docker-compose run --rm job-search python inspect_db.py --scored

# Full detail for one job (outreach draft, search queries, red flags)
docker-compose run --rm job-search python inspect_db.py --job <id>

# View scrape run history + DB stats
docker-compose run --rm job-search python inspect_db.py --stats

# Tail live logs
docker-compose logs -f job-search
```

---

## What the digest email contains

For each role above your `MIN_FIT_SCORE`:

- **Fit score** (0-10) with Claude's reasoning
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
- HN scraping uses the official Algolia API — very stable, no browser needed.
- Claude API calls are batched (default 3 concurrent) to stay within rate limits.
  Increase `AGENT_BATCH_SIZE` in `.env` if you have a higher-tier API plan.
