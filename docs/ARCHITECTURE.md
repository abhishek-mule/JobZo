# JobZo Architecture

**Architecture v1.0** — closed-loop adaptive decision system. Frozen. No new abstractions, providers, or subsystems will be added unless an experiment proves they unlock measurable value.

---

## System overview

```
Internet
    │
Job Providers
    │
Company Registry + Skill Graph
    │
Retriever + Ranker + Normalizer
    │
DecisionSnapshot (versioned, immutable)
    │
OpportunitySnapshot (read-model)
    │
TaskProviderRegistry
 ├── ApplyTaskProvider
 ├── OutreachTaskProvider
 ├── FollowupTaskProvider
 └── InterviewTaskProvider
    │
GreedyPlanner (depth + density + budget)
    │
Mission (accepted + rejected + provenance)
    │
Execution (lifecycle manager)
    │
Observation event pipeline
    │
Projection + Calibration + Root-cause Analysis
    │
Career Capital (6-dim objective)
    │
Experiment framework (A/B tests)
    │
Better DecisionSnapshot ───→ loop
```

---

## Data flow

### 1. Collection

```
Job providers (LinkedIn, Indeed, Greenhouse, etc.)
    → Raw Job rows in SQLite
    → ATS-specific parsing (7 platforms: Ashby, BambooHR,
      Greenhouse, Lever, Personio, SmartRecruiters,
      Teamtailor, Workday)
```

### 2. Scoring

```
Retriever:
  - Skill matching against user's resume (weighted DAG)
  - Experience fit (years, seniority levels)
  - Location eligibility
  - Title normalization

Ranker:
  - 6-dim score vector (skills, experience, location,
    education, domain, culture)
  - Composite score (0-100)
  - Tier assignment (apply_now, strong_match, worth_trying, stretch)
  - Interview probability estimation

DecisionSnapshot:
  - Immutable record of every scored decision
  - Includes retriever/ranker/registry/skill-graph versions
  - Stores full score vector + matched/missing skills
  - Enables audit: "what did the system think on this date?"
```

### 3. Planning

```
OpportunitySnapshot (read-model from DecisionSnapshot):
  - Pure data, no DB references
  - Input to all task providers

TaskProviderRegistry:
  - Pluggable providers registered by priority
  - supports(context) filters applicable providers
  - build_all(context, opportunities) generates tasks

Providers:
  - ApplyTaskProvider (priority 10):
      Filters below min_score, computes expected value from goal,
      sets uncertainty from confidence, populates why() lines
  - OutreachTaskProvider (priority 20):
      Generates ranked contact suggestions per company tier,
      selects strategy (startup→founder, FAANG→referral, etc.)
  - (Future) FollowupTaskProvider, InterviewTaskProvider, LearningTaskProvider

GreedyPlanner:
  - Computes dependency depth for each task
  - Sorts by (depth, -value_density)
  - Packs greedily into time budget
  - Returns accepted + rejected tasks with provenance
  - Instrumented for A/B experiments (planner_version, experiment_treatment)
```

### 4. Execution

```
MissionExecution:
  - start/pause/resume/complete/fail (mission lifecycle)
  - execute_task/complete_task/skip_task/defer_task/fail_task (task lifecycle)
  - next_actionable() returns tasks whose dependencies are met
  - blocked_reason() explains why a task can't run
```

### 5. Observation pipeline

```
Every action produces an immutable Event:
  - APPLICATION_SUBMITTED, APPLICATION_VIEWED
  - OA_RECEIVED, OA_COMPLETED
  - INTERVIEW_SCHEDULED, INTERVIEW_PASSED
  - OFFER_RECEIVED, OFFER_ACCEPTED, OFFER_DECLINED
  - REJECTED, GHOSTED
  - EMAIL_SENT, EMAIL_OPENED, EMAIL_REPLIED
  - REFERRAL_REQUESTED, REFERRAL_RECEIVED

ObservationService:
  - record() — typed wrapper over Event table
  - get_for_application() — full timeline, oldest first
  - current_stage() — most advanced stage reached
  - get_all_for_company() — cross-application observations
```

### 6. Analytics

```
ProjectionService:
  - company_funnel() — conversion rates per stage for a company
  - global_funnel() — aggregate conversion rates
  - time_to_next_stage() — days between two stages
  - average_time_to_stage() — mean days from submission

CalibrationService:
  - build_curve() — isotonic bucketed observed vs predicted
  - calibrate() — adjust raw probability using curve
  - confidence_interval() — Wilson score interval
  - expected_value() — probability * value - (1-p) * cost

CalibrationAnalyzer:
  - analyze() — per-dimension prediction error decomposition
  - batch_analyze() — across all snapshots with outcomes
  - Tells you which assumption was wrong, not just that it was wrong
```

### 7. Career Capital

