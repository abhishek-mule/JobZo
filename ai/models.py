"""Canonical models for the retrieval + ranking pipeline.

Every component (scorer, ranker, mission planner, dashboard) consumes
exactly one object: RankedOpportunity.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ai.score_vector import ScoreVector


@dataclass
class RankedOpportunity:
    """A fully evaluated job — output of the retriever + ranker pipeline.

    This is the one canonical object consumed by every downstream component:
      - Mission Planner → feed
      - Dashboard → display
      - Auto Apply → selection
      - Explanation Engine → breakdown
    """

    # ── Job identity ──────────────────────────────────────────────────
    job_id: str
    company: str
    title: str
    url: str
    location: str
    remote: bool
    posted_at: datetime | None = None
    source: str = ""

    # ── Normalization ─────────────────────────────────────────────────
    canonical_role: str = "UNKNOWN"
    role_confidence: float = 0.0
    seniority: str = "mid"

    # ── Skills ────────────────────────────────────────────────────────
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[tuple[str, float, str]] = field(default_factory=list)
    expanded_skills: dict[str, float] = field(default_factory=dict)

    # ── Scoring ───────────────────────────────────────────────────────
    score_vector: ScoreVector = field(default_factory=ScoreVector)
    retrieval_score: float = 0.0
    skill_overlap: float = 0.0      # raw overlap before vector weighting
    freshness: float = 1.0

    # ── Ranking ───────────────────────────────────────────────────────
    interview_probability: int = 0   # 0-100
    confidence: str = "Low"          # Low | Medium | High
    risk: str = "Medium"             # Easy | Medium | Hard
    effort_minutes: int = 15

    # ── Context ───────────────────────────────────────────────────────
    raw_description: str = ""
    eligibility_reason: str = ""
    explanation: str = ""

    # ── Computed helpers ──────────────────────────────────────────────

    def composite_score(self) -> int:
        """0-100 composite score from the score vector."""
        return self.score_vector.composite_int()

    def tier(self) -> str:
        """Recommendation tier based on score."""
        return self.score_vector.tier()

    def summary(self) -> dict[str, Any]:
        """Dict summary for serialization / CLI display."""
        return {
            "company": self.company,
            "title": self.title,
            "score": self.composite_score(),
            "tier": self.tier(),
            "interview_probability": self.interview_probability,
            "confidence": self.confidence,
            "risk": self.risk,
            "score_vector": self.score_vector.to_dict(),
            "matched_skills": self.matched_skills,
            "missing_skills": [s for s, _, _ in self.missing_skills],
        }
