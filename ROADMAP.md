# Research Roadmap

Not a feature roadmap. Every item on this list is a hypothesis to test,
not a feature to build. Progress means evidence, not code.

---

## Phase 1: Intelligence ✓

*Built.* The system can discover, score, and rank opportunities.

*What we learned:* A rule-based decision engine with immutable snapshots
is sufficient for reliable ranking. LLM-based ranking added complexity
without measurable improvement in benchmark scores.

---

## Phase 2: Planning ✓

*Built.* The planner can schedule tasks into a time budget, respect
dependencies, and explain why tasks were accepted or rejected.

*What we learned:* Greedy value-density packing within dependency levels
is competitive with more complex knapsack solvers. The depth-respecting
sort prevents the planner from scheduling a task whose dependencies
aren't met.

---

## Phase 3: Observation pipeline ✓

*Built.* Every action produces an immutable observation. Funels,
calibration curves, and root-cause analysis are operational.

*What we learned:* Typed observation enums are more useful than a
generic event store. `current_stage()` and `company_funnel()` provide
immediate value from raw event data without additional instrumentation.

---

## Phase 4: Career Capital ✓

*Built.* Six-dimensional capital model with goal-based profiles.
Kind contributions map every task type to its capital vector.

*What we learned:* The capital model reframes the planner's objective
function. Whether it produces better outcomes than interview-probability-only
optimization is an open question — Experiment 1 will answer it.

---

## Phase 5: Experiment framework ✓

*Built.* A/B test registration, treatment assignment, observation recording,
and statistical analysis are operational.

*What we learned:* The framework works. Whether any experiment produces
statistically significant results depends on user adoption.

---

## Current: Outcome learning

**Hypothesis:** Observed outcomes can calibrate decision snapshots to
produce more accurate interview probability estimates.

**Experiment:** Compare calibrated vs uncalibrated planner recommendations
over 30 days. Measure offer rate difference.

**Status:** Awaiting sufficient outcome data.

**Prerequisite:** N ≥ 100 applications with terminal outcomes
(interview or rejection) across all users.

---

## Next: World model

**Hypothesis:** Hiring momentum (seasonality, company health, recruiter
responsiveness) is a measurable signal that improves planner recommendations.

**Approach:**
1. Collect timestamps for every application → interview → offer → rejection.
2. Compute per-company, per-ATS, per-month response rates.
3. Integrate as a Bayesian prior on interview probability.

**Status:** Requires observation data from Phase 3 to establish baselines.

---

## Future: Personalized RL planner

**Hypothesis:** A reinforcement learning planner that adapts to individual
user behavior and outcomes outperforms the fixed greedy planner.

**Approach:**
1. Train on historical DecisionSnapshot + Observation data.
2. Reward function = offer (100) + interview (10) + capital gain (variable).
3. Cold-start with greedy planner, warm-switch to RL after N observations.

**Status:** Requires 1,000+ completed user missions for training data.

---

## Future: Career graph

**Hypothesis:** A unified graph connecting people, skills, companies,
projects, and outcomes produces better skill recommendations than
the current weighted DAG.

**Approach:**
1. Link DecisionSnapshots → companies → skills → outcomes.
2. Infer skill transition probabilities ("knows Python → likely to learn FastAPI").
3. Recommend skill investments based on observed career trajectories.

**Status:** Requires large-scale outcome data. This is the long-term moat.

---

## Experiment backlog

These experiments are defined but not yet running (awaiting data):

| ID | Hypothesis | Metric | Expected |
|----|-----------|--------|----------|
| E1 | Career Capital planner > interview probability planner | Offer rate | +15% |
| E2 | Outreach tasks improve interview rate | Interview rate | +10% |
| E3 | ASAP vs growth profiles produce different outcomes | Offer rate | Unknown |
| E4 | Calibration reduces prediction error | Calibration error | -30% |
| E5 | Tuesday/Wednesday applications outperform | Interview rate | +5% |
| E6 | Resume v6 > v5 for backend roles | Interview rate | +10% |
| E7 | Founder emails > recruiter emails for startups | Reply rate | +15% |

---

## How to read this roadmap

Progress means narrowing confidence intervals, not shipping features.

When an experiment produces p < 0.05 with adequate sample size,
the conclusion enters the permanent research record in `docs/RESEARCH.md`.
When it doesn't, the hypothesis is discarded or refined.

That is how the system improves.
