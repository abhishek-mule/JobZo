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
from ai.scorer import score_pending_jobs, _keyword_pre_score, SKILL_KEYWORDS
from ai.llm import ask
from browser.assistant import BrowserAssistant, KNOWN_ATS_DOMAINS
from tracker.applications import list_applications, transition_status, get_application
from tracker.tasks import list_pending_tasks, complete_task
from database.connection import get_session
from database.models import Job, Application, Task
from sqlalchemy.orm import joinedload

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
    """Today's Mission — guided session."""
    global MISSION_NEXT

    with Progress(
        TextColumn("[bold cyan]JobZo[/bold cyan] • Collecting jobs..."),
        BarColumn(),
        transient=True,
    ) as p:
        p.add_task("", total=None)
        asyncio.run(collect_all())
    with Progress(
        TextColumn("[bold cyan]JobZo[/bold cyan] • Scoring jobs..."),
        BarColumn(),
        transient=True,
    ) as p:
        p.add_task("", total=None)
        asyncio.run(score_pending_jobs())

    session = get_session()
    drafted = session.query(Application).filter(
        Application.status.in_(["drafted", "ready"])
    ).count()
    submitted = session.query(Application).filter(
        Application.status == "submitted"
    ).count()
    pending_tasks = session.query(Task).filter(Task.done == False).count()
    interviews = session.query(Application).filter(
        Application.status == "interview"
    ).count()
    session.close()

    total_items = drafted + pending_tasks
    done_items = submitted + interviews
    progress_pct = min(int(done_items / max(total_items, 1) * 100), 100)

    est_minutes = drafted * 3 + pending_tasks * 2

    console.clear()
    console.print()
    console.print(Panel.fit(
        "[bold yellow]🎯 Today's Mission[/bold yellow]\n\n"
        f"Estimated time: [bold]{est_minutes} minutes[/bold]\n\n"
        f"{'█' * (progress_pct // 10)}{'░' * (10 - progress_pct // 10)}  {progress_pct}%\n\n"
        f"{'📋' if drafted else '✅'} Review [bold]{drafted}[/bold] new {'job' if drafted == 1 else 'jobs'}\n"
        f"{'📝' if submitted else '✅'} {'📝 Apply to open positions' if submitted == 0 else f'Applied [bold]{submitted}[/bold] jobs'}\n"
        f"{'📅' if pending_tasks else '✅'} {'📅 ' + str(pending_tasks) + ' follow-up' + ('s' if pending_tasks != 1 else '') + ' due' if pending_tasks else 'No pending tasks'}\n"
        f"{'🎤' if interviews else '✅'} {'🎤 ' + str(interviews) + ' interview' + ('s' if interviews != 1 else '') + ' coming up' if interviews else 'No upcoming interviews'}\n",
        box=box.ROUNDED,
        padding=(1, 4),
    ))

    options = []
    if drafted:
        options.append(("[1]", "Review jobs", "_review_jobs"))
    if submitted:
        options.append(("[2]", "Check progress", "_show_progress"))
    if pending_tasks:
        options.append(("[3]", "Complete follow-ups", "_do_followups"))
    if interviews:
        options.append(("[4]", "Prepare for interviews", "_show_interviews"))
    options.append(("[q]", "Quit", None))

    console.print("What would you like to do?")
    for key, label, _ in options:
        console.print(f"  {key} {label}")

    choice = input("\n> ").strip().lower()

    if choice == "1" and drafted:
        _review_jobs()
    elif choice == "2" and submitted:
        _show_progress()
    elif choice == "3" and pending_tasks:
        _do_followups()
    elif choice == "4" and interviews:
        _show_interviews()
    elif choice == "q":
        console.print("\n[green]Good luck today![/green]")
        return
    else:
        console.print("\n[yellow]Invalid choice[/yellow]")
        input("Press Enter to continue...")

    mission()


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
    total = asyncio.run(score_pending_jobs(skill_list, experience))
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
    scored = asyncio.run(score_pending_jobs(skill_list, experience))
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
