"""Resume Scorer — scores resume variants against a job description across 7 independent dimensions.

Each dimension scores 0-100. The composite is a weighted sum.

Dimensions:
  Technical      — skill overlap weighted by rarity
  Experience     — years/level match
  Domain         — industry domain overlap
  Project        — project skills relevance
  Education      — basic education check
  Location       — remote/location fit (from eligibility)
  Eligibility    — pass/fail from eligibility engine
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

from skills import canonical_name, skill_weight
from resumes.jd_analyzer import JDAnalysis
from resumes.registry import ResumeMeta

logger = logging.getLogger("jobzo.scorer")

# Default weights for composite score (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "technical": 0.30,
    "experience": 0.20,
    "domain": 0.15,
    "project": 0.15,
    "education": 0.05,
    "location": 0.10,
    "eligibility": 0.05,
}


@dataclass
class DimensionScore:
    score: float = 0.0  # 0-100
    details: str = ""


def _default_dim() -> DimensionScore:
    return DimensionScore()

@dataclass
class ResumeScore:
    resume_name: str
    technical: DimensionScore = field(default_factory=_default_dim)
    experience: DimensionScore = field(default_factory=_default_dim)
    domain: DimensionScore = field(default_factory=_default_dim)
    project: DimensionScore = field(default_factory=_default_dim)
    education: DimensionScore = field(default_factory=_default_dim)
    location: DimensionScore = field(default_factory=_default_dim)
    eligibility: DimensionScore = field(default_factory=_default_dim)

    @property
    def dimensions(self) -> dict[str, DimensionScore]:
        return {
            "technical": self.technical,
            "experience": self.experience,
            "domain": self.domain,
            "project": self.project,
            "education": self.education,
            "location": self.location,
            "eligibility": self.eligibility,
        }

    def composite(self, weights: dict[str, float] | None = None) -> float:
        w = weights or DEFAULT_WEIGHTS
        total = 0.0
        for key, dim in self.dimensions.items():
            total += dim.score * w.get(key, 0)
        return round(total, 1)


def _score_technical(jd_skills: list[str], resume: ResumeMeta) -> DimensionScore:
    if not jd_skills or not resume.skills:
        return DimensionScore(0.0, "No skills to compare")

    resume_skills_lower = {s.lower() for s in resume.all_skill_names}
    jd_set = {s.lower() for s in jd_skills}

    matched: list[str] = []
    total_weight = 0.0
    matched_weight = 0.0

    for jd_skill in jd_set:
        w = skill_weight(jd_skill)
        total_weight += w
        if jd_skill in resume_skills_lower:
            matched_weight += w
            matched.append(jd_skill)

    if total_weight == 0:
        return DimensionScore(0.0, "No weighted skills found")

    overlap = matched_weight / total_weight
    score = round(overlap * 100, 1)
    details = f"Matched {len(matched)}/{len(jd_set)} skills: {', '.join(matched[:8])}"
    return DimensionScore(score, details)


def _score_experience(jd_analysis: JDAnalysis, resume: ResumeMeta) -> DimensionScore:
    jd_level = jd_analysis.experience_level
    resume_level = resume.experience

    level_order = {"intern": 0, "junior": 1, "mid": 2, "senior": 3, "staff": 4}
    jd_val = level_order.get(jd_level)
    res_val = level_order.get(resume_level)

    if jd_val is None and res_val is None:
        # No level info — assume mid for both
        return DimensionScore(75.0, "No experience level specified; assuming mid")

    if jd_val is None:
        return DimensionScore(70.0, f"Resume level: {resume_level}; JD unspecified")

    if res_val is None:
        # Resume has no level set — check experience text
        return DimensionScore(50.0, f"JD requires {jd_level}; resume level unknown")

    diff = abs(jd_val - res_val)
    if diff == 0:
        return DimensionScore(100.0, f"Exact match: {jd_level}")
    elif diff == 1:
        return DimensionScore(80.0, f"Close match: JD={jd_level}, Resume={resume_level}")
    else:
        return DimensionScore(max(20, 60 - diff * 15), f"Gap: JD={jd_level}, Resume={resume_level}")


def _score_domain(jd_domains: list[str], resume: ResumeMeta) -> DimensionScore:
    if not jd_domains or not resume.domains:
        return DimensionScore(0.0, "No domains to compare")

    jd_lower = [d.lower() for d in jd_domains]
    res_lower = [d.lower() for d in resume.domains]

    matched = [d for d in jd_lower if d in res_lower]
    if not matched and not jd_lower:
        return DimensionScore(0.0, "No domain overlap")

    project_domains = [d.lower() for d in resume.project_domains]
    project_matched = [d for d in jd_lower if d in project_domains]

    score = 0.0
    if matched:
        score += (len(matched) / len(jd_lower)) * 60.0  # resume domain match
    if project_matched:
        score += (len(project_matched) / len(jd_lower)) * 40.0  # project evidence

    details_parts = []
    if matched:
        details_parts.append(f"Domain: {', '.join(matched)}")
    if project_matched:
        details_parts.append(f"Project evidence: {', '.join(project_matched)}")

    return DimensionScore(round(min(score, 100), 1), "; ".join(details_parts))


def _score_project(jd_skills: list[str], resume: ResumeMeta) -> DimensionScore:
    if not jd_skills:
        return DimensionScore(0.0, "No JD skills")

    jd_set = set(s.lower() for s in jd_skills)
    project_skills = set(s.lower() for s in resume.project_skills)

    if not project_skills:
        return DimensionScore(0.0, "Resume has no project skills")

    matched = jd_set & project_skills
    if not matched:
        return DimensionScore(0.0, "No project skill overlap")

    coverage = len(matched) / len(jd_set)
    score = round(coverage * 100, 1)
    return DimensionScore(score, f"Project skills cover {len(matched)}/{len(jd_set)} JD skills")


def _score_education(jd_text: str, resume: ResumeMeta) -> DimensionScore:
    if not resume.education:
        return DimensionScore(50.0, "No education info")

    jd_lower = jd_text.lower()
    edu_lower = resume.education.lower()

    # Check if JD mentions degree requirement
    degree_mentions = ["bachelor", "b.tech", "b.e", "bs", "master", "m.tech", "m.s", "phd", "degree"]
    mentions_degree = any(kw in jd_lower for kw in degree_mentions)

    if not mentions_degree:
        return DimensionScore(100.0, "No degree requirement specified")

    edu_keywords = ["b.tech", "bachelor", "computer", "engineering", "technology"]
    if any(kw in edu_lower for kw in edu_keywords):
        return DimensionScore(100.0, f"Education matches: {resume.education}")
    return DimensionScore(60.0, f"Has degree: {resume.education}")


def score_resumes(
    jd_analysis: JDAnalysis,
    resumes: list[ResumeMeta],
    jd_text: str = "",
    is_eligible: bool = True,
) -> list[ResumeScore]:
    """Score all resume variants against a JD analysis.

    Args:
        jd_analysis: Analyzed job description
        resumes: List of resume metadata to score
        jd_text: Raw JD text (for education scoring)
        is_eligible: Whether the job passed eligibility

    Returns:
        Sorted list of ResumeScore, highest composite first
    """
    results: list[ResumeScore] = []

    for resume in resumes:
        score = ResumeScore(resume_name=resume.name)
        score.technical = _score_technical(jd_analysis.skills, resume)
        score.experience = _score_experience(jd_analysis, resume)
        score.domain = _score_domain(jd_analysis.domains, resume)
        score.project = _score_project(jd_analysis.skills, resume)
        score.education = _score_education(jd_text or jd_analysis.raw_text, resume)
        score.location = DimensionScore(100.0 if is_eligible else 0.0, "Eligible" if is_eligible else "Ineligible")
        score.eligibility = DimensionScore(100.0 if is_eligible else 0.0, "Pass" if is_eligible else "Fail")
        results.append(score)

    results.sort(key=lambda r: r.composite(), reverse=True)
    return results


def best_resume(
    jd_analysis: JDAnalysis,
    resumes: list[ResumeMeta],
    jd_text: str = "",
    is_eligible: bool = True,
) -> ResumeScore | None:
    scored = score_resumes(jd_analysis, resumes, jd_text, is_eligible)
    return scored[0] if scored else None
