"""Resume Optimizer — generates actionable suggestions to improve resume-JD alignment.

Not just a gap list. Produces concrete actions:
- Reorder projects
- Add/remove keywords
- Reword summary
- Emphasize specific skills
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

from skills import canonical_name, skill_category
from resumes.jd_analyzer import JDAnalysis
from resumes.registry import ResumeMeta

logger = logging.getLogger("jobzo.optimizer")


@dataclass
class Suggestion:
    action: str  # add / remove / reorder / reword / emphasize / mention
    target: str  # what to change
    reason: str
    priority: str = "medium"  # high / medium / low


@dataclass
class Optimization:
    resume_name: str
    suggestions: list[Suggestion] = field(default_factory=list)
    score_improvement: float = 0.0  # estimated % improvement

    def summary(self) -> str:
        if not self.suggestions:
            return f"{self.resume_name}: No improvements needed"
        lines = [f"{self.resume_name}:"]
        for s in self.suggestions:
            icon = {"high": "!!", "medium": "!", "low": "?"}.get(s.priority, "?")
            lines.append(f"  {icon} {s.action} {s.target} — {s.reason}")
        return "\n".join(lines)


def optimize(
    resume: ResumeMeta,
    jd_analysis: JDAnalysis,
    jd_text: str = "",
) -> Optimization:
    """Generate optimization suggestions for a single resume against a JD."""
    opt = Optimization(resume_name=resume.name)
    jd_lower = jd_text.lower() if jd_text else ""
    jd_title = ""
    if jd_text:
        for line in jd_text.split("\n"):
            line = line.strip()
            if line and len(line) < 200:
                jd_title = line
                break

    # 1. Missing high-value skills
    if jd_analysis.skills:
        resume_skills_lower = {s.lower() for s in resume.skills}
        missing = []
        for s in jd_analysis.skills:
            if s.lower() not in resume_skills_lower:
                missing.append(s)
        if missing:
            opt.suggestions.append(Suggestion(
                action="add",
                target=f"Mention {', '.join(missing[:4])}",
                reason=f"JD requires {len(missing)} skill(s) not on resume",
                priority="high" if len(missing) <= 3 else "medium",
            ))

    # 2. Summary rephrasing
    if jd_title and resume.target_roles:
        jd_role_lower = jd_title.lower()
        has_role_match = any(r.lower() in jd_role_lower or jd_role_lower in r.lower()
                             for r in resume.target_roles)
        if not has_role_match:
            opt.suggestions.append(Suggestion(
                action="reword",
                target="Summary headline",
                reason=f"JD title '{jd_title}' doesn't match resume target roles",
                priority="high",
            ))

    # 3. Project reordering
    if jd_analysis.domains and resume.projects:
        jd_domains_lower = [d.lower() for d in jd_analysis.domains]
        best_project = None
        best_matches = 0
        for p in resume.projects:
            pd = p.get("domain", "").lower()
            if pd in jd_domains_lower:
                matches = sum(1 for s in p.get("skills", [])
                              if s.lower() in (s2.lower() for s2 in jd_analysis.skills))
                if matches > best_matches:
                    best_matches = matches
                    best_project = p["name"]
        if best_project:
            first_project = resume.projects[0]["name"] if resume.projects else ""
            if best_project != first_project:
                opt.suggestions.append(Suggestion(
                    action="reorder",
                    target=f"Move '{best_project}' above '{first_project}'",
                    reason=f"Better domain + skill match for this JD",
                    priority="high",
                ))

    # 4. Redundant or irrelevant skills
    jd_skills_lower = {s.lower() for s in jd_analysis.skills}
    for s in resume.skills:
        if s.lower() not in jd_skills_lower:
            cat = skill_category(s)
            if cat == "Frontend" and not any(d in jd_analysis.domains for d in ["web", "ecommerce"]):
                opt.suggestions.append(Suggestion(
                    action="de-emphasize",
                    target=f"'{s}' ({cat})",
                    reason=f"Not mentioned in JD and category doesn't match",
                    priority="low",
                ))

    # 5. Emphasize skill keywords — suggest repeating key skills
    key_skills = [s for s in jd_analysis.skills if skill_category(s) not in ("Language", "Tool")]
    if key_skills and len(key_skills) > len(resume.skills) * 0.5:
        mentioned = [s for s in key_skills if s.lower() in resume_skills_lower]
        if mentioned:
            opt.suggestions.append(Suggestion(
                action="emphasize",
                target=f"Mention {', '.join(mentioned[:3])} twice in summary and experience",
                reason=f"These core JD skills should appear prominently",
                priority="medium",
            ))

    return opt
