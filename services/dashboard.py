"""Founder Dashboard — aggregate metrics from poll events + job data.

All metrics are derived from event tables (PollRun, Event) and
current state (Job, Application, Company). Nothing is stored —
everything is computed on demand.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from database.connection import get_session
from database.models import Job, Application, Company, PollRun, Event
from sqlalchemy import func, text


def founder_dashboard() -> dict[str, Any]:
    """Return all dashboard metrics as a nested dict."""
    today = datetime.utcnow().date()
    today_start = datetime(today.year, today.month, today.day)
    today_end = today_start + timedelta(days=1)

    session = get_session()
    try:
        # ── 1. Supply Metrics (today) ────────────────────────────────
        poll_runs_today = session.query(PollRun).filter(
            PollRun.finished_at >= today_start,
            PollRun.finished_at < today_end,
        ).all()

        companies_checked = len(set(p.company_id for p in poll_runs_today))
        companies_succeeded = sum(1 for p in poll_runs_today if p.status == "success")
        companies_failed = sum(1 for p in poll_runs_today if p.status in ("error", "empty"))
        jobs_found_today = sum(p.jobs_new for p in poll_runs_today)
        total_errors = sum(p.parse_errors for p in poll_runs_today)

        # ── 2. Total Pipeline ────────────────────────────────────────
        total_jobs = session.query(func.count(Job.id)).scalar() or 0
        eligible_jobs = session.query(func.count(Job.id)).filter(Job.eligible == True).scalar() or 0
        total_applications = session.query(func.count(Application.id)).scalar() or 0
        total_applied = (
            session.query(func.count(Application.id))
            .filter(Application.status.in_(["submitted", "applied", "in_progress"]))
            .scalar() or 0
        )

        # Recommended = applications with score above threshold
        recommended = (
            session.query(func.count(Application.id))
            .filter(Application.score >= 40)
            .scalar() or 0
        )

        # Interview + offer from events
        interviews = (
            session.query(func.count(Event.id))
            .filter(Event.event_type == "interview_scheduled")
            .scalar() or 0
        )
        offers = (
            session.query(func.count(Event.id))
            .filter(Event.event_type == "offer_received")
            .scalar() or 0
        )

        # ── 3. New Jobs Today ────────────────────────────────────────
        new_jobs_today = (
            session.query(func.count(Job.id))
            .filter(Job.created_at >= today_start, Job.created_at < today_end)
            .scalar() or 0
        )
        duplicate_today = (
            session.query(func.count(Job.id))
            .filter(
                Job.eligibility_reason.like("%duplicate%"),
                Job.created_at >= today_start,
                Job.created_at < today_end,
            )
            .scalar() or 0
        )

        # ── 4. Category Mix ──────────────────────────────────────────
        category_counts: Counter = Counter()
        if total_jobs > 0:
            rows = (
                session.query(Company.primary_category, func.count(Job.id))
                .select_from(Job)
                .join(Company, Job.company == Company.name)
                .group_by(Company.primary_category)
                .all()
            )
            for cat, cnt in rows:
                category_counts[cat] = cnt

        # ── 5. ATS Distribution ──────────────────────────────────────
        ats_counts: Counter = Counter()
        if total_jobs > 0:
            rows = (
                session.query(Company.ats, func.count(Job.id))
                .select_from(Job)
                .join(Company, Job.company == Company.name)
                .group_by(Company.ats)
                .all()
            )
            for ats, cnt in rows:
                ats_counts[ats] = cnt

        # ── 6. Top Skills ────────────────────────────────────────────
        # Aggregate skills from all jobs (JSON field)
        skill_counter: Counter = Counter()
        if total_jobs > 0:
            job_skills = (
                session.query(Job.skills)
                .filter(Job.skills.isnot(None), Job.skills != "[]")
                .limit(500)
                .all()
            )
            for (skills,) in job_skills:
                if isinstance(skills, list):
                    for s in skills:
                        if isinstance(s, str):
                            skill_counter[s.lower()] += 1

        # ── 7. Freshness ────────────────────────────────────────────
        today_jobs = 0
        yesterday_jobs = 0
        this_week_jobs = 0
        older_jobs = 0
        if total_jobs > 0:
            yesterday_start = today_start - timedelta(days=1)
            week_start = today_start - timedelta(days=7)
            today_jobs = (
                session.query(func.count(Job.id))
                .filter(Job.posted_at >= today_start)
                .scalar() or 0
            )
            yesterday_jobs = (
                session.query(func.count(Job.id))
                .filter(Job.posted_at >= yesterday_start, Job.posted_at < today_start)
                .scalar() or 0
            )
            this_week_jobs = (
                session.query(func.count(Job.id))
                .filter(Job.posted_at >= week_start, Job.posted_at < yesterday_start)
                .scalar() or 0
            )
            older_jobs = (
                session.query(func.count(Job.id))
                .filter(Job.posted_at < week_start)
                .scalar() or 0
            )

        # ── 8. Source ROI ────────────────────────────────────────────
        source_roi: list[dict] = []
        if total_jobs > 0:
            rows = (
                session.query(Company.ats, func.count(Job.id))
                .select_from(Job)
                .join(Company, Job.company == Company.name)
                .group_by(Company.ats)
                .all()
            )
            for ats_name, job_cnt in rows:
                # Eligible count per source
                eligible_cnt = (
                    session.query(func.count(Job.id))
                    .select_from(Job)
                    .join(Company, Job.company == Company.name)
                    .filter(Company.ats == ats_name, Job.eligible == True)
                    .scalar() or 0
                )
                # Applied per source (from jobs -> applications)
                applied_cnt = (
                    session.query(func.count(Application.id))
                    .select_from(Application)
                    .join(Job)
                    .join(Company, Job.company == Company.name)
                    .filter(
                        Company.ats == ats_name,
                        Application.status.in_(["submitted", "applied", "in_progress"]),
                    )
                    .scalar() or 0
                )
                source_roi.append({
                    "source": ats_name or "unknown",
                    "jobs": job_cnt,
                    "eligible": eligible_cnt,
                    "applied": applied_cnt,
                })

        # ── Compile ─────────────────────────────────────────────────
        eligible_pct = round(eligible_jobs / total_jobs * 100) if total_jobs else 0

        return {
            "supply": {
                "companies_checked": companies_checked,
                "companies_succeeded": companies_succeeded,
                "companies_failed": companies_failed,
                "jobs_found_today": jobs_found_today,
                "new_jobs_today": new_jobs_today,
                "duplicate_today": duplicate_today,
                "parse_errors": total_errors,
            },
            "pipeline": {
                "jobs_discovered": total_jobs,
                "eligible": eligible_jobs,
                "eligible_pct": eligible_pct,
                "recommended": recommended,
                "applied": total_applied,
                "interviews": interviews,
                "offers": offers,
            },
            "freshness": {
                "today": today_jobs,
                "yesterday": yesterday_jobs,
                "this_week": this_week_jobs,
                "older": older_jobs,
            },
            "categories": dict(category_counts.most_common()),
            "ats_distribution": dict(ats_counts.most_common()),
            "top_skills": skill_counter.most_common(10),
            "source_roi": source_roi,
            "quotas": [],
        }
        try:
            from services.quotas import quota_usage
            data["quotas"] = quota_usage()
        except Exception:
            pass
        return data
    finally:
        session.close()


def _generate_suggestions(data: dict) -> list[str]:
    """Generate actionable suggestions from dashboard data."""
    suggestions = []
    supply = data["supply"]
    pipeline = data["pipeline"]
    ats_dist = data["ats_distribution"]

    # Parser issues
    if supply["companies_failed"] > 0:
        fail_pct = supply["companies_failed"] / max(supply["companies_checked"], 1) * 100
        if fail_pct > 20:
            suggestions.append(
                f"[red]High failure rate ({supply['companies_failed']}/{supply['companies_checked']} "
                f"= {fail_pct:.0f}%). Check failed companies for URL/ATS issues.[/red]"
            )

    # Parse errors
    if supply["parse_errors"] > 10:
        suggestions.append(
            f"[yellow]{supply['parse_errors']} parse errors today. Check ATS parsers.[/yellow]"
        )

    # Duplicate rate
    if supply["duplicate_today"] > 0 and supply["new_jobs_today"] > 0:
        dup_pct = supply["duplicate_today"] / (supply["new_jobs_today"] + supply["duplicate_today"]) * 100
        if dup_pct > 50:
            suggestions.append(
                f"[yellow]High duplicate rate ({dup_pct:.0f}%). Dedup may need tuning.[/yellow]"
            )

    # ATS gaps
    generic_count = ats_dist.get("generic", ats_dist.get("", 0))
    total_ats = sum(ats_dist.values()) or 1
    if generic_count / total_ats > 0.3:
        suggestions.append(
            "[yellow]30%+ of jobs use generic parser. Add ATS-specific parsers for better coverage.[/yellow]"
        )

    # Pipeline bottleneck
    if pipeline["eligible"] > 0 and pipeline["applied"] == 0:
        suggestions.append("[bold]No applications submitted. Run [green]jobzo review[/green] to start applying.[/bold]")
    elif pipeline["recommended"] > pipeline["applied"] > 0:
        gap = pipeline["recommended"] - pipeline["applied"]
        suggestions.append(
            f"[yellow]{gap} recommended jobs not yet applied to. "
            f"Run [green]jobzo review[/green] to process them.[/yellow]"
        )

    # Supply gap
    if pipeline["jobs_discovered"] == 0:
        suggestions.append("[bold]No jobs yet. Run [green]jobzo collect[/green] to populate the pipeline.[/bold]")
    elif pipeline["eligible"] == 0:
        suggestions.append(
            "[yellow]All jobs filtered out. Check eligibility rules "
            "(experience, visa, location, company filters).[/yellow]"
        )

    if not suggestions:
        suggestions.append("[green]All systems nominal.[/green]")

    return suggestions


def print_dashboard(data: dict[str, Any]):
    """Print the Founder Dashboard to the console."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

    c = Console()

    supply = data["supply"]
    pipeline = data["pipeline"]
    freshness = data["freshness"]
    categories = data["categories"]
    ats_dist = data["ats_distribution"]
    top_skills = data["top_skills"]
    source_roi = data["source_roi"]

    total_checked = supply["companies_checked"]

    # ── Header ─────────────────────────────────────────────────────
    c.print()
    c.print("[bold cyan]========== Founder Dashboard ==========[/bold cyan]")
    c.print()

    # ── Supply + Pipeline ───────────────────────────────────────────
    t = Table(box=box.SIMPLE, padding=(0, 2))
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")

    t.add_row("Companies Checked (today)", str(total_checked))
    t.add_row("  Successful", str(supply["companies_succeeded"]))
    t.add_row("  Failed", str(supply["companies_failed"]))
    t.add_row("  Parse Errors", str(supply["parse_errors"]))
    t.add_row("")
    t.add_row("Jobs Found (today)", str(supply["jobs_found_today"]))
    t.add_row("  New", str(supply["new_jobs_today"]))
    t.add_row("  Duplicates", str(supply["duplicate_today"]))
    t.add_row("")
    t.add_row("[bold]Jobs Discovered (total)[/bold]", str(pipeline["jobs_discovered"]))
    t.add_row(f"[bold]Eligible[/bold]", f"{pipeline['eligible']} ({pipeline['eligible_pct']}%)")
    t.add_row("[bold]Recommended[/bold]", str(pipeline["recommended"]))
    t.add_row("[bold]Applied[/bold]", str(pipeline["applied"]))
    t.add_row("[bold]Interviews[/bold]", str(pipeline["interviews"]))
    t.add_row("[bold]Offers[/bold]", str(pipeline["offers"]))
    c.print(t)

    # ── Freshness ──────────────────────────────────────────────────
    t2 = Table(box=box.SIMPLE, padding=(0, 2))
    t2.add_column("Posted", style="bold")
    t2.add_column("Jobs", justify="right")
    t2.add_row("Today", str(freshness["today"]))
    t2.add_row("Yesterday", str(freshness["yesterday"]))
    t2.add_row("This Week", str(freshness["this_week"]))
    t2.add_row("Older", str(freshness["older"]))
    c.print("[bold]\n  Freshness[/bold]")
    c.print(t2)

    # ── Category Mix ────────────────────────────────────────────────
    c.print("[bold]\n  Category Mix[/bold]")
    if categories:
        total_cat = sum(categories.values())
        t3 = Table(box=box.SIMPLE, padding=(0, 2))
        t3.add_column("Category", style="bold")
        t3.add_column("Jobs", justify="right")
        t3.add_column("%", justify="right")
        for cat, cnt in categories.items():
            pct = round(cnt / total_cat * 100)
            t3.add_row(cat.replace("_", " ").title(), str(cnt), f"{pct}%")
        c.print(t3)
    else:
        c.print("  (no data)")

    # ── ATS Distribution ────────────────────────────────────────────
    c.print("[bold]\n  ATS Distribution[/bold]")
    if ats_dist:
        t4 = Table(box=box.SIMPLE, padding=(0, 2))
        t4.add_column("ATS", style="bold")
        t4.add_column("Jobs", justify="right")
        for ats_name, cnt in ats_dist.items():
            t4.add_row(ats_name or "unknown", str(cnt))
        c.print(t4)
    else:
        c.print("  (no data)")

    # ── Top Skills ──────────────────────────────────────────────────
    c.print("[bold]\n  Top Skills in Market[/bold]")
    if top_skills:
        t5 = Table(box=box.SIMPLE, padding=(0, 2))
        t5.add_column("Skill", style="bold")
        t5.add_column("Jobs", justify="right")
        for skill, cnt in top_skills:
            t5.add_row(skill, str(cnt))
        c.print(t5)
    else:
        c.print("  (no data — run collector first)")

    # ── Source ROI ──────────────────────────────────────────────────
    c.print("[bold]\n  Source ROI[/bold]")
    if source_roi:
        t6 = Table(box=box.SIMPLE, padding=(0, 2))
        t6.add_column("Source", style="bold")
        t6.add_column("Jobs", justify="right")
        t6.add_column("Eligible", justify="right")
        t6.add_column("Applied", justify="right")
        for entry in source_roi:
            t6.add_row(
                entry["source"].replace("_", " ").title(),
                str(entry["jobs"]),
                str(entry["eligible"]),
                str(entry["applied"]),
            )
        c.print(t6)
    else:
        c.print("  (no data)")

    # ── Platform Quotas ────────────────────────────────────────────
    quotas = data.get("quotas", [])
    if quotas:
        exhausted = [q for q in quotas if q["exhausted"]]
        active = [q for q in quotas if q["limit"] is not None]
        if active:
            c.print("[bold]\n  Platform Quotas (this week)[/bold]")
            tq = Table(box=box.SIMPLE, padding=(0, 2))
            tq.add_column("Platform", style="bold")
            tq.add_column("Used", justify="right")
            tq.add_column("Limit", justify="right")
            tq.add_column("Remaining", justify="right")
            for q in active:
                remaining = str(q["remaining"]) if q["remaining"] is not None else "—"
                style = "red" if q["exhausted"] else "green"
                tq.add_row(q["platform"].replace("_", " ").title(), str(q["used"]), str(q["limit"] or "—"), f"[{style}]{remaining}[/{style}]")
            c.print(tq)
        if exhausted:
            names = ", ".join(e["platform"].replace("_", " ").title() for e in exhausted)
            c.print(f"  [yellow]⚠ {names} exhausted — explore other sources[/yellow]")

    # ── Top Hiring Priorities ───────────────────────────────────────
    from services.hiring_weight import rank_by_weight
    top = rank_by_weight(limit=10)
    if top:
        c.print("[bold]\n  Top Hiring Priorities[/bold]")
        t7 = Table(box=box.SIMPLE, padding=(0, 2))
        t7.add_column("Company", style="bold")
        t7.add_column("Category")
        t7.add_column("Weight", justify="right")
        t7.add_column("Interval", justify="right")
        t7.add_column("Label")
        for entry in top:
            t7.add_row(entry["name"], entry["category"].replace("_", " ").title(),
                       str(entry["weight"]), f"{entry['interval_h']}h", entry["label"])
        c.print(t7)

    # ── Portfolio Risk ──────────────────────────────────────────────
    from services.portfolio import portfolio_risk
    pr = portfolio_risk()
    if pr.get("total_applications", 0) > 0:
        c.print("[bold]\n  Portfolio Risk[/bold]")
        risk_color = {"low": "green", "medium": "yellow", "high": "red"}
        c.print(f"  Risk: [{risk_color.get(pr['risk_level'], 'white')}]{pr['risk_level'].upper()}[/{risk_color.get(pr['risk_level'], 'white')}]")
        if pr.get("distribution"):
            t8 = Table(box=box.SIMPLE, padding=(0, 2))
            t8.add_column("Category", style="bold")
            t8.add_column("Apps", justify="right")
            t8.add_column("%", justify="right")
            for cat, info in pr["distribution"].items():
                t8.add_row(cat.replace("_", " ").title(), str(info["count"]), f"{info['pct']}%")
            c.print(t8)
        for rec in pr.get("recommendations", []):
            c.print(f"  • {rec}")

    # ── Suggestions ─────────────────────────────────────────────────
    c.print("[bold]\n  Actions[/bold]")
    suggestions = _generate_suggestions(data)
    for s in suggestions:
        c.print(f"  • {s}")

    c.print()
    c.print("[dim]Last updated: {}[/dim]".format(
        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    )
    c.print()
