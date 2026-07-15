# JobZo Research Logbook

Every claim JobZo makes should be reproducible. This document is the scientific record: hypotheses, experiments, results, and conclusions. Both successes and failures are recorded — institutional knowledge is built from what didn't work, not just what did.

---

## Experiment template

Every experiment in this logbook follows this structure:

```text
Question     What are we trying to learn?
Hypothesis  What do we believe will happen?
Metric      How do we measure success?
Design      Control vs treatment, assignment method
Baseline    What embarrassingly simple strategy are we beating?
Sample      Number of observations needed
Result      Observed effect size
CI          95% confidence interval
p-value     Statistical significance
Decision    Keep / Modify / Remove
```

---

## Methodology

### How we measure

Every prediction (score, tier, interview probability) is stored in an immutable `DecisionSnapshot`. Every outcome (interview, offer, rejection) is recorded as an `Observation` event. The `CalibrationService` compares predicted vs observed outcomes. The `ExperimentService` runs controlled A/B tests with statistical analysis.

### Baseline requirement

No experiment result is accepted without a baseline comparison. Baselines are intentionally simple:

| Baseline | Strategy |
|----------|----------|
| Random | Pick 5 random drafted applications |
| Recency | Apply to newest jobs first |
| Keyword | Simple keyword overlap sort (no planner) |

If the planner, calibration, or capital model cannot consistently outperform these baselines, the complexity is not justified.

### North-star metric: Career Return on Time (CRT)

CRT = Career Value Gained / Hours Invested

| Value | Source |
|-------|--------|
| 10 pts | Interview received |
| 50 pts | Offer received |

Time invested = sum of estimated_minutes from all completed tasks / 60.

CRT replaces interview rate and offer rate as the primary product metric because it captures efficiency, not just volume. A system that produces 2 offers in 20 hours (CRT = 5.0) is better than one that produces 3 offers in 60 hours (CRT = 2.5).

### User behavior metrics (prerequisite to outcome metrics)

Before measuring interview or offer rates, we must measure whether users actually follow the system's recommendations:

| Metric | Definition | Source |
|--------|-----------|--------|
| Mission acceptance rate | Tasks accepted / tasks offered | `MISSION_ACCEPTED` / `MISSION_REJECTED` events |
| Application completion rate | Tasks completed / tasks accepted | `APPLICATION_SUBMITTED` / `MISSION_ACCEPTED` |
| Task skip rate | Tasks skipped / tasks offered | `APPLICATION_SKIPPED` events |
| Session count | Number of mission loop sessions | `SESSION_START` events |
| Avg actions per session | Total actions / session count | All action events / `SESSION_START` |
| Weekly retention | % returning within 7 days | `SESSION_START` per user per week |

### Statistical framework

- **Test:** Welch's t-test (unequal variance, unequal sample sizes)
- **Effect size:** Cohen's d
- **Significance threshold:** p < 0.05 (95% confidence)
- **Minimum sample size:** 30 per treatment group

---

## Active experiments

### Experiment 0: Do users follow missions?

**Question:** Do users actually accept inbox recommendations, or do they ignore the planner entirely?

**Hypothesis:** Users accept ≥60% of inbox recommendations within a session.

**Metric:** Mission acceptance rate (accepted / accepted + rejected)

**Baseline:** Random acceptance (50% if binary accept/reject)

**Design:** Instrumented via `MISSION_ACCEPTED` / `MISSION_REJECTED` observations in the mission loop.

**Sample:** 30 sessions across all users.

**Status:** Awaiting data (n=0).

**If this fails:** No subsequent experiment can be interpreted. If users ignore the planner, the planner's sophistication is irrelevant.

---

### Experiment 1: Planner v1 vs Career Capital

**Question:** Does optimizing for career capital produce more offers than optimizing for interview probability alone?

**Hypothesis:** The Career Capital planner produces more offers.

| | Control (interview prob) | Treatment (career capital) |
|---|---|---|
| Objective | Maximize `expected_value` | Maximize `capital_value(kind, profile) * expected_value` |
| Sort key | `value_density` | `capital_value * expected_value / minutes` |
| Profile | — | "Get placed ASAP" |

**Baseline:** Random pick (5 random drafted apps) — must outperform this first.

**Status:** Awaiting data (n=0).

---

### Experiment 2: Outreach effectiveness

**Question:** Do outreach tasks (contacting recruiters/founders) improve interview rate beyond applications alone?

**Hypothesis:** Adding outreach tasks alongside applications increases interview rate by at least 10% (relative).

**Baseline:** Applications-only (no outreach). Random email to 5 contacts per week.

**Status:** Awaiting data (n=0).

---

### Experiment 3: Capital profile comparison

**Question:** Does the "Get placed ASAP" profile produce higher offer rates than "Career growth (long-term)"?

**Hypothesis:** Users with different capital profiles achieve different outcomes. Direction unknown — exploratory.

**Status:** Awaiting data (n=0).

---

## Simulation results

Before real user data, we validated the planner architecture through simulation.

### Scenario: 8-hour weekend

Three strategies compared over 30 simulated days (20 Monte Carlo runs each):

| Scenario | Applications | Interviews | Offers | Capital score |
|----------|-------------|-----------|--------|--------------|
| Apply only | 32 | 5.2 | 1.1 | 14.2 |
| Learn first (50%) | 16 | 4.8 | 0.9 | 18.7 |
| Apply + network (60/40) | 19 | 5.6 | 1.2 | 21.4 |

**Preliminary conclusion:** Mixing outreach with applications maximizes both offers and capital accumulation. Pure application volume does not win.

> **Caveat:** These numbers use default probability ranges (10-60% interview, 25% interview→offer). Real calibration will replace these defaults.

---

## Calibration analysis

When run on synthetic data, the calibration curve reveals systematic biases:

| Bucket | Predictions | Observed | Error |
|--------|------------|---------|-------|
| 0-10% | 0 | — | — |
| 10-20% | 0 | — | — |
| 20-30% | 0 | — | — |
| 30-40% | 0 | — | — |
| 40-50% | 0 | — | — |
| 50-60% | 0 | — | — |
| 60-70% | 0 | — | — |
| 70-80% | 0 | — | — |
| 80-90% | 1 | 0% | -80% |
| 90-100% | 0 | — | — |

> **Note:** Empty buckets indicate no real outcome data yet. Every application with a scored prediction and a terminal outcome (interview or rejection) fills one bucket.

---

## How to contribute evidence

1. Use JobZo for your job search
2. The system automatically records observations for every action
3. After N≥30 applications with outcomes, run:
   ```bash
   python3 -c "
   from domain.analytics import CalibrationService
   curve = CalibrationService.build_curve()
   for p in curve:
       print(f'{p.expected:.0%} → {p.observed:.0%} (n={p.count})')
   "
   python3 -c "
   from domain.experiment import ExperimentService, create_default_experiments
   svc = ExperimentService()
   for exp in create_default_experiments():
       svc.register(exp)
   for r in svc.compare_all_active():
       print(r['hypothesis'], 'p=', r['p_value'])
   "
   ```
4. Submit findings via GitHub Issues with tag `evidence`

---

## Privacy

- All observation data is stored locally in SQLite
- No data leaves your machine
- Experiment results are computed locally
- Aggregate statistics can be published anonymously with user consent
