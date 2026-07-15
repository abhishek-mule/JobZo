"""Mission Engine — guided daily workflow orchestrator.

The primary interface for JobZo. Instead of 20+ commands, the user runs
`jobzo` and gets an inbox with prioritized actions. Everything else is internal.
"""

from __future__ import annotations
import asyncio
import logging
import re
import webbrowser
from datetime import datetime, timedelta
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt

from database.connection import get_session
from database.models import Application, Job, Contact
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from resumes.registry import get_registry
from resumes.prepare import prepare
from resumes.skill_roadmap import build_roadmap
from tracker.applications import transition_status
from tracker.tasks import list_pending_tasks, complete_task, list_overdue_tasks
from tracker.personal import learn_weights, resume_stats
from tracker.reach import find_contact, generate_email_draft, log_interaction

from ai.cover_letter import generate_cover_letter
from services.collector import collect_all
from ai.scorer import score_pending_jobs
from mission.inbox import build_inbox, InboxItem, inbox_summary, build_timeline, TimelineEvent

logger = logging.getLogger("jobzo.mission")
console = Console()


def _profile_name() -> str:
    try:
        import yaml
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "resumes" / "master" / "profile.yaml"
        if profile_path.exists():
            with open(profile_path) as f:
                p = yaml.safe_load(f)
            if p and p.get("name"):
                return p["name"].split()[0]
    except Exception:
        pass
    return "there"


def _greeting() -> str:
    hour = datetime.utcnow().hour
    if hour < 12:
        return "Good Morning"
    elif hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def _weekly_stats() -> list[str]:
    """Build a one-liner weekly stats line for the dashboard header."""
    session = get_session()
    try:
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        submitted = session.query(func.count(Application.id)).filter(
            Application.status.in_(["submitted", "interview", "offer", "rejected"]),
        ).scalar() or 0
        interviews = session.query(func.count(Application.id)).filter(
            Application.status.in_(["interview", "offer"]),
        ).scalar() or 0
        week_apps = session.query(func.count(Application.id)).filter(
            Application.created_at >= week_ago,
            Application.status.in_(["submitted", "interview", "offer", "rejected"]),
        ).scalar() or 0
        offers = session.query(func.count(Application.id)).filter(
            Application.status == "offer",
        ).scalar() or 0
        response_rate = round(interviews / submitted * 100, 1) if submitted > 0 else 0.0
        parts = [f"Applied [bold]{submitted}[/bold]", f"Interviews [bold]{interviews}[/bold]"]
        if offers:
            parts.append(f"Offers [bold green]{offers}[/bold green]")
        if response_rate:
            style = "green" if response_rate >= 15 else "yellow" if response_rate >= 5 else "red"
            parts.append(f"Rate [{style}]{response_rate}%[/{style}]")
        return parts
    finally:
        session.close()


def _briefing(items: list[InboxItem]) -> list[str]:
    """Build the morning briefing line."""
    if not items:
        return ["All caught up! Run sync to find new jobs."]

    total_min = 0
    interviews = 0
    followups = 0
    reviews = 0
    for item in items[:10]:
        if item.time_required:
            parts = item.time_required.split()
            if len(parts) == 2 and parts[1] in ("min", "min"):
                total_min += int(parts[0])
            elif len(parts) == 2 and parts[1] == "h":
                total_min += int(parts[0]) * 60
            elif len(parts) == 2 and parts[1] == "sec":
                total_min += 1  # round up
        if item.category == "interview":
            interviews += 1
        elif item.category == "followup":
            followups += 1
        elif item.category in ("review", "apply"):
            reviews += 1

    lines = []
    if reviews:
        lines.append(f"Review {reviews} {'opportunity' if reviews == 1 else 'opportunities'}")
    if interviews:
        lines.append(f"Prepare {interviews} {'interview' if interviews == 1 else 'interviews'}")
    if followups:
        lines.append(f"Send {followups} {'follow-up' if followups == 1 else 'follow-ups'}")
    summary = " \u00b7 ".join(lines) if lines else "See inbox below"
    return [f"Today: {summary}. [dim]~{total_min} min[/dim]"]


BANNER = """[bold cyan]
       ░▒▓█▓▒░  ░▒▓██████▓▒░  ░▒▓███████▓▒░  ░▒▓████████▓▒░  ░▒▓██████▓▒░
       ░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░        ░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░
       ░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░      ░▒▓██▓▒░  ░▒▓█▓▒░░▒▓█▓▒░
       ░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓███████▓▒░     ░▒▓██▓▒░    ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░  ░▒▓██▓▒░      ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░░▒▓█▓▒░ ░▒▓█▓▒░        ░▒▓█▓▒░░▒▓█▓▒░
 ░▒▓██████▓▒░   ░▒▓██████▓▒░  ░▒▓███████▓▒░  ░▒▓████████▓▒░  ░▒▓██████▓▒░
[/bold cyan]"""


def show_dashboard() -> tuple[str | int, InboxItem | None]:
    """Render the inbox dashboard. Returns (choice, selected_item)."""
    console.clear()
    console.print(BANNER)
    name = _profile_name()
    greet = _greeting()
    stats_parts = _weekly_stats()
    items = build_inbox()

    lines = []
    lines.append(f"[bold cyan]  {greet} {name}[/bold cyan]  [dim]{' | '.join(stats_parts)}[/dim]")
    lines.append("")

    briefing = _briefing(items)
    for b in briefing:
        lines.append(f"  {b}")

    lines.append("")
    if items:
        lines.append(f"  {'─' * 46}")

        cat_icons = {"interview": "\U0001f3a4", "followup": "\U0001f4e7", "apply": "\U0001f680", "review": "\U0001f4c4", "task": "\u23f0"}

        top_items = items[:10]
        for idx, item in enumerate(top_items, 1):
            ci = cat_icons.get(item.category, "\U0001f4cc")
            score = ""
            if item.score:
                if item.score >= 80:
                    score = " \u2b50\u2b50\u2b50\u2b50\u2b50"
                elif item.score >= 60:
                    score = " \u2b50\u2b50\u2b50\u2b50"
                elif item.score >= 40:
                    score = " \u2b50\u2b50\u2b50"
                elif item.score >= 20:
                    score = " \u2b50\u2b50"
                else:
                    score = " \u2b50"

            time_r = f" [{item.time_required}]" if item.time_required else ""

            company_str = f"{item.company}" if item.company else ""
            title_str = f" \u2014 {item.role}" if item.role else ""
            header = f"{company_str}{title_str}" or item.title

            lines.append(f"  [bold]{idx}.[/bold] {ci} [bold]{header}[/bold]{score}{time_r}")

            if item.justification:
                j = _format_justification(item.justification[:3])
                if j.strip():
                    lines.append(f"      {j}")

            if item.expected_outcome:
                lines.append(f"      [dim]\u2192 {item.expected_outcome}[/dim]")
    else:
        lines.append(f"  \u2728 [dim]All caught up! Run sync to find new jobs.[/dim]")

    lines.append("")
    lines.append(f"  [bold]Quick Actions[/bold]")
    lines.append(f"  {'─' * 46}")
    lines.append(f"  [bold][s][/bold] Sync new jobs    [bold][i][/bold] Insights    [bold][r][/bold] Review all    [bold][q][/bold] Quit")

    console.print()
    console.print(Panel.fit("\n".join(lines), box=box.ROUNDED, padding=(1, 2)))
    console.print()

    max_inbox = min(len(items), 10)
    prompt_choices = [str(i) for i in range(1, max_inbox + 1)] + ["s", "i", "r", "q"]
    default_choice = "1" if max_inbox > 0 else "s"
    choice = Prompt.ask("  What should I do?", choices=prompt_choices, default=default_choice)

    if choice.isdigit():
        idx = int(choice) - 1
        return idx, items[idx] if idx < len(items) else None
    return choice, None


