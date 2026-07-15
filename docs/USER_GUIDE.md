# JobZo User Guide

---

## The mission loop (primary interface)

Run `jobzo` or `python3 -m mission.engine` to start.

### Dashboard

```
Good Morning Alice ─ Applied 14 ─ Interviews 3 ─ Rate 21.4%

Today: Review 4 opportunities · Prepare 1 interview · ~45 min

  1. 🚀 BrowserStack — Backend Engineer  ⭐⭐⭐⭐⭐ [15 min]
  2. 🚀 Postman — API Engineer           ⭐⭐⭐⭐  [20 min]
  3. 🎤 Groww — Interview Prep           [30 min]
  4. 📧 Nubank — Follow up               [5 min]

Quick Actions
  [s] Sync    [i] Insights    [r] Review all    [q] Quit
```

**What you see:**
- **Weekly stats** — total applications, interviews, response rate
- **Today's briefing** — number of items per category and estimated total time
- **Inbox items** — prioritized by expected value per minute. Each shows category icon, company, role, score (star rating), and estimated time.

**What to do:**
- Pick a number (`1`, `2`, etc.) to view the item and act on it
- `s` to sync new jobs
- `i` for personal insights
- `r` to browse all ranked jobs
- `q` to quit

### Item detail

When you select an item (e.g. `1` for BrowserStack):

```
BrowserStack — Backend Engineer

  ✅ Excellent  Score: 85/100
     Skills         72/100 ████████
     Experience     81/100 ████████
     Location       90/100 ████████
     Education      70/100 ███████
     Domain         65/100 ██████
     Culture        75/100 ███████

  ✓ Matched: Python, Django, PostgreSQL, Redis
  − Missing: Kafka, Kubernetes
  → Would improve to: Kafka: 91/100 (+6) · Kubernetes: 88/100 (+3)

  Competitiveness 68% (confidence: Medium)
  Resume: backend_v3
  Source: greenhouse

Next step
  1. Apply now
  2. View fit report
  3. Skip
  4. Back to inbox
```

**Item categories:**

| Icon | Category | What it means |
|------|----------|---------------|
| 🚀 | apply | High-scoring opportunity ready to apply |
| 📄 | review | Needs review — score is borderline |
| 🎤 | interview | Scheduled interview — prepare |
| 📧 | followup | Past-due follow-up |
| ⏰ | task | General task |

### Apply flow

When you choose `1` (Apply now):

1. JobZo shows a preview of the application (resume + cover letter)
2. Launches Playwright browser
3. Navigates to the application URL
4. Detects the ATS (Greenhouse, Lever, etc.) and fills the form
5. You review the filled form
6. On confirmation, JobZo submits and records an `APPLICATION_SUBMITTED` observation

### Review flow

When you choose `r` (Review all):

```
◀ 1/36 ▶  BrowserStack — Backend Engineer

  ✅ Excellent  Score: 85/100
  ...

  [a] Apply    [s] Save    [x] Skip    [b] Back
```

Navigate through all ranked opportunities. Each shows the same detail view with matched/missing skills, score breakdown, and "what if I learned X" projections.

---

## CLI commands

### `jobzo collect`

Fetch jobs from all enabled providers.

```bash
jobzo collect
jobzo collect --keywords "python,backend,remote"
```

### `jobzo rank`

Score all unscored jobs against your skills and experience.

```bash
jobzo rank
jobzo rank --skills "python,django,redis" --experience 2
```

### `jobzo apply`

Submit an application using browser automation.

```bash
jobzo apply <app_id>              # Apply to a specific app
jobzo apply --daily               # Apply to top 5 drafted apps
jobzo apply                       # Interactive picker
```

### `jobzo track`

Update application status. Records an observation for the new status.

```bash
jobzo track <app_id> --status submitted
jobzo track <app_id> --status interview
jobzo track <app_id> --status rejected
jobzo track <app_id> --status offer
```

### `jobzo outcome`

Record detailed outcome data after an interview or offer decision.

```bash
jobzo outcome <app_id> --rounds 3 --reason "Technical fit" --feedback "Good DSA, weak system design"
jobzo outcome <app_id> --salary "18 LPA"
```

### `jobzo prepare`

Generate an interview preparation plan.

```bash
jobzo prepare <app_id>
```

Output includes: topics to review (from JD), estimated preparation time, practice questions, company-specific patterns.

### `jobzo insight`

Personal analytics dashboard.

