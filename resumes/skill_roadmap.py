"""Skill Roadmap — aggregates skill demand from recommended jobs and identifies gaps vs resume.

Default: based on recommended jobs (Application.status == 'recommended').
Filters: --all, --applied, --company, --role
"""

from __future__ import annotations
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Job, Application
from database.connection import get_session
from skills import canonical_name, skill_category, skill_weight, skill_patterns
from resumes.registry import ResumeRegistry

logger = logging.getLogger("jobzo.roadmap")


@dataclass
class SkillDemand:
    skill: str
    frequency: int
    percentage: float
    category: str
    weight: int
    on_resume: bool = False
    gap: bool = False  # high demand but not on resume


@dataclass
class SkillRoadmap:
    total_jobs_analyzed: int = 0
    skills: list[SkillDemand] = field(default_factory=list)
    gaps: list[SkillDemand] = field(default_factory=list)
    filter_description: str = ""

    def format_text(self, max_skills: int = 20) -> str:
        lines: list[str] = []
        lines.append(f"Skill Roadmap")
        lines.append(f"Based on {self.total_jobs_analyzed} jobs ({self.filter_description})")
        lines.append(f"{'─'*60}")

        if not self.skills:
            lines.append("No skill data available.")
            return "\n".join(lines)

        lines.append(f"\n  Top Skills in Demand")
        for sd in self.skills[:max_skills]:
            bar = "█" * int(sd.percentage / 5) + "░" * (20 - int(sd.percentage / 5))
            gap_marker = " ⚠️  GAP" if sd.gap else ""
            lines.append(f"  {sd.skill:20s} {sd.percentage:5.1f}% {bar} {gap_marker}")

        if self.gaps:
            lines.append(f"\n  Your Missing Skills (High Priority)")
            for g in self.gaps[:8]:
                lines.append(f"    ⚠️  {g.skill} — appears in {g.percentage:.0f}% of jobs")
            lines.append(f"\n  Recommendation: Focus on learning these to improve interview probability.")

        return "\n".join(lines)


def build_roadmap(
    registry: ResumeRegistry,
    status_filter: str = "recommended",
    company_filter: str | None = None,
    role_filter: str | None = None,
    limit: int = 1000,
) -> SkillRoadmap:
    """Build skill roadmap from database.

    Args:
        registry: Resume registry to check skill coverage
        status_filter: 'recommended' (default), 'all', 'applied'
        company_filter: Optional company name filter
        role_filter: Optional role keyword filter
        limit: Max jobs to analyze

    Returns:
        SkillRoadmap with demand data
    """
    session: Session = get_session()
    roadmap = SkillRoadmap()

    try:
        query = select(Job)

        if status_filter == "recommended":
            query = query.join(Application, Application.job_id == Job.id).where(
                Application.status == "recommended"
            )
        elif status_filter == "applied":
            query = query.join(Application, Application.job_id == Job.id).where(
                Application.status.in_(["applied", "submitted", "interviewing"])
            )

        if company_filter:
            query = query.where(Job.company.ilike(f"%{company_filter}%"))

        if role_filter:
            query = query.where(Job.title.ilike(f"%{role_filter}%"))

        query = query.limit(limit)
        jobs = session.execute(query).scalars().all()
        roadmap.total_jobs_analyzed = len(jobs)

        # Count skill mentions across all jobs
        skill_counter: Counter = Counter()
        patterns = skill_patterns()

        for job in jobs:
            text = f"{job.title} {job.description}"
            text_lower = text.lower()
            seen_in_job: set[str] = set()
            for pattern, canonical in patterns:
                if pattern.search(text_lower):
                    if canonical not in seen_in_job:
                        seen_in_job.add(canonical)
                        skill_counter[canonical] += 1

        # Build resume skill set
        resume_skills: set[str] = set()
        for meta in registry.all():
            resume_skills.update(s.lower() for s in meta.skills)
            resume_skills.update(s.lower() for s in meta.project_skills)

        total = roadmap.total_jobs_analyzed
        if total == 0:
            return roadmap

        skill_demands: list[SkillDemand] = []
        for skill, count in skill_counter.most_common():
            pct = round(count / total * 100, 1)
            canon = canonical_name(skill)
            cat = skill_category(skill)
            w = skill_weight(skill)
            on_resume = skill.lower() in resume_skills
            is_gap = pct >= 50 and not on_resume
            skill_demands.append(SkillDemand(
                skill=canon,
                frequency=count,
                percentage=pct,
                category=cat,
                weight=w,
                on_resume=on_resume,
                gap=is_gap,
            ))

        roadmap.skills = skill_demands
        roadmap.gaps = [s for s in skill_demands if s.gap]

        roadmap.filter_description = status_filter
        if company_filter:
            roadmap.filter_description += f", company={company_filter}"
        if role_filter:
            roadmap.filter_description += f", role={role_filter}"

    finally:
        session.close()

    return roadmap