def _format_justification(reasons: list[str]) -> str:
    """Format justification lines with checkmark or bullet."""
    parts = []
    for r in reasons:
        r_lower = r.lower()
        if "excellent" in r_lower or "good" in r_lower or "top" in r_lower:
            parts.append(f"[green]\u2713 {r}[/green]")
        elif "miss" in r_lower or "low" in r_lower or "bad" in r_lower or "skip" in r_lower:
            parts.append(f"[red]\u26a0 {r}[/red]")
        else:
            parts.append(f"[cyan]\u25b8 {r}[/cyan]")
    return "  ".join(parts)


def _get_top_drafted(limit: int = 10) -> list[tuple[Application, Job]]:
    """Get top drafted applications with their jobs, ordered by tier then score."""
    session = get_session()
    try:
        from ai.scorer import TIER_ORDER
        apps = session.query(Application).options(
            joinedload(Application.job),
            joinedload(Application.current_decision),
        ).filter(
            Application.status.in_(["drafted", "recommended"]),
        ).order_by(Application.score.desc()).limit(limit + 20).all()
        result = []
        for a in apps:
            j = a.job
            if j:
                result.append((a, j))
        return result[:limit]
    finally:
        session.close()


def _confidence_label(score: int) -> tuple[str, str]:
    if score >= 80:
        return "\U0001f7e2 Excellent", "green"
    elif score >= 60:
        return "\U0001f7e1 Good", "yellow"
    elif score >= 40:
        return "\U0001f7e0 Fair", "orange3"
    else:
        return "\U0001f534 Low", "red"


def _top_job_why(app: Application, job: Job) -> str:
    """Short explanation of why the top job scored what it did."""
    parts = []
    if app.current_decision:
        from services.decision_snapshot import snapshot_to_inbox_data
        data = snapshot_to_inbox_data(app.current_decision)
        matched = data.get("matched_skills", [])
        missing = data.get("missing_skills", [])
        if matched:
            parts.append(f"skill overlap ({len(matched)} skills)")
        if missing:
            parts.append(f"gaps: {', '.join(missing[:3])}")
    if not parts and app.notes:
        for note in app.notes.split(" | "):
            n = note.strip()
            if n.startswith("Skill match"):
                parts.append(f"skill overlap ({n.split(':')[1].strip()})")
            elif "stretch" in n.lower() or "senior" in n.lower():
                parts.append(f"role level ({n})")
            elif "below" in n.lower():
                parts.append(f"experience gap")
    return ", ".join(parts[:3]) if parts else "check the job details"


