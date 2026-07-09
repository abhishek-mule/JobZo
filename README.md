# JobZo

A personal AI-powered job application assistant for serious job seekers. Collects jobs, scores them against your skills, auto-fills browser application forms, and tracks outcomes — all offline, local-first, and free.

```text
Collect  →  Filter  →  Score  →  Apply  →  Track
```

No subscriptions. No data leaving your machine. No VC-funded recruiter spam.

---

## How it works

### Pipeline

```text
RSS Feed (HN) ───────────┐
Manual URL Import ───────┤
                          ├──→ Dedup → Keyword Filter → Rule Score → [Optional LLM] → Apply
Company Career Pages ─────┘
```

Each step is independent. The system degrades gracefully: if the LLM isn't available, deterministic rule-scoring takes over.

### Rule-based scoring (no AI required)

Every job gets scored on four dimensions:

| Component      | Weight | Description                          |
|----------------|--------|--------------------------------------|
| Skill overlap  | 50 pts | Matches between your skills and the JD |
| Freshness      | 20 pts | How recently the job was posted      |
| Experience     | 20 pts | How well your experience level fits  |
| Location       | 10 pts | Remote-friendly or local             |

Each component returns a human-readable reason, so every score is explainable:

```text
Score: 44
  Skill match (9/26): react, typescript, postgresql, sql, docker
  Freshness: 20%
  your 1yr is below requires 3+ years
  remote friendly
```

The LLM is optional — it can refine scores, generate cover letters, and recommend resumes, but the pipeline works without it.

---

## Quick start

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) (optional, for local LLM scoring)
- Chrome/Chromium (for browser automation)

### Install

```bash
# Clone
git clone https://github.com/abhishek-mule/JobZo.git
cd JobZo

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
python -m playwright install chromium
```

### Configure

```bash
# Copy browser config (then fill in your name, email, phone)
cp config/browser.yaml.example config/browser.yaml
```

Edit `config/browser.yaml`:

```yaml
profile:
  name: "Your Name"
  email: "you@example.com"
  phone: "+1-555-0123"
  linkedin: "https://linkedin.com/in/your-profile"
```

Add your resume metadata JSON files to `resumes/`. Each file lists your skills for scoring:

```json
{
  "skills": ["react", "typescript", "postgresql", "docker", "spring boot"]
}
```

### Run your first daily workflow

```bash
jobzo daily
```

This runs the full pipeline: collect jobs → score them → open the top 5 in your browser for review.

---

## CLI reference

| Command | Description |
|---------|-------------|
| `collect` | Fetch jobs from all enabled sources |
| `rank` | Score all unscored job descriptions |
| `apply` | Open browser, fill form, wait for your confirmation |
| `import-url` | Import a job URL from any career page |
| `track` | Dashboard or record an outcome (submitted, interview, rejected, offer) |
| `task` | View pending tasks (e.g. interview prep) |
| `export` | Export all applications to CSV |
| `daily` | Full pipeline: collect → rank → apply top 5 |
| `skill-gap` | Analyze which skills appear most in collected jobs |

### Examples

```bash
# Collect jobs
jobzo collect

# Import a specific job URL
jobzo import-url "https://boards.greenhouse.io/example/jobs/123"

# Score everything
jobzo rank

# See your dashboard
jobzo track

# Record an application outcome
jobzo track <app-id> --status submitted --note "Applied via Lever"

# Submit an application (opens browser, you review before submission)
jobzo apply <app-id>

# Export everything for analysis
jobzo export --out applications.csv

# Check skill demand across collected jobs
jobzo skill-gap
```

---

## Commands in detail

### `jobzo collect`

Fetches jobs from enabled providers. Currently supports:
- **HN RSS** — Hacker News "Who is hiring?" threads
- **YC company pages** — auto-enriches HN jobs with full descriptions from YC company pages
- **Manual import** — jobs added via `jobzo import-url`

Deduplication is by URL + normalized company/title/location hash.

### `jobzo rank`

For each unscored job, runs:
1. **Keyword pre-filter** — quick tech-stack match (Spring, React, Docker, etc.)
2. **Skill overlap** — how many of your skills appear in the description
3. **Experience match** — seniority level fit
4. **Location match** — remote/on-site preference
5. **Freshness** — how recently posted
6. **LLM (optional)** — if a capable model is available, refines the score

Jobs below the keyword threshold (20) are skipped entirely. Rules run first; the LLM is only consulted if it can improve the result.

### `jobzo apply <id>`

Launches the browser and:
1. Navigates to the job URL
2. Detects the application form (checks for known ATS domains)
3. Fills in your profile (name, email, phone, LinkedIn)
4. Uploads your resume
5. Pastes the cover letter
6. Waits for you to review and click Submit

You always approve before submission.

### `jobzo import-url <url>`

Imports a job from any URL. Uses Playwright to render JavaScript-heavy career pages, then falls back to HTTP + BeautifulSoup, then regex. Extracts company name and job title automatically.

