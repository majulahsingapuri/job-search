# Job Agent — Automated ML/AI Job Pipeline

Scrapes job boards daily, scores each role against your profile using Claude,
picks the right resume variant, drafts a LinkedIn outreach note, and delivers
everything to your inbox as a ranked digest.

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
│   ├── simplify.py          # Playwright scraper
│   └── hn.py                # HN Algolia API (no browser)
├── agent/
│   ├── routing_agent.py     # Claude scoring + outreach drafting
│   └── pipeline.py          # Batch runner for routing agent
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

Edit `.env`:

| Variable            | What to set                                      |
|---------------------|--------------------------------------------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key                           |
| `SMTP_USER`         | `your@yourdomain.com` (Porkbun email address)    |
| `SMTP_PASS`         | Porkbun email password                           |
| `NOTIFY_TO`         | Where to send digests (can be same as SMTP_USER) |
| `JOB_KEYWORDS`      | Comma-separated search terms                     |
| `JOB_LOCATION`      | e.g. `Boston, MA`                                |
| `MIN_FIT_SCORE`     | Minimum score to include in digest (default: 6)  |
| `SCRAPE_TIME`       | Daily run time in 24h format (default: `08:00`)  |

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

---

## Commands

```bash
# Run full pipeline right now (scrape + score + email)
docker-compose run --rm job-search python main.py --now

# Score unscored jobs only, then send digest (useful after first run)
docker-compose run --rm job-search python main.py --score-only

# Just send the digest (jobs already scored)
docker-compose run --rm job-search python main.py --digest-only

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

## Porkbun SMTP settings

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
- Simplify.jobs scraping uses the Typesense API (no browser/DOM selectors).
- HN scraping uses the official Algolia API — very stable, no browser needed.
- Claude API calls are batched (default 3 concurrent) to stay within rate limits.
  Increase `AGENT_BATCH_SIZE` in `.env` if you have a higher-tier API plan.