def _score_rank(score: int) -> tuple[int, int] | None:
    """Display a single job card in the review flow."""
    from ai.scorer import TIER_LABELS

    tier_name = getattr(app, "tier", None) or "ignore"
    tier_label = TIER_LABELS.get(tier_name, tier_name)

    snapshot_data = {}
    if app.current_decision:
        from services.decision_snapshot import snapshot_to_inbox_data
        snapshot_data = snapshot_to_inbox_data(app.current_decision)

    matched = snapshot_data.get("matched_skills", [])
    missing = snapshot_data.get("missing_skills", [])
    breakdown = snapshot_data.get("score_breakdown", {})
    prob = snapshot_data.get("interview_probability", 0)
    confidence = snapshot_data.get("confidence", "Low")

    lines = []
    lines.append(f"[bold]{job.company}[/bold] \u2014 {job.title}")
    lines.append("")
    lines.append(f"  {tier_label}  Score: [bold]{app.score}[/bold]/100")

    # Score breakdown
    if breakdown:
        lines.append("")
        for label, val in breakdown.items():
            if isinstance(val, (int, float)):
                bar = "█" * int(min(val / 100 * 10, 10))
                lines.append(f"     {label:20s} {int(val):3d}/100 {bar}")

    # Ranking (#2 of 36)
    rank = _score_rank(app.score)
    if rank:
        lines.append(f"  [dim]#{rank[0]} of {rank[1]} opportunities[/dim]")
    lines.append("")

    # Matched skills
    if matched:
        lines.append(f"  [green]\u2713[/green] [bold]Matched[/bold]")
        lines.append(f"     {', '.join(matched[:4])}")
        lines.append("")

    # Missing skills
    if missing:
        lines.append(f"  [red]\u2212[/red] [bold]Missing[/bold]")
        for s in missing[:3]:
            lines.append(f"     {s}")
        lines.append("")

        # "What if I learned X?" - dynamic score estimation
        what_if = _what_if_analysis(app, job, missing[:3])
        if what_if:
            lines.append(f"  [bold]Would improve to[/bold]")
            for skill_name, projected in what_if:
                delta = projected - app.score
                lines.append(f"     {skill_name}: [bold]{projected}/100[/bold] [dim](+{delta})[/dim]")
            lines.append("")

    # Resume recommendation
    if app.resume_used:
        lines.append(f"  [bold]Resume[/bold]  {app.resume_used}")

    # Interview probability
    prob_color = "green" if prob >= 40 else "yellow" if prob >= 20 else "red"
    lines.append(f"  [bold]Competitiveness[/bold]  [{prob_color}]{prob}%[/{prob_color}]  [dim](confidence: {confidence})[/dim]")

    lines.append("")
    lines.append(f"  Source: {job.source}")

    panel = Panel.fit(
        "\n".join(lines),
        title=f"[bold cyan]\u25c0 {idx}/{total} \u25b6[/bold cyan]",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print(panel)
    return lines


def _score_rank(score: int) -> tuple[int, int] | None:
    """Return (rank, total) for this score among drafted applications."""
    from database.connection import get_session
    from database.models import Application
    session = get_session()
    try:
        total = session.query(Application).filter(
            Application.status == "drafted",
        ).count()
        if total <= 1:
            return None
        higher = session.query(Application).filter(
            Application.status == "drafted",
            Application.score > score,
        ).count()
        rank = higher + 1
        return (rank, total)
    finally:
        session.close()


def _what_if_analysis(app: Application, job: Job, missing_skills: list[str]) -> list[tuple[str, int]]:
    """Estimate score if user had each missing skill by re-scoring with the skill added.

    Uses the retriever's skill matching and experience/location scoring to simulate
    the impact of acquiring each missing skill.
    Returns list of (skill_name, projected_score).
    """
    from ai.retriever import _match_skills, _experience_match, _location_match
    from ai.skill_graph import expand
    from services.freshness import freshness_score
    from ai.scorer import SKILL_KEYWORDS

    results = []
    base_skills = list(set(s.lower() for s in SKILL_KEYWORDS))

    for skill in missing_skills:
        skill_lower = skill.lower()
        if skill_lower in base_skills:
            continue

        desc_text = (job.description + " " + job.title).lower()
        if skill_lower not in desc_text:
            continue

        # Augment skill set with this missing skill
        augmented = base_skills + [skill_lower]

        # Run skill matching with augmented skills
        job_skills = [s for s in base_skills if s in desc_text]
        expanded_resume = expand(augmented, max_depth=2, min_strength=0.3)
        expanded_job = expand(job_skills, max_depth=1, min_strength=0.3)
        matched = _match_skills(augmented, job_skills, expanded_resume, expanded_job)
        exp_match, _ = _experience_match(job.description, job.experience_required, 1)
        loc_match, _ = _location_match(job.location, job.remote)
        fresh = freshness_score(job.posted_at)

        skill_pts = int(matched["overlap"] * 50)
        fresh_pts = int(fresh * 20)
        exp_pts = int(exp_match * 20)
        loc_pts = int(loc_match * 10)
        raw_score = skill_pts + fresh_pts + exp_pts + loc_pts
        final = min(int(raw_score * 1.0), 100)

        results.append((skill.title(), final))

    return results[:3]


def _show_counterfactual(app: Application):
    """Show decision intelligence insight if enough data."""
    job = None
    session = get_session()
    try:
        job = session.get(Job, app.job_id)
    finally:
        session.close()
    if not job:
        return
    try:
        from services.decision_snapshot import snapshot_to_inbox_data

        if app.current_decision:
            data = snapshot_to_inbox_data(app.current_decision)
            prob = data.get("interview_probability", 0)
            confidence = data.get("confidence", "Low")
            matched = data.get("matched_skills", [])
            missing = data.get("missing_skills", [])
        else:
            prob, confidence, matched, missing = 0, "Low", [], []

        lines = [
            f"  Interview Probability: {prob}%",
            f"  Confidence: {confidence}",
            "",
            f"  Skills matched: {len(matched)}",
        ]
        if missing:
            lines.append(f"  Skills to learn: {', '.join(missing[:5])}")
        if app.score:
            lines.append(f"  Score: {app.score}/100 ({app.tier.replace('_', ' ').title()})")
        lines.append("")
        if missing:
            lines.append("  [yellow]Tip: Learning missing skills could boost your score.[/yellow]")

        console.print()
        console.print(Panel.fit(
            "\n".join(lines),
            box=box.ROUNDED,
            padding=(1, 2),
            title="Decision Intelligence",
        ))
    except Exception as e:
        logger.debug("Decision intelligence unavailable: %s", e)


def _action_review_jobs():
    """Interactive job review: browse by tier, apply, or skip.

    Shows ALL scored jobs grouped by tier. Never ends with "0 jobs."
    """
    from ai.scorer import TIER_LABELS, TIER_ORDER, _assign_tier

    apps = _get_top_drafted(limit=50)
    if not apps:
        console.print("[yellow]No jobs have been scored yet. Run Sync first.[/yellow]")
        input("\nPress Enter to continue...")
        return "dashboard"

    # Group by tier, preserving tier order
    grouped: dict[str, list[tuple[Application, Job]]] = {}
    for app, job in apps:
        t = getattr(app, "tier", None) or _assign_tier(app.score)
        grouped.setdefault(t, []).append((app, job))
    missing_tiers = [t for t in TIER_ORDER if t not in grouped]
    for t in missing_tiers:
        grouped[t] = []

    console.clear()
    console.print("[bold yellow]\U0001f4cb Job Review[/bold yellow]\n")
    console.print("Tiers:\n")
    for t in TIER_ORDER:
        if grouped[t]:
            console.print(f"  {TIER_LABELS.get(t, t)}  [dim]({len(grouped[t])})[/dim]")
    console.print(f"  \U0001f534 Ignore  [dim]({len(grouped[TIER_ORDER[-1]])})[/dim]\n")

    total_shown = 0
    for tier_idx, t in enumerate(TIER_ORDER):
        jobs = grouped[t]
        if not jobs:
            continue
        # Skip Ignore tier in normal flow
        if t == TIER_ORDER[-1]:
            continue

        tier_label = TIER_LABELS.get(t, t)

        console.print(Panel.fit(
            f"[bold]{tier_label}[/bold]  [dim]{len(jobs)} jobs[/dim]",
            box=box.ROUNDED, padding=(0, 1),
        ))
        console.print()

        for idx, (app, job) in enumerate(jobs, 1):
            _show_job_card(app, job, idx, len(jobs))
            console.print()

            if app.status == "drafted":
                action = input("Action [o/a/s/x/d/q]: ").strip().lower()
            else:
                action = "s"

            if action == "o":
                webbrowser.open(job.url)
                console.print(f"  [green]\u2713[/green] Opened {job.url}")
                # Show card again after opening
                console.print()
                _show_job_card(app, job, idx, len(jobs))
                console.print()
            elif action == "a":
                submitted = _run_apply_session(app)
                after_apply(submitted)
                return "dashboard"
            elif action == "s":
                console.print("  [blue]\u2713 Saved for later[/blue]")
            elif action == "x":
                transition_status(str(app.id), "skipped")
                console.print("  [red]\u2717 Skipped[/red]")
            elif action == "d":
                _show_counterfactual(app)
                console.print()
                cont = input("Continue reviewing? [y/q]: ").strip().lower()
                if cont == "q":
                    return "dashboard"
            elif action == "q":
                console.print(f"[green]\u2713[/green] Review complete")
                after_review()
                return "dashboard"
            total_shown += 1
            console.print()

        # After each non-ignore tier, ask if user wants to continue
        remaining = [
            nt for nt in TIER_ORDER[tier_idx + 1:]
            if nt != TIER_ORDER[-1] and grouped.get(nt)
        ]
        if remaining:
            cont = input(f"Continue to next tier? [Y/q]: ").strip().lower()
            if cont == "q":
                break

    if total_shown == 0:
        # All ignored — show top 5 anyway with explanation
        console.print(Panel.fit(
            "[yellow]All scored jobs are below 45/100.[/yellow]\n\n"
            f"Top job scored {apps[0][0].score}/100 — "
            + _top_job_why(apps[0][0], apps[0][1]),
            box=box.ROUNDED, padding=(1, 2),
        ))
        console.print()
        console.print("Showing top 5 anyway — apply if interested:\n")
        for idx, (app, job) in enumerate(apps[:5], 1):
            _show_job_card(app, job, idx, min(5, len(apps)))
            console.print()
            action = input("Action [o/a/s/x/q]: ").strip().lower()
            if action == "o":
                webbrowser.open(job.url)
                console.print(f"  [green]\u2713[/green] Opened")
                console.print()
            elif action == "a":
                submitted = _run_apply_session(app)
                after_apply(submitted)
                return "dashboard"

    console.print(f"[green]\u2713[/green] Review complete")
    after_review()
    return "dashboard"


def _run_apply_session(app: Application) -> bool:
    """Apply to a single job with full automation. Returns True if submitted."""
    session = get_session()
    try:
        job = session.get(Job, app.job_id)
        if not job:
            console.print("[red]Job not found[/red]")
            return False
        company = job.company
        title = job.title
        url = job.url
        description = job.description
    finally:
        session.close()

    # ── Application Preview ──────────────────────────────────────
    _show_apply_preview(app, company, title, description)
    confirm = input("\n  Submit? [Y/n]: ").strip().lower()
    if confirm == "n":
        console.print("  [blue]\u2713 Saved for later[/blue]")
        return False

    # ── Playwright check ─────────────────────────────────────────
    playwright_available = _check_playwright()
    if not playwright_available:
        console.print()
        console.print(Panel.fit(
            "[red]\u2717 Application could not be submitted.[/red]\n\n"
            "Browser automation requires Playwright.\n\n"
            "  [bold]pip install playwright[/bold]\n"
            "  [bold]playwright install chromium[/bold]\n\n"
            "Or apply manually by opening the URL in your browser.",
            box=box.ROUNDED, padding=(1, 2),
        ))
        console.print("\n[bold cyan]What would you like to do?[/bold cyan]")
        console.print("  [bold]1[/bold]. Open company page manually")
        console.print("  [bold]2[/bold]. Save as Ready to Apply")
        console.print("  [bold]3[/bold]. Cancel")
        next_c = Prompt.ask("  Choose", choices=["1", "2", "3"], default="2")
        if next_c == "1":
            webbrowser.open(url)
            transition_status(str(app.id), "ready")
            console.print(f"  [green]\u2713[/green] Opened {url}")
        elif next_c == "2":
            transition_status(str(app.id), "ready")
            console.print("  [blue]\u2713 Saved as Ready to Apply[/blue]")
        return False

    console.print(f"\n  [bold]Applying to {company} \u2014 {title}[/bold]")

    resume_path = app.resume_used or ""

    # ── Resume Manager ───────────────────────────────────────────
    if resume_path:
        # Verify resume file exists
        from pathlib import Path
        from resumes.registry import get_registry
        registry = get_registry()
        meta = registry.get(resume_path)
        file_found = False
        if meta:
            file_path = meta.file
            if Path(file_path).exists() or Path(Path(__file__).parent.parent / file_path).exists():
                file_found = True
        if not file_found:
            # Try with .pdf extension
            if Path(Path(__file__).parent.parent / "resumes" / f"{resume_path}.pdf").exists():
                file_found = True

        if not file_found:
            console.print(f"  [yellow]Resume '{resume_path}' not found.[/yellow]")
            resume_path = ""
    if not resume_path:
        # Show available resumes and let user pick
        from pathlib import Path
        resume_dir = Path(__file__).parent.parent / "resumes"
        available = sorted(p.stem for p in resume_dir.glob("*.pdf") if p.stem != "generated")
        if available:
            console.print("\n  [bold]Available resumes:[/bold]")
            for i, name in enumerate(available, 1):
                console.print(f"     [bold]{i}[/bold]. {name}.pdf")
            console.print()
            choice = Prompt.ask("  Select resume", choices=[str(i) for i in range(1, len(available) + 1)], default="1")
            resume_path = available[int(choice) - 1]
            app.resume_used = resume_path
            session_resume = get_session()
            try:
                db_app = session_resume.get(Application, app.id)
                if db_app:
                    db_app.resume_used = resume_path
                    session_resume.commit()
            finally:
                session_resume.close()
        else:
            console.print("  [red]No resume files found.[/red]")
            return False

    cover_letter = generate_cover_letter(
        company=company, role=title, description=description, resume_type=resume_path,
    )

    if app.status == "drafted":
        transition_status(str(app.id), "ready")

    async def _apply():
        from browser.assistant import BrowserAssistant, KNOWN_ATS_DOMAINS
        from services.app_log import log_application
        import time

        assistant = BrowserAssistant()
        start_ts = time.time()
        submitted = False
        ats = ""
        fields_filled = 0
        fields_total = 0
        try:
            await assistant.start()
            await assistant.navigate(url)
            for d in KNOWN_ATS_DOMAINS:
                if d in url:
                    ats = d.replace(".com", "").replace(".io", "").replace(".co", "").replace(".hq", "").title()
                    break
            form_type = await assistant.detect_form()
            if form_type == "apply_button":
                clicked = await assistant.click_apply()
                if clicked:
                    await assistant._page.wait_for_timeout(3000)
                    form_type = await assistant.detect_form()
                    if not form_type:
                        form_type = "inputs_present"
            if form_type:
                await assistant.autofill(resume_path, cover_letter, url)
                results = getattr(assistant, "_results", {})
                fields_filled = sum(1 for v in results.values() if v)
                fields_total = len(results)
                confirmed = await assistant.wait_for_confirmation()
                if confirmed:
                    transition_status(str(app.id), "submitted")
                    submitted = True
                    console.print(f"  [green]\u2713[/green] Submitted!")
                else:
                    console.print("  [yellow]Skipped[/yellow]")
            else:
                console.print("  [yellow]Form not detected. Marked as submitted.[/yellow]")
                transition_status(str(app.id), "submitted")
                submitted = True
        except Exception as e:
            logger.error("Browser automation failed: %s", e)
            console.print(f"  [red]Browser error: {e}[/red]")
        finally:
            await assistant.close()
            elapsed = int(time.time() - start_ts)
            log_application(
                company=company, ats=ats or "Unknown", time_seconds=elapsed,
                fields_filled=fields_filled, fields_manual=fields_total - fields_filled,
                resume=resume_path, cover_letter="template", submitted=submitted, title=title,
            )
            return submitted

    submitted = asyncio.run(_apply())
    return submitted


def _show_apply_preview(app: Application, company: str, title: str, description: str):
    """Show application preview before submitting."""
    missing = _missing_skills_for_desc(description)
    matched = _matched_skills_for_desc(description)
    interview_chance = ""
    if app.current_decision:
        interview_chance = f"{app.current_decision.interview_probability}%"

    lines = []
    lines.append(f"[bold cyan]Application Preview[/bold cyan]\n")
    lines.append(f"  [bold]Company[/bold]     {company}")
    lines.append(f"  [bold]Role[/bold]        {title}")
    lines.append(f"  [bold]Resume[/bold]      {app.resume_used or 'Default'}")
    if app.score:
        lines.append(f"  [bold]Score[/bold]      {app.score}/100")
    if interview_chance:
        lines.append(f"  [bold]Interview[/bold]  {interview_chance} chance")
    if missing:
        lines.append(f"\n  [bold]Missing skills[/bold]")
        for s in missing[:4]:
            lines.append(f"    [red]\u2212[/red] {s}")
    if matched:
        lines.append(f"\n  [bold]Matched skills[/bold]")
        for s in matched[:4]:
            lines.append(f"    [green]\u2713[/green] {s}")
    lines.append(f"\n  [dim]Source: {app.job.url if app.job else ''}[/dim]")

    console.print(Panel.fit("\n".join(lines), box=box.ROUNDED, padding=(1, 2)))


def _check_playwright() -> bool:
    """Return True if Playwright is available."""
    try:
        import playwright  # noqa
        return True
    except ImportError:
        return False


def _missing_skills_for_desc(description: str) -> list[str]:
    """Extract missing skills from a job description string."""
    from ai.scorer import SKILL_KEYWORDS
    desc_lower = description.lower()
    user_set = {s.lower() for s in SKILL_KEYWORDS}
    terms = [
        "kubernetes", "docker", "aws", "gcp", "azure",
        "terraform", "jenkins", "kafka", "redis", "elasticsearch",
        "mongodb", "graphql", "grpc", "react", "angular",
        "typescript", "node", "python", "django", "flask",
        "java", "spring", "golang", "rust", "kotlin",
        "postgresql", "mysql", "microservices",
    ]
    return [
        t.title() for t in terms
        if t in desc_lower and t not in user_set
    ][:5]


def _matched_skills_for_desc(description: str) -> list[str]:
    """Extract matched skills from a job description string."""
    from ai.scorer import SKILL_KEYWORDS
    desc_lower = description.lower()
    return [
        s.replace("_", " ").title()
        for s in SKILL_KEYWORDS
        if s.lower() in desc_lower
    ][:6]


def after_apply(submitted: bool = True):
    """Suggest next steps after an application attempt."""
    console.print()
    if not submitted:
        console.print("[bold cyan]What's next?[/bold cyan]")
        console.print("  1. Continue reviewing jobs")
        console.print("  2. Back to dashboard")
        console.print()
        next_c = Prompt.ask("  Choose", choices=["1", "2"], default="1")
        if next_c == "1":
            _action_review_jobs()
        return

    console.print("[bold cyan]Next Steps[/bold cyan]")
    console.print("  1. Draft recruiter email")
    console.print("  2. Prepare for interview")
    console.print("  3. Continue applying")
    console.print("  4. Back to dashboard")
    console.print()
    next_c = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="4")
    if next_c == "1":
        _draft_recruiter_email()
    elif next_c == "2":
        _action_prepare_interview()
    elif next_c == "3":
        _action_review_jobs()


