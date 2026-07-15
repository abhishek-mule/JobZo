# Design Principles

These principles guide every decision in JobZo. They are the filter for
feature proposals, PR reviews, and architectural changes.

---

## Principle 1: Optimize for offers, not applications.

Most job tools measure success by *applications submitted*. That rewards
volume. JobZo measures success by *career outcomes* — interviews, offers,
salary, and long-term capital accumulation.

**What this means:**
- The planner ranks tasks by expected value per minute, not convenience.
- A 6-minute founder email can outrank a 15-minute application.
- The system tracks outcomes and calibrates its predictions.
- "More applications" is never a goal by itself.

---

## Principle 2: Everything observable.

Every prediction, action, and outcome is recorded as an immutable,
typed event. No silent state changes. No untracked side effects.

**What this means:**
- `DecisionSnapshot` records every scored prediction with full version info.
- `Observation` events record every application lifecycle transition.
- `Experiment` observations record every A/B test data point.
- Calibration is only possible because nothing is invisible.

---

## Principle 3: Immutable decisions.

Once a decision is made, it never changes. The record is frozen.
Future decisions can be better, but past decisions are truth.

**What this means:**
- `DecisionSnapshot` contains the exact retriever/ranker/registry versions used.
- You can reconstruct exactly what the system recommended on any date.
- Improvement requires better future predictions, not rewriting history.
- Auditability is a feature, not an afterthought.

---

## Principle 4: Predictions must calibrate.

Every probability estimate must be measurable against reality.
If the system predicts 60% interview probability, approximately 60%
of those predictions should result in interviews.

**What this means:**
- `CalibrationService.build_curve()` compares every prediction bucket against observed outcomes.
- `CalibrationAnalyzer` decomposes prediction error per score dimension.
- The system learns which weights are wrong, not just that it was wrong.
- Uncalibrated predictions are technical debt.

---

## Principle 5: Planner optimizes career capital, not interview probability.

Interview probability is one signal. Career capital is six:
resume, skill, network, interview, reputation, opportunity.
The planner maximizes the weighted combination, not any single dimension.

**What this means:**
- Different goals produce different capital profiles.
- "Get placed ASAP" weights opportunity heavily; "Career growth" weights skill and reputation.
- Every task type maps to a capital contribution vector.
- The planner compares "learn Redis" vs "apply to BrowserStack" on the same scale.

---

## Principle 6: User time is the most constrained resource.

The system exists to save the user time, not to maximize time spent in the tool.
Every feature must either save time or improve outcomes enough to justify the
time it costs.

**What this means:**
- The mission loop shows estimated time for every task.
- The planner packs into a time budget; rejected tasks are explained, not dropped.
- Browser automation exists because manual form-filling is low-value work.
- Insights and analytics must be faster than manual review.

---

## Principle 7: Benchmarks before heuristics.

Every heuristic or weight change must be measurable against a benchmark.
If a change improves benchmark scores, it can be considered.
If it doesn't, it's an opinion, not an improvement.

**What this means:**
- 11 benchmark suites cover retrieval, ranking, and score accuracy.
- Planner changes are validated through Monte Carlo simulation.
- Experiment results (p-values) replace gut feelings for decision-making.
- "It feels better" is not an acceptable justification.

---

## Principle 8: Architecture serves the loop.

The architecture exists to support the closed loop:
*Decide → Act → Observe → Calibrate → Improve.*

Every subsystem must contribute to this loop or be removed.

**What this means:**
- No feature is added without a clear path to measuring its impact.
- The experiment framework evaluates whether changes actually improve outcomes.
- Architecture v1.0 is frozen. New abstractions require evidence they're needed.
- The system grows by proving value, not by adding code.

---

## Summary

| # | Principle | Filter question |
|---|-----------|----------------|
| 1 | Optimize for offers | "Does this increase offer probability?" |
| 2 | Everything observable | "Can we measure the effect?" |
| 3 | Immutable decisions | "Can we audit this later?" |
| 4 | Predictions calibrate | "Will we know if we're wrong?" |
| 5 | Career capital | "Which capital dimension does this build?" |
| 6 | User time | "Does this save time or improve outcomes enough?" |
| 7 | Benchmarks | "Can we measure before and after?" |
| 8 | Architecture serves the loop | "Does this improve the decision loop?" |
