"""Dig — high-confidence opportunity discovery for the current strategy.

Pipeline (product architecture):
  ALL JOBS → Hard Eligibility → Trust (safety gate) → Freshness (priority
  weight) → Apply Priority (ranking) → DIG

Only 🚨 suspicious jobs are excluded. ⚠ unverified jobs are kept — they are
exactly the local / startup / GCC / college openings a fresher needs.

  jobzo dig           → everything non-suspicious, ranked by priority
  jobzo dig --fresh   → aggressive early-applicant hunting (0-3 days)
  jobzo dig --strict  → only 🛡 verified / ✓ trusted
"""

from dataclasses import dataclass, field
from typing import Any

from database.connection import get_session
from database.models import Application, Job
from sqlalchemy.orm import joinedload

from services.freshness import freshness_band
from services.trust import TIER_VERIFIED, TIER_TRUSTED, TIER_SUSPICIOUS, TrustResult, trust_for_job

# Indian opportunities realistic for a fresher profile. Company-level
# categories that are either homegrown (indian_product/startup/service) or
# India-headquartered verticals (fintech/edtech/healthtech) plus GCC captives.
LOCAL_CATEGORIES = ("indian_product", "startup", "service", "gcc",
                    "fintech", "edtech", "healthtech")


def _local_company_names() -> list[str]:
    from services.company_registry import get_all
    return [c.name for c in get_all(active_only=True)
            if c.primary_category in LOCAL_CATEGORIES]


@dataclass
class DigItem:
    app: Application
    job: Job
    score: int
    tier: str
    trust: TrustResult
    priority: float
    priority_reasons: list[str] = field(default_factory=list)
    expected_value: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True


@dataclass
class DigResult:
    items: list[DigItem] = field(default_factory=list)
    suspicious: list[DigItem] = field(default_factory=list)
    total: int = 0

    @property
    def shown(self) -> int:
        return len(self.items)


def _build_item(app: Application, job: Job, profile: dict) -> DigItem:
    from services.opportunity_intelligence import enrich

    trust = trust_for_job(job)
    oi = enrich(app.id, profile.get("declared_skills", []))
    priority = float(oi.get("apply_priority", app.score / 10.0)) if oi else app.score / 10.0
    return DigItem(
        app=app,
        job=job,
        score=app.score or 0,
        tier=app.tier or "",
        trust=trust,
        priority=round(priority, 1),
        priority_reasons=oi.get("reasons", []) if oi else [],
        expected_value=oi.get("expected_value", {}) if oi else {},
        freshness=freshness_band(job.posted_at),
    )


def qualifies(item: DigItem, *, fresh_only: bool = False, strict: bool = False,
              min_priority: float = 0.0, max_days: int | None = None) -> bool:
    """Pure filter rule: the safety gate is trust, priority is ranking."""
    if item.trust.tier == TIER_SUSPICIOUS:
        return False
    if strict and item.trust.tier not in (TIER_VERIFIED, TIER_TRUSTED):
        return False
    if item.priority < min_priority:
        return False
    if fresh_only and item.freshness.get("level") not in ("very_fresh", "fresh"):
        return False
    days_ago = item.freshness.get("days_ago")
    if max_days is not None and (days_ago is None or days_ago > max_days):
        return False
    return True


def dig_jobs(*, fresh_only: bool = False, strict: bool = False,
             min_priority: float = 0.0, max_days: int | None = None,
             min_score: int = 50, local_only: bool = False) -> DigResult:
    """Run the dig pipeline against the scored opportunity set.

    Hard eligibility is enforced at score time; we additionally require a
    minimum match score and a non-skip strategy. Trust is the safety gate
    (suspicious → blocked), priority is the ranking.
    """
    session = get_session()
    profile: dict[str, Any] = {}

    try:
        from services.user_profile import load_profile
        profile = load_profile()

        apps = (
            session.query(Application)
            .options(joinedload(Application.job))
            .filter(
                Application.status.in_(["drafted", "ready"]),
                Application.strategy != "skip",
                Application.score >= min_score,
            )
        )
        if local_only:
            local_names = _local_company_names()
            if not local_names:
                apps = apps.filter(False)
            else:
                apps = apps.join(Application.job).filter(Job.company.in_(local_names))
        apps = apps.all()
    finally:
        session.close()

    result = DigResult(total=len(apps))
    for app in apps:
        job = app.job if hasattr(app, "job") else None
        if not job:
            continue
        item = _build_item(app, job, profile)
        if item.trust.tier == TIER_SUSPICIOUS:
            result.suspicious.append(item)
            continue
        if qualifies(item, fresh_only=fresh_only, strict=strict,
                     min_priority=min_priority, max_days=max_days):
            result.items.append(item)

    result.items.sort(key=lambda x: (-x.priority, -x.trust.score))
    result.suspicious.sort(key=lambda x: -x.trust.score)
    return result


def run_dig(
    *,
    fresh_only: bool = True,
    strict: bool = True,
    min_priority: float = 6.0,
    max_days: int | None = None,
    local_only: bool = True,
    min_score: int = 50,
) -> DigResult:
    """Pure Python entry point — no Typer, safe for internal calls."""
    return dig_jobs(
        fresh_only=fresh_only,
        strict=strict,
        min_priority=min_priority,
        max_days=max_days,
        min_score=min_score,
        local_only=local_only,
    )


def pending_unscored_count() -> int:
    """How many eligible jobs still need scoring? (feed before dig)."""
    session = get_session()
    try:
        from database.models import Job
        from sqlalchemy import select, func
        return session.execute(
            select(func.count(Job.id)).where(
                Job.is_active == True,
                Job.eligible == True,
                ~Job.id.in_(select(Application.job_id)),
            )
        ).scalar() or 0
    finally:
        session.close()