def after_review():
    """Suggest next steps after reviewing jobs."""
    console.print()
    console.print("[bold cyan]What's next?[/bold cyan]")
    console.print("  1. Apply now")
    console.print("  2. Back to dashboard")
    console.print()
    next_c = Prompt.ask("  Choose", choices=["1", "2"], default="2")
    if next_c == "1":
        apps = _get_top_drafted(limit=1)
        if apps:
            submitted = _run_apply_session(apps[0][0])
            after_apply(submitted)


def _action_apply_now():
    """Apply to top ready applications."""
    session = get_session()
    try:
        apps = session.query(Application).options(
            joinedload(Application.job)
        ).filter(
            Application.status == "ready",
            Application.strategy != "skip",
        ).order_by(Application.score.desc()).limit(5).all()
    finally:
        session.close()

    if not apps:
        console.print("[yellow]No applications ready to submit. Review jobs first.[/yellow]")
        input("\nPress Enter to continue...")
        return "dashboard"

    console.clear()
    console.print("[bold yellow]🚀 Applications to Submit[/bold yellow]\n")

    for idx, app in enumerate(apps, 1):
        job = app.job
        if not job:
            continue
        console.print(f"  [bold]{idx}.[/bold] {job.company} — {job.title}")
        console.print(f"       Score: {app.score}/100  |  Resume: {app.resume_used or 'default'}")

    console.print()
    choice = Prompt.ask("  Apply to which job", choices=[str(i) for i in range(1, len(apps) + 1)] + ["q"], default="1")
    if choice == "q":
        return "dashboard"

    app = apps[int(choice) - 1]
    submitted = _run_apply_session(app)
    after_apply(submitted)
    return "dashboard"


