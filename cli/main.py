import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn
from rich import box

from services.logging_setup import setup_logging
from services.collector import collect_all
from services.config import Config
from ai.scorer import score_pending_jobs, SKILL_KEYWORDS
from ai.llm import ask
from browser.assistant import BrowserAssistant, KNOWN_ATS_DOMAINS
from tracker.applications import list_applications, transition_status, get_application
from tracker.tasks import list_pending_tasks, complete_task
from database.connection import get_session
from database.models import Job, Application, Task
from sqlalchemy.orm import joinedload
from resumes.registry import get_registry
from resumes.fit_report import generate as generate_fit_report
from resumes.skill_roadmap import build_roadmap
from resumes.feed import build_feed
from resumes.prepare import prepare
from tracker.intelligence import (
    compute_quality_score,
    company_stats,
    verify_application,
)
from tracker.reach import (
    find_contact,
    find_or_create_contact,
    generate_email_draft,
    log_interaction,
    get_contact_interactions,
)
from tracker.outreach import (
    outreach_summary,
    template_performance,
    company_responsiveness,
    best_contact_time,
)
from tracker.outcomes import get_or_create_outcome, update_outcome, get_outcome
from tracker.features import extract_features
from tracker.decision import predict_interview, counterfactual
from tracker.personal import (
    learn_weights,
    resume_stats,
    resume_detail,
    company_intelligence,
    ats_intelligence,
    timing_intelligence,
    skill_intelligence,
    personal_predict,
    simulate,
)
from mission.engine import run_mission
from database.models import Contact, Interaction, ApplicationOutcome

logger = logging.getLogger("jobzo")
console = Console()
app = typer.Typer(name="jobzo")

setup_logging()


# ── Entry point ──────────────────────────────────────────────────────────────

def entry_point():
    if len(sys.argv) <= 1:
        _morning_assistant()
    else:
        app()


@app.callback()
def main():
    """Your daily job hunting assistant. Run [bold]jobzo[/bold] to start."""


# ── Morning Assistant (default) ─────────────────────────────────────────────

def _morning_assistant():
    """Smart daily assistant. Orchestrates sync/collect/score, shows feed."""
    from datetime import datetime
    from services.feed import build_feed, print_feed
    from services.config import Config

    console.clear()
    now = datetime.utcnow()
    hour = now.hour
    greeting = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 18 else "Good Evening"

    # Try to get user name from profile
    user_name = ""
    try:
        profile_path = Path(__file__).parent.parent / "resumes" / "master" / "profile.yaml"
        if profile_path.exists():
            import yaml
            with open(profile_path) as f:
                p = yaml.safe_load(f) or {}
            user_name = p.get("name", "").split()[0] if p.get("name") else ""
    except Exception:
        pass

    console.print(f"[bold cyan]{greeting}{f' {user_name}' if user_name else ''} 👋[/bold cyan]")
    console.print()

    # ── Profile check (first run) ────────────────────────────────────
    from services.user_profile import is_complete, load_profile, prompt_setup
    user_profile = load_profile()
    if not is_complete(user_profile):
        user_profile = prompt_setup(console)
        console.print("  [green]✓[/green] Profile saved")
        console.print()

    # ── Step 1: Schema check / sync ────────────────────────────────────
    console.print("  [dim]Checking schema...[/dim]")
    from database.connection import get_session
    from sqlalchemy import inspect

    session = get_session()
    engine = session.get_bind()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    session.close()

    needs_sync = "poll_runs" not in existing_tables or "hiring_channels" not in existing_tables

    from services.company_registry import sync_companies_from_registry, get_stats
    stats = get_stats()
    needs_seed = stats.get("companies", 0) == 0

    if needs_sync or needs_seed:
        if needs_sync:
            console.print("  ↳ Schema updated — syncing...")
        try:
            result = sync_companies_from_registry()
            console.print(f"  [green]✓[/green] Registry synced: {result['total']} companies")
        except Exception:
            from database.connection import DB_PATH
            console.print(f"  [yellow]⚠ Database schema out of date.[/yellow]")
            console.print(f"     Delete [bold]{DB_PATH}[/bold] and try again.")
            console.print(f"     Or run [green]jobzo doctor[/green] for help.")
    else:
        console.print(f"  [green]✓[/green] Registry ready: {stats['active']} active companies")

    # ── Step 2: Smart collect ──────────────────────────────────────────
    console.print("  [dim]Checking for new opportunities...[/dim]")
    try:
        from database.models import PollRun
        session = get_session()
        latest_poll = session.query(PollRun.finished_at).order_by(PollRun.finished_at.desc()).first()
        session.close()

        should_collect = True
        if latest_poll and latest_poll[0]:
            hours_since = (datetime.utcnow() - latest_poll[0]).total_seconds() / 3600
            if hours_since < 1:
                should_collect = False
                console.print(f"  [green]✓[/green] Recent poll ({int(hours_since)}m ago) — skipping")
            elif hours_since < 6:
                console.print(f"  [dim]  ↳ Last poll {int(hours_since)}h ago — collecting[/dim]")
            else:
                console.print(f"  [dim]  ↳ Last poll {int(hours_since)}h ago — collecting[/dim]")

        if should_collect:
            import asyncio
            total = asyncio.run(collect_all())
            from ai.scorer import score_pending_jobs
            scored = asyncio.run(score_pending_jobs())
            scored_count = scored.get("scored", 0) if isinstance(scored, dict) else scored
            console.print(f"  [green]✓[/green] Collected: {total} jobs  Scored: {scored_count} new")
        else:
            from ai.scorer import score_pending_jobs
            import asyncio
            scored = asyncio.run(score_pending_jobs())
            scored_count = scored.get("scored", 0) if isinstance(scored, dict) else scored
            if scored_count:
                console.print(f"  [green]✓[/green] Scored: {scored_count} new applications")

    except Exception as e:
        logger.debug("Collection error (non-fatal): %s", e)
        console.print(f"  [yellow]⚠ Collection skipped: {e}[/yellow]")

    # ── Step 3: Show feed ─────────────────────────────────────────────
    console.print()
    items = build_feed()
    if items:
        print_feed(items)
    else:
        console.print("  [yellow]No recommendations yet. Run [green]jobzo review[/green] to explore.[/yellow]")

    # ── Step 4: Interactive loop ─────────────────────────────────────
    while True:
        console.print("  [bold]What next?[/bold]")
        console.print("  [1] [red]🔥 Apply now[/red] — dig, high priority")
        console.print("  [2] [green]🇮🇳 Fresher — India[/green] — realistic fresher openings")
        console.print("  [3] [blue]📋 Review[/blue] — all scored opportunities")
        console.print("  [4] [magenta]📊 Dashboard[/magenta] — analytics")
        console.print("  [5] [yellow]🩺 Doctor[/yellow] — system health")
        console.print("  [6] [cyan]✅ Approval inbox[/cyan] — approve applies & emails")
        console.print("  [7] [cyan]☀️ Daily brief[/cyan] — 15-minute decision screen")
        console.print("  [0] [dim]Exit[/dim]")
        console.print()
        choice = input("  → ").strip().lower()

        if choice in ("0", "q", "quit", "exit"):
            console.print("[dim]See you tomorrow 👋[/dim]")
            break
        elif choice in ("1", "a", "dig", "apply", "🔥"):
            _apply_now_screen()
        elif choice in ("2", "f", "fresher", "🇮🇳"):
            _fresher_screen(limit=15)
        elif choice in ("3", "r", "review"):
            review_opportunities()
            input("  Press Enter to return...")
        elif choice in ("4", "d", "dashboard"):
            _cmd_stats()
            input("  Press Enter to return...")
        elif choice in ("5", "doctor"):
            doctor()
            input("  Press Enter to return...")
        elif choice in ("6", "inbox", "approve"):
            inbox()
            input("  Press Enter to return...")
        elif choice in ("7", "brief", "today"):
            brief()
            input("  Press Enter to return...")
        else:
            console.print("[dim]Type jobzo <command> for anything else[/dim]")
            input("  Press Enter to return...")


def _confidence_label(score: int) -> tuple[str, str]:
    if score >= 80:
        return "🟢 Excellent", "green"
    elif score >= 60:
        return "🟡 Good", "yellow"
    elif score >= 40:
        return "🟠 Fair", "orange3"
    else:
        return "🔴 Low", "red"


def _parse_notes(notes: str) -> list[tuple[str, str, str]]:
    """Parse notes field into list of (icon, label, detail) tuples."""
    if not notes:
        return []
    parts = notes.split(" | ")
    rows = []
    for part in parts:
        part = part.strip()
        if part.startswith("Skill match"):
            m = re.search(r"Skill match \((\d+)/(\d+)\): (.+)", part)
            if m:
                matched_count, total, skills_str = m.groups()
                skills = [s.strip() for s in skills_str.split(",")]
                rows.append(("✓", f"Skills: {matched_count}/{total}", ", ".join(skills[:4])))
        elif part.startswith("Freshness"):
            pct = part.split(":")[-1].strip()
            rows.append(("📅", "Freshness", pct))
        elif "experience" in part.lower() or "yr" in part.lower():
            rows.append(("📊", "Experience", part))
        elif "remote" in part.lower() or "on-site" in part.lower() or "location" in part.lower():
            rows.append(("📍", "Location", part))
        else:
            rows.append(("ℹ️", "", part))
    return rows


def _missing_skills(job) -> list[str]:
    desc = (job.description + " " + job.title).lower()
    missing = []
    for skill in SKILL_KEYWORDS:
        if skill.lower() in desc:
            continue
        skill_label = skill.replace("_", " ").title()
        missing.append(skill_label)
    return missing[:5]


