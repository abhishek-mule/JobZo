"""Benchmark runner — loads profiles, runs evaluations, reports metrics."""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich import box

from services.config import Config
from services.company_registry import get_stats, get_by_category, get_by_technology, get_fresher_friendly, get_all

console = Console()

PROFILES_DIR = Path(__file__).parent / "profiles"


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    name: str
    status: str  # PASS | FAIL | ERROR
    metrics: dict[str, Any] = field(default_factory=dict)
    details: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BenchmarkJob:
    id: str
    company: str
    title: str
    description: str
    location: str
    experience_required: str
    remote: bool
    salary: str
    relevant: bool
    expected_tier: str | None  # apply_now | strong_match | ... | null for irrelevant
    expected_score_min: int | None
    expected_score_max: int | None
    expected_rank_min: int | None
    expected_rank_max: int | None


# ── Profile loader ───────────────────────────────────────────────────────────

def load_profiles() -> list[dict]:
    """Load all benchmark profiles from benchmark/profiles/*.json."""
    profiles = []
    for f in sorted(PROFILES_DIR.glob("*.json")):
        with open(f) as fp:
            data = json.load(fp)
            data["_file"] = f.stem
            profiles.append(data)
    return profiles


def jobs_from_profile(profile: dict) -> list[BenchmarkJob]:
    """Convert profile job dicts to BenchmarkJob objects."""
    jobs = []
    for jd in profile.get("jobs", []):
        jobs.append(BenchmarkJob(
            id=jd["id"],
            company=jd.get("company", ""),
            title=jd.get("title", ""),
            description=jd.get("description", ""),
            location=jd.get("location", ""),
            experience_required=jd.get("experience_required", ""),
            remote=jd.get("remote", False),
            salary=jd.get("salary", ""),
            relevant=jd.get("relevant", False),
            expected_tier=jd.get("expected_tier"),
            expected_score_min=jd.get("expected_score_min"),
            expected_score_max=jd.get("expected_score_max"),
            expected_rank_min=jd.get("expected_rank_min"),
            expected_rank_max=jd.get("expected_rank_max"),
        ))
    return jobs


# ── Scorer wrapper ───────────────────────────────────────────────────────────

class _BenchJob:
    """Minimal object matching Job's interface used by the retriever."""
    def __init__(self, j: BenchmarkJob):
        self.id = f"bench-{j.id}"
        self.company = j.company
        self.title = j.title
        self.description = j.description
        self.location = j.location
        self.remote = j.remote
        self.salary = j.salary
        self.experience_required = j.experience_required
        self.skills = []
        self.url = f"https://benchmark/{j.id}"
        self.source = "benchmark"
        self.posted_at = datetime.utcnow()
        self.raw_html = ""


def score_job(job: BenchmarkJob, skills: list[str], experience_years: int) -> dict:
    """Run the new retriever + ranker pipeline on a benchmark job. Returns score dict."""
    from ai.retriever import retrieve
    from ai.ranker import rank

    bj = _BenchJob(job)
    opp = retrieve(bj, skills, experience_years)
    if opp is None:
        return {
            "score": 0,
            "tier": "ignore",
            "retrieved": False,
            "overlap": 0.0,
            "matched": [],
            "experience_match": 0.0,
            "experience_reason": "",
            "location_match": 0.0,
            "keyword_score": 0,
            "freshness": 0.0,
            "seniority_mult": 0.0,
            "seniority_reason": "",
        }

    ranked = rank([opp], skills, experience_years)
    r = ranked[0] if ranked else None
    if r is None:
        score = int(opp.retrieval_score * 100)
        tier = _assign_tier(score)
        return {
            "score": score,
            "tier": tier,
            "retrieved": tier not in ("ignore",),
            "overlap": opp.skill_overlap,
            "matched": opp.matched_skills,
            "experience_match": opp.score_vector.experience_fit,
            "experience_reason": "",
            "location_match": opp.score_vector.location_fit,
            "keyword_score": 0,
            "freshness": opp.freshness,
            "seniority_mult": 1.0,
            "seniority_reason": opp.seniority,
        }

    return {
        "score": r.composite_score(),
        "tier": r.tier(),
        "retrieved": r.tier() not in ("ignore",),
        "overlap": opp.skill_overlap,
        "matched": opp.matched_skills,
        "experience_match": opp.score_vector.experience_fit,
        "experience_reason": "",
        "location_match": opp.score_vector.location_fit,
        "keyword_score": 0,
        "freshness": opp.freshness,
        "seniority_mult": 1.0,
        "seniority_reason": opp.seniority,
    }


