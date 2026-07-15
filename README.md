<img width="1407" height="768" alt="Gemini_Generated_Image_brclqgbrclqgbrcl" src="https://github.com/user-attachments/assets/3600c1e9-c997-45a8-8682-57c16f6cf0ab" />

# JobZo — Career Optimization Engine

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Tests](https://img.shields.io/badge/Tests-126%20passed-brightgreen)]()
[![Benchmarks](https://img.shields.io/badge/Benchmarks-11%2F11%20passing-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)]()

Modern job search tools optimize for **applications submitted**.
JobZo optimizes for **career outcomes**.

Every recommendation, task, and application is chosen to maximize the expected
long-term value of a developer's career — not simply the number of applications
sent.

---

## The problem

Most platforms answer:

> *"What jobs exist?"*

JobZo answers:

> *"Given your skills, goals, history, and limited time today, what is the
> single highest-impact action you should take next?"*

That means the system does not just find jobs. It estimates interview probability
from a graph-aware skill match, scores opportunities across six dimensions,
snapshots every decision immutably, generates tasks from providers, schedules
them within a daily time budget using a dependency-respecting planner, and
executes them through a lifecycle-aware mission engine.

The output is not a list of jobs. It is a **mission**.

---

## Architecture

```text
                           ┌──────────────────┐
                           │  Company Registry │
                           │    Skill Graph    │
                           │  DecisionSnapshot │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │     Retriever    │
                           │     Ranker       │
                           │    Normalizer    │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │ OpportunitySnap. │
                           └────────┬─────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌──────▼──────┐  ┌─────▼─────┐
              │   Apply   │  │  Follow-up  │  │  Future   │
              │  Provider │  │  Provider   │  │ Providers │
              └─────┬─────┘  └──────┬──────┘  └─────┬─────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                           ┌────────▼─────────┐
                           │  TaskProviderReg. │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │  GreedyPlanner   │
                           │ (depth + density │
                           │  + budget pack)  │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │     Mission      │
                           │   (accepted +    │
                           │   rejected +     │
                           │   provenance)    │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │    Execution     │
                           │  (lifecycle mgr) │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │   Events /       │
                           │   Benchmarks /   │
                           │   Simulation     │
                           └──────────────────┘
```

Layers are independent. Each has a distinct responsibility, owns its own tests,
and can be replaced without touching the others.

---

## Design Principles

1. **Optimize outcomes, not activity.**  
   Tasks are ranked by expected value per minute, not by convenience.

2. **Every recommendation must be explainable.**  
   `TaskNode.why()` returns a human-readable justification for every task.

3. **Decisions are versioned and reproducible.**  
   `DecisionSnapshot` records the full retriever/ranker/registry versions
   so any historical decision can be audited.

4. **Execution is more valuable than discovery.**  
   The planner packs into a time budget; tasks that don't fit are rejected
   with a reason, not silently dropped.

5. **Benchmarks are required before heuristics change.**  
   Retrieval accuracy, ranker accuracy, and planner efficiency are measured
   before and after every change.

6. **User time is the most constrained resource.**  
   A mission tells you what to do, what to skip, and why.

---

## Current status

**Today:**

- Rule- and graph-based decision engine.
- Benchmark-driven retrieval and ranking (11 suites, 126 tests).
- Mission planning over expected value with dependency resolution.
- Immutable, versioned decision snapshots.
- Simulation framework for planner comparison.

**Next:**

- Outcome-driven probability estimation from real application results.
- Adaptive planning that learns from user feedback.
- Additional task providers (follow-up, interview prep, networking, learning).
- Career graph for long-term trajectory optimization.

---

## Roadmap

```text
Phase 1 — Intelligence
    ✓ Retrieval engine (skill graph, normalization, eligibility)
    ✓ Ranking engine (interview probability, confidence, risk)
    ✓ Decision snapshots (versioned, immutable, reproducible)

Phase 2 — Planning
    ✓ Mission engine (TaskNode, lifecycle, dependencies)
    ✓ Task provider registry (pluggable, priority-ordered)
    ✓ Greedy planner (depth-respecting value density ranking)

Phase 3 — Learning
    □ Outcome engine (calibrate probabilities from real outcomes)
    □ Probability calibration (observed → predicted)
    □ Adaptive planning (planner adjusts from user behavior)

Phase 4 — Career Graph
    □ Long-term career trajectory optimization
    □ Skill investment modeling (learn X → unlock Y)
    □ Community outcome analytics (anonymized, aggregated)
```

---

## Stack

- **Language:** Python 3.12+
- **Storage:** SQLite via SQLAlchemy 2.0 (local-first, zero-infrastructure)
- **Parsing:** Reusable Playwright scripts + ATS-specific extractors (Ashby,
  BambooHR, Greenhouse, Lever, Personio, SmartRecruiters, Teamtailor, Workday)
- **Graph:** Weighted DAG for skill relationships (parent, complement)
- **Decision Engine:** Composite score vector (6-dim), tier assignment,
  interview probability estimation
- **Planning:** Dependency-graph-aware greedy scheduler with budget packing
- **Testing:** 126 tests, 11 benchmark suites, simulation framework

---

## Quick start

```bash
git clone https://github.com/abhishek-mule/JobZo.git
cd JobZo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m mission.engine    # start the mission loop
```

```bash
# Run the full test suite (126 tests)
python3 -m pytest tests/

# Run benchmarks (11 suites)
python3 -c "from benchmark.runner import run_all, print_results; print_results(run_all())"

# Run a planner simulation (30 days)
python3 -c "
from domain.planner import GreedyPlanner
from domain.simulation import simulate, generate_task_pool, SimulationConfig
result = simulate(GreedyPlanner(), SimulationConfig(days=30))
print(result.summary())
"
```

---

## Project structure

```
├── ai/              Retriever, ranker, normalizer, skill graph, score vector
├── ats/             ATS-specific parsers (7 platforms)
├── benchmark/       Retrieval, ranker, and score accuracy benchmarks
├── domain/          Mission planner, task providers, execution engine
│   ├── models.py    TaskNode, Mission, ProviderResult, OpportunitySnapshot
│   ├── providers.py TaskProvider protocol + ApplyTaskProvider
│   ├── registry.py  TaskProviderRegistry (pluggable provider discovery)
│   ├── planner.py   GreedyPlanner (dependency depth + value density)
│   ├── execution.py MissionExecution (lifecycle manager)
│   └── simulation.py  Monte Carlo planner evaluation
├── database/        SQLAlchemy models + connection management
├── mission/         Interactive mission engine (inbox, review, execution)
├── resumes/         Resume registry, optimizer, JD analyzer, generator
├── services/        Eligibility engine, company registry, config, caching
├── skills/          Skill knowledge base (YAML dictionary with aliases)
├── tracker/         Decision intelligence, events, outreach, outcomes
└── tests/           126 tests across all layers
```

---

## License

MIT. See [LICENSE](LICENSE).
