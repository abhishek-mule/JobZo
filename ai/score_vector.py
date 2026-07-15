"""Decomposed scoring — score vector with explainable dimensions.

Instead of a single opaque score, produces a vector with interpretable components:
  role_fit:      How well the role type matches the user's profile
  skill_fit:     Skill overlap (with graph-aware expansion)
  experience_fit: Years of experience vs. requirements
  company_fit:   Company category/quality preference match
  location_fit:  Location/remote preference match
  growth_value:  Expected career growth from this opportunity
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreVector:
    role_fit: float = 0.0       # 0-1
    skill_fit: float = 0.0      # 0-1
    experience_fit: float = 0.0 # 0-1
    company_fit: float = 0.0    # 0-1
    location_fit: float = 0.0   # 0-1
    growth_value: float = 0.0   # 0-1

    # Explanation strings
    explanations: dict[str, str] = field(default_factory=dict)

    _weights: dict[str, float] = field(default_factory=lambda: {
        "role_fit": 0.25,
        "skill_fit": 0.25,
        "experience_fit": 0.15,
        "company_fit": 0.15,
        "location_fit": 0.10,
        "growth_value": 0.10,
    })

    def composite(self) -> float:
        """Weighted sum of all components."""
        return sum(
            getattr(self, dim) * self._weights.get(dim, 0.0)
            for dim in ["role_fit", "skill_fit", "experience_fit", "company_fit", "location_fit", "growth_value"]
        )

    def composite_int(self) -> int:
        """0-100 integer score."""
        return min(int(self.composite() * 100), 100)

    def tier(self) -> str:
        """Map composite score to recommendation tier."""
        score = self.composite_int()
        if score >= 90:
            return "apply_now"
        elif score >= 75:
            return "strong_match"
        elif score >= 60:
            return "worth_trying"
        elif score >= 45:
            return "stretch"
        return "ignore"

    def summary(self) -> list[tuple[str, float, str]]:
        """Return list of (component_name, value, explanation) sorted by impact."""
        dims = ["role_fit", "skill_fit", "experience_fit", "company_fit", "location_fit", "growth_value"]
        result = []
        for d in dims:
            val = getattr(self, d)
            expl = self.explanations.get(d, "")
            result.append((d, val, expl))
        return result

    def explain(self) -> str:
        """Generate a human-readable explanation string."""
        parts = []
        for name, val, expl in self.summary():
            bar = "█" * int(val * 10) + "░" * (10 - int(val * 10))
            if expl:
                parts.append(f"{name:20s} {val:.0%} {bar}  {expl}")
            else:
                parts.append(f"{name:20s} {val:.0%} {bar}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_fit": round(self.role_fit, 3),
            "skill_fit": round(self.skill_fit, 3),
            "experience_fit": round(self.experience_fit, 3),
            "company_fit": round(self.company_fit, 3),
            "location_fit": round(self.location_fit, 3),
            "growth_value": round(self.growth_value, 3),
            "composite": self.composite_int(),
            "tier": self.tier(),
            "explanations": self.explanations,
        }


def compute_score_vector(
    *,
    role_fit: float = 0.0,
    skill_fit: float = 0.0,
    experience_fit: float = 0.0,
    company_fit: float = 0.0,
    location_fit: float = 0.0,
    growth_value: float = 0.0,
    explanations: dict[str, str] | None = None,
    weights: dict[str, float] | None = None,
) -> ScoreVector:
    """Factory function to create a ScoreVector with optional overrides."""
    sv = ScoreVector(
        role_fit=max(0.0, min(1.0, role_fit)),
        skill_fit=max(0.0, min(1.0, skill_fit)),
        experience_fit=max(0.0, min(1.0, experience_fit)),
        company_fit=max(0.0, min(1.0, company_fit)),
        location_fit=max(0.0, min(1.0, location_fit)),
        growth_value=max(0.0, min(1.0, growth_value)),
        explanations=explanations or {},
    )
    if weights:
        sv._weights = weights
    return sv