def _assign_tier(score: int) -> str:
    if score >= 90:
        return "apply_now"
    elif score >= 75:
        return "strong_match"
    elif score >= 60:
        return "worth_trying"
    elif score >= 45:
        return "stretch"
    return "ignore"


# ── Registry benchmark ───────────────────────────────────────────────────────

def run_registry_benchmark() -> BenchmarkResult:
    """Check registry coverage against expected company counts."""
    stats = get_stats()
    result = BenchmarkResult(name="Registry", status="PASS")
    result.metrics = {
        "companies": stats["companies"],
        "active": stats["active"],
        "aliases": stats["aliases"],
        "categories": stats["categories"],
    }
    result.details.append(f"Companies: {stats['companies']}")
    result.details.append(f"Active: {stats['active']}")
    result.details.append(f"Aliases: {stats['aliases']}")
    result.details.append(f"Categories: {stats['categories']}")
    return result


def run_registry_coverage(profile: dict) -> BenchmarkResult:
    """Validate registry coverage matches expected counts from a profile."""
    name = profile["name"]
    result = BenchmarkResult(name=f"Coverage: {name}", status="PASS")
    expected = profile.get("registry", {})
    if not expected:
        result.status = "SKIP"
        return result

    for key, expected_count in expected.items():
        actual = 0
        if key.startswith("category:"):
            cat = key.split(":", 1)[1]
            actual = len(get_by_category(cat))
        elif key.startswith("tech:"):
            tech = key.split(":", 1)[1]
            actual = len(get_by_technology(tech))
        elif key == "fresher_friendly":
            actual = len(get_fresher_friendly())
        elif key == "total":
            actual = len(get_all())

        if actual >= expected_count:
            result.details.append(f"  [green]PASS[/green] {key}: {actual} (expected >= {expected_count})")
        else:
            result.status = "FAIL"
            result.details.append(f"  [red]FAIL[/red] {key}: {actual} (expected >= {expected_count})")

    return result


# ── Retrieval benchmark ──────────────────────────────────────────────────────