def _show_job_card(app, job, idx, total):
    reasons = _parse_notes(app.notes)

    # Opportunity Intelligence
    from services.opportunity_intelligence import enrich
    oi = enrich(app.id)

    lines = []
    lines.append(f"[bold]{job.company}[/bold] — {job.title}")
    lines.append("")

    # ── Apply Priority (North Star) ──────────────────────────────
    if oi and "apply_priority" in oi:
        ap = oi["apply_priority"]
        ap_color = "green" if ap >= 7 else "yellow" if ap >= 4 else "red"
        lines.append(f"  [bold]Apply Priority: [{ap_color}]{ap}/10[/{ap_color}][/bold]")
        # Expected Value stars
        ev = oi.get("expected_value", {})
        if ev:
            lines.append(f"  Expected Value       {ev.get('stars', '')}  ({ev.get('label', '')})")
            lines.append(f"  Est. time            ~{ev.get('effort_minutes', '?')} min  ({ev.get('difficulty', '')})")
        for reason in oi.get("reasons", [])[:4]:
            lines.append(f"    ✓ {reason}")
        lines.append("")

    # ── Trust (safety gate, evidence-backed) ─────────────────────
    from services.trust import trust_for_job, TIER_LABELS
    trust = trust_for_job(job)
    t_color = {"verified": "green", "trusted": "cyan",
               "unverified": "yellow", "suspicious": "red"}.get(trust.tier, "white")
    lines.append(f"  [bold][{t_color}]{trust.emoji} Trust: {trust.score:.0f}/100 ({TIER_LABELS[trust.tier]})[/{t_color}][/bold]")
    for reason in trust.reasons[:4]:
        lines.append(f"    ✓ {reason}")
    for risk in trust.risks[:2]:
        lines.append(f"    [red]✗ {risk['keyword']}[/red]")
    if trust.why_not_100:
        lines.append(f"    [dim]Why not 100: {', '.join(trust.why_not_100[:2])}[/dim]")
    lines.append("")

    # ── Quick signals row ────────────────────────────────────────
    if oi:
        fresh = oi.get("freshness", {}).get("label", "")
        comp = oi.get("competition", {}).get("label", "")
        gf = oi.get("graduate_friendly", "unknown")
        gf_label = {"high": "High", "medium": "Med", "low": "Low"}.get(gf, "?")
        hi = oi.get("hiring_intensity", {}).get("label", "")
        trend = oi.get("hiring_trend", {})
        trend_str = f" {trend['symbol']}{trend['pct']}%" if trend.get("pct") else ""
        resp = oi.get("expected_response_days")
        resp_str = f"~{resp}d" if resp else "—"

        lines.append(f"  [dim]{fresh}[/dim]  |  Comp: [dim]{comp}[/dim]  |  Grad: [dim]{gf_label}[/dim]")
        lines.append(f"  Hiring: [dim]{hi}{trend_str}[/dim]  |  Response: [dim]{resp_str}[/dim]")

    # ── Learn ROI ────────────────────────────────────────────────
    if oi:
        roi = oi.get("learning_roi", [])
        if roi:
            lines.append("")
            lines.append("  [bold]Learning ROI[/bold]")
            for r in roi[:3]:
                lines.append(f"    {r['skill']:<12} {r['roi_stars']}  {r['jobs']} jobs  ~{r['learn_days']}d to learn")

    # ── Company Memory ───────────────────────────────────────────
    if oi:
        mem = oi.get("company_memory", {})
        if mem and mem.get("total_applicants", 0) > 0:
            lines.append("")
            ir = mem.get("interview_rate", "?")
            rr = mem.get("rejection_rate", "?")
            rd = mem.get("avg_interview_rounds", "?")
            lines.append(f"  Company History: {mem['total_applicants']} tracked  |  Interview: {ir}%  |  Rounds: ~{rd}")

    # ── Original notes ──────────────────────────────────────────
    notes_shown = False
    for icon, label_text, detail in reasons:
        if detail:
            if not notes_shown:
                lines.append("")
                lines.append("  [bold]Notes[/bold]")
                notes_shown = True
            lines.append(f"  {icon} [bold]{label_text}[/bold]")
            lines.append(f"     {detail}")

    lines.append("")
    lines.append(f"  Source: {job.source}")

    panel = Panel.fit(
        "\n".join(lines),
        title=f"[bold cyan]◀ {idx}/{total} ▶[/bold cyan]",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print(panel)


def _review_jobs():
    session = get_session()
    apps = session.query(Application).options(
        joinedload(Application.job)
    ).filter(
        Application.status.in_(["drafted", "ready"])
    ).order_by(Application.score.desc()).all()
    session.close()

    if not apps:
        console.print("[yellow]No jobs to review[/yellow]")
        input("Press Enter to continue...")
        return

    console.clear()
    console.print("[bold yellow]📋 Job Review[/bold yellow]\n")
    console.print("Review each job. Choose an action:\n")
    console.print("  [bold green][a][/bold green] Apply — open browser and submit")
    console.print("  [bold blue][s][/bold blue] Save — keep for later")
    console.print("  [bold red][x][/bold red] Skip — not interested")
    console.print("  [bold][q][/bold] Quit review\n")

    for idx, app in enumerate(apps, 1):
        job = app.job
        _show_job_card(app, job, idx, len(apps))
        console.print()

        action = input("Action [a/s/x/q]: ").strip().lower()

        if action == "a":
            session = get_session()
            fresh = session.query(Application).filter(Application.id == app.id).first()
            fresh_job = session.query(Job).filter(Job.id == app.job_id).first()
            session.close()
            if fresh and fresh_job:
                _run_apply(fresh, fresh_job)
        elif action == "s":
            console.print("  [blue]Saved for later[/blue]")
        elif action == "x":
            console.print("  [red]Skipped[/red]")
        elif action == "q":
            console.print("  [yellow]Review ended[/yellow]")
            break

        console.print()

    completed = len(apps)
    console.print(f"[green]✓[/green] Reviewed {completed} jobs")
    input("Press Enter to continue...")


def _run_apply(application, job):
    if application.strategy == "skip":
        console.print("  [yellow]Skipping (strategy: skip)[/yellow]")
        return

    console.print(f"\n  [bold]Applying to {job.company} — {job.title}[/bold]")

    resume_path = application.resume_used or ""
    cover_letter = ""

    try:
        result = ask("cover_letter", f"""Company: {job.company}
Role: {job.title}
Description: {job.description[:1500]}
Resume type: {resume_path}""")
        if isinstance(result, dict):
            cover_letter = result.get("cover_letter", "")
        else:
            cover_letter = str(result)
    except Exception:
        cover_letter = f"I am excited to apply for the {job.title} role at {job.company}."

    if not resume_path:
        resume_path = "backend_v3.pdf"

    if application.status == "drafted":
        transition_status(str(application.id), "ready")

    assistant = BrowserAssistant()
    try:
        asyncio.run(assistant.start())
        asyncio.run(assistant.navigate(job.url))
        form_type = asyncio.run(assistant.detect_form())
        if form_type:
            asyncio.run(assistant.autofill(resume_path, cover_letter, job.url))
            confirmed = asyncio.run(assistant.wait_for_confirmation())
            if confirmed:
                transition_status(str(application.id), "submitted")
                console.print(f"  [green]✓[/green] Submitted!")
            else:
                console.print("  [yellow]Skipped[/yellow]")
        else:
            console.print("  [yellow]Form not detected — nothing was submitted.[/yellow]")
            console.print("  [dim]Apply manually in the opened page, then confirm below.[/dim]")
            manual = input("  Did you submit the application manually? [y/N]: ").strip().lower()
            if manual == "y":
                transition_status(str(application.id), "submitted")
                console.print("  [green]✓[/green] Marked submitted by user confirmation.")
            else:
                console.print("  [dim]Left as ready; no submission recorded.[/dim]")
    except Exception as e:
        logger.error("Browser automation failed: %s", e)
        console.print(f"  [red]Browser error: {e}[/red]")
    finally:
        asyncio.run(assistant.close())


def _show_progress():
    session = get_session()
    apps = session.query(Application).options(
        joinedload(Application.job)
    ).order_by(Application.created_at.desc()).all()
    session.close()

    if not apps:
        console.print("[yellow]No applications yet[/yellow]")
        input("Press Enter to continue...")
        return

    console.clear()
    console.print("[bold yellow]📈 Progress[/bold yellow]\n")

    table = Table(box=box.SIMPLE)
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Score")
    table.add_column("Timeline")

    for app in apps:
        job = app.job
        status_style = {
            "drafted": "yellow", "ready": "blue",
            "submitted": "cyan", "interview": "green",
            "rejected": "red", "offer": "bold green",
        }.get(app.status, "white")

        timeline = {
            "drafted": "○ Found", "ready": "● Ready",
            "submitted": "● Applied", "interview": "● Interview ◇",
            "rejected": "● ✗", "offer": "● ● ● ✓",
        }.get(app.status, app.status)

        table.add_row(
            job.company if job else "?",
            job.title[:30] if job else "?",
            Text(app.status, style=status_style),
            str(app.score),
            timeline,
        )

    console.print(table)
    console.print()
    console.print("  [dim]○ Found → ● Applied → ◇ Interview → ✓ Offer[/dim]")
    input("\nPress Enter to continue...")


def _do_followups():
    pending = list_pending_tasks()

    if not pending:
        console.print("[green]No pending follow-ups![/green]")
        input("Press Enter to continue...")
        return

    console.clear()
    console.print("[bold yellow]📅 Follow-ups[/bold yellow]\n")

    for t in pending:
        console.print(f"  [bold]{t.title}[/bold]")
        if t.due_date:
            console.print(f"     Due: {t.due_date}")
        console.print(f"     [dim]ID: {str(t.id)[:8]}[/dim]")
        done = input("     Mark as done? [y/n]: ").strip().lower()
        if done == "y":
            complete_task(str(t.id))
            console.print("     [green]✓ Completed![/green]")
        console.print()

    input("Press Enter to continue...")


def _show_interviews():
    session = get_session()
    apps = session.query(Application).options(
        joinedload(Application.job)
    ).filter(Application.status == "interview").all()
    session.close()

    if not apps:
        console.print("[yellow]No upcoming interviews[/yellow]")
        input("Press Enter to continue...")
        return

    console.clear()
    console.print("[bold yellow]🎤 Interview Prep[/bold yellow]\n")

    for app in apps:
        job = app.job
        console.print(Panel(
            f"[bold]{job.company}[/bold] — {job.title}\n"
            f"  Score: {app.score}/100\n"
            f"  Applied: {app.applied_at.strftime('%b %d') if app.applied_at else 'N/A'}\n"
            f"  Notes: {app.notes or 'None'}\n",
            title=job.company,
            box=box.ROUNDED,
        ))

    input("\nPress Enter to continue...")


# ── CLI Commands ─────────────────────────────────────────────────────────────

@app.command(hidden=True)
def sync_companies():
    """Sync the company registry YAML → database."""
    from services.company_registry import sync_companies_from_registry

    console.print("[bold cyan]JobZo[/bold cyan] — Syncing company registry...")
    try:
        result = sync_companies_from_registry()
        console.print(f"[green]✓[/green] Created {result['created']} new companies")
        console.print(f"[green]✓[/green] Updated {result['updated']} existing companies")
        console.print(f"[green]✓[/green] Added {result['aliases_created']} aliases")
        console.print(f"[cyan]Total:[/cyan] {result['total']} companies in database")
    except Exception as e:
        console.print(f"[red]✗ Sync failed:[/red] {e}")
        raise typer.Exit(code=1)


@app.command(name="validate-registry", hidden=True)
def validate_registry():
    """Validate the company registry YAML files for correctness."""
    from services.company_registry import validate_registry

    console.print("[bold cyan]JobZo[/bold cyan] — Validating company registry...\n")
    results = validate_registry()
    passed = 0
    failed = 0
    errors = 0
    for r in results:
        if r["status"] == "PASS":
            passed += 1
            icon = "[green]✓[/green]"
        elif r["status"] == "FAIL":
            failed += 1
            icon = "[red]✗[/red]"
        else:
            errors += 1
            icon = "[yellow]⚠[/yellow]"
        console.print(f"  {icon} {r['check']}")
        for issue in r.get("issues", []):
            console.print(f"       {issue}")
    console.print()
    if errors:
        console.print(f"[yellow]Errors:[/yellow] {errors}")
    if failed:
        console.print(f"[red]Failed checks:[/red] {failed}")
    console.print(f"[green]Passed:[/green] {passed}")
    if failed == 0 and errors == 0:
        console.print("\n[bold green]Registry validation PASSED[/bold green]")


@app.command(hidden=True)
def benchmark(
    profile: str = typer.Option("", "--profile", "-p", help="Run only a specific profile (by name)"),
):
    """Run the benchmark suite — evaluate registry, retrieval, ranker, and scores."""
    from benchmark.runner import run_all, print_results

    console.print("[bold cyan]JobZo[/bold cyan] — Running benchmarks...\n")
    profile_names = [p.strip() for p in profile.split(",") if p.strip()] if profile else None
    results = run_all(profile_names)
    print_results(results)


@app.command(hidden=True)
def collect(
    keywords: str = typer.Option("", help="Comma-separated keywords to search for"),
):
    """Collect jobs from all enabled providers."""
    console.print("[bold cyan]JobZo[/bold cyan] — Collecting jobs...")
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] or None
    total = asyncio.run(collect_all(kw_list))
    console.print(f"[green]✓[/green] Collected {total} new jobs")


@app.command(name="list", hidden=True)
def list_jobs(
    all: bool = typer.Option(False, "--all", help="Show all jobs instead of top 20"),
):
    """List collected jobs."""
    session = get_session()
    try:
        query = session.query(Job).options(
            joinedload(Job.application)
        ).order_by(Job.created_at.desc())

        if not all:
            query = query.limit(20)

        jobs = query.all()

        if not jobs:
            console.print("[yellow]No jobs collected yet[/yellow]")
            return

        table = Table(title="Collected Jobs")
        table.add_column("#")
        table.add_column("ID")
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Score")
        table.add_column("Status")

        for idx, job in enumerate(jobs, 1):
            app = job.application
            score = str(app.score) if app else "—"
            status = app.status if app else "collected"
            status_style = {
                "drafted": "yellow", "ready": "blue",
                "submitted": "cyan", "interview": "green",
                "rejected": "red", "offer": "bold green",
            }.get(status, "white")

            table.add_row(
                str(idx),
                str(job.id)[:8],
                job.company,
                job.title[:40],
                score,
                Text(status, style=status_style),
            )

        console.print(table)
    finally:
        session.close()


@app.command(hidden=True)
def rank(
    skills: str = typer.Option("", help="Comma-separated skill keywords"),
    experience: int = typer.Option(1, help="Your years of experience"),
):
    """Score all unscored jobs."""
    console.print("[bold cyan]JobZo[/bold cyan] — Ranking jobs...")
    skill_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    r = asyncio.run(score_pending_jobs(skill_list, experience))
    total = r["scored"]
    console.print(f"[green]✓[/green] Scored {total} jobs")

    _show_top_applications()


@app.command(name="review")
def review_opportunities():
    """Review your top opportunities — scored and ranked."""
    list_jobs(all=False)


@app.command(name="dig")
def dig(
    fresh: bool = typer.Option(False, "--fresh", help="Only postings from the last 3 days (early-applicant hunting)"),
    strict: bool = typer.Option(False, "--strict", help="Only verified/trusted postings (exclude unverified)"),
    min_priority: float = typer.Option(0.0, "--min-priority", help="Minimum Apply Priority (0-10)"),
    days: int = typer.Option(0, "--days", help="Only jobs posted within N days"),
    local: bool = typer.Option(False, "--local", help="Only Indian product/startup/service/GCC + fintech/edtech/healthtech companies"),
    quiet: bool = typer.Option(False, "--quiet", help="Just print the table, no interactive loop"),
):
    """High-confidence discovery for your strategy — suitable + trusted + worth-it."""
    import asyncio

    from services.dig import run_dig, pending_unscored_count
    from ai.scorer import score_pending_jobs

    pending = pending_unscored_count()
    if pending:
        console.print(f"[dim]Scoring {pending} eligible jobs first…[/dim]")
        try:
            asyncio.run(score_pending_jobs())
            console.print("[green]✓[/green] Scored\n")
        except Exception as e:
            logger.debug("Scoring before dig failed (non-fatal): %s", e)
            console.print(f"  [yellow]⚠ Scoring skipped: {e}[/yellow]\n")

    result = run_dig(
        fresh_only=fresh,
        strict=strict,
        min_priority=min_priority,
        max_days=days or None,
        local_only=local,
    )

    console.clear()
    console.print("[bold yellow]⛏️  DIG — worth-it opportunities for your strategy[/bold yellow]\n")
    mode = []
    if fresh:
        mode.append("fresh (≤3d)")
    if strict:
        mode.append("strict (verified/trusted only)")
    if min_priority:
        mode.append(f"priority ≥ {min_priority}")
    if days:
        mode.append(f"≤ {days}d old")
    if local:
        mode.append("local (Indian product/startup/service/GCC/vertical)")
    if mode:
        console.print(f"  [dim]Filters: {', '.join(mode)}[/dim]\n")

    if not result.items:
        console.print("[yellow]No opportunities match right now.[/yellow]")
        if result.suspicious:
            console.print(f"  [red]🚨 {len(result.suspicious)} suspicious job{'' if len(result.suspicious) == 1 else 's'} blocked.[/red]")
        console.print()
        console.print("[dim]Tip: [green]jobzo dig[/green] without filters shows more.[/dim]")
        return

    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="right")
    table.add_column("Company", style="bold")
    table.add_column("Role")
    table.add_column("Priority", justify="right")
    table.add_column("Match", justify="right")
    table.add_column("Trust", justify="right")
    table.add_column("Fresh", justify="right")

    for idx, item in enumerate(result.items, 1):
        t = item.trust
        trust_display = f"{t.emoji} {t.score:.0f}"
        trust_style = {"verified": "green", "trusted": "cyan",
                       "unverified": "yellow", "suspicious": "red"}.get(t.tier, "white")
        fresh = item.freshness
        fresh_display = f"{fresh.get('emoji', '')} {fresh.get('label', '')}"
        table.add_row(
            str(idx),
            item.job.company,
            item.job.title[:40],
            f"{item.priority:.1f}",
            str(item.score),
            Text(trust_display, style=trust_style),
            fresh_display,
        )
    console.print(table)

    if result.suspicious:
        console.print(f"\n  [red]🚨 {len(result.suspicious)} suspicious job{'' if len(result.suspicious) == 1 else 's'} blocked[/red]")
        for s in result.suspicious[:3]:
            flag = s.trust.risks[0]["keyword"] if s.trust.risks else "scam indicators"
            console.print(f"     [dim]{s.job.company} — {s.job.title[:40]} ({flag})[/dim]")
        if len(result.suspicious) > 3:
            console.print(f"     [dim]… and {len(result.suspicious) - 3} more[/dim]")

    console.print()
    if quiet:
        return

    console.print("  [dim]Pick a job to inspect (or [green]0[/green] to exit):[/dim]")
    choice = input("  → ").strip()
    if not choice.isdigit() or int(choice) == 0:
        return
    idx = int(choice) - 1
    if idx < 0 or idx >= len(result.items):
        console.print("[red]Invalid choice[/red]")
        return
    item = result.items[idx]
    _show_job_card(item.app, item.job, idx + 1, len(result.items))
    console.print()
    action = input("Action [a]pply / [o]pen / [s]kip / [q]uit: ").strip().lower()
    if action == "a":
        _run_apply(item.app, item.job)
    elif action == "o":
        import webbrowser
        webbrowser.open(item.job.url)
    elif action == "s":
        console.print("  [blue]Skipped[/blue]")