def _action_prepare_interview():
    """Show upcoming interviews and prepare for one."""
    session = get_session()
    try:
        apps = session.query(Application).options(
            joinedload(Application.job)
        ).filter(
            Application.interview_date.isnot(None),
            Application.status.in_(["interview", "offer"]),
        ).order_by(Application.interview_date).all()
    finally:
        session.close()

    if not apps:
        console.print("[yellow]No upcoming interviews[/yellow]")
        input("\nPress Enter to continue...")
        return "dashboard"

    console.clear()
    console.print("[bold yellow]🎤 Interview Prep[/bold yellow]\n")

    for idx, app in enumerate(apps, 1):
        job = app.job
        if not job:
            continue
        iv_date = app.interview_date
        if iv_date:
            days = (iv_date - datetime.utcnow()).days
            date_str = f"{iv_date.strftime('%b %d')} ({days}d away)"
        else:
            date_str = "TBD"
        console.print(f"  [bold]{idx}.[/bold] {job.company} — {job.title}")
        console.print(f"       {date_str}  |  Score: {app.score}/100")

    console.print()
    choice = Prompt.ask("  Prepare for", choices=[str(i) for i in range(1, len(apps) + 1)] + ["q"], default="1")
    if choice == "q":
        return "dashboard"

    app = apps[int(choice) - 1]
    job = app.job
    if not job:
        return "dashboard"

    jd_text = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    plan = prepare(job.company, job.title, jd_text, location=job.location)
    console.print(Panel.fit(plan.format_text(), box=box.ROUNDED, padding=(1, 2)))

    console.print()
    console.print("[bold cyan]After preparation[/bold cyan]")
    console.print("  1. Back to dashboard")
    console.print("  2. Practice another interview")
    next_c = Prompt.ask("  Choose", choices=["1", "2"], default="1")
    if next_c == "2":
        return _action_prepare_interview()
    return "dashboard"


def _action_recruiter_followup():
    """Show pending follow-ups and draft emails."""
    pending = list_pending_tasks()
    overdue = list_overdue_tasks()

    console.clear()
    console.print("[bold yellow]📧 Recruiter Follow-ups[/bold yellow]\n")

    if not pending and not overdue:
        console.print("[green]No pending follow-ups![/green]")
        input("\nPress Enter to continue...")
        return "dashboard"

    if overdue:
        console.print(f"  [bold red]Overdue ({len(overdue)})[/bold red]")
        for t in overdue:
            console.print(f"    ⏰ {t.title} [dim](ID: {str(t.id)[:8]})[/dim]")
            done = input("    Mark done? [y/n]: ").strip().lower()
            if done == "y":
                complete_task(str(t.id))
                console.print("    [green]✓ Completed[/green]")
        console.print()

    if pending:
        console.print(f"  [bold]Pending ({len(pending)})[/bold]")
        for t in pending:
            console.print(f"    📅 {t.title}")
            if t.due_date:
                console.print(f"       Due: {t.due_date}")
            done = input("    Mark done? [y/n]: ").strip().lower()
            if done == "y":
                complete_task(str(t.id))
                console.print("    [green]✓ Completed[/green]")
        console.print()

    console.print("[bold cyan]Draft a recruiter email?[/bold cyan]")
    console.print("  1. Yes, pick an application")
    console.print("  2. No, back to dashboard")
    next_c = Prompt.ask("  Choose", choices=["1", "2"], default="2")
    if next_c == "1":
        _draft_recruiter_email()
    return "dashboard"


def _draft_recruiter_email():
    """Pick an application and send a reach-out email."""
    session = get_session()
    try:
        apps = session.query(Application).options(
            joinedload(Application.job)
        ).filter(
            Application.status.in_(["submitted", "interview"]),
        ).order_by(Application.applied_at.desc()).limit(10).all()
    finally:
        session.close()

    if not apps:
        console.print("[yellow]No submitted applications to follow up on[/yellow]")
        input("\nPress Enter to continue...")
        return

    console.clear()
    console.print("[bold yellow]Select Application for Follow-up[/bold yellow]\n")
    for idx, app in enumerate(apps, 1):
        job = app.job
        if not job:
            continue
        applied = app.applied_at.strftime("%b %d") if app.applied_at else "?"
        console.print(f"  [bold]{idx}.[/bold] {job.company} — {job.title}  [dim](applied {applied})[/dim]")

    console.print()
    choice = Prompt.ask("  Choose", choices=[str(i) for i in range(1, len(apps) + 1)] + ["q"], default="1")
    if choice == "q":
        return

    app = apps[int(choice) - 1]
    job = app.job
    if not job:
        return

    session = get_session()
    try:
        contact = find_contact(session, job.company)
        if not contact:
            name = input(f"  Recruiter name for {job.company}: ").strip() or "Hiring Team"
            role = input(f"  Role (Recruiter/HM): ").strip() or "Recruiter"
            contact = Contact(company=job.company, name=name, role=role, source="jobzo_mission")
            session.add(contact)
            session.commit()
            session.refresh(contact)

        contact_name = contact.name
        contact_role = contact.role
        contact_id = contact.id
    finally:
        session.close()

    draft = generate_email_draft(job.company, job.title, contact_name)
    console.print(Panel.fit(
        f"[bold]To:[/bold] {contact_name} ({contact_role}) at {job.company}\n"
        f"[bold]Re:[/bold] {job.title}\n"
        f"\n"
        f"[bold]Subject:[/bold] {draft['subject']}\n"
        f"\n"
        f"{draft['body']}",
        box=box.ROUNDED,
        padding=(1, 2),
        title="Follow-up Email",
    ))

    action = input("\nSend? [y/n]: ").strip().lower()
    if action == "y":
        interaction_id = log_interaction(contact_id, str(app.id), draft["subject"], draft["body"])
        console.print(f"[green]✓[/green] Follow-up logged (ID: {interaction_id[:8]})")
    else:
        console.print("[yellow]Skipped[/yellow]")


