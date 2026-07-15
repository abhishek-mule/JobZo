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
from mission import run_mission
from database.models import Contact, Interaction, ApplicationOutcome

logger = logging.getLogger("jobzo")
console = Console()
app = typer.Typer(name="jobzo")

setup_logging()


# ── Entry point ──────────────────────────────────────────────────────────────

def entry_point():
    if len(sys.argv) <= 1:
        mission()
    else:
        app()


@app.callback()
def main():
    """JobZo — Your daily job search co-pilot."""


# ── Mission (default) ────────────────────────────────────────────────────────

MISSION_NEXT = None


def mission():
    """Mission Engine — guided daily workflow dashboard."""
    run_mission()


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
    label, color = _confidence_label(app.score)
    reasons = _parse_notes(app.notes)
    missing = _missing_skills(job)

    lines = []
    lines.append(f"[bold]{job.company}[/bold] — {job.title}")
    lines.append("")
    lines.append(f"[{color}]{label}[/{color}]  Score: [bold]{app.score}[/bold]/100")
    lines.append("")

    for icon, label_text, detail in reasons:
        if detail:
            lines.append(f"  {icon} [bold]{label_text}[/bold]")
            lines.append(f"     {detail}")

    if missing:
        lines.append("")
        lines.append("  [yellow]⚠[/yellow] [bold]Missing skills[/bold]")
        for s in missing[:3]:
            lines.append(f"     • {s}")

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
            console.print("  [yellow]Form not detected. Marked as submitted.[/yellow]")
            transition_status(str(application.id), "submitted")
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

@app.command()
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


@app.command(name="validate-registry")
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


@app.command()
def benchmark(
    profile: str = typer.Option("", "--profile", "-p", help="Run only a specific profile (by name)"),
):
    """Run the benchmark suite — evaluate registry, retrieval, ranker, and scores."""
    from benchmark.runner import run_all, print_results

    console.print("[bold cyan]JobZo[/bold cyan] — Running benchmarks...\n")
    profile_names = [p.strip() for p in profile.split(",") if p.strip()] if profile else None
    results = run_all(profile_names)
    print_results(results)


@app.command()
def collect(
    keywords: str = typer.Option("", help="Comma-separated keywords to search for"),
):
    """Collect jobs from all enabled providers."""
    console.print("[bold cyan]JobZo[/bold cyan] — Collecting jobs...")
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] or None
    total = asyncio.run(collect_all(kw_list))
    console.print(f"[green]✓[/green] Collected {total} new jobs")


@app.command(name="list")
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


@app.command()
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
                            console.print(f"  [green]✓[/green] Marked as submitted")
                        else:
                            console.print("  [yellow]Skipped[/yellow]")
                    else:
                        console.print("  [yellow]Form not detected. Apply manually.[/yellow]")
                        transition_status(str(application.id), "submitted")
                        submitted = True
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
def prepare_for_interview(
    job_id: str = typer.Argument(..., help="Job ID to prepare for"),
):
    """Generate interview preparation plan for a specific job."""
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


@app.command()
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


@app.command(name="contact-interactions")
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
def stats():
    """Show application statistics."""
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
        title="[bold cyan]JobZo Statistics[/bold cyan]",
        border_style="cyan",
    )
    console.print(panel)


if __name__ == "__main__":
    entry_point()