@app.command(name="fresher", hidden=False)
def fresher(
    limit: int = typer.Option(30, "--limit", help="How many opportunities to list"),
    local: bool = typer.Option(True, "--local/--all", help="Indian companies only (default local)"),
    quiet: bool = typer.Option(False, "--quiet", help="Just print the report, no interactive loop"),
):
    """🎓 Indian fresher opportunities — where an interview is realistic.

    Five honest states (confirmed / high / plausible / experienced /
    insufficient). Explicit experience requirements always beat company
    hints, and India-visible is enforced separately from classification.
    """
    _fresher_screen(limit=limit, local=local, quiet=quiet)


def _apply_now_screen() -> bool:
    """Apply Now screen — dig pipeline with role-family relevance for SWE."""
    from services.dig import run_dig, pending_unscored_count
    from services.user_profile import load_profile
    from services.probability import estimate

    profile = load_profile()

    # Score pending jobs first
    pending = pending_unscored_count()
    if pending:
        import asyncio
        try:
            asyncio.run(score_pending_jobs())
        except Exception as e:
            logger.debug("Scoring before dig failed (non-fatal): %s", e)

    result = run_dig(fresh_only=True, strict=True, min_priority=4.0, local_only=True)

    console.clear()
    console.print("[bold red]🔥 APPLY NOW[/bold red]\n")

    if not result.items:
        console.print("[yellow]No high-priority opportunities right now.[/yellow]")
        console.print("  [dim]Try: jobzo dig --local --min-priority 3[/dim]\n")
        input("  Press Enter to return...")
        return True

    # Re-sort / re-filter by estimate priority + role-family
    scored = []
    for item in result.items:
        try:
            e = estimate(item.job, profile)
            est_priority = round(e["score"] / 10.0, 1)
        except Exception:
            est_priority = item.priority
        scored.append((item, est_priority))
    scored.sort(key=lambda x: -x[1])

    # Show top N with priority >= 4.0
    top_items = [s[0] for s in scored if s[1] >= 4.0][:15]

    if not top_items:
        console.print("[yellow]No opportunities meet the role-family + priority threshold.[/yellow]")
        console.print("  [dim]Try running [green]jobzo dig --local[/green] to see all.[/dim]\n")
        input("  Press Enter to return...")
        return True

    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="right")
    table.add_column("Company", style="bold")
    table.add_column("Role")
    table.add_column("Est Priority", justify="right")
    table.add_column("Dig Priority", justify="right")
    table.add_column("Trust", justify="right")
    table.add_column("Fresh", justify="right")

    for idx, item in enumerate(top_items, 1):
        est = estimate(item.job, profile)
        trust = item.trust
        fresh = item.freshness
        trust_display = f"{trust.emoji} {trust.score:.0f}" if trust else "— —"
        fresh_display = f"{fresh.get('emoji', '')} {fresh.get('label', '')}"
        table.add_row(
            str(idx),
            item.job.company,
            item.job.title[:40],
            f"{item[1]:.1f}",
            f"{item.priority:.1f}",
            Text(trust_display, style={"verified": "green", "trusted": "cyan", "unverified": "yellow", "suspicious": "red"}.get(trust.tier if trust else "none", "white")),
            fresh_display,
        )
    console.print(table)

    console.print()
    console.print("[dim]Pick a job to act on (or [green]0[/green] back):[/dim]")
    choice = input("  → ").strip().lower()
    if choice in ("0", "q", "quit", "exit", ""):
        return True
    if not choice.isdigit():
        return _apply_now_screen()
    idx = int(choice) - 1
    if idx < 0 or idx >= len(top_items):
        return _apply_now_screen()
    item = top_items[idx]
    _show_job_card(item.app, item.job, idx + 1, len(top_items))
    console.print()
    action = input("Action [a]pply / [o]pen / [s]kip / [b]ack: ").strip().lower()
    if action == "a":
        _run_apply(item.app, item.job)
    elif action == "o":
        import webbrowser
        webbrowser.open(item.job.url)
    elif action == "s":
        console.print("  [blue]Skipped[/blue]")
    input("  Press Enter to return...")
    return _apply_now_screen()


def _fresher_screen(limit: int = 30, local: bool = True, quiet: bool = False) -> bool:
    """Render the fresher report + interactive loop. Returns True to stay in
    the caller's mission loop, False when the user quits out entirely."""
    from services.fresher import (
        FRESHER_CONFIRMED,
        FRESHER_EXPERIENCED,
        FRESHER_HIGH,
        FRESHER_INSUFFICIENT,
        FRESHER_PLAUSIBLE,
        fresher_funnel,
        top_fresher_jobs,
    )

    f = fresher_funnel(local_only=local)
    t = f["totals"]
    e = f["experience"]
    funnel = f["funnel"]

    console.clear()
    console.print("[bold yellow]🎓 FRESHER — realistic Indian openings[/bold yellow]\n")

    label = "local" if local else "all companies"
    console.print(f"[dim]Scope: {label} · {t['collected']} real jobs[/dim]\n")

    console.print("[bold]🇮🇳 INDIA FRESHER FUNNEL[/bold]")
    console.print(f"  [bold]{funnel['collected']:>4}[/bold] real jobs")
    console.print(f"    ↓ {funnel['india_visible']:>3} India-visible")
    console.print(f"    ↓ {funnel['fresher']:>3} fresher / plausible")
    console.print(f"    ↓ {funnel['profile_match']:>3} match your profile")
    console.print(f"    ↓ {funnel['high_priority']:>3} high-priority")
    console.print(f"    ↓ [bold green]{funnel['apply_now']:>3} apply now[/bold green]\n")

    console.print("[bold]Experience classification (across all collected)[/bold]")
    console.print(f"  [green]🎓 confirmed         {e[FRESHER_CONFIRMED]:>4}[/green]")
    console.print(f"  [cyan]🟢 high probability   {e[FRESHER_HIGH]:>4}[/cyan]")
    console.print(f"  [yellow]🟡 plausible          {e[FRESHER_PLAUSIBLE]:>4}[/yellow]")
    console.print(f"  [red]🔴 experienced        {e[FRESHER_EXPERIENCED]:>4}[/red]")
    console.print(f"  [dim]⚪ insufficient signal {e[FRESHER_INSUFFICIENT]:>4}[/dim]")
    console.print()

    top = top_fresher_jobs(limit=limit, local_only=local)
    hidden_exp = e[FRESHER_EXPERIENCED]

    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="right")
    table.add_column("Fresher", justify="center")
    table.add_column("Company", style="bold")
    table.add_column("Role")
    table.add_column("Priority", justify="right")
    table.add_column("Trust", justify="right")
    table.add_column("Fresh", justify="right")

    icons = {FRESHER_CONFIRMED: "🎓", FRESHER_HIGH: "🟢", FRESHER_PLAUSIBLE: "🟡",
             FRESHER_EXPERIENCED: "🔴", FRESHER_INSUFFICIENT: "⚪"}
    for idx, x in enumerate(top, 1):
        job, fr = x["job"], x["fresher"]
        trow = x["trust"]
        trust_style = {"verified": "green", "trusted": "cyan",
                       "unverified": "yellow", "suspicious": "red"}.get(trow.tier, "white")
        fresh = x["freshness"]
        fresh_display = f"{fresh.get('emoji', '')} {fresh.get('label', '')}"
        table.add_row(
            str(idx),
            f"{icons[fr.status]} {fr.status[:4]}",
            job.company,
            job.title[:40],
            f"{x['priority']:.1f}",
            Text(f"{trow.emoji} {trow.score:.0f}", style=trust_style),
            fresh_display,
        )
    console.print(table)

    if hidden_exp:
        console.print(f"\n  [dim]🚫 HIDDEN: {hidden_exp} experienced roles — explicit 2+ yrs / senior "
                      f"titles. Use [green]jobzo dig[/green] for everything.[/dim]")
    console.print()

    if quiet:
        return True

    while True:
        console.print("[dim]Pick a job (or [green]0[/green] back / [q] quit):[/dim]")
        choice = input("  → ").strip().lower()
        if choice in ("0", "q", "quit", "exit", ""):
            return choice not in ("q", "quit", "exit")
        if not choice.isdigit():
            continue
        idx = int(choice) - 1
        if idx < 0 or idx >= len(top):
            console.print("[red]Invalid choice[/red]")
            continue
        x = top[idx]
        _show_fresher_card(x)
        console.print()
        action = input("Action [a]pply / [o]pen / [s]kip / [b]ack: ").strip().lower()
        if action == "a":
            _run_apply(x["job"].application if hasattr(x["job"], "application") else None, x["job"])
        elif action == "o":
            import webbrowser
            webbrowser.open(x["job"].url)
        elif action == "s":
            console.print("  [blue]Skipped[/blue]")
        input("  Press Enter to return to fresher opportunities...")
        console.clear()
        return _fresher_screen(limit=limit, local=local, quiet=True) and True


def _show_fresher_card(x: dict) -> None:
    """Auditable job card: fresher evidence, priority, trust, eligibility."""
    job, fr = x["job"], x["fresher"]
    trust = x["trust"]
    fresh = x["freshness"]

    console.clear()
    console.print(f"[bold]{job.company} — {job.title}[/bold]")
    console.print(f"  [dim]{job.location or '—'} · {job.url}[/dim]\n")

    pct = int(fr.prob * 100)
    prob_color = "green" if fr.prob >= 0.8 else "yellow" if fr.prob >= 0.5 else "red"
    console.print(f"  Fresher probability: [bold {prob_color}]{pct}%[/bold {prob_color}]  "
                  f"Status: {fr.status}")
    console.print(f"  Apply priority: [bold]{x['priority']:.1f}/10[/bold]   "
                  f"Trust: {trust.emoji} {trust.score:.0f} ({trust.tier})   "
                  f"Fresh: {fresh.get('label', '?')}")

    if job.eligible is True:
        console.print("  [green]✓[/green] Passes eligibility")
    elif job.eligible is False:
        console.print("  [red]✗[/red] Blocked by eligibility engine")
    else:
        console.print("  [dim]? Not yet scored by eligibility[/dim]")

    print()
    for ev in fr.evidence:
        console.print(f"  [green]{ev}[/green]")
    for neg in fr.negatives:
        console.print(f"  [red]{neg}[/red]")
    if not fr.evidence and not fr.negatives:
        console.print("  [dim]? No fresher evidence in JD[/dim]")
    print()


@app.command(name="search", hidden=True)
def search_jobs(
    query: str = typer.Argument("", help="Free-text search across job titles and companies"),
    min_score: int = typer.Option(0, "--min-score", "-s", help="Minimum score filter"),
    location: str = typer.Option("", "--location", "-l", help="Filter by location"),
):
    """Search collected jobs by keyword, score, or location."""
    from sqlalchemy import or_ as sql_or
    session = get_session()
    try:
        q = session.query(Job).options(joinedload(Job.application)).filter(Job.is_active == True)
        if query:
            like = f"%{query}%"
            q = q.filter(
                sql_or(Job.title.ilike(like), Job.company.ilike(like), Job.description.ilike(like))
            )
        if location:
            q = q.filter(Job.location.ilike(f"%{location}%"))
        if min_score:
            q = q.join(Application).filter(Application.score >= min_score)
        q = q.order_by(Job.created_at.desc()).limit(30)
        jobs = q.all()
        if not jobs:
            console.print("[yellow]No jobs match your search[/yellow]")
            return
        table = Table(title=f"Search Results: {query or 'all'}")
        table.add_column("#"); table.add_column("Company"); table.add_column("Title")
        table.add_column("Location"); table.add_column("Score"); table.add_column("Status")
        for idx, job in enumerate(jobs, 1):
            app = job.application; score = str(app.score) if app else "—"
            status = app.status if app else "collected"
            table.add_row(str(idx), job.company, job.title[:45], job.location or "", score, status)
        console.print(table)
    finally:
        session.close()