def _action_insights():
    """Show analytics and personal intelligence."""
    console.clear()
    console.print("[bold cyan]📊 Career Insights[/bold cyan]\n")
    stats = _dashboard_stats()

    panel_lines = [
        f"  Applications    {stats['total_apps']}",
        f"  Submitted       {stats['submitted']}",
        f"  Interviews      {stats['interviews']}",
        f"  Offers          {stats['offers']}",
        f"  Rejected        {stats['rejected']}",
        f"  Response Rate   {stats['response_rate']}%",
    ]
    console.print(Panel.fit(
        "\n".join(panel_lines),
        box=box.ROUNDED,
        padding=(1, 2),
        title="Overview",
    ))

    weights = learn_weights()
    if weights.confidence != "Low":
        console.print()
        console.print(Panel.fit(
            weights.format_text(),
            box=box.ROUNDED,
            padding=(1, 2),
            title="Personal Intelligence",
        ))

    resumes = resume_stats()
    if resumes:
        console.print()
        console.print("[bold]Resume Performance[/bold]")
        for r in resumes:
            console.print(f"  {r.name:20s}  {r.applications} apps  {r.interviews} interviews  {r.interview_rate:.0f}% rate  [{r.confidence}]")

    console.print()
    console.print("[bold cyan]Next[/bold cyan]")
    console.print("  1. View Skill Roadmap")
    console.print("  2. Back to dashboard")
    next_c = Prompt.ask("  Choose", choices=["1", "2"], default="2")
    if next_c == "1":
        registry = get_registry()
        road = build_roadmap(registry)
        console.print(Panel.fit(road.format_text(max_skills=20), box=box.ROUNDED, padding=(1, 2)))
        input("\nPress Enter to continue...")
    return "dashboard"


