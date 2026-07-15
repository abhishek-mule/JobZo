<img width="1407" height="768" alt="Gemini_Generated_Image_brclqgbrclqgbrcl" src="https://github.com/user-attachments/assets/3600c1e9-c997-45a8-8682-57c16f6cf0ab" />

# JobZo — Evidence-Driven Career Decisions

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Tests](https://img.shields.io/badge/Tests-200%20passed-brightgreen)]()
[![Benchmarks](https://img.shields.io/badge/Benchmarks-11%2F11%20passing-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)]()

## Why JobZo exists

Most job tools optimize for **applications submitted**. JobZo optimizes for **career outcomes**.

Instead of asking *"Which jobs match my resume?"* JobZo asks: ***"What's the highest-return thing I can do in the next 90 minutes to improve my career?"***

Sometimes that's applying. Sometimes it's networking. Sometimes it's learning a skill. Sometimes it's preparing for tomorrow's interview.

```
You → Today's Mission → Action → Observation → Learning → Better Tomorrow
```

JobZo continuously measures which actions produce interviews and offers, then updates its recommendations using observed outcomes instead of fixed rules. **Every cycle improves the next one.**

---

## Quick start (under 5 minutes)

```bash
git clone https://github.com/abhishek-mule/JobZo.git
cd JobZo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start the mission loop — no commands needed
python3 -m mission.engine
```

You'll see a prioritized inbox:

```
Good Morning — Applied 14 — Interviews 3 — Rate 21.4%

Today: Review 4 opportunities · Prepare 1 interview · ~45 min

  1. 🚀 BrowserStack — Backend Engineer  ⭐⭐⭐⭐⭐ [15 min]
  2. 🚀 Postman — API Engineer           ⭐⭐⭐⭐  [20 min]
  3. 🎤 Groww — Interview Prep           [30 min]

Quick Actions
  [s] Sync  [i] Insights  [r] Review all  [q] Quit
```

Pick an item (`1`, `2`, `3`) and JobZo shows you why it recommended it, the expected outcome, and the next action. Apply directly from the terminal.

---

## This is not a claim — it's a hypothesis being tested. Every experiment, including failures, is documented in [the research logbook](docs/RESEARCH.md).

Further reading: [Design principles](docs/DESIGN_PRINCIPLES.md) · [Research logbook](docs/RESEARCH.md) · [Roadmap](ROADMAP.md)

---

## What JobZo does differently

**Closes the feedback loop.** Every application, skip, interview, and offer becomes an observation. JobZo compares every prediction against what actually happened, identifies which assumptions were wrong, and improves the next recommendation. This compounds — the more you use it, the smarter it gets.

**Optimizes your scarce resource (time).** The planner ranks tasks by expected value per minute. A 6-minute founder email can outrank a 15-minute application if the data says it's more likely to produce an outcome.

**Learns from outcomes, not rules.** After enough observations, JobZo discovers proprietary knowledge: *"Resume v6 outperforms v5 by 23% for backend roles"* or *"Tuesday morning applications get 12% more responses."* No competitor can buy that dataset.

---

## Commands reference

| Command | What it does | When to use it |
|---------|-------------|----------------|
| `jobzo` | Open the mission loop (inbox + actions) | Every day |
| `jobzo collect` | Fetch new jobs from all providers | Weekly |
| `jobzo rank` | Score unscored jobs against your skills | After collect |
| `jobzo track <id> --status <s>` | Update application status | When something changes |
| `jobzo outcome <id> --rounds 3` | Record interview outcome | After an interview |
| `jobzo prepare <id>` | Generate interview prep plan | Before an interview |
| `jobzo insight` | Personal analytics dashboard | Weekly review |
| `jobzo benchmark` | Run the 11-benchmark suite | After code changes |

---

## How it works (the 30-second version)

```
You apply → JobZo observes the outcome → Compares prediction vs reality
→ Identifies which assumption was wrong → Improves next recommendation
```

Every component feeds into this loop. The architecture is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Detailed usage is in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

---

## What's built

| Layer | What | Status |
|-------|------|--------|
| Knowledge | Company registry, skill graph, title normalization | ✓ |
| Decision | Retriever, ranker, decision snapshots (versioned, immutable) | ✓ |
| Execution | Mission planner, task providers, outreach, follow-ups | ✓ |
| Learning | Observation pipeline, calibration curves, root-cause analysis | ✓ |
| Research | A/B experiment framework, statistical evaluation | ✓ |
| Capital | 6-dim career objective (resume, skill, network, interview, reputation, opportunity) | ✓ |
| Simulation | Monte Carlo scenario comparison ("apply vs learn vs network?") | ✓ |

---

## License

MIT. See [LICENSE](LICENSE).