def run_retrieval_benchmark(profile: dict) -> BenchmarkResult:
    """Evaluate retrieval: recall, precision, MRR, top-10 accuracy."""
    name = profile["name"]
    resume = profile.get("resume", "backend_v3")
    skills = profile.get("skills", [])
    exp_years = profile.get("experience_years", 1)
    jobs = jobs_from_profile(profile)

    result = BenchmarkResult(name=f"Retrieval: {name}", status="PASS")

    # Score all jobs
    scored = []
    for job in jobs:
        s = score_job(job, skills, exp_years)
        scored.append((job, s))

    # Ground truth: relevant jobs that should be retrieved
    relevant = [j for j in jobs if j.relevant]
    irrelevant = [j for j in jobs if not j.relevant]

    # Retrieved jobs (not scored as "ignore")
    retrieved_ids = {j.id for j, s in scored if s["retrieved"]}
    relevant_ids = {j.id for j in relevant}
    irrelevant_ids = {j.id for j in irrelevant}

    # True positives / false negatives / false positives
    tp = len(relevant_ids & retrieved_ids)
    fn = len(relevant_ids - retrieved_ids)
    fp = len(irrelevant_ids & retrieved_ids)

    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0

    result.metrics["recall"] = round(recall, 3)
    result.metrics["precision"] = round(precision, 3)
    result.metrics["f1"] = round(f1, 3)

    # MRR: Mean Reciprocal Rank of first relevant result
    sorted_by_score = sorted(scored, key=lambda x: x[1]["score"], reverse=True)
    relevant_ranked = [i + 1 for i, (j, s) in enumerate(sorted_by_score) if j.relevant]
    mrr = sum(1.0 / r for r in relevant_ranked) / len(relevant_ranked) if relevant_ranked else 0.0
    result.metrics["mrr"] = round(mrr, 3)

    # Top-10 accuracy
    top_10 = sorted_by_score[:10]
    top_10_relevant = sum(1 for j, s in top_10 if j.relevant)
    top_10_acc = top_10_relevant / min(10, len(top_10)) if top_10 else 0.0
    result.metrics["top_10_accuracy"] = round(top_10_acc, 3)

    # Check against thresholds
    min_recall = profile.get("min_recall", 0.75)
    min_precision = profile.get("min_precision", 0.70)
    min_top10 = profile.get("min_top10", 0.80)

    result.details.append(f"  Recall: {recall:.1%} (threshold: {min_recall:.0%}) {'[green]PASS[/green]' if recall >= min_recall else '[red]FAIL[/red]'}")
    result.details.append(f"  Precision: {precision:.1%} (threshold: {min_precision:.0%}) {'[green]PASS[/green]' if precision >= min_precision else '[red]FAIL[/red]'}")
    result.details.append(f"  F1: {f1:.1%}")
    result.details.append(f"  MRR: {mrr:.3f}")
    result.details.append(f"  Top-10 Accuracy: {top_10_acc:.1%} (threshold: {min_top10:.0%}) {'[green]PASS[/green]' if top_10_acc >= min_top10 else '[red]FAIL[/red]'}")

    if tp + fn > 0:
        result.details.append(f"\n  True Positives: {tp}  False Negatives: {fn}  False Positives: {fp}")

    # Failures
    if recall < min_recall or precision < min_precision or top_10_acc < min_top10:
        result.status = "FAIL"

    # Show missed relevant jobs
    missed = relevant_ids - retrieved_ids
    if missed:
        result.details.append(f"\n  [yellow]Missed relevant:[/yellow]")
        for jid in sorted(missed):
            j = next(j for j in relevant if j.id == jid)
            result.details.append(f"    {j.company} - {j.title}")

    # Show false positives
    false_pos = irrelevant_ids & retrieved_ids
    if false_pos:
        result.details.append(f"\n  [yellow]False positives:[/yellow]")
        for jid in sorted(false_pos):
            j = next(j for j in irrelevant if j.id == jid)
            result.details.append(f"    {j.company} - {j.title}")

    return result


# ── Ranker benchmark ─────────────────────────────────────────────────────────

def run_ranker_benchmark(profile: dict) -> BenchmarkResult:
    """Evaluate ranking accuracy against expected ordering."""
    name = profile["name"]
    skills = profile.get("skills", [])
    exp_years = profile.get("experience_years", 1)
    jobs = jobs_from_profile(profile)

    result = BenchmarkResult(name=f"Ranker: {name}", status="PASS")

    # Score and rank
    scored = []
    for job in jobs:
        s = score_job(job, skills, exp_years)
        scored.append((job, s))

    sorted_jobs = sorted(scored, key=lambda x: x[1]["score"], reverse=True)
    ordered_ids = [j.id for j, s in sorted_jobs]

    # Check rank expectations
    rank_errors = 0
    rank_checks = 0
    for job in jobs:
        if job.expected_rank_min is not None or job.expected_rank_max is not None:
            rank_checks += 1
            rank = ordered_ids.index(job.id) + 1 if job.id in ordered_ids else -1
            lo = job.expected_rank_min or 1
            hi = job.expected_rank_max or len(jobs)
            in_range = lo <= rank <= hi
            if not in_range:
                rank_errors += 1
                result.details.append(f"  [red]FAIL[/red] {job.company} - {job.title}: rank {rank}, expected {lo}-{hi}")

    rank_accuracy = 1.0 - (rank_errors / rank_checks) if rank_checks > 0 else 1.0
    result.metrics["rank_accuracy"] = round(rank_accuracy, 3)

    if rank_checks > 0:
        result.details.append(f"\n  Rank accuracy: {rank_accuracy:.1%} ({rank_checks - rank_errors}/{rank_checks}) {'[green]PASS[/green]' if rank_accuracy >= 0.8 else '[red]FAIL[/red]'}")
        if rank_accuracy < 0.8:
            result.status = "FAIL"

    return result


# ── Score accuracy benchmark ─────────────────────────────────────────────────