def _action_sync():
    """Run full sync: collect + score. Never leaves user stranded."""
    console.clear()
    console.print("[bold cyan]\U0001f4e1 Syncing...[/bold cyan]\n")

    from services.config import Config
    config = Config()

    console.print("[bold]Step 1/2: Collecting jobs...[/bold]")
    skills = config.get("skills", "")
    exp = config.get("experience", 1)
    kw_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    collected = asyncio.run(collect_all(kw_list))
    console.print(f"[green]\u2713[/green] {collected} new jobs\n")

    console.print("[bold]Step 2/2: Scoring jobs...[/bold]")
    result = asyncio.run(score_pending_jobs(kw_list, exp))
    if result["scored"] > 0:
        msg = f"{result['scored']} jobs scored"
    else:
        msg = "No suitable jobs found today."
    if result["hidden"]:
        msg += f"  ({result['hidden']} hidden by eligibility)"
    console.print(f"[green]\u2713[/green] {msg}\n")

    # ── Build stats ──────────────────────────────────────────────────
    from database.connection import get_session
    from database.models import Job, Application
    from ai.scorer import TIER_ORDER, TIER_LABELS, _assign_tier
    session = get_session()
    try:
        total_jobs = session.query(Job).count()
        total_apps = session.query(Application).count()
        tier_counts = {}
        for t in TIER_ORDER:
            tier_counts[t] = session.query(Application).filter(
                Application.tier == t,
                Application.status == "drafted",
            ).count()

        # Top scored app + job
        top_app = session.query(Application).filter(
            Application.status == "drafted",
        ).order_by(Application.score.desc()).first()
        top_score = top_app.score if top_app else 0
        top_job = session.get(Job, top_app.job_id) if top_app else None
    finally:
        session.close()

    # ── Build summary panel ──────────────────────────────────────────
    lines = []

    # Today's Sync section
    lines.append(f"[bold cyan]Today's Sync[/bold cyan]\n")
    lines.append(f"  {'New jobs found':25s} [bold]{collected}[/bold]")
    lines.append(f"  {'New jobs scored':25s} [bold]{result['scored']}[/bold]")

    # Pipeline integrity
    accounted = result["hidden"] + result["scored"]
    balance = "[green]\u2713[/green]" if result["discovered"] == accounted else f"[red]\u2717 off by {result['discovered'] - accounted}[/red]"
    if result["discovered"] > 0 or result["hidden"] > 0:
        lines.append(f"  Balance:                   {balance}")
    if result.get("hidden_reasons"):
        lines.append(f"  Hidden:  {', '.join(f'{k}: {v}' for k, v in sorted(result['hidden_reasons'].items(), key=lambda x: -x[1]))}")

    lines.append("")
    lines.append(f"[bold cyan]Database[/bold cyan]\n")
    lines.append(f"  {'Total jobs':25s} {total_jobs}")
    lines.append(f"  {'Scored (applications)':25s} {total_apps}")

    # Opportunity Breakdown with proper tier labels
    non_zero_tiers = [(t, n) for t in TIER_ORDER if (n := tier_counts.get(t, 0)) > 0]
    if non_zero_tiers or top_app:
        lines.append("")
        lines.append(f"[bold cyan]Opportunity Breakdown[/bold cyan]\n")
        for t in TIER_ORDER:
            n = tier_counts.get(t, 0)
            label = TIER_LABELS.get(t, t)
            if n > 0:
                lines.append(f"  {label:40s} {n}")
            else:
                lines.append(f"  [dim]{label:40s} 0[/dim]")

    # Top match
    if top_app and top_job:
        tier_name = getattr(top_app, "tier", None) or _assign_tier(top_app.score)
        tier_label = TIER_LABELS.get(tier_name, "").split(" ")[0]
        lines.append(f"\n[bold cyan]Top Match[/bold cyan]\n")
        lines.append(f"  [bold]{top_job.company}[/bold] \u2014 {top_job.title}")
        lines.append(f"  {top_app.score}/100  {tier_label}")

        # Most common missing skill
        missing_analysis = _most_common_missing_skill()
        if missing_analysis:
            skill_name, freq, total_checked = missing_analysis
            if freq >= 3:
                lines.append(f"\n  Most missing skill: [yellow]{skill_name}[/yellow]")
                lines.append(f"  Present in {freq}/{total_checked} scored jobs")
                boost_estimate = min(round(freq / total_checked * 25), 15)
                old_avg = _average_score()
                new_avg = min(old_avg + boost_estimate, 100)
                lines.append(f"  Learning it could raise avg from {old_avg} \u2192 [bold]{new_avg}[/bold]")

    panel = Panel.fit(
        "\n".join(lines),
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print(panel)
    console.print()

    # ── Post-sync menu ───────────────────────────────────────────────
    console.print("[bold cyan]What's next?[/bold cyan]")

    has_scored = total_apps > 0
    if has_scored:
        console.print(f"  [bold]1[/bold]. Review existing opportunities  [dim]({total_apps} scored)[/dim]")
    console.print("  [bold]2[/bold]. Back to dashboard")
    console.print("  [bold]3[/bold]. Practice interview")
    console.print("  [bold]4[/bold]. Insights")

    choices = []
    if has_scored:
        choices.append("1")
    choices.extend(["2", "3", "4"])
    default_c = "1" if has_scored else "2"
    next_c = Prompt.ask("  Choose", choices=choices, default=default_c)

    if next_c == "1" and has_scored:
        return _action_review_jobs()
    elif next_c == "3":
        return _action_prepare_interview()
    elif next_c == "4":
        return _action_insights()
    return "dashboard"


def _average_score() -> int:
    """Average score of all drafted applications."""
    from database.connection import get_session
    from database.models import Application
    from sqlalchemy import func
    session = get_session()
    try:
        avg = session.query(func.avg(Application.score)).filter(
            Application.status == "drafted",
        ).scalar()
        return round(avg or 0)
    finally:
        session.close()


def _most_common_missing_skill() -> tuple[str, int, int] | None:
    """Find the skill most frequently missing across scored jobs.

    Returns (skill_name, frequency, total_checked) or None.
    """
    from database.connection import get_session
    from database.models import Application, DecisionSnapshot
    from collections import Counter
    import json
    session = get_session()
    try:
        apps = session.query(Application).filter(
            Application.status == "drafted",
            Application.current_decision_id.isnot(None),
        ).order_by(Application.score.desc()).limit(30).all()
        if not apps:
            return None
        counter: Counter[str] = Counter()
        for app in apps:
            snapshot = session.get(DecisionSnapshot, app.current_decision_id)
            if not snapshot:
                continue
            try:
                details = json.loads(snapshot.details_json) if snapshot.details_json else {}
                missing = details.get("missing_skills", [])
                for s in missing:
                    skill_name = s[0] if isinstance(s, (list, tuple)) else s
                    counter[skill_name] += 1
            except Exception:
                continue
        if not counter:
            return None
        most_common = counter.most_common(1)[0]
        return (most_common[0], most_common[1], len(apps))
    finally:
        session.close()


def _handle_inbox_item(item: InboxItem):
    """Show item detail (timeline + score) then offer actions."""
    _show_item_detail(item)
    if item.app_id and item.category in ("apply", "review"):
        from domain.observation import ObservationService, ObservationType
        ObservationService.record(item.app_id, ObservationType.APPLICATION_VIEWED)
    console.print()
    console.print("[bold cyan]Next step[/bold cyan]")

    action_map = {
        "apply": "1. Apply now\n2. View fit report\n3. Skip\n4. Back to inbox",
        "interview": "1. Prepare now\n2. Mark done\n3. Back to inbox",
        "followup": "1. Send email\n2. Mark done\n3. Back to inbox",
        "review": "1. Apply now\n2. View fit report\n3. Skip\n4. Back to inbox",
        "task": "1. Mark complete\n2. Back to inbox",
    }
    actions = action_map.get(item.category, "1. Back to inbox")
    console.print(actions)
    console.print()
    choice = input("  Choose: ").strip()

    if item.category in ("apply", "review") and choice == "1":
        session = get_session()
        try:
            app = session.get(Application, item.app_id) if item.app_id else None
        finally:
            session.close()
        if app:
            submitted = _run_apply_session(app)
            after_apply(submitted)
    elif item.category == "interview" and choice == "1":
        _run_prepare_for(item)
    elif item.category in ("followup",) and choice == "1":
        _run_followup_for(item)
    elif item.category == "interview" and choice == "2":
        if item.ref_id:
            complete_task(item.ref_id)
            console.print("[green]✓ Completed[/green]")
            input("\nPress Enter to continue...")
    elif choice == "2" and item.category == "review":
        _show_counterfactual_for(item)
        input("\nPress Enter to continue...")
    elif choice == "3" and item.category in ("apply", "review"):
        if item.app_id:
            transition_status(item.app_id, "skipped")
            console.print("[red]✗ Skipped[/red]")
            input("\nPress Enter to continue...")
    else:
        return


def _show_item_detail(item: InboxItem):
    """Show detailed view for an inbox item: justification, confidence, timeline."""
    console.clear()
    lines = []
    ci = "\U0001f3a4" if item.category == "interview" else "\U0001f4e7" if item.category == "followup" else "\U0001f680" if item.category == "apply" else "\U0001f4c4"

    header = f"[bold]{item.company}[/bold]" if item.company else "[bold]{item.title}[/bold]"
    if item.role:
        header += f" \u2014 {item.role}"

    lines.append(header)
    lines.append("")

    # Decision + Confidence
    decision = "Recommended" if item.category in ("apply", "interview") else "Review"
    score_display = ""
    if item.score:
        if item.score >= 80:
            score_display = "\u2b50\u2b50\u2b50\u2b50\u2b50 Excellent"
        elif item.score >= 60:
            score_display = "\u2b50\u2b50\u2b50\u2b50 Good"
        elif item.score >= 40:
            score_display = "\u2b50\u2b50\u2b50 Fair"
        else:
            score_display = "\u2b50 Low"

    lines.append(f"  {decision}  {score_display}")
    lines.append("")

    # Why
    if item.justification:
        lines.append(f"  [bold]Why[/bold]")
        for j in item.justification:
            lines.append(f"    \u2713 {j}")
        lines.append("")

    # Interview probability
    if item.expected_outcome:
        lines.append(f"  [bold]Interview Chance[/bold]")
        lines.append(f"    {item.expected_outcome}")
        if item.score and item.score >= 20:
            lines.append(f"    [dim](If delayed 7d: ~{max(item.score - 10, 5)}%)[/dim]")
        lines.append("")

    # Recommended resume
    if item.category in ("apply", "review") and item.app_id:
        session = get_session()
        try:
            app = session.get(Application, item.app_id)
            if app and app.resume_used:
                lines.append(f"  [bold]Resume[/bold]")
                lines.append(f"    {app.resume_used}")
                lines.append("")
        finally:
            session.close()

    # Urgency
    if item.urgency:
        urgency_color = "red" if item.priority == "high" else "yellow"
        lines.append(f"  [bold]Urgency[/bold]")
        lines.append(f"    [{urgency_color}]{item.urgency}[/{urgency_color}]")
        if item.expiry_days and item.expiry_days > 3:
            lines.append(f"    [dim]Posted {item.expiry_days}d ago[/dim]")
        lines.append("")

    # Time
    if item.time_required:
        lines.append(f"  [bold]Time[/bold]")
        lines.append(f"    {item.time_required}")
        lines.append("")

    # Timeline
    if item.app_id:
        events = build_timeline(item.app_id)
        if events:
            lines.append(f"  [bold]Timeline[/bold]")
            for ev in events:
                icon = ev.icon or "\u25cf"
                lines.append(f"    {icon} {ev.stage}  [dim]{ev.date}[/dim]")
                if ev.detail:
                    lines.append(f"       [dim]{ev.detail}[/dim]")
            lines.append("")

    console.print(Panel.fit("\n".join(lines), box=box.ROUNDED, padding=(1, 2), title="Detail"))


def _show_counterfactual_for(item: InboxItem):
    """Show decision intelligence for an inbox item."""
    session = get_session()
    try:
        if item.app_id:
            app = session.query(Application).options(
                joinedload(Application.job),
                joinedload(Application.current_decision),
            ).filter(Application.id == item.app_id).first()
            if app and app.job:
                from services.decision_snapshot import snapshot_to_inbox_data
                if app.current_decision:
                    data = snapshot_to_inbox_data(app.current_decision)
                    prob = data.get("interview_probability", 0)
                    confidence = data.get("confidence", "Low")
                    matched = data.get("matched_skills", [])
                    missing = data.get("missing_skills", [])
                else:
                    prob, confidence, matched, missing = 0, "Low", [], []
                lines = [
                    f"  Interview Probability: {prob}%",
                    f"  Confidence: {confidence}",
                    f"  Score: {app.score}/100 ({app.tier.replace('_', ' ').title()})",
                    f"  Skills matched: {len(matched)}",
                ]
                if missing:
                    lines.append(f"  Skills to learn: {', '.join(missing[:5])}")
                console.print(Panel.fit(
                    "\n".join(lines),
                    box=box.ROUNDED, padding=(1, 2), title="Decision Intelligence",
                ))
    finally:
        session.close()


def _run_prepare_for(item: InboxItem):
    """Prepare for an interview from inbox."""
    session = get_session()
    try:
        if item.job_id:
            job = session.get(Job, item.job_id)
        elif item.app_id:
            app = session.get(Application, item.app_id)
            job = session.get(Job, app.job_id) if app else None
        else:
            job = None
    finally:
        session.close()

    if not job:
        console.print("[red]Job not found[/red]")
        input("\nPress Enter to continue...")
        return

    jd_text = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    plan = prepare(job.company, job.title, jd_text, location=job.location)
    console.clear()
    console.print(Panel.fit(plan.format_text(), box=box.ROUNDED, padding=(1, 2)))
    input("\nPress Enter to continue...")


def _run_followup_for(item: InboxItem):
    """Send follow-up email from inbox."""
    _draft_recruiter_email_for(item) if item.app_id else None
    if not item.app_id:
        console.print("[yellow]No application linked[/yellow]")
        input("\nPress Enter to continue...")


def _draft_recruiter_email_for(item: InboxItem):
    """Draft a follow-up email for a specific inbox item."""
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id == item.app_id).first() if item.app_id else None
        if not app or not app.job:
            console.print("[red]Application not found[/red]")
            return
        job = app.job

        contact = find_contact(session, job.company)
        if not contact:
            name = input(f"  Recruiter name for {job.company}: ").strip() or "Hiring Team"
            role = input(f"  Role (Recruiter/HM): ").strip() or "Recruiter"
            contact = Contact(company=job.company, name=name, role=role, source="jobzo_mission")
            session.add(contact)
            session.commit()
            session.refresh(contact)

        contact_name = contact.name
        contact_role = contact.role
        contact_id = contact.id
    finally:
        session.close()

    if not app or not job:
        return

    draft = generate_email_draft(job.company, job.title, contact_name)
    console.clear()
    console.print(Panel.fit(
        f"[bold]To:[/bold] {contact_name} ({contact_role}) at {job.company}\n"
        f"[bold]Re:[/bold] {job.title}\n"
        f"\n"
        f"[bold]Subject:[/bold] {draft['subject']}\n"
        f"\n"
        f"{draft['body']}",
        box=box.ROUNDED,
        padding=(1, 2),
        title="Follow-up Email",
    ))

    action = input("\nSend? [y/n]: ").strip().lower()
    if action == "y":
        interaction_id = log_interaction(contact_id, str(app.id), draft["subject"], draft["body"])
        console.print(f"[green]✓[/green] Follow-up logged (ID: {interaction_id[:8]})")
    else:
        console.print("[yellow]Skipped[/yellow]")
    input("\nPress Enter to continue...")


