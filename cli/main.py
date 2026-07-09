import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from services.logging_setup import setup_logging
from services.collector import collect_all
from services.config import Config
from ai.scorer import score_pending_jobs
from ai.llm import ask
from browser.assistant import BrowserAssistant
from tracker.applications import list_applications, transition_status, get_application
from tracker.tasks import list_pending_tasks, complete_task
from database.connection import get_session
from database.models import Job, Application
from sqlalchemy.orm import joinedload

logger = logging.getLogger("jobzo")
console = Console()
app = typer.Typer(name="jobzo", no_args_is_help=True)

setup_logging()


@app.command()
def collect(
    keywords: str = typer.Option("", help="Comma-separated keywords to search for"),
):
    """Collect jobs from all enabled providers."""
    console.print("[bold cyan]JobZo[/bold cyan] — Collecting jobs...")
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] or None
    total = asyncio.run(collect_all(kw_list))
    console.print(f"[green]✓[/green] Collected {total} new jobs")


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
                Application.id == app_id
            ).first()
            apps = [app_obj] if app_obj else []
        else:
            console.print("[red]Provide an application ID or use --daily[/red]")
            return

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

            if application.strategy == "skip":
                console.print("  [yellow]Skipping (strategy: skip)[/yellow]")
                continue

            try:
                result = ask("cover_letter", f"""Company: {job.company}
Role: {job.title}
Description: {job.description[:1500]}
Resume type: {resume_path}""")
                if isinstance(result, dict):
                    cover_letter = result.get("cover_letter", "")
                else:
                    cover_letter = str(result)
            except Exception as e:
                console.print(f"  [red]Cover letter failed: {e}[/red]")
                cover_letter = f"I am excited to apply for the {job.title} role at {job.company}."

            if not resume_path:
                console.print("  [yellow]No resume selected, applying default[/yellow]")
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
                        console.print(f"  [green]✓[/green] Marked as submitted")
                    else:
                        console.print("  [yellow]Skipped[/yellow]")
                else:
                    console.print("  [yellow]Form not detected. Apply manually.[/yellow]")
                    transition_status(str(application.id), "submitted")
            except Exception as e:
                logger.error("Browser automation failed: %s", e)
                console.print(f"  [red]Browser error: {e}[/red]")
            finally:
                asyncio.run(assistant.close())

    finally:
        session.close()


@app.command()
def export(
    fmt: str = typer.Argument("csv", help="Export format (csv)"),
    out: str = typer.Option("applications.csv", "--out", "-o", help="Output file path"),
):
    """Export applications to CSV for analysis."""
    import csv
    from database.connection import get_session
    from database.models import Application, Job

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


if __name__ == "__main__":
    app()