```bash
jobzo import-url "https://careers.example.com/jobs/123"
```

You can override with flags:

```bash
jobzo import-url <url> --company "Acme" --title "Senior Engineer" --remote
```

### `jobzo track`

Three modes:

```bash
# Dashboard
jobzo track

# Detail view
jobzo track <app-id-prefix>

# Record outcome
jobzo track <app-id-prefix> --status submitted --note "Applied via Greenhouse"
```

Valid statuses: `drafted` → `ready` → `submitted` → `interview` → `rejected` → `offer`

Application IDs support prefix matching — `a1b2c3` works for `a1b2c3-...-...`.

### `jobzo export`

Exports all applications to CSV for analysis in Excel, LibreOffice, or Python:

```bash
jobzo export --out applications.csv
```

Columns: id, company, title, url, status, score, strategy, resume, applied_at, response_date, interview_date, first_response_at, last_activity_at, source, location, notes.

---

## Configuration

All config is in `config/` as YAML files.

### `config/providers.yaml`

Enable/disable job sources:

```yaml
rss:
  enabled: true
  urls:
    - https://hnrss.org/jobs

company_pages:
  enabled: false
  targets: []

manual:
  enabled: true
```

### `config/llm.yaml`

LLM provider settings:

```yaml
provider: ollama
ollama:
  model: qwen3:4b
  base_url: http://localhost:11434/v1
openai:
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY}
```

Set `provider: openai` and `api_key` to use OpenAI. The system detects small models (<2B params) and skips them to avoid unreliable JSON output.

### `config/resume.yaml`

Resume versions for A/B testing:

```yaml
resumes:
  backend:
    file: resumes/backend_v3.pdf
    metadata: resumes/backend_v3.json
    active: true
  fullstack:
    file: resumes/fullstack_v4.pdf
    metadata: resumes/fullstack_v4.json
    active: true
```

### `config/browser.yaml`

Browser automation settings and your profile:

```yaml
headless: false
slow_mo: 500
executable_path: /usr/bin/google-chrome
profile:
  name: ""
  email: ""
  phone: ""
  linkedin: ""
  github: ""
```

ATS domain whitelist (greenhouse, lever, ashby, workday, etc.) is built into the browser assistant. Unknown domains prompt for confirmation before autofilling.

---

## Project structure

```text
JobZo/
├── ai/               # LLM client, scoring, prompts, validators
│   ├── client.py     # OpenAI / Ollama switch, cache, timeout
│   ├── scorer.py     # Keyword filter + rule scoring + LLM refinement
│   ├── llm.py        # Prompt loading and dispatch
│   └── validator.py  # Pydantic models for LLM output
├── browser/
│   └── assistant.py  # Playwright automation, form detection, autofill
├── cli/
│   └── main.py       # 9 Typer CLI commands
├── config/           # YAML configs for providers, LLM, browser, resumes
├── database/
│   ├── models.py     # Job, Application, Resume, Task (SQLAlchemy)
│   └── connection.py # SQLite init
├── prompts/          # System prompts for each LLM task
├── providers/
│   ├── base.py       # RawJob dataclass + JobProvider ABC
│   ├── rss.py        # HN RSS feed parser
│   ├── hn_scraper.py # YC company page scraper
│   ├── manual.py     # JSON queue for imported URLs
│   └── telegram.py   # Telegram channel listener (disabled)
├── services/
│   ├── collector.py  # Pipeline orchestration + dedup
│   ├── config.py     # YAML config loader
│   └── freshness.py  # Time-decay freshness score
├── tests/
│   ├── golden/       # 3 golden test fixtures (input → expected JSON)
│   ├── test_filters.py
│   └── test_golden.py
├── tracker/
│   ├── applications.py  # CRUD + status transitions + response timestamps
│   └── tasks.py         # Pending tasks (follow-ups, interview prep)
└── resumes/          # JSON skill metadata for each resume version
```

---

## Testing

```bash
pytest tests/ -v
```

6 tests covering keyword scoring, skill overlap, experience matching, freshness decay, dedup keys, and golden regression tests.

---

## Design principles

- **Rules before AI** — deterministic scoring runs first. LLM is an optional refinement, not a dependency.
- **Human in the loop** — browser automation never submits without your review.
- **Explainable scores** — every score includes human-readable reasons for each component.
- **Graceful degradation** — everything works without internet, without an LLM, without any single component.
- **Local-first** — SQLite, offline scoring, no data leaves your machine.
- **Resource efficient** — designed for 8 GB RAM, dual-core, ₹0 budget.

---

## Roadmap

**v1.0** — Daily driver (current)

**After 100 applications** (data-driven):
- Company career-page crawling
- Resume A/B testing analytics
- `jobzo review` command with stats
- Learning scoring weights from interview outcomes

**Future** (only if needed):
- Cloud LLM routing
- Automatic recruiter email classification
- Recruiter CRM