def _run_review_item(item: InboxItem):
    """Review a single job from inbox — show fit, then offer actions."""
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id == item.app_id).first() if item.app_id else None
    finally:
        session.close()

    if not app or not app.job:
        console.print("[red]Application not found[/red]")
        input("\nPress Enter to continue...")
        return

    from domain.observation import ObservationService, ObservationType
    ObservationService.record(str(app.id), ObservationType.APPLICATION_VIEWED)

    job = app.job
    _show_job_card(app, job, 1, 1)
    console.print()
    console.print("  [bold green][a][/bold green] Apply")
    console.print("  [bold blue][s][/bold blue] Save for later")
    console.print("  [bold red][x][/bold red] Skip")
    console.print("  [bold][b][/bold] Back to inbox")
    action = input("\nAction: ").strip().lower()
    if action == "a":
        submitted = _run_apply_session(app)
        after_apply(submitted)
    elif action == "x":
        transition_status(str(app.id), "skipped")
        console.print("[red]✗ Skipped[/red]")
        input("\nPress Enter to continue...")
    elif action == "s":
        console.print("[blue]✓ Saved[/blue]")
        input("\nPress Enter to continue...")


def _system_check():
    """Check dependencies once at startup and show status."""
    checks = []
    # Playwright
    pw_ok = _check_playwright()
    checks.append(("[green]\u2713[/green]" if pw_ok else "[red]\u2717[/red]", "Playwright", pw_ok))
    # Database
    try:
        from database.connection import get_session
        s = get_session()
        s.close()
        checks.append(("[green]\u2713[/green]", "SQLite", True))
    except Exception:
        checks.append(("[red]\u2717[/red]", "SQLite", False))
    # Ollama / LLM
    try:
        from services.config import Config
        cfg = Config.llm_config()
        model = cfg.get("ollama", {}).get("model", "")
        checks.append(("[green]\u2713[/green]" if model else "[yellow]~[/yellow]",
                       f"LLM ({model or 'none'})", bool(model)))
    except Exception:
        checks.append(("[yellow]~[/yellow]", "LLM config", False))

    status_icon, _, all_ok = checks[0]
    if not all_ok:
        for check in checks:
            if not check[2]:
                status_icon = "[yellow]\u26a0[/yellow]"

    panel_lines = [f"  {icon} {label}" for icon, label, _ in checks]
    panel_lines.append("")
    if not pw_ok:
        panel_lines.append("  [dim]Run: pip install playwright && playwright install chromium[/dim]")
    console.print(Panel.fit(
        "\n".join(panel_lines),
        title="System Check",
        box=box.ROUNDED, padding=(0, 2),
    ))
    console.print()


def run_mission():
    """Main loop: show inbox → act → repeat until exit."""
    _system_check()

    from domain.observation import ObservationService, ObservationType
    import uuid
    _session_id = uuid.uuid4().hex[:8]
    ObservationService.record(_session_id, ObservationType.SESSION_START, actor="user")

    while True:
        try:
            choice, item = show_dashboard()

            if choice == "q":
                ObservationService.record(_session_id, ObservationType.SESSION_END, actor="user")
                console.clear()
                console.print("[bold cyan]Good luck with your job search! 🚀[/bold cyan]")
                break
            elif choice == "s":
                _action_sync()
            elif choice == "i":
                _action_insights()
            elif choice == "r":
                _action_review_jobs()
            elif isinstance(choice, int) and item:
                ObservationService.record(
                    item.app_id or _session_id,
                    ObservationType.MISSION_ACCEPTED,
                    actor="user",
                    metadata={"category": item.category, "score": item.score},
                )
                _handle_inbox_item(item)

        except KeyboardInterrupt:
            console.print("\n[yellow]Exiting...[/yellow]")
            break
        except Exception as e:
            logger.error("Mission error: %s", e)
            console.print(f"\n[red]Something went wrong: {e}[/red]")
            console.print("[dim]Check the logs for details.[/dim]")
            input("\nPress Enter to return to dashboard...")