def run_score_benchmark(profile: dict) -> BenchmarkResult:
    """Validate individual job scores against expected ranges."""
    name = profile["name"]
    skills = profile.get("skills", [])
    exp_years = profile.get("experience_years", 1)
    jobs = jobs_from_profile(profile)

    result = BenchmarkResult(name=f"Scores: {name}", status="PASS")

    score_errors = 0
    tier_errors = 0
    score_checks = 0
    tier_checks = 0

    for job in jobs:
        s = score_job(job, skills, exp_years)

        # Score range check
        if job.expected_score_min is not None:
            score_checks += 1
            if s["score"] < job.expected_score_min:
                score_errors += 1
                result.details.append(f"  [red]FAIL[/red] {job.company} - {job.title}: score {s['score']} < min {job.expected_score_min}")
        if job.expected_score_max is not None:
            if s["score"] > job.expected_score_max:
                score_errors += 1
                result.details.append(f"  [red]FAIL[/red] {job.company} - {job.title}: score {s['score']} > max {job.expected_score_max}")

        # Tier check
        if job.expected_tier:
            tier_checks += 1
            if s["tier"] != job.expected_tier:
                tier_errors += 1
                result.details.append(f"  [yellow]TIER MISMATCH[/yellow] {job.company} - {job.title}: expected {job.expected_tier}, got {s['tier']} (score {s['score']})")

    min_score_acc = profile.get("min_score_accuracy", 0.85)
    if score_checks > 0:
        score_acc = 1.0 - (score_errors / score_checks) if score_checks > 0 else 1.0
        result.metrics["score_accuracy"] = round(score_acc, 3)
        result.details.append(f"  Score accuracy: {score_acc:.1%} ({score_checks - score_errors}/{score_checks}) {'[green]PASS[/green]' if score_acc >= min_score_acc else '[red]FAIL[/red]'}")
        if score_acc < min_score_acc:
            result.status = "FAIL"

    if tier_checks > 0:
        tier_acc = 1.0 - (tier_errors / tier_checks) if tier_checks > 0 else 1.0
        result.metrics["tier_accuracy"] = round(tier_acc, 3)
        result.details.append(f"  Tier accuracy: {tier_acc:.1%} ({tier_checks - tier_errors}/{tier_checks}) {'[green]PASS[/green]' if tier_acc >= 0.80 else '[red]FAIL[/red]'}")
        if tier_acc < 0.80:
            result.status = "FAIL"

    return result


# ── Overall runner ───────────────────────────────────────────────────────────

def run_all(profile_names: list[str] | None = None) -> list[BenchmarkResult]:
    """Run all benchmarks. Optionally filter by profile name."""
    results = []

    # 1. Registry benchmark
    results.append(run_registry_benchmark())

    # 2. Profile-based benchmarks
    profiles = load_profiles()
    for profile in profiles:
        if profile_names and profile["name"] not in profile_names:
            continue
        results.append(run_registry_coverage(profile))
        results.append(run_retrieval_benchmark(profile))
        results.append(run_ranker_benchmark(profile))
        results.append(run_score_benchmark(profile))

    return results


def print_results(results: list[BenchmarkResult]):
    """Pretty-print benchmark results."""
    passed = 0
    failed = 0
    skipped = 0
    errored = 0

    for r in results:
        if r.status == "PASS":
            passed += 1
            icon = "[green]PASS[/green]"
        elif r.status == "FAIL":
            failed += 1
            icon = "[red]FAIL[/red]"
        elif r.status == "SKIP":
            skipped += 1
            icon = "[yellow]SKIP[/yellow]"
        else:
            errored += 1
            icon = f"[red]ERROR[/red]"

        console.print(f"\n[bold]{icon}[/bold]  {r.name}")

        for detail in r.details:
            console.print(f"  {detail}")

        if r.metrics:
            metrics_str = " | ".join(f"{k}: {v}" for k, v in r.metrics.items())
            console.print(f"  [dim]{metrics_str}[/dim]")

        if r.error:
            console.print(f"  [red]{r.error}[/red]")

    console.print(f"\n{'='*50}")
    console.print(f"[bold]Results:[/bold] {passed} passed, {failed} failed, {skipped} skipped, {errored} errors")
    if failed == 0 and errored == 0:
        console.print("[bold green]All benchmarks PASSED[/bold green]")
    else:
        console.print(f"[bold red]{failed + errored} benchmark(s) FAILED[/bold red]")
