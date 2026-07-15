"""Job Fit Report — on-demand human-readable report for a single job vs resume.

This is a derived artifact, NOT stored in the database.
Regenerated every time it's viewed, so it's always current.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from resumes.jd_analyzer import JDAnalysis, analyze as analyze_jd
from resumes.scorer import score_resumes, ResumeScore, DEFAULT_WEIGHTS
from resumes.registry import ResumeRegistry, ResumeMeta
from resumes.optimizer import optimize


@dataclass
class FitReport:
    company: str = ""
    title: str = ""
    location: str = ""
    analysis: JDAnalysis | None = None
    scores: list[ResumeScore] = field(default_factory=list)
    recommended_resume: str = ""
    confidence: float = 0.0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    optimization: str = ""
    interview_probability: str = "Medium"

    def format_text(self) -> str:
        lines: list[str] = []
        lines.append(f"{'='*60}")
        lines.append(f"  {self.title}")
        lines.append(f"  {self.company}")
        if self.location:
            lines.append(f"  {self.location}")
        lines.append(f"{'='*60}")

        if self.analysis:
            lines.append(f"\n  Overall Match: {self.confidence:.0f}%")
            lines.append(f"  Interview Probability: {self.interview_probability}")

            lines.append(f"\n  Why?")
            for s in self.strengths:
                lines.append(f"    + {s}")

            if self.matched_skills:
                lines.append(f"\n  Matched Skills")
                for s in self.matched_skills[:8]:
                    lines.append(f"    ✓ {s}")

            if self.missing_skills:
                lines.append(f"\n  Penalty")
                for s in self.missing_skills[:8]:
                    lines.append(f"    - {s}")

        if self.scores:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  Resume Rankings")
            lines.append(f"{'─'*60}")

            best = self.scores[0]
            for score in self.scores:
                comp = score.composite()
                marker = "★" if score.resume_name == self.recommended_resume else " "
                dims = []
                for key, dim in score.dimensions.items():
                    dims.append(f"{key}={dim.score:.0f}")
                lines.append(f"  {marker} {score.resume_name:20s} {comp:5.1f}%  ({', '.join(dims)})")

            if self.optimization:
                lines.append(f"\n  Suggestions")
                for line in self.optimization.split("\n")[:5]:
                    lines.append(f"    {line}")

            lines.append(f"\n  Recommended Resume: {self.recommended_resume}")
            lines.append(f"  Confidence: {self.confidence:.0f}%")

        lines.append(f"{'='*60}")
        return "\n".join(lines)


def generate(
    company: str,
    title: str,
    jd_text: str,
    registry: ResumeRegistry,
    is_eligible: bool = True,
    use_llm: bool = False,
    location: str = "",
) -> FitReport:
    """Generate a fit report for a job against all resume variants."""
    analysis = analyze_jd(jd_text, use_llm=use_llm)
    all_resumes = registry.all()
    scored = score_resumes(analysis, all_resumes, jd_text, is_eligible)

    report = FitReport(
        company=company,
        title=title,
        location=location,
        analysis=analysis,
        scores=scored,
    )

    if not scored:
        return report

    best = scored[0]
    report.recommended_resume = best.resume_name
    report.confidence = best.composite()

    # Compute matched/missing skills from best resume
    if analysis and analysis.skills:
        best_meta = registry.get(best.resume_name)
        if best_meta:
            resume_skills_lower = {s.lower() for s in best_meta.all_skill_names}
            for s in analysis.skills:
                if s.lower() in resume_skills_lower:
                    report.matched_skills.append(s)
                else:
                    report.missing_skills.append(s)

    # Explainability: build strength/penalty explanations
    strengths = []
    penalties = []

    tech = best.technical
    if tech.score >= 50:
        num_matched = len(report.matched_skills)
        num_total = len(analysis.skills) if analysis and analysis.skills else 0
        strengths.append(f"Skills: matched {num_matched}/{num_total} JD requirements")

    exp = best.experience
    if exp.score >= 70:
        strengths.append(f"Experience: {exp.details[:40]}")
    elif exp.score < 40:
        penalties.append(f"Experience gap: {exp.details[:40]}")

    dom = best.domain
    if dom.score >= 50:
        strengths.append(f"Domain: {dom.details[:40]}")
    elif dom.score > 0 and dom.score < 40:
        penalties.append(f"Domain mismatch: {dom.details[:40]}")

    proj = best.project
    if proj.score >= 50:
        strengths.append(f"Projects: {proj.details[:40]}")

    loc = best.location
    if loc.score < 100:
        penalties.append(f"Location: not eligible")

    report.strengths = strengths or ["General fit within range"]
    report.weaknesses = penalties or []

    # Interview probability heuristic
    composite = best.composite()
    if composite >= 80:
        report.interview_probability = "High"
    elif composite >= 50:
        report.interview_probability = "Medium"
    else:
        report.interview_probability = "Low"

    # Generate optimization suggestions for best resume
    if best_meta:
        opt = optimize(best_meta, analysis, jd_text)
        report.optimization = opt.summary()
        report.weaknesses.extend(
            f"Suggestion: {s.action} {s.target}" for s in opt.suggestions
            if s.priority == "high"
        )

    return report
