"""Opportunity Feed — today's prioritized decisions.

  🔥 Recommended    (probability ≥ 60)
  ★ Stretch         (probability ≥ 30)
  ⏭️ Hidden          (probability < 30)

Hidden jobs are shown as a count. The user can reveal them.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.connection import get_session
from database.models import Application, Job
from sqlalchemy.orm import joinedload

from services.probability import estimate_for_application
from services.user_profile import load_profile, strategy_multiplier
from services.freshness import freshness_band
from services.trust import (
    TIER_SUSPICIOUS,
    TIER_VERIFIED,
    TrustResult,
    trust_for_job,
)


@dataclass
class FeedItem:
    app: Application
    job: Job
    prob: dict[str, Any]
    combined: float
    tier: str
    tier_emoji: str
    tier_rank: int
    trust: TrustResult | None = None
    freshness: dict[str, Any] | None = None


def build_feed() -> list[FeedItem]:
    """Build feed sorted by estimated interview probability × strategy."""
    session = get_session()
    profile = load_profile()
    strat_mult = strategy_multiplier(profile.get("strategy", "balanced"))

    try:
        apps = (
            session.query(Application)
            .options(joinedload(Application.job))
            .filter(Application.status.in_(["drafted", "ready"]))
            .all()
        )
    finally:
        session.close()

    items = []
    for app in apps:
        job = app.job if hasattr(app, 'job') else None
        if not job:
            continue
        try:
            prob = estimate_for_application(app, get_session(), profile)
            combined = prob["score"] * strat_mult
            tier, emoji, rank = _classify(combined, prob["score"])
            trust = trust_for_job(job)
            fresh = freshness_band(job.posted_at)
            items.append(FeedItem(app=app, job=job, prob=prob, combined=combined,
                                  tier=tier, tier_emoji=emoji, tier_rank=rank,
                                  trust=trust, freshness=fresh))
        except Exception:
            continue

    items.sort(key=lambda x: (-x.tier_rank, -x.combined))
    return items


def _classify(combined: float, raw_score: int) -> tuple[str, str, int]:
    if combined >= 60:
        return "recommended", "🔥", 3
    elif combined >= 30:
        return "stretch", "★", 2
    else:
        return "hidden", "⏭️", 1


TIER_LABELS = {
    "recommended": "RECOMMENDED",
    "stretch": "STRETCH",
    "hidden": "HIDDEN",
}


def print_feed(items: list[FeedItem]):
    from rich.console import Console
    from rich.table import Table

    c = Console()
    today_str = datetime.utcnow().strftime("%a %b %d")
    c.print()
    c.print(f"[bold cyan]TODAY — {today_str}[/bold cyan]")
    c.print()

    # Trust is a safety gate: 🚨 suspicious postings are blocked from the feed.
    blocked = [i for i in items if i.trust and i.trust.tier == TIER_SUSPICIOUS]
    visible = [i for i in items if not (i.trust and i.trust.tier == TIER_SUSPICIOUS)]
    shown = [i for i in visible if i.tier != "hidden"]
    hidden = [i for i in visible if i.tier == "hidden"]

    current_tier = None
    idx = 0

    for item in shown:
        if item.tier != current_tier:
            current_tier = item.tier
            label = TIER_LABELS.get(item.tier, item.tier.upper())
            emoji = item.tier_emoji
            count = sum(1 for i in shown if i.tier == item.tier)
            c.print(f"\n  [bold]{emoji} {label}[/bold]  ([dim]{count}[/dim])\n")

        idx += 1
        prob = item.prob
        score = prob["score"]
        confidence = prob["confidence"]
        reasons = prob.get("reasons", [])
        color = "green" if score >= 60 else "yellow" if score >= 30 else "red"

        trust = item.trust
        trust_badge = ""
        trust_str = ""
        if trust:
            trust_badge = f"{trust.emoji} "
            t_color = {"verified": "green", "trusted": "cyan",
                       "unverified": "yellow", "suspicious": "red"}.get(trust.tier, "white")
            trust_str = f"  [bold][{t_color}]Trust: {trust.score:.0f}[/{t_color}][/bold]"
        fresh = item.freshness or {}
        fresh_str = f"  [dim]{fresh.get('emoji', '')} {fresh.get('label', '')}[/dim]" if fresh.get("label") else ""

        c.print(
            f"  [dim]{idx}.[/dim] "
            f"{trust_badge}[bold]{item.job.company}[/bold] — "
            f"{item.job.title[:50]}"
        )
        c.print(
            f"       [bold]Probability: [{color}]{score}%[/{color}][/bold]"
            f"  [dim]Confidence: {confidence}[/dim]"
            f"{trust_str}{fresh_str}"
        )
        for r in reasons[:3]:
            c.print(f"  {r}")

    if hidden:
        c.print(f"\n  [dim]⏭️  {len(hidden)} hidden opportunities (probability < 30)[/dim]")
        c.print(f"  [dim]  Run [green]jobzo feed[/green] to reveal all[/dim]")

    if blocked:
        c.print()
        c.print(f"  [bold red]🚨 BLOCKED — {len(blocked)} suspicious job{'' if len(blocked) == 1 else 's'} hidden[/bold red]")
        for b in blocked[:3]:
            flag = b.trust.risks[0]["keyword"] if b.trust and b.trust.risks else "scam indicators"
            c.print(f"     [dim]{b.job.company} — {b.job.title[:40]} ({flag})[/dim]")
        if len(blocked) > 3:
            c.print(f"     [dim]… and {len(blocked) - 3} more[/dim]")

    c.print()
    c.print("[dim]Actions: jobzo apply <id> | jobzo review (full detail) | jobzo dig (fresher dig)[/dim]")
    c.print()