@app.command()
def apply(
    app_id: str = typer.Argument(None, help="Application ID to apply to"),
    daily: bool = typer.Option(False, "--daily", help="Apply to top 5 drafted applications"),
):
    """Submit an application: resume → cover letter → browser → confirm."""
    session = get_session()

    try:
        if daily:
            apps = session.query(Application).filter(
                Application.status == "drafted",
            ).order_by(Application.score.desc()).limit(5).all()
        elif app_id:
            app_obj = session.query(Application).filter(
                Application.id.startswith(app_id)
            ).first()
            apps = [app_obj] if app_obj else []
        else:
            apps = session.query(Application).filter(
                Application.status.in_(["drafted", "ready"]),
                Application.strategy != "skip",
            ).order_by(Application.score.desc()).limit(10).all()

            if not apps:
                console.print("[yellow]No drafted or ready applications[/yellow]")
                return

            console.print("[bold yellow]Choose an application to submit:[/bold yellow]\n")
            for i, a in enumerate(apps, 1):
                j = session.query(Job).filter(Job.id == a.job_id).first()
                company = j.company if j else "?"
                title = j.title[:50] if j else "?"
                console.print(f"  [bold]{i}.[/bold] {company} — {title}  [dim](score: {a.score})[/dim]")

            console.print()
            choice = input("Choose a job (1-N) or 0 to cancel: ").strip()

            if not choice.isdigit() or int(choice) == 0:
                return

            idx = int(choice) - 1
            if idx < 0 or idx >= len(apps):
                console.print("[red]Invalid choice[/red]")
                return

            apps = [apps[idx]]

        if not apps:
            console.print("[yellow]No applications to process[/yellow]")
            return

        for idx, application in enumerate(apps, 1):
            job = session.query(Job).filter(Job.id == application.job_id).first()
            if not job:
                continue

            console.print(f"\n[bold cyan][{idx}/{len(apps)}][/bold cyan] {job.company} — {job.title}")
            console.print(f"  Score: {application.score}/100")
            console.print(f"  Strategy: {application.strategy}")

            # Check platform quota
            from services.quotas import is_exhausted, quota_usage
            src = job.source or ""
            if is_exhausted(src):
                console.print(f"  [red]⚠ {src} limit reached this week[/red]")
                alt = [q for q in quota_usage() if not q["exhausted"] and q["limit"] is not None]
                if alt:
                    names = ", ".join(a["platform"] for a in alt[:3])
                    console.print(f"  [dim]   Try: {names}[/dim]")
                console.print("  [yellow]Skipping — apply from other sources[/yellow]")
                continue

            resume_path = application.resume_used or ""
            cover_letter = ""

            if application.status in ("submitted", "skipped", "rejected", "offered"):
                console.print(f"  [yellow]Already {application.status} — skipping[/yellow]")
                continue

            if application.strategy == "skip":
                console.print("  [yellow]Skipping (strategy: skip)[/yellow]")
                continue

            from ai.cover_letter import generate_cover_letter
            cover_letter = generate_cover_letter(
                company=job.company,
                role=job.title,
                description=job.description,
                resume_type=resume_path,
            )

            if not resume_path:
                console.print("  [yellow]No resume selected, applying default[/yellow]")
                resume_path = "backend_v3.pdf"

            if application.status == "drafted":
                transition_status(str(application.id), "ready")

            from services.app_log import log_application
            import time

            async def run_apply():
                assistant = BrowserAssistant()
                start_ts = time.time()
                submitted = False
                ats = ""
                fields_filled = 0
                fields_total = 0
                try:
                    await assistant.start()
                    await assistant.navigate(job.url)
                    for d in KNOWN_ATS_DOMAINS:
                        if d in job.url:
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
                        await assistant.autofill(resume_path, cover_letter, job.url)
                        results = getattr(assistant, "_results", {})
                        fields_filled = sum(1 for v in results.values() if v)
                        fields_total = len(results)
                        confirmed = await assistant.wait_for_confirmation()
                        if confirmed:
                            transition_status(str(application.id), "submitted")
                            submitted = True
                            from services.quotas import record_submission
                            record_submission(job.source or "manual", job.company, job.title)
                            console.print(f"  [green]✓[/green] Marked as submitted")
                        else:
                            console.print("  [yellow]Skipped[/yellow]")
                    else:
                        console.print("  [yellow]Form not detected. Apply manually.[/yellow]")
                        transition_status(str(application.id), "submitted")
                        submitted = True
                        from services.quotas import record_submission
                        record_submission(job.source or "manual", job.company, job.title)
                except Exception as e:
                    logger.error("Browser automation failed: %s", e)
                    console.print(f"  [red]Browser error: {e}[/red]")
                finally:
                    await assistant.close()
                    elapsed = int(time.time() - start_ts)
                    log_application(
                        company=job.company,
                        ats=ats or "Unknown",
                        time_seconds=elapsed,
                        fields_filled=fields_filled,
                        fields_manual=fields_total - fields_filled,
                        resume=resume_path,
                        cover_letter="template" if "I am excited" not in cover_letter else "template",
                        submitted=submitted,
                        title=job.title,
                    )

            asyncio.run(run_apply())

    finally:
        session.close()


@app.command(hidden=True)
def export(
    fmt: str = typer.Argument("csv", help="Export format (csv)"),
    out: str = typer.Option("applications.csv", "--out", "-o", help="Output file path"),
):
    """Export applications to CSV for analysis."""
    import csv

    session = get_session()
    try:
        apps = session.query(Application).options(joinedload(Application.job)).order_by(
            Application.created_at.desc()
        ).all()
        if not apps:
            console.print("[yellow]No applications to export[/yellow]")
            return

        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "company", "title", "url", "status", "score",
                "strategy", "resume", "applied_at", "response_date",
                "interview_date", "first_response_at", "last_activity_at",
                "source", "location", "notes",
            ])
            for a in apps:
                j = a.job
                writer.writerow([
                    a.id, j.company if j else "", j.title if j else "",
                    j.url if j else "", a.status, a.score, a.strategy,
                    a.resume_used, a.applied_at, a.response_date,
                    a.interview_date, a.first_response_at, a.last_activity_at,
                    j.source if j else "", j.location if j else "", a.notes,
                ])
        console.print(f"[green]✓[/green] Exported {len(apps)} applications to [bold]{out}[/bold]")
    finally:
        session.close()


def _fetch_page_js(url: str, timeout: int = 15) -> str | None:
    """Fetch a URL's rendered HTML, using Playwright when available."""
    try:
        return _fetch_with_playwright(url, timeout)
    except Exception:
        pass
    try:
        import httpx
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.text
    except Exception as e:
        logger.debug("HTTP fallback failed: %s", e)
        return None


def _fetch_with_playwright(url: str, timeout: int) -> str:
    import asyncio
    from playwright.async_api import async_playwright

    async def _get():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = await page.content()
            await browser.close()
            return html

    return asyncio.run(_get())


@app.command(hidden=True)
def import_url(
    url: str = typer.Argument(..., help="Job posting URL to import"),
    title: str = typer.Option("", "--title", "-t", help="Job title (auto-detected if empty)"),
    company: str = typer.Option("", "--company", "-c", help="Company name (auto-detected if empty)"),
    remote: bool = typer.Option(False, "--remote", "-r", help="Mark as remote"),
):
    """Import a job URL for scoring and tracking."""
    from providers.manual import ManualProvider

    if not company:
        m = re.search(r"/companies/([^/]+)", url)
        if m:
            company = m.group(1).replace("-", " ").replace("_", " ").title()

    if not title:
        m = re.search(r"/jobs/[^-]+-(.+)", url)
        if m:
            title = m.group(1).replace("-", " ").title()

    if not company or not title:
        html = _fetch_page_js(url)
        if html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            if not title and soup.title:
                title = soup.title.string.strip()
            if not company:
                for meta in soup.find_all("meta", property=re.compile(r"(og|twitter):(site_name|title)", re.I)):
                    c = meta.get("content", "")
                    if c and "yc" not in c.lower():
                        company = c
                        break
                if not company:
                    for cls in ("company", "org", "employer"):
                        tag = soup.find(class_=re.compile(cls, re.I))
                        if tag:
                            company = tag.get_text(strip=True)
                            break

    ManualProvider.add_to_queue(url=url, company=company, title=title, remote=remote)
    console.print(f"[green]✓[/green] Imported [bold]{title or url}[/bold]")
    if company:
        console.print(f"  Company: {company}")
    console.print("\nRun [bold]jobzo collect[/bold] to process this job.")


@app.command(hidden=True)
def track(
    app_id: str = typer.Argument(None, help="Application ID to update"),
    status: str = typer.Option("", "--status", "-s", help="New status: submitted, interview, rejected, offer"),
    note: str = typer.Option("", "--note", "-n", help="Add a note about this application"),
):
    """Show application dashboard or update an application's outcome."""
    if app_id and status:
        valid = {"submitted", "interview", "rejected", "offer", "drafted", "ready"}
        if status not in valid:
            console.print(f"[red]Invalid status. Choose from: {', '.join(sorted(valid))}[/red]")
            raise typer.Exit(1)

        session = get_session()
        app_obj = session.query(Application).filter(Application.id.startswith(app_id)).first()
        if not app_obj:
            console.print(f"[red]Application matching '{app_id}' not found[/red]")
            session.close()
            return
        success = transition_status(str(app_obj.id), status)
        if not success:
            console.print(f"[red]Could not transition to '{status}'[/red]")
            session.close()
            return
        msg = f"✓ {app_id[:8]} marked as {status}"
        if note:
            existing = app_obj.notes or ""
            app_obj.notes = (existing + "\n" + note).strip()
            session.commit()
            msg += f" with note"
        session.close()
        console.print(f"[green]{msg}[/green]")
        return

    if app_id:
        session = get_session()
        app_obj = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id.startswith(app_id)).first()
        if not app_obj:
            session.close()
            console.print(f"[red]Application matching '{app_id}' not found[/red]")
            return
        job = app_obj.job
        console.print(Panel(
            f"[bold]{job.company}[/bold] — {job.title}\n"
            f"Status: [bold]{app_obj.status}[/bold] | Score: {app_obj.score}/100\n"
            f"Strategy: {app_obj.strategy} | Resume: {app_obj.resume_used or 'N/A'}\n"
            f"Applied: {app_obj.applied_at.strftime('%b %d, %Y') if app_obj.applied_at else 'Not yet'}\n"
            f"Notes: {app_obj.notes or 'None'}",
            title=f"Application {app_id[:8]}",
        ))
        session.close()
        return

    apps = list_applications()

    if not apps:
        console.print("[yellow]No applications yet. Run 'jobzo collect' and 'jobzo rank' first.[/yellow]")
        return

    table = Table(title="Applications")
    table.add_column("ID")
    table.add_column("Status", style="bold")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Score")
    table.add_column("Strategy")
    table.add_column("Applied")

    for app in apps:
        job = app.job
        company = job.company if job else "?"
        role = job.title if job else "?"
        applied = app.applied_at.strftime("%b %d") if app.applied_at else "—"
        style = {
            "drafted": "yellow",
            "ready": "blue",
            "submitted": "cyan",
            "interview": "green",
            "rejected": "red",
            "offer": "bold green",
        }.get(app.status, "white")

        table.add_row(
            str(app.id)[:8],
            Text(app.status, style=style),
            company, role,
            str(app.score),
            app.strategy,
            applied,
        )

    console.print(table)

    pending = list_pending_tasks()
    if pending:
        task_table = Table(title="Pending Tasks")
        task_table.add_column("ID")
        task_table.add_column("Type")
        task_table.add_column("Title")
        task_table.add_column("Due")
        for t in pending:
            task_table.add_row(str(t.id)[:8], t.type, t.title, str(t.due_date or ""))
        console.print(task_table)


@app.command(hidden=True)
def task(
    complete: str = typer.Option("", "--done", help="Task ID to mark complete"),
):
    """View pending tasks."""
    if complete:
        if complete_task(complete):
            console.print(f"[green]✓[/green] Task {complete[:8]} completed")
        else:
            console.print(f"[red]Task {complete[:8]} not found[/red]")
        return

    pending = list_pending_tasks()
    if not pending:
        console.print("[green]No pending tasks[/green]")
        return

    table = Table(title="Pending Tasks")
    table.add_column("ID")
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("Due")
    for t in pending:
        table.add_row(str(t.id)[:8], t.type, t.title, str(t.due_date or ""))
    console.print(table)