```
CapitalVector — 6 dimensions:
  - resume: job titles, brands, achievements
  - skill: technical/domain knowledge
  - network: people who know you
  - interview: practice, process familiarity
  - reputation: public proof of work (OSS, writing)
  - opportunity: active applications in pipeline

Goal profiles (each maps goal → capital weights):
  - "Get placed ASAP" → 55% opportunity
  - "Maximize salary" → 20% resume, 20% skill, 20% interview
  - "Build network" → 45% network
  - "Career growth (long-term)" → 25% skill, 25% reputation

KindContributions — every task type maps to a CapitalVector:
  - apply → 0.6 opportunity
  - outreach → 0.5 network
  - learning → 0.5 skill
  - github_contribution → 0.5 reputation
  - interview_prep → 0.4 interview

Planner uses: capital_value(kind, profile) * expected_value
```

### 8. Experiments

```
Experiment:
  - id, hypothesis, treatments (control + variants)
  - AssignmentStrategy (RANDOM | USER_ID_HASH)
  - Metric (INTERVIEW_RATE | OFFER_RATE | etc.)

ExperimentService:
  - register(), assign(), record_observation(), analyze()
  - Welch's t-test + Cohen's d + p-value approximation
  - compare_all_active() — analyze all running experiments

Built-in experiments:
  1. planner_v1_vs_capital — Career Capital vs interview probability
  2. outreach_effectiveness — with vs without outreach tasks
  3. capital_profile_asap_vs_growth — short-term vs long-term weighting
```

---

## Domain model

### Core objects

```
TaskNode:
  - id, kind, title, description, source
  - estimated_minutes, expected_value, uncertainty
  - urgency, deadline, dependencies, blockers
  - lifecycle: pending → active → completed | skipped | deferred | failed
  - why() — human-readable justification

Mission:
  - tasks, state, completed_task_ids
  - rejected_tasks, deferred_tasks, provider_results
  - plan_provenance — planner version, experiment treatment, provider versions

MissionContext:
  - time_budget, goal, today, preferences (immutable)

OpportunitySnapshot:
  - snapshot_id, company, title, url
  - score, tier, interview_probability, confidence, risk
  - matched_skills, missing_skills

ProviderResult:
  - provider, provider_version
  - tasks, warnings, statistics

Person:
  - name, skills, projects, interview_history
  - network_size, goal, availability
  - skill_gaps(), offer_rate()
```

### Supporting objects

```
Contact:
  - role (RECRUITER | HIRING_MANAGER | FOUNDER | EMPLOYEE | etc.)
  - source, relationship, hiring_authority
  - confidence, response_status, priority_score()

OutreachStrategy:
  - per company tier (STARTUP | GROWTH | MID_SIZE | LARGE_MNC | FAANG)

CapitalVector:
  - 6-dim float vector for any task's capital contribution
  - dot(weights) → planner utility value

Observation:
  - typed wrapper over Event table
  - observation_type (ObservationType enum), metadata

ExperimentObservation:
  - experiment_id, treatment, user_id, metric
  - predicted_value, actual_value
```

---

## Design principles

1. **Optimize outcomes, not activity.** Tasks are ranked by expected value per minute, not by convenience.

2. **Every recommendation must be explainable.** `TaskNode.why()` returns a human-readable justification.

3. **Decisions are versioned and reproducible.** `DecisionSnapshot` records the full retriever/ranker/registry versions so any historical decision can be audited.

4. **Execution is more valuable than discovery.** The planner packs into a time budget; tasks that don't fit are rejected with a reason, not silently dropped.

5. **Benchmarks are required before heuristics change.** Retrieval accuracy, ranker accuracy, and planner efficiency are measured before and after every change.

6. **User time is the most constrained resource.** A mission tells you what to do, what to skip, and why.

---

## Project structure

```
├── ai/                 Retriever, ranker, normalizer, skill graph, score vector
├── ats/                ATS-specific parsers (7 platforms)
├── benchmark/          Retrieval, ranker, and score accuracy benchmarks
├── domain/             All domain logic (no I/O)
│   ├── models.py       TaskNode, Mission, ProviderResult, OpportunitySnapshot
│   ├── providers.py    TaskProvider protocol + ApplyTaskProvider
│   ├── registry.py     TaskProviderRegistry
│   ├── planner.py      GreedyPlanner
│   ├── execution.py    MissionExecution
│   ├── simulation.py   Scenario comparison engine
│   ├── observation.py  Observation event pipeline
│   ├── analytics.py    Funnels, calibration, root-cause analyzer
│   ├── capital.py      Career Capital model
│   ├── outreach.py     Contact types, ranking, strategy
│   ├── outreach_provider.py  OutreachTaskProvider
│   ├── experiment.py   A/B test framework
│   └── person.py       Person model
├── database/           SQLAlchemy models + connection management
├── mission/            Interactive mission engine
├── resumes/            Resume registry, optimizer, JD analyzer
├── services/           Eligibility, company registry, config, caching
├── skills/             Skill knowledge base (YAML)
├── tracker/            Decision intelligence, events, outreach
└── tests/              200 tests
```

---

## Stack

- **Language:** Python 3.12+
- **Storage:** SQLite via SQLAlchemy 2.0 (local-first, zero-infrastructure)
- **Parsing:** Playwright scripts + ATS-specific extractors (7 platforms)
- **Graph:** Weighted DAG for skill relationships
- **Decision Engine:** 6-dim composite score, tier assignment, interview probability
- **Planning:** Dependency-graph-aware greedy scheduler with experiment instrumentation
- **Testing:** 200 tests, 11 benchmark suites, simulation framework