```bash
jobzo insight
jobzo insight --view weights     # Learned personal weights
jobzo insight --view resumes     # Resume performance stats
jobzo insight --view companies   # Company response rates
jobzo insight --view ats         # ATS platform performance
jobzo insight --view timing      # Best days/times to apply
jobzo insight --view skills      # Skill gap analysis
jobzo insight --view all         # Everything
```

### `jobzo contact`

Find and manage contacts (recruiters, hiring managers).

```bash
jobzo contact <app_id>             # Find contacts for an application
jobzo contact <app_id> --email     # Generate email draft
```

### `jobzo outcome` (extended)

View or update the full outcome record for an application.

```bash
jobzo outcome <app_id>             # View outcome
jobzo outcome <app_id> --reason "hired" --rounds 4 --salary "20 LPA"
```

### `jobzo prepare`

Interview preparation plan.

```bash
jobzo prepare <app_id>
```

### `jobzo benchmark`

Run the evaluation suite.

```bash
jobzo benchmark
jobzo benchmark --profile backend_fresher  # Single profile
```

---

## Setup

### Prerequisites

- Python 3.12+
- Playwright (for browser automation)

### Installation

```bash
git clone https://github.com/abhishek-mule/JobZo.git
cd JobZo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Configuration

Edit `resumes/master/profile.yaml` with your details:

```yaml
name: Your Name
current_role: Software Engineer
years_of_experience: 2
skills:
  - Python
  - Django
  - PostgreSQL
  - Redis
  - Docker
```

Set your resume files in `resumes/` directory (see `resumes/README.md` for format).

### First run

```bash
# Sync company registry
jobzo sync-companies

# Collect jobs
jobzo collect

# Score them
jobzo rank

# Start the mission loop
jobzo
```

---

## Workflows

### Daily workflow (15 min)

```bash
jobzo                          # Open mission loop
  → Pick top apply item        # Submit an application
  → Check interview prep       # Prepare if any interviews scheduled
  → Review new opportunities   # Review and skip/save
  → Quit                       # Done
```

### Weekly workflow (30 min)

```bash
jobzo collect                  # Fetch new jobs
jobzo rank                     # Score them
jobzo insight --view all       # Review analytics
jobzo                          # Daily mission
```

### Post-interview

```bash
jobzo track <id> --status interview   # Mark interview scheduled
# ... after interview ...
jobzo outcome <id> --rounds 3 --feedback "Good, but weak on system design"
jobzo track <id> --status rejected    # Or: --status offer
```

---

## Advanced

### Running experiments

JobZo includes a built-in A/B experiment framework. Experiments run automatically when you use the mission loop. View results:

```bash
python3 -c "
from domain.experiment import ExperimentService, create_default_experiments
svc = ExperimentService()
for exp in create_default_experiments():
    svc.register(exp)
for r in svc.compare_all_active():
    print(r['hypothesis'], '-', r['recommendation'])
"
```

### Simulation

```bash
# Compare career strategies
python3 -c "
from domain.simulation import Scenario, compare_scenarios
from domain.capital import CapitalProfile

scenarios = [
    Scenario('Apply only', 'Just apply everywhere', {'apply': 1.0}),
    Scenario('Learn first', 'Learn Redis + Kafka before applying', {'learning': 0.5, 'apply': 0.5}),
    Scenario('Network', 'Apply + outreach to contacts', {'apply': 0.6, 'outreach': 0.4}),
]
result = compare_scenarios(scenarios, total_hours=8, runs=20,
    capital_profile=CapitalProfile.for_goal('Get placed ASAP'))
print('Recommended:', result['recommendation'])
for s in result['scenarios']:
    print(f\"  {s['name']}: {s['total_offers']:.1f} offers, capital: {s['capital_score']}\")
"
```

### Calibration analysis

```bash
# Check if the system's predictions are calibrated
python3 -c "
from domain.analytics import CalibrationService
curve = CalibrationService.build_curve()
for p in curve:
    print(f'Predicted {p.expected:.0%} → Observed {p.observed:.0%} (n={p.count})')
"
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| `No jobs collected` | No providers configured | Check `services/config.py` for provider API keys |
| Playwright fails | Chromium not installed | `playwright install chromium` |
| Score all 0 | No resume profile | Configure `resumes/master/profile.yaml` |
| Mission loop empty | No scored applications | Run `jobzo collect && jobzo rank` |
| Database locked | Multiple sessions | Close other terminal windows running JobZo |