@app.command(hidden=True)
def daily(
    skills: str = typer.Option("", help="Comma-separated skill keywords"),
    experience: int = typer.Option(1, help="Your years of experience"),
):
    """Full daily workflow: collect → rank → apply top 5."""
    console.print("[bold cyan]JobZo — Daily Workflow[/bold cyan]")
    console.print(f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n")

    console.print("[bold]Step 1/3: Collecting jobs...[/bold]")
    kw_list = [k.strip() for k in skills.split(",") if k.strip()] or None
    collected = asyncio.run(collect_all(kw_list))
    console.print(f"[green]✓[/green] {collected} new jobs\n")

    console.print("[bold]Step 2/3: Ranking jobs...[/bold]")
    skill_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    r = asyncio.run(score_pending_jobs(skill_list, experience))
    scored = r["scored"]
    console.print(f"[green]✓[/green] {scored} jobs scored\n")

    console.print("[bold]Step 3/3: Applying to top 5...[/bold]")
    apply(app_id=None, daily=True)


@app.command(hidden=True)
def skill_gap():
    """Analyze skill demand from collected jobs."""
    session = get_session()
    try:
        jobs = session.query(Job).filter(Job.is_active == True).limit(200).all()
    finally:
        session.close()

    if not jobs:
        console.print("[yellow]No jobs collected yet. Run 'jobzo collect' first.[/yellow]")
        return

    from collections import Counter
    from ai.llm import ask

    all_descriptions = "\n---\n".join(
        f"{j.company}: {j.title}\n{j.description[:500]}" for j in jobs
    )

    try:
        result = ask("skill_extract", all_descriptions[:5000])
        skills = result.get("skills", []) if isinstance(result, dict) else []
    except Exception as e:
        console.print(f"[red]Skill extraction failed: {e}[/red]")
        return

    if not skills:
        console.print("[yellow]Could not extract skills[/yellow]")
        return

    skill_counts = Counter(s.lower().strip() for s in skills)

    table = Table(title="Skill Demand (Top 15)")
    table.add_column("Skill")
    table.add_column("Mentions")
    table.add_column("In Your Resumes?")

    resumes = Config.resume_config().get("resumes", {})
    all_resume_skills = set()
    for r in resumes.values():
        meta_path = Path(__file__).parent.parent / r.get("metadata", "")
        if meta_path.exists():
            import json
            try:
                meta = json.loads(meta_path.read_text())
                all_resume_skills.update(s.lower() for s in meta.get("skills", []))
            except (json.JSONDecodeError, OSError):
                pass

    for skill, count in skill_counts.most_common(15):
        has_it = "[green]Yes[/green]" if skill in all_resume_skills else "[red]No[/red]"
        table.add_row(skill.capitalize(), str(count), has_it)

    console.print(table)

    missing = [s for s, _ in skill_counts.most_common(15) if s not in all_resume_skills]
    if missing:
        console.print("\n[bold yellow]Skill Gaps (Consider Learning):[/bold yellow]")
        for s in missing[:5]:
            console.print(f"  • {s.capitalize()}")


@app.command(hidden=True)
def sync(
    skills: str = typer.Option("", help="Comma-separated skill keywords"),
    experience: int = typer.Option(1, help="Your years of experience"),
):
    """Refresh data: collect new jobs and score them."""
    console.print("[bold cyan]JobZo Sync[/bold cyan] — Discovering new opportunities\n")

    console.print("[bold]Step 1/2: Collecting jobs...[/bold]")
    start = datetime.utcnow()
    kw_list = [k.strip() for k in skills.split(",") if k.strip()] or None
    collected = asyncio.run(collect_all(kw_list))
    collect_time = (datetime.utcnow() - start).total_seconds()

    console.print("[bold]Step 2/2: Scoring jobs...[/bold]")
    start = datetime.utcnow()
    skill_list = [s.strip() for s in skills.split(",") if s.strip()] or None
    r = asyncio.run(score_pending_jobs(skill_list, experience))
    scored = r["scored"]
    score_time = (datetime.utcnow() - start).total_seconds()

    total_time = collect_time + score_time

    # Gather metrics from DB
    session = get_session()
    try:
        total_jobs = session.query(Job).count()
        eligible = session.query(Job).filter(Job.eligible == True).count()
        ineligible = session.query(Job).filter(Job.eligible == False).count()
        recommended = session.query(Application).filter(
            Application.status.in_(["drafted", "recommended"])
        ).count()
        companies = session.query(Job.company).distinct().count()
        duplicates = session.query(Job).filter(Job.eligible == False, Job.eligibility_reason.like("%duplicate%")).count()
        # Count jobs with scrape metadata as proxy for pages scanned
        scanned = session.query(Job).count()  # all discovered jobs
    finally:
        session.close()

    console.print()
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    table.add_row("Companies discovered", str(companies))
    table.add_row("Total jobs found", str(total_jobs))
    table.add_row("New this sync", str(collected))
    table.add_row("Scored this sync", str(scored))
    table.add_row("Eligible", str(eligible))
    table.add_row("Ineligible (hidden)", str(ineligible))
    table.add_row("Recommended/applications ready", str(recommended))
    table.add_row("Collection time", f"{collect_time:.1f}s")
    table.add_row("Scoring time", f"{score_time:.1f}s")
    table.add_row("Total time", f"{total_time:.1f}s")
    console.print(table)
    console.print(f"\n[green]✓[/green] Sync complete. Run [bold]jobzo[/bold] to see your feed.")


@app.command(hidden=True)
def fit(
    job_id: str = typer.Argument(..., help="Job ID to analyze"),
):
    """Show job fit report — how well your resume matches a specific job."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            console.print(f"[red]Job not found: {job_id}[/red]")
            return
    finally:
        session.close()

    registry = get_registry()
    jd_text = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    report = generate_fit_report(
        job.company, job.title, jd_text, registry,
        is_eligible=job.eligible, location=job.location,
    )
    console.print(Panel.fit(report.format_text(), box=box.ROUNDED, padding=(1, 2)))

    if report.recommended_resume:
        meta = registry.get(report.recommended_resume)
        if meta:
            from database.models import Application
            app = session.query(Application).filter(Application.job_id == job_id).first()
            if app:
                prediction = predict_interview(app, job, jd_text)
                cf = counterfactual(app, job, jd_text)
                console.print()
                console.print(Panel.fit(
                    prediction.format_text(),
                    box=box.ROUNDED,
                    padding=(1, 2),
                    title="Decision Intelligence",
                ))
                if cf.recommendation:
                    console.print(f"\n[yellow]{cf.recommendation}[/yellow]")


@app.command(hidden=True)
def roadmap(
    source: str = typer.Option("recommended", help="Source: recommended (default), all, applied"),
    company: str = typer.Option("", help="Filter by company name"),
    role: str = typer.Option("", help="Filter by role keyword"),
    max_skills: int = typer.Option(20, help="Max skills to show"),
):
    """Show skill demand from jobs. Default: recommended jobs only."""
    registry = get_registry()
    company_filter = company or None
    role_filter = role or None
    road = build_roadmap(registry, status_filter=source, company_filter=company_filter, role_filter=role_filter)
    console.print(Panel.fit(road.format_text(max_skills=max_skills), box=box.ROUNDED, padding=(1, 2)))


def _cmd_interview(job_id: str):
    """Generate and display interview prep plan."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            console.print(f"[red]Job not found: {job_id}[/red]")
            return
        jd_text = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    finally:
        session.close()

    plan = prepare(job.company, job.title, jd_text, location=job.location)
    console.print(Panel.fit(plan.format_text(), box=box.ROUNDED, padding=(1, 2)))


@app.command()
def interview(
    job_id: str = typer.Argument(..., help="Job ID to prepare for"),
):
    """Generate interview preparation plan for a specific job."""
    _cmd_interview(job_id)


@app.command(hidden=True)
def prepare_for_interview(
    job_id: str = typer.Argument(..., help="Job ID to prepare for"),
):
    """Alias for interview."""
    _cmd_interview(job_id)


@app.command(hidden=True)
def quality(
    job_id: str = typer.Argument(..., help="Job ID to analyze"),
    resume: str = typer.Option("", help="Resume variant to evaluate"),
):
    """Compute pre-submission application quality score."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            console.print(f"[red]Job not found: {job_id}[/red]")
            return
        jd_text = f"{job.title}\n{job.company}\n{job.location}\n{job.description}"
    finally:
        session.close()

    registry = get_registry()
    resume_name = resume or ""
    if not resume_name:
        # Use the best resume from the fit report
        from resumes.fit_report import generate as generate_fit_report
        report = generate_fit_report(job.company, job.title, jd_text, registry, is_eligible=job.eligible)
        resume_name = report.recommended_resume

    meta = registry.get(resume_name) if resume_name else None
    if not meta:
        console.print(f"[red]Resume '{resume_name}' not found[/red]")
        console.print(f"Available: {', '.join(registry.names())}")
        return

    qs = compute_quality_score(job, meta, jd_text)
    console.print(Panel.fit(
        qs.format_text(),
        box=box.ROUNDED,
        padding=(1, 2),
        title=f"Quality — {job.company} {job.title}",
    ))

    # Show improvement estimate
    if qs.missing_skills:
        console.print(f"\n[yellow]If you add '{', '.join(qs.missing_skills[:3])}', probability improves by ~{min(len(qs.missing_skills) * 3, 15)}%[/yellow]")


@app.command(hidden=True)
def verify(
    application_id: str = typer.Argument(..., help="Application ID"),
    ats_id: str = typer.Option("", help="ATS application ID"),
    portal_url: str = typer.Option("", help="Applicant portal URL"),
):
    """Record ATS confirmation for an application."""
    result = verify_application(application_id, ats_id=ats_id, portal_url=portal_url, confirmed=True)
    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/red]")
    else:
        console.print(f"[green]✓[/green] Application {application_id} verified")


@app.command()
def crm(
    company: str = typer.Option("", help="Filter by company"),
):
    """View applicant CRM — contacts, interactions, and follow-ups."""
    session = get_session()
    try:
        if company:
            contacts_q = session.query(Contact).filter(Contact.company.ilike(f"%{company}%")).all()
        else:
            contacts_q = session.query(Contact).order_by(Contact.last_contacted.desc().nullslast()).limit(20).all()
    finally:
        session.close()

    if not contacts_q:
        console.print("[yellow]No contacts yet. Add one with 'jobzo contact-add'[/yellow]")
        return

    table = Table(title=f"Contacts ({'company: ' + company if company else 'recent 20'})")
    table.add_column("Name", style="cyan")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Next Follow-up")
    table.add_column("Score")

    for c in contacts_q:
        status = ""
        now = datetime.utcnow()
        if c.next_followup and c.next_followup <= now:
            status = "[red]Follow-up due[/red]"
        elif c.last_contacted:
            days = (now - c.last_contacted).days if c.last_contacted else 0
            status = f"[yellow]{days}d ago[/yellow]"
        next_up = c.next_followup.strftime("%b %d") if c.next_followup else ""
        table.add_row(
            c.name, c.company, c.role, status, next_up,
            f"{'★' * min(c.relationship_score // 20 + 1, 5)}",
        )

    console.print(table)


@app.command(name="contact-add")
def contact_add(
    company: str = typer.Argument(..., help="Company name"),
    name: str = typer.Argument(..., help="Contact name"),
    role: str = typer.Option("", help="Role (Recruiter, HM, etc.)"),
    email: str = typer.Option("", help="Email address"),
    linkedin: str = typer.Option("", help="LinkedIn URL"),
):
    """Add a contact to the CRM."""
    session = get_session()
    try:
        contact = Contact(
            company=company,
            name=name,
            role=role,
            email=email,
            linkedin=linkedin,
        )
        session.add(contact)
        session.commit()
        console.print(f"[green]✓[/green] Added {name} ({role}) at {company}")
    except Exception as e:
        session.rollback()
        console.print(f"[red]Error: {e}[/red]")
    finally:
        session.close()


@app.command()
def reach(
    application_id: str = typer.Argument(..., help="Application ID"),
):
    """Generate a reach-out email for an application and track it."""
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id.startswith(application_id)).first()
        if not app:
            console.print(f"[red]Application matching '{application_id}' not found[/red]")
            return
        job = app.job
        if not job:
            console.print("[red]Application has no linked job[/red]")
            return

        contact = find_contact(session, job.company)
        if not contact:
            name = input(f"  Recruiter name for {job.company}: ").strip() or "Hiring Team"
            role = input(f"  Role (Recruiter/HM, default Recruiter): ").strip() or "Recruiter"
            contact = Contact(company=job.company, name=name, role=role, source="jobzo_reach")
            session.add(contact)
            session.commit()
            session.refresh(contact)

        contact_name = contact.name
        contact_role = contact.role
        contact_id = contact.id
        company = job.company
        title = job.title
        app_id = app.id
    finally:
        session.close()

    draft = generate_email_draft(company, title, contact_name)

    console.print(Panel.fit(
        f"[bold blue]Reach-Out Draft[/bold blue]\n"
        f"\n"
        f"[bold]To:[/bold] {contact_name} ({contact_role}) at {company}\n"
        f"[bold]Re:[/bold] {title}\n"
        f"\n"
        f"[bold]Subject:[/bold] {draft['subject']}\n"
        f"\n"
        f"{draft['body']}",
        box=box.ROUNDED,
        padding=(1, 2),
    ))

    action = input("\nMark as sent? [y/n/c] (y=mark sent, c=customize, n=cancel): ").strip().lower()
    if action == "c":
        subject = input("Subject: ").strip() or draft["subject"]
        body = input("Body (multi-line, empty line to finish):\n").strip()
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        body = body + "\n" + "\n".join(lines) if lines else body
        draft = {"subject": subject, "body": body}
        action = input("\nSend this customized version? [y/n]: ").strip().lower()

    if action == "y":
        interaction_id = log_interaction(contact_id, app_id, draft["subject"], draft["body"])
        console.print(f"[green]✓[/green] Reach-out logged (ID: {interaction_id[:8]})")
    else:
        console.print("[yellow]Reach-out skipped[/yellow]")


@app.command(name="contact-interactions", hidden=True)
def contact_interactions(
    contact_id: str = typer.Argument(..., help="Contact ID"),
):
    """View all interactions with a specific contact."""
    session = get_session()
    try:
        contact = session.get(Contact, contact_id)
        if not contact:
            console.print(f"[red]Contact '{contact_id}' not found[/red]")
            return
        contact_name = contact.name
        contact_company = contact.company
    finally:
        session.close()

    interactions = get_contact_interactions(contact_id)

    if not interactions:
        console.print(f"[yellow]No interactions with {contact_name} yet[/yellow]")
        return

    table = Table(title=f"Interactions with {contact_name} ({contact_company})")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Direction")
    table.add_column("Subject")
    table.add_column("Outcome")

    for i in interactions:
        occurred = i["occurred_at"]
        table.add_row(
            occurred.strftime("%b %d") if occurred else "-",
            i["type"],
            i["direction"],
            i["subject"][:40] if i["subject"] else "-",
            i["outcome"] or "-",
        )

    console.print(table)


@app.command(hidden=True)
def decide(
    application_id: str = typer.Argument(..., help="Application ID"),
    simulate_resume: str = typer.Option("", "--simulate-resume", help="Simulate a different resume"),
    simulate_skill: str = typer.Option("", "--simulate-skill", help="Simulate adding a skill"),
):
    """Interview probability prediction with personalized weights and simulation."""
    session = get_session()
    try:
        app = session.query(Application).options(
            joinedload(Application.job)
        ).filter(Application.id.startswith(application_id)).first()
        if not app:
            console.print(f"[red]Application '{application_id}' not found[/red]")
            return
        job = app.job
        if not job:
            console.print("[red]Application has no linked job[/red]")
            return
    finally:
        session.close()

    # Show personalized weights if data available
    weights = learn_weights()
    if weights.confidence != "Low":
        console.print(Panel.fit(
            weights.format_text(),
            box=box.ROUNDED,
            padding=(1, 1),
            title="Personal Intelligence",
        ))
        console.print()

    # Show simulation if requested
    if simulate_resume or simulate_skill:
        changes = []
        if simulate_resume:
            changes.append({"kind": "resume", "value": simulate_resume})
        if simulate_skill:
            changes.append({"kind": "skill", "skill": simulate_skill, "count": 1})
        result = simulate(app, changes, job)
        console.print(Panel.fit(
            result.format_text(),
            box=box.ROUNDED,
            padding=(1, 2),
            title=f"Simulation — {job.company} {job.title}",
        ))
        return

    # Use personal prediction
    pred = personal_predict(app, job, weights=weights)
    cf = counterfactual(app, job)

    lines = [
        f"  Interview Probability: {pred['score']:.0f}%",
        f"  Confidence: {pred['confidence']}",
        f"",
        f"  Breakdown",
    ]
    for b in pred["breakdown"]:
        lines.append(f"    {b['label']:25s}  {b['weight']:4.0%}  {b['score']:.0f}/100")
    if pred["reasons"]:
        lines.append(f"  Why This Helps")
        for r in pred["reasons"][:4]:
            lines.append(f"    {r}")
    if pred["risks"]:
        lines.append(f"  Risks")
        for r in pred["risks"][:4]:
            lines.append(f"    {r}")

    console.print(Panel.fit(
        "\n".join(lines),
        box=box.ROUNDED,
        padding=(1, 2),
        title=f"Decision — {job.company} {job.title}",
    ))

    if cf.recommendation:
        console.print(f"\n[bold yellow]Counterfactual[/bold yellow]")
        console.print(f"  {cf.recommendation}")
        if cf.action and cf.action != "Do not apply":
            console.print(f"  Action: {cf.action}")

    # Show simulate hint
    console.print(f"\n[dim]Run with --simulate-resume backend_v3 or --simulate-skill Redis to test changes[/dim]")


@app.command()
def outcome(
    application_id: str = typer.Argument(..., help="Application ID"),
    rejection_reason: str = typer.Option("", "--reason", help="Rejection reason"),
    feedback: str = typer.Option("", "--feedback", help="Feedback received"),
    interview_rounds: int = typer.Option(0, "--rounds", help="Number of interview rounds"),
    salary: str = typer.Option("", "--salary", help="Salary offered"),
):
    """View or update application outcome."""
    if any([rejection_reason, feedback, interview_rounds, salary]):
        kwargs = {}
        if rejection_reason:
            kwargs["rejection_reason"] = rejection_reason
        if feedback:
            kwargs["feedback"] = feedback
        if interview_rounds:
            kwargs["interview_rounds"] = interview_rounds
        if salary:
            kwargs["salary"] = salary
        result = update_outcome(application_id, **kwargs)
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            console.print(f"[green]✓[/green] Outcome updated")
        return

    data = get_outcome(application_id)
    if not data:
        console.print("[yellow]No outcome recorded yet. Status transitions auto-record outcomes.[/yellow]")
        return

    lines = [
        f"  Application:   {data['application_id'][:8]}",
        f"  Company:       {data['company']}",
        f"  Role:          {data['role']}",
        f"  Resume:        {data['resume_used']}",
        f"  ATS:           {data['ats'] or '—'}",
    ]
    dates = []
    for label, key in [("Applied", "applied_at"), ("Viewed", "viewed_at"),
                        ("Online Assessment", "oa_at"), ("Interview", "interview_at"),
                        ("Offer", "offer_at"), ("Rejected", "rejected_at"),
                        ("Ghosted", "ghosted_at")]:
        val = data.get(key)
        if val:
            dates.append(f"  {label+':':20s} {val.strftime('%b %d, %Y') if hasattr(val, 'strftime') else val}")
    lines.extend(dates)
    if data["rejection_reason"]:
        lines.append(f"  Rejection:      {data['rejection_reason']}")
    if data["feedback"]:
        lines.append(f"  Feedback:      {data['feedback']}")
    if data["interview_rounds"]:
        lines.append(f"  Rounds:        {data['interview_rounds']}")
    if data["salary"]:
        lines.append(f"  Salary:        {data['salary']}")

    console.print(Panel.fit("\n".join(lines), box=box.ROUNDED, padding=(1, 2), title="Application Outcome"))


@app.command(hidden=True)
def personal(
    view: str = typer.Option("weights", "--view", "-v", help="View: weights, resumes, companies, ats, timing, skills, all"),
    company: str = typer.Option("", "--company", help="Filter company intelligence"),
    resume: str = typer.Option("", "--resume", help="Filter resume stats"),
):
    """Personal Intelligence Dashboard — your personalized insights."""
    if view in ("weights", "all"):
        w = learn_weights()
        console.print(Panel.fit(w.format_text(), box=box.ROUNDED, padding=(1, 2), title="Personal Weights"))

    if view in ("resumes", "all") or resume:
        if resume:
            r = resume_detail(resume)
            if r:
                console.print(Panel.fit(r.format_text(), box=box.ROUNDED, padding=(1, 2), title="Resume Intelligence"))
            else:
                console.print(f"[yellow]No data for resume '{resume}'[/yellow]")
        else:
            rows = resume_stats()
            if rows:
                table = Table(title="Resume Intelligence")
                table.add_column("Resume")
                table.add_column("Applications")
                table.add_column("Interviews")
                table.add_column("Rate")
                table.add_column("Confidence")
                for r in rows:
                    table.add_row(r.name, str(r.applications), str(r.interviews),
                                  f"{r.interview_rate:.1f}%", r.confidence)
                console.print(table)
            else:
                console.print("[yellow]No resume outcome data yet[/yellow]")

    if view in ("companies", "all"):
        rows = company_intelligence(company or None)
        if rows:
            table = Table(title=f"Company Intelligence{f' — {company}' if company else ''}")
            table.add_column("Company")
            table.add_column("Apps")
            table.add_column("Replies")
            table.add_column("Interviews")
            table.add_column("Offers")
            table.add_column("Avg Reply")
            table.add_column("Best Resume")
            for r in rows[:10]:
                table.add_row(r.company, str(r.applications), str(r.replies),
                              str(r.interviews), str(r.offers),
                              f"{r.avg_reply_days:.0f}d" if r.avg_reply_days else "-",
                              r.best_resume)
            console.print(table)
        else:
            console.print("[yellow]No company data yet[/yellow]")

    if view in ("ats", "all"):
        rows = ats_intelligence()
        if rows:
            table = Table(title="ATS Intelligence")
            table.add_column("ATS")
            table.add_column("Applications")
            table.add_column("Interviews")
            table.add_column("Interview Rate")
            for r in rows:
                table.add_row(r["ats"], str(r["applications"]), str(r["interviews"]),
                              f"{r['interview_rate']:.1f}%")
            console.print(table)
        else:
            console.print("[yellow]No ATS data yet[/yellow]")

    if view in ("timing", "all"):
        info = timing_intelligence()
        if info.get("by_day"):
            table = Table(title="Timing Intelligence — Best Days")
            table.add_column("Day")
            table.add_column("Apps")
            table.add_column("Interviews")
            table.add_column("Rate")
            table.add_column("Confidence")
            for d in info["by_day"]:
                if d["applications"] > 0:
                    table.add_row(d["day"], str(d["applications"]), str(d["interviews"]),
                                  f"{d['rate']:.1f}%", d["confidence"])
            console.print(table)
        else:
            console.print("[yellow]Not enough timing data yet[/yellow]")

    if view in ("skills", "all"):
        rows = skill_intelligence()
        if rows:
            table = Table(title="Skill Intelligence — From Successful Applications")
            table.add_column("Skill")
            table.add_column("Count")
            table.add_column("Frequency")
            for r in rows[:15]:
                table.add_row(r["skill"], str(r["count"]), f"{r['frequency']:.0f}%")
            console.print(table)
            if rows:
                top = rows[0]["skill"]
                console.print(f"\n[yellow]Learning '{top}' may improve opportunities across target roles[/yellow]")
        else:
            console.print("[yellow]No skill data yet. Apply to more jobs to see patterns.[/yellow]")


@app.command(hidden=True)
def outreach(
    detail: str = typer.Option("", "--detail", "-d", help="Show detail: templates, companies, timing"),
):
    """Outreach Intelligence — reply analytics and template performance."""
    summary = outreach_summary()
    console.print(Panel.fit(
        summary.format_text(),
        box=box.ROUNDED,
        padding=(1, 2),
        title="Outreach Intelligence",
    ))

    if detail == "templates":
        rows = template_performance()
        if not rows:
            console.print("[yellow]No outreach data yet[/yellow]")
            return
        table = Table(title="Template Performance")
        table.add_column("Subject")
        table.add_column("Sent")
        table.add_column("Replies")
        table.add_column("Rate")
        for r in rows[:10]:
            table.add_row(
                r["subject"][:50],
                str(r["sent"]),
                str(r["replied"]),
                f"{r['reply_rate']:.0f}%",
            )
        console.print(table)

    elif detail == "companies":
        rows = company_responsiveness()
        if not rows:
            console.print("[yellow]No outreach data yet[/yellow]")
            return
        table = Table(title="Company Responsiveness")
        table.add_column("Company")
        table.add_column("Sent")
        table.add_column("Replies")
        table.add_column("Rate")
        for r in rows[:10]:
            table.add_row(
                r["company"],
                str(r["sent"]),
                str(r["replied"]),
                f"{r['reply_rate']:.0f}%",
            )
        console.print(table)

    elif detail == "timing":
        info = best_contact_time()
        if info["total_replies_analyzed"] == 0:
            console.print("[yellow]Not enough reply data to analyze timing[/yellow]")
            return
        console.print(f"[bold]Best day:[/bold] {info['best_day']} ({info['best_day_count']} replies)")
        console.print(f"[bold]Best hour:[/bold] {info['best_hour']}:00 ({info['best_hour_count']} replies)")


@app.command()
def doctor():
    """Run preflight checks on the JobZo environment."""
    from pathlib import Path as _P
    import shutil, json

    console.print("[bold cyan]JobZo Doctor[/bold cyan] — Preflight Check\n")

    checks = []

    # Python
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python 3.11+", py_ok, sys.version.split()[0]))

    # Playwright
    pw_ok = False
    try:
        import playwright
        pw_ok = True
    except ImportError:
        pass
    checks.append(("Playwright installed", pw_ok, ""))

    # Chromium
    cr_ok = _P(_P.home() / ".cache" / "ms-playwright").exists()
    checks.append(("Chromium installed", cr_ok, ""))

    # SQLite / DB
    db_path = _P(__file__).parent.parent / "data" / "jobzo.db"
    db_ok = db_path.exists()
    if not db_ok:
        from database.connection import get_session
        try:
            s = get_session()
            s.close()
            db_ok = True
        except Exception:
            pass
    checks.append(("Database initialized", db_ok, ""))

    # Browser profile
    cfg_path = _P(__file__).parent.parent / "config" / "browser.yaml"
    profile_ok = False
    if cfg_path.exists():
        import yaml
        with open(cfg_path) as f:
            cfg_data = yaml.safe_load(f)
        profile = cfg_data.get("profile", {})
        filled = [k for k, v in profile.items() if v]
        profile_ok = bool(filled)
        profile_detail = f"{len(filled)} fields filled"
    else:
        profile_detail = "file missing"
    checks.append(("Browser profile", profile_ok, profile_detail))

    # Resumes
    resume_cfg = _P(__file__).parent.parent / "config" / "resume.yaml"
    resumes_ok = False
    resume_count = 0
    if resume_cfg.exists():
        import yaml
        with open(resume_cfg) as f:
            rcfg = yaml.safe_load(f)
        active = [n for n, i in rcfg.get("resumes", {}).items() if i.get("active")]
        resume_count = len(active)
        resumes_ok = all(
            (_P(__file__).parent.parent / rcfg["resumes"][n]["file"]).exists()
            for n in active
        )
    checks.append(("Resume PDFs", resumes_ok, f"{resume_count} active"))

    # Ollama / LLM
    llm_ok = False
    llm_detail = ""
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            llm_ok = True
            llm_detail = ", ".join(models[:3])
    except Exception:
        pass
    if not llm_ok:
        llm_cfg = _P(__file__).parent.parent / "config" / "llm.yaml"
        if llm_cfg.exists():
            import yaml
            with open(llm_cfg) as f:
                lcfg = yaml.safe_load(f)
            if lcfg.get("openai", {}).get("api_key"):
                llm_ok = True
                llm_detail = "OpenAI configured (fallback)"
            else:
                llm_detail = "No LLM available (template fallback will be used)"
        else:
            llm_detail = "No LLM configured (template fallback will be used)"
    checks.append(("LLM available", llm_ok, llm_detail))

    # RSS provider
    try:
        from services.config import Config
        rss_cfg = Config.provider_config("rss")
        rss_ok = rss_cfg.get("enabled", True)
    except Exception:
        rss_ok = True
    checks.append(("RSS provider", rss_ok, "enabled" if rss_ok else "disabled"))

    # Appl. log dir
    log_dir = _P(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    checks.append(("Log directory", log_dir.exists(), str(log_dir)))

    table = Table(box=box.SIMPLE)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    for name, ok, detail in checks:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]" if detail else "[yellow]~[/yellow]"
        table.add_row(name, icon, detail)

    console.print(table)

    all_ok = all(ok for _, ok, _ in checks[:6])
    if all_ok:
        console.print("\n[bold green]Ready to apply![/bold green]")
    else:
        console.print("\n[yellow]Some checks failed. Fix the issues above before running real applications.[/yellow]")


def _show_top_applications():
    session = get_session()
    try:
        apps = session.query(Application).filter(
            Application.status == "drafted",
        ).order_by(Application.score.desc()).limit(10).all()

        if not apps:
            return

        table = Table(title="Top Scored Jobs")
        table.add_column("ID")
        table.add_column("Company")
        table.add_column("Role")
        table.add_column("Score")
        table.add_column("Strategy")

        for app in apps:
            job = session.query(Job).filter(Job.id == app.job_id).first()
            table.add_row(
                str(app.id)[:8],
                job.company if job else "?",
                job.title[:40] if job else "?",
                str(app.score),
                app.strategy,
            )

        console.print(table)
        console.print("Run [bold]jobzo apply <id>[/bold] to submit an application")
    finally:
        session.close()


@app.command(hidden=True)
def priorities():
    """Show companies ranked by hiring weight (drive poll priority)."""
    from services.hiring_weight import rank_by_weight
    from rich.table import Table

    ranked = rank_by_weight(limit=30)
    if not ranked:
        console.print("[yellow]No data yet — run jobzo collect first[/yellow]")
        return

    table = Table(title="Hiring Priority Queue", box=box.SIMPLE)
    table.add_column("#")
    table.add_column("Company", style="bold")
    table.add_column("Category")
    table.add_column("Weight", justify="right")
    table.add_column("Interval", justify="right")
    table.add_column("Label")

    for i, entry in enumerate(ranked, 1):
        table.add_row(
            str(i), entry["name"],
            entry["category"].replace("_", " ").title(),
            str(entry["weight"]), f"{entry['interval_h']}h", entry["label"],
        )

    console.print(table)
    console.print("\n[dim]Higher weight = more frequent polling. Derived from recent job volume + success rate.[/dim]")


@app.command(hidden=True)
def feed():
    """Opportunity Feed — today's prioritized decisions."""
    from services.feed import build_feed, print_feed
    items = build_feed()
    if not items:
        console.print("[yellow]No opportunities to show. Run [green]jobzo collect[/green] then [green]jobzo review[/green] first.[/yellow]")
        return
    print_feed(items)


def _cmd_stats():
    """Internal: print the stats dashboard."""
    from services.dashboard import founder_dashboard, print_dashboard
    data = founder_dashboard()
    print_dashboard(data)


@app.command()
def dashboard():
    """Founder analytics — pipeline health, ecosystem mix, portfolio risk."""
    _cmd_stats()


@app.command(hidden=True)
def stats():
    """Alias for dashboard."""
    _cmd_stats()


@app.command(hidden=True)
def lab(
    action: str = typer.Argument("benchmark", help="benchmark | experiment | simulate"),
    profile: str = typer.Option("", "--profile", "-p", help="Benchmark profile name"),
):
    """Research lab: benchmarks, experiments, and simulations."""
    if action == "benchmark":
        from benchmark.runner import run_all, print_results
        console.print("[bold cyan]JobZo Lab[/bold cyan] — Running benchmarks...\n")
        profile_names = [p.strip() for p in profile.split(",") if p.strip()] if profile else None
        results = run_all(profile_names)
        print_results(results)
    elif action == "experiment":
        try:
            from domain.experiment import create_default_experiments, ExperimentService
            exps = create_default_experiments()
            console.print(f"[bold cyan]JobZo Lab[/bold cyan] — {len(exps)} experiments registered\n")
            for e in exps:
                status = "active" if e.is_active else "inactive"
                console.print(f"  {e.name:40s} {status}")
            console.print("\nRun [bold]python3 -m pytest tests/test_experiment.py -v[/bold] to test.")
        except ImportError as e:
            console.print(f"[yellow]Experiment module not available: {e}[/yellow]")
    elif action == "simulate":
        try:
            from domain.simulation import compare_scenarios
            console.print("[bold cyan]JobZo Lab[/bold cyan] — Scenario Simulation\n")
            result = compare_scenarios()
            console.print(result)
        except ImportError as e:
            console.print(f"[yellow]Simulation module not available: {e}[/yellow]")
    else:
        console.print(f"[red]Unknown lab action: {action}[/red]")
        console.print("Usage: jobzo lab [benchmark|experiment|simulate]")


@app.command(hidden=True)
def form_stats():
    """Application form autofill statistics."""
    import json
    from pathlib import Path

    log_file = Path(__file__).parent.parent / "logs" / "applications.jsonl"
    if not log_file.exists():
        console.print("[yellow]No application data yet[/yellow]")
        return

    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not entries:
        console.print("[yellow]No application data yet[/yellow]")
        return

    unique = {}
    for e in entries:
        key = (e.get("company", ""), e.get("title", "")[:40])
        unique[key] = e

    apps = list(unique.values())
    total = len(apps)
    submitted = sum(1 for a in apps if a.get("submitted"))

    if total == 0:
        console.print("[yellow]No application data yet[/yellow]")
        return

    times = [a.get("time_seconds", 0) for a in apps if a.get("time_seconds", 0) > 0]
    avg_time = sum(times) / len(times) if times else 0

    filled_list = [a.get("fields_filled", 0) for a in apps]
    total_list = [a.get("fields_filled", 0) + a.get("fields_manual", 0) for a in apps]
    avg_filled = sum(filled_list) / len(filled_list) if filled_list else 0
    avg_total = sum(total_list) / len(total_list) if total_list else 0
    avg_pct = (avg_filled / avg_total * 100) if avg_total > 0 else 0

    ats_counts = {}
    resume_counts = {}
    source_counts = {}
    manual_fields_list = []

    for a in apps:
        ats = a.get("ats", "Unknown")
        ats_counts[ats] = ats_counts.get(ats, 0) + 1

        resume = a.get("resume", "")
        if resume:
            resume_counts[resume] = resume_counts.get(resume, 0) + 1

        title = a.get("title", "")
        source = "Company Pages" if a.get("company") in ("Postman", "Stripe", "CloudSEK", "Glean", "Instead") else "RSS" if "rss" in a.get("source", "").lower() else "Manual"
        source_counts[source] = source_counts.get(source, 0) + 1

        manual = a.get("fields_manual", 0)
        if manual > 0:
            manual_fields_list.append(manual)

    best_ats = max(ats_counts, key=ats_counts.get) if ats_counts else "—"
    best_resume = max(resume_counts, key=resume_counts.get) if resume_counts else "—"
    avg_manual = sum(manual_fields_list) / len(manual_fields_list) if manual_fields_list else 0
    time_saved_min = int(avg_time * total / 60) if avg_time else 0

    interviews = 0

    panel = Panel.fit(
        "\n".join([
            "",
            f"  [bold]Applications[/bold]        {total}",
            f"  [bold]Submitted[/bold]            {submitted}",
            f"  [bold]Interviews[/bold]           {interviews}",
            f"  [bold]Interview Rate[/bold]       [yellow]—[/yellow] (no responses yet)",
            "",
            f"  [bold]Average Time[/bold]         {avg_time:.0f}s ({avg_time/60:.1f} min)",
            f"  [bold]Average Autofill[/bold]     {avg_filled:.0f}/{avg_total:.0f} fields ({avg_pct:.0f}%)",
            f"  [bold]Manual Fields (avg)[/bold]  {avg_manual:.1f}",
            f"  [bold]Time Saved[/bold]           ~{time_saved_min} min",
            "",
            f"  [bold]Best ATS[/bold]             {best_ats}",
            f"  [bold]Best Resume[/bold]          {best_resume}",
            f"  [bold]Best Source[/bold]          {max(source_counts, key=source_counts.get) if source_counts else '—'}",
            "",
        ]),
        title="[bold cyan]Application Form Stats[/bold cyan]",
        border_style="cyan",
    )
    console.print(panel)


# ── Human-approved action pipeline ──────────────────────────────────────────

def _approval_card(req, idx: int, total: int):
    """Render one approval request as an auditable card."""
    payload = req.get("payload", {})
    evidence = req.get("evidence", {})
    score = evidence.get("score", {})
    trust = evidence.get("trust", {})
    fresher = evidence.get("fresher", {})
    contact = evidence.get("contact")

    lines = [
        f"[bold]{req.get('title', '')}[/bold]",
        f"[dim]{req.get('subtitle', '')} · {req.get('kind', '')}[/dim]",
        "",
    ]
    if score:
        total_score = score.get("total", 0)
        color = "green" if total_score >= 85 else "yellow" if total_score >= 70 else "red"
        lines.append(
            f"  Opportunity [{color}]{total_score}/100[/{color}] "
            f"({score.get('label', '')}) · Effort ~{req.get('effort_minutes') or '?'} min"
        )
        lines.append(
            f"  Trust {trust.get('score', 0):.0f}/100 ({trust.get('tier', '?')}) · "
            f"Fresher {fresher.get('status', '?')} ({int((fresher.get('probability') or 0) * 100)}%)"
        )
        lines.append("")
        lines.append("  [bold]Why[/bold]")
        for factor in score.get("factors", [])[:5]:
            lines.append(
                f"    {factor.get('points', 0):>4.1f}/{factor.get('weight', 0)}  "
                f"{factor.get('label', '')} — {(factor.get('reasons') or [''])[0][:80]}"
            )
        if score.get("hard_rejects"):
            lines.append("")
            lines.append("  [red]Hard reject: " + "; ".join(score["hard_rejects"]) + "[/red]")
        if score.get("warnings"):
            lines.append("  [yellow]Warnings: " + "; ".join(score["warnings"][:3]) + "[/yellow]")

    if contact:
        lines.append("")
        lines.append(
            f"  Contact: {contact.get('name', '')} ({contact.get('role', '')}) · "
            f"{contact.get('status', '')} {int((contact.get('confidence') or 0) * 100)}%"
        )
        lines.append(f"  Email: {contact.get('email', '')}")

    if req.get("kind") == "application":
        resume = payload.get("resume", {})
        lines.append("")
        lines.append(f"  Resume: {resume.get('name', '—')} ({resume.get('file', '—')})")
        lines.append(f"  Channel: {payload.get('application_channel', '—')} · Source: {payload.get('source', '—')}")
        if payload.get("url"):
            lines.append(f"  Job: {payload['url']}")
    else:
        lines.append("")
        lines.append(f"  [bold]To:[/bold] {payload.get('to', '')}")
        lines.append(f"  [bold]Subject:[/bold] {payload.get('subject', '')}")
        lines.append("")
        for line in (payload.get("body", "") or "").splitlines():
            lines.append(f"  {line}")

    console.print(Panel.fit(
        "\n".join(lines),
        title=f"[bold cyan]{idx}/{total} · {req.get('id', '')[:8]}[/bold cyan]",
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def _execute_approved(req):
    """Execute an approved request. Applications still use the browser flow."""
    from services.approval import KIND_APPLICATION, mark_executed
    from services.cold_outreach import send_draft

    if req.get("kind") == KIND_APPLICATION:
        session = get_session()
        try:
            app = session.query(Application).options(
                joinedload(Application.job)
            ).filter(Application.id == req.get("application_id")).first()
            job = app.job if app else None
        finally:
            session.close()
        if app is None or job is None:
            console.print("[red]Application or job not found.[/red]")
            return False

        console.print("\n  [bold]Opening the application flow…[/bold]")
        _run_apply(app, job)

        session = get_session()
        try:
            status = session.query(Application.status).filter(
                Application.id == app.id
            ).scalar()
        finally:
            session.close()
        if status == "submitted":
            mark_executed(req["id"], "Application submitted")
            console.print("  [green]✓[/green] Submission recorded; follow-ups scheduled.")
            return True
        console.print("  [yellow]Not marked submitted — apply manually if the form blocked automation.[/yellow]")
        return False

    draft_id = req.get("outreach_draft_id")
    if not draft_id:
        console.print("[red]No draft linked to this approval.[/red]")
        return False
    result = send_draft(draft_id)
    if not result.get("ok"):
        console.print(f"  [red]Send failed: {result.get('error')}[/red]")
        return False
    if result.get("dry_run"):
        console.print(f"  [yellow]Saved to outbox (not sent):[/yellow] {result.get('path')}")
        console.print("  [dim]Configure JOBZO_SMTP_* to send for real.[/dim]")
    else:
        console.print(f"  [green]✓ Email sent via {result.get('transport')}[/green]")
    return True


@app.command()
def brief(
    queue: bool = typer.Option(False, "--queue", help="Queue top opportunities before showing the brief"),
    limit: int = typer.Option(5, "--limit", help="How many opportunities to queue"),
    min_priority: float = typer.Option(5.0, "--min-priority", help="Minimum dig priority to queue"),
    local: bool = typer.Option(False, "--local", help="Only Indian/local companies"),
):
    """15-minute daily briefing: decisions, not job-portal browsing."""
    from services.brief import build_brief, format_brief_text
    from services.approval import enqueue_top_applications

    if queue:
        result = enqueue_top_applications(
            limit=limit, min_priority=min_priority, local_only=local,
        )
        console.print(f"[green]✓[/green] New: {len(result['queued'])} · "
                      f"already queued: {len(result['existing'])} · "
                      f"auto-rejected {len(result['rejected'])} · skipped {len(result['skipped'])}")
        console.print()

    data = build_brief()
    console.print(Panel.fit(
        format_brief_text(data),
        title="[bold cyan]JOBZO DAILY BRIEF[/bold cyan]",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print("\n  [dim]Next: jobzo inbox · jobzo contacts · jobzo followups[/dim]")


@app.command(name="queue")
def queue_opportunities(
    limit: int = typer.Option(5, "--limit", help="How many applications to queue"),
    min_priority: float = typer.Option(5.0, "--min-priority", help="Minimum dig priority"),
    local: bool = typer.Option(False, "--local", help="Only Indian/local companies"),
    fresh: bool = typer.Option(False, "--fresh", help="Only postings from the last 3 days"),
):
    """Queue the best current opportunities for human approval."""
    from services.approval import enqueue_top_applications

    result = enqueue_top_applications(
        limit=limit, min_priority=min_priority, local_only=local, fresh_only=fresh,
    )
    console.print(f"[green]✓[/green] Candidates: {result['candidates']} · "
                  f"New: {len(result['queued'])} · Already queued: {len(result['existing'])} · "
                  f"Auto-rejected: {len(result['rejected'])}")
    if result["queued"]:
        table = Table(box=box.SIMPLE)
        table.add_column("Company")
        table.add_column("Role")
        table.add_column("Score", justify="right")
        table.add_column("Effort", justify="right")
        for req in result["queued"]:
            table.add_row(
                req.get("payload", {}).get("company", ""),
                req.get("payload", {}).get("role", "")[:40],
                f"{req.get('opportunity_score') or 0:.0f}",
                f"{req.get('effort_minutes') or '?'} min",
            )
        console.print(table)
    for item in result["rejected"][:5]:
        console.print(f"  [red]✗[/red] {item['title']}: [dim]{item['reason']}[/dim]")
    console.print("\n  [dim]Review with: jobzo inbox[/dim]")


@app.command()
def inbox(
    limit: int = typer.Option(20, "--limit", help="Maximum requests to review"),
):
    """Review pending actions. Approve, reject, edit, or skip — nothing sends first."""
    from services.approval import (
        PENDING, decide_request, list_requests,
    )
    from services.cold_outreach import update_draft

    pending = list_requests(PENDING, limit=limit)
    if not pending:
        console.print("[green]No pending approvals.[/green]")
        console.print("  [dim]Run: jobzo brief --queue[/dim]")
        return

    for idx, req in enumerate(pending, 1):
        _approval_card(req, idx, len(pending))
        console.print()
        action = input("Action [a]pprove / [r]eject / [e]dit / [s]kip / [q]uit: ").strip().lower()

        if action in ("q", "quit"):
            break
        if action in ("s", "skip", ""):
            continue
        if action == "e":
            if req.get("kind") == "application":
                console.print("  [yellow]Application answers are edited in the browser flow.[/yellow]")
                continue
            draft = update_draft(
                req.get("outreach_draft_id"),
                subject=typer.prompt("Subject", default=req.get("payload", {}).get("subject", "")),
                body=typer.edit(req.get("payload", {}).get("body", "")),
            )
            if draft.get("ok"):
                console.print("  [green]✓[/green] Draft updated — approval reset for safety.")
            else:
                console.print(f"  [red]{draft.get('error')}[/red]")
            continue

        if action == "r":
            note = typer.prompt("Reason (optional)", default="")
            result = decide_request(req["id"], "rejected", note)
            if result.get("ok"):
                console.print("  [blue]Rejected[/blue]")
            else:
                console.print(f"  [red]{result.get('error')}[/red]")
            continue

        if action == "a":
            result = decide_request(req["id"], "approved")
            if not result.get("ok"):
                console.print(f"  [red]{result.get('error')}[/red]")
                continue
            console.print("  [green]✓ Approved[/green]")
            _execute_approved(req)
            continue

        console.print("[dim]Unknown action.[/dim]")


@app.command()
def approve(
    request_id: str = typer.Argument(..., help="Approval request ID"),
):
    """Approve one request by ID, then execute it."""
    from services.approval import decide_request, get_request
    result = decide_request(request_id, "approved")
    if not result.get("ok"):
        console.print(f"[red]{result.get('error')}[/red]")
        return
    req = get_request(request_id)
    console.print("[green]✓ Approved[/green]")
    _execute_approved(req)


@app.command()
def reject(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    note: str = typer.Option("", "--note", help="Reason for rejection"),
):
    """Reject one request by ID."""
    from services.approval import decide_request
    result = decide_request(request_id, "rejected", note)
    if result.get("ok"):
        console.print("[blue]Rejected[/blue]")
    else:
        console.print(f"[red]{result.get('error')}[/red]")


@app.command(name="contacts")
def contacts(
    company: str = typer.Argument("", help="Filter by company"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", help="Minimum confidence 0-1"),
):
    """List contacts with separate identity/domain/mailbox/relevance evidence."""
    from services.contacts import list_contacts
    records = list_contacts(company, min_confidence=min_confidence)
    if not records:
        console.print("[yellow]No contacts yet.[/yellow]")
        console.print("  Add one: jobzo contact-add --company Acme --name 'Jane Doe' --role 'Talent Acquisition' "
                      "--email jane@acme.com --source-url https://acme.com/careers")
        return

    table = Table(box=box.SIMPLE)
    table.add_column("Company")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Email")
    table.add_column("Status")
    table.add_column("Conf", justify="right")
    table.add_column("Verified levels")
    for r in records:
        table.add_row(
            r.contact.company,
            r.contact.name,
            r.contact.role[:24],
            r.contact.email or "—",
            Text(r.status, style={"verified": "green", "verified_domain": "cyan",
                                  "risky": "red", "unverified": "yellow"}.get(r.status, "white")),
            f"{r.confidence:.0%}",
            ", ".join(r.levels) or "—",
        )
    console.print(table)


@app.command(name="contact-add")
def contact_add(
    company: str = typer.Option(..., "--company", help="Company name"),
    name: str = typer.Option(..., "--name", help="Person or team name"),
    role: str = typer.Option("", "--role", help="Title, e.g. Talent Acquisition"),
    email: str = typer.Option("", "--email", help="Public email address"),
    linkedin: str = typer.Option("", "--linkedin", help="Public LinkedIn profile URL"),
    source_url: str = typer.Option("", "--source-url", help="Where this contact is publicly listed"),
    evidence_kind: str = typer.Option("user_confirmed", "--evidence-kind",
                                      help="official_careers_page | official_website | linkedin_profile | user_confirmed"),
):
    """Add a contact with evidence. Never guesses an email address."""
    from services.contacts import save_contact
    if not email and not linkedin:
        console.print("[red]Provide a public email or LinkedIn URL.[/red]")
        return
    record = save_contact(
        company=company, name=name, role=role, email=email, linkedin=linkedin,
        source="manual", evidence_kind=evidence_kind,
        source_url=source_url or linkedin,
    )
    color = "green" if record.is_verified else "yellow"
    console.print(f"[{color}]{record.status}[/{color}] · confidence {record.confidence:.0%}")
    console.print(f"  Verified: {', '.join(record.levels) or 'nothing yet'}")
    if not record.is_verified:
        console.print("  [dim]Add an official source URL that ties the person to the company domain.[/dim]")


@app.command(name="contact-discover")
def contact_discover(
    company: str = typer.Argument(..., help="Company name"),
    careers_url: str = typer.Option("", "--url", help="Official careers page URL"),
):
    """Discover publicly listed emails on official company pages only."""
    from services.contacts import discover_public_contacts
    console.print(f"[dim]Fetching official pages for {company}…[/dim]")
    result = discover_public_contacts(company, careers_url=careers_url)
    console.print(f"Domain: {result['domain'] or '—'} · pages: {result['pages_fetched']} · "
                  f"emails: {result['emails_found']}")
    for c in result["contacts"]:
        console.print(f"  • {c['email']} — {c['name']} ({c['status']}, {c['confidence']:.0%})")
    if not result["contacts"]:
        console.print("  [yellow]No public emails found. Do not guess one.[/yellow]")
        console.print("  [dim]Find the recruiter on LinkedIn, then add the profile URL with jobzo contact-add.[/dim]")


@app.command(name="cold-email")
def cold_email(
    application_id: str = typer.Argument(..., help="Application ID"),
    contact_id: str = typer.Option("", "--contact", help="Specific contact ID"),
    kind: str = typer.Option("initial", "--kind", help="initial | followup | final_followup"),
):
    """Draft a personalized email to a verified contact and queue it for approval."""
    from services.cold_outreach import draft_outreach
    result = draft_outreach(application_id=application_id, contact_id=contact_id or None, kind=kind)
    if not result.ok:
        console.print(f"[red]{result.error}[/red]")
        return
    draft = result.draft or {}
    console.print(Panel.fit(
        f"[bold]To:[/bold] {draft.get('contact_name')} <{draft.get('contact_email')}>\n"
        f"[bold]Subject:[/bold] {draft.get('subject')}\n\n{draft.get('body')}",
        title="[bold cyan]OUTREACH DRAFT[/bold cyan]",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print(f"  Approval: {draft.get('approval_request_id', '')[:8]} · "
                  f"Review with [green]jobzo inbox[/green]")


@app.command(name="drafts")
def drafts(
    status: str = typer.Option("", "--status", help="pending_approval | approved | outbox | sent"),
    limit: int = typer.Option(20, "--limit", help="Maximum drafts"),
):
    """List cold-email and follow-up drafts."""
    from services.cold_outreach import list_drafts
    rows = list_drafts(status=status, limit=limit)
    if not rows:
        console.print("[yellow]No drafts.[/yellow]")
        return
    table = Table(box=box.SIMPLE)
    table.add_column("ID")
    table.add_column("Company")
    table.add_column("Contact")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Subject")
    for d in rows:
        table.add_row(d["id"][:8], d["company"], d["contact_name"], d["kind"],
                      d["status"], d["subject"][:46])
    console.print(table)


@app.command(name="email-send")
def email_send(
    draft_id: str = typer.Argument(..., help="Draft ID"),
    approve: bool = typer.Option(False, "--approve", help="Approve the linked request first (explicit)"),
):
    """Send an approved draft. Without SMTP this writes a local outbox .eml."""
    from services.approval import decide_request
    from services.cold_outreach import get_draft, send_draft
    draft = get_draft(draft_id)
    if draft is None:
        console.print("[red]Draft not found.[/red]")
        return
    if approve and draft.get("approval_request_id"):
        result = decide_request(draft["approval_request_id"], "approved")
        if not result.get("ok"):
            console.print(f"[red]{result.get('error')}[/red]")
            return
    result = send_draft(draft_id)
    if not result.get("ok"):
        console.print(f"[red]{result.get('error')}[/red]")
        return
    if result.get("dry_run"):
        console.print(f"[yellow]Saved to outbox (not sent):[/yellow] {result.get('path')}")
    else:
        console.print(f"[green]✓ Sent via {result.get('transport')}[/green]")


@app.command()
def followups(
    due: bool = typer.Option(True, "--due/--all", help="Only due follow-ups"),
    draft: bool = typer.Option(False, "--draft", help="Create email drafts for due follow-ups"),
    done: str = typer.Option("", "--done", help="Mark a follow-up ID as done"),
    skipped: str = typer.Option("", "--skip", help="Skip a follow-up ID"),
):
    """See and act on the follow-up schedule."""
    from services.followups import (
        complete_followup, draft_followup, list_followups,
    )
    if done:
        console.print("[green]✓ Done[/green]" if complete_followup(done, "done") else "[red]Not found[/red]")
        return
    if skipped:
        console.print("[blue]Skipped[/blue]" if complete_followup(skipped, "skipped") else "[red]Not found[/red]")
        return

    rows = list_followups(due_only=due)
    if not rows:
        console.print("[green]No follow-ups due.[/green]")
        return

    table = Table(box=box.SIMPLE)
    table.add_column("ID")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Kind")
    table.add_column("Due")
    table.add_column("Contact")
    table.add_column("Status")
    for item in rows:
        table.add_row(
            item["id"][:8], item["company"], item["role"][:30], item["kind"],
            item["due_at"].strftime("%b %d"), item["contact_name"] or "portal check",
            item["status"],
        )
    console.print(table)

    if draft:
        for item in rows:
            result = draft_followup(item["id"])
            if result.get("needs_manual"):
                console.print(f"  [yellow]{item['company']}: {result.get('message')}[/yellow]")
            elif result.get("ok"):
                console.print(f"  [green]✓ Draft queued[/green] {item['company']} — review with jobzo inbox")
            else:
                console.print(f"  [red]{item['company']}: {result.get('error')}[/red]")


@app.command()
def resume(
    action: str = typer.Argument("list", help="list | fit | analyze"),
    target: str = typer.Option("", "--job", "-j", help="Job ID for fit analysis"),
):
    """Resume management: list variants, check fit, analyze gaps."""
    if action == "fit" and target:
        fit(target)
    elif action == "analyze":
        console.print("[yellow]Resume analysis not yet implemented[/yellow]")
    else:
        from resumes.registry import get_registry
        reg = get_registry()
        console.print(f"[bold cyan]Resumes[/bold cyan] — {len(reg.all())} variants")
        for r in reg.all():
            console.print(f"  • {r.name}")


@app.command()
def config(
    key: str = typer.Argument("", help="Config key to view or set"),
    value: str = typer.Option("", "--set", "-s", help="Set a config value"),
):
    """View or modify JobZo configuration."""
    console.print("[yellow]Config management not yet implemented[/yellow]")
    console.print("  Config file: config/settings.yaml (coming soon)")


if __name__ == "__main__":
    entry_point()
