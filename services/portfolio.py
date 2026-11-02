"""Portfolio Risk — analyzes the user's application distribution.

Warns when applications concentrate in one category/ecosystem
and recommends diversification for better interview probability.
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from database.connection import get_session
from database.models import Application, Job, Company
from sqlalchemy import func


def portfolio_risk(user_id: str | None = None) -> dict[str, Any]:
    """Analyze the user's application portfolio for concentration risk.

    Returns:
        risk_level: "low" | "medium" | "high"
        distribution: breakdown by category
        recommendations: list of suggested diversifications
    """
    session = get_session()
    try:
        # All applications (across all statuses)
        apps = (
            session.query(Application)
            .filter(Application.status.in_(["drafted", "submitted", "applied", "in_progress"]))
            .all()
        )
        total = len(apps)
        if total == 0:
            return {
                "total_applications": 0,
                "risk_level": "low",
                "distribution": {},
                "recommendations": ["Start applying to build your portfolio"],
            }

        # Categorize by company category
        cat_counts: Counter = Counter()
        company_names = set()
        for app in apps:
            job = session.query(Job).filter(Job.id == app.job_id).first()
            if not job:
                continue
            company_names.add(job.company)
            company = _get_company(job.company, session)
            if company:
                cat_counts[company.primary_category] += 1
            else:
                cat_counts["unknown"] += 1

        total_categorized = sum(cat_counts.values())
        distribution = {}
        for cat, count in cat_counts.most_common():
            distribution[cat] = {
                "count": count,
                "pct": round(count / total_categorized * 100),
            }

        # Risk assessment
        max_cat_pct = max((v["pct"] for v in distribution.values()), default=0)
        num_categories = len(distribution)

        if max_cat_pct >= 80:
            risk_level = "high"
        elif max_cat_pct >= 60 or num_categories <= 2:
            risk_level = "medium"
        else:
            risk_level = "low"

        # Recommendations
        recommendations = _portfolio_recommendations(distribution, risk_level, session)

        return {
            "total_applications": total,
            "unique_companies": len(company_names),
            "risk_level": risk_level,
            "distribution": distribution,
            "recommendations": recommendations,
        }
    finally:
        session.close()


def _get_company(name: str, session) -> Company | None:
    from services.company_registry import get_by_alias
    return get_by_alias(name)


def _portfolio_recommendations(
    distribution: dict, risk_level: str, session,
) -> list[str]:
    recs = []

    if risk_level == "high":
        top_cat = max(distribution, key=lambda k: distribution[k]["pct"])
        recs.append(
            f"Portfolio too concentrated in {top_cat.replace('_', ' ').title()} "
            f"({distribution[top_cat]['pct']}% of applications)"
        )
        recs.append("Diversify across 3+ ecosystems to increase interview probability")

    # Check if missing key categories
    present = set(distribution.keys())
    high_value = {"gcc", "service", "indian_product"}
    missing = high_value - present
    if missing:
        labels = [m.replace("_", " ").title() for m in sorted(missing)]
        recs.append(f"Consider adding companies from: {', '.join(labels)}")

    if not recs:
        recs.append("Portfolio is well-diversified")

    return recs
