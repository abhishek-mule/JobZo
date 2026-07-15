"""Canonical title normalizer — maps raw job titles to standardized role types."""

import re
from typing import NamedTuple

from skills import resolve_skill


# ── Canonical Role Types ──────────────────────────────────────────────────────

BACKEND_ENGINEER = "BACKEND_ENGINEER"
FRONTEND_ENGINEER = "FRONTEND_ENGINEER"
FULLSTACK_ENGINEER = "FULLSTACK_ENGINEER"
DEVOPS_ENGINEER = "DEVOPS_ENGINEER"
SRE = "SRE"
DATA_ENGINEER = "DATA_ENGINEER"
ML_ENGINEER = "ML_ENGINEER"
MOBILE_ENGINEER = "MOBILE_ENGINEER"
QA_ENGINEER = "QA_ENGINEER"
SECURITY_ENGINEER = "SECURITY_ENGINEER"
PLATFORM_ENGINEER = "PLATFORM_ENGINEER"
SOFTWARE_ENGINEER = "SOFTWARE_ENGINEER"
INTERN = "INTERN"
MANAGER = "MANAGER"
NON_TECH = "NON_TECH"
UNKNOWN = "UNKNOWN"

ROLE_LABELS = {
    BACKEND_ENGINEER: "Backend Engineer",
    FRONTEND_ENGINEER: "Frontend Engineer",
    FULLSTACK_ENGINEER: "Fullstack Engineer",
    DEVOPS_ENGINEER: "DevOps Engineer",
    SRE: "Site Reliability Engineer",
    DATA_ENGINEER: "Data Engineer",
    ML_ENGINEER: "ML Engineer",
    MOBILE_ENGINEER: "Mobile Engineer",
    QA_ENGINEER: "QA Engineer",
    SECURITY_ENGINEER: "Security Engineer",
    PLATFORM_ENGINEER: "Platform Engineer",
    SOFTWARE_ENGINEER: "Software Engineer",
    INTERN: "Intern",
    MANAGER: "Manager",
    NON_TECH: "Non-Technical",
    UNKNOWN: "Unknown",
}


# ── Title patterns ───────────────────────────────────────────────────────────

# Pattern: (regex, role_if_match, role_if_no_match_backend_context)
# Order matters — first match wins (most specific first)
TITLE_PATTERNS: list[tuple[re.Pattern, str | None, str | None]] = [
    # Explicit roles
    (re.compile(r"(?i)\bintern\b"), INTERN, None),
    (re.compile(r"(?i)\bback\s*end\b.*\bengineer\b"), BACKEND_ENGINEER, None),
    (re.compile(r"(?i)\bbackend\b.*\bdeveloper\b"), BACKEND_ENGINEER, None),
    (re.compile(r"(?i)\bfront\s*end\b.*\bengineer\b"), FRONTEND_ENGINEER, None),
    (re.compile(r"(?i)\bfrontend\b.*\bdeveloper\b"), FRONTEND_ENGINEER, None),
    (re.compile(r"(?i)\bfull\s*stack\b"), FULLSTACK_ENGINEER, None),
    (re.compile(r"(?i)\bfullstack\b"), FULLSTACK_ENGINEER, None),
    (re.compile(r"(?i)\bdevops\b"), DEVOPS_ENGINEER, None),
    (re.compile(r"(?i)\bsite\s*reliability\b"), SRE, None),
    (re.compile(r"(?i)\bsre\b"), SRE, None),
    (re.compile(r"(?i)\bdata\s*engineer\b"), DATA_ENGINEER, None),
    (re.compile(r"(?i)\bmachine\s*learning\b"), ML_ENGINEER, None),
    (re.compile(r"(?i)\bml\s*engineer\b"), ML_ENGINEER, None),
    (re.compile(r"(?i)\bai\s*engineer\b"), ML_ENGINEER, None),
    (re.compile(r"(?i)\bmobile\b.*\b(?:engineer|developer)\b"), MOBILE_ENGINEER, None),
    (re.compile(r"(?i)\bandroid\b.*\b(?:engineer|developer)\b"), MOBILE_ENGINEER, None),
    (re.compile(r"(?i)\bios\b.*\b(?:engineer|developer)\b"), MOBILE_ENGINEER, None),
    (re.compile(r"(?i)\bqa\b.*\bengineer\b"), QA_ENGINEER, None),
    (re.compile(r"(?i)\btest\b.*\bengineer\b"), QA_ENGINEER, None),
    (re.compile(r"(?i)\bsecurity\b.*\bengineer\b"), SECURITY_ENGINEER, None),
    (re.compile(r"(?i)\bplatform\b.*\bengineer\b"), PLATFORM_ENGINEER, None),
    (re.compile(r"(?i)\bplatform\b.*\bdeveloper\b"), PLATFORM_ENGINEER, None),
    # Manager / non-tech
    (re.compile(r"(?i)\bmanager\b|director|head\s*of|vp\b|chief\b"), MANAGER, None),
    # Designer roles — not relevant for SWE searches
    (re.compile(r"(?i)\bdesigner\b"), NON_TECH, None),
    (re.compile(r"(?i)\bmarketing\b|sales\b|hr\b|finance\b|legal\b|operations\b|hr\b|recruiter\b"), NON_TECH, None),
    # Catch-all engineer
    (re.compile(r"(?i)\bsoftware\s*engineer\b"), None, SOFTWARE_ENGINEER),
    (re.compile(r"(?i)\bengineer\b"), None, SOFTWARE_ENGINEER),
    (re.compile(r"(?i)\bdeveloper\b"), None, SOFTWARE_ENGINEER),
    (re.compile(r"(?i)\bsde\b"), None, SOFTWARE_ENGINEER),
    (re.compile(r"(?i)\bswe\b"), None, SOFTWARE_ENGINEER),
    # Catch-all tech
    (re.compile(r"(?i)\bprogrammer\b|coder\b|technician\b"), None, SOFTWARE_ENGINEER),
]

# Skills that indicate backend context
BACKEND_SKILLS = {"java", "spring", "spring boot", "kafka", "hibernate", "microservices"}
FRONTEND_SKILLS = {"react", "angular", "vue", "css", "html", "tailwind", "frontend"}


class NormalizedTitle(NamedTuple):
    canonical: str
    label: str
    confidence: float  # 0-1
    description: str = ""


def normalize(title: str, description: str = "", skills: list[str] | None = None) -> NormalizedTitle:
    """Map a raw job title to a canonical role type.

    Uses title patterns first, then falls back to description/skills for ambiguous titles.
    """
    title_lower = title.lower()
    desc_lower = description.lower()
    skill_set = set(s.lower() for s in (skills or []))

    for pattern, type_on_match, type_on_mismatch in TITLE_PATTERNS:
        if pattern.search(title_lower):
            if type_on_match is not None:
                return NormalizedTitle(
                    canonical=type_on_match,
                    label=ROLE_LABELS.get(type_on_match, title),
                    confidence=0.95,
                    description=f"Title matched '{pattern.pattern}'",
                )
            # Ambiguous match (e.g. "Software Engineer" — needs context)
            inferred = _infer_from_context(desc_lower, skill_set)
            if type_on_mismatch:
                resolved = type_on_mismatch
                # Check if description/skills suggest backend vs frontend
                if inferred:
                    resolved = inferred
                return NormalizedTitle(
                    canonical=resolved,
                    label=ROLE_LABELS.get(resolved, title),
                    confidence=0.80,
                    description=f"Title + context resolved to {resolved}",
                )
            break

    # No title pattern matched — try description only
    inferred = _infer_from_context(desc_lower, skill_set)
    if inferred:
        return NormalizedTitle(
            canonical=inferred,
            label=ROLE_LABELS.get(inferred, title),
            confidence=0.65,
            description="Inferred from description/skills only",
        )

    return NormalizedTitle(
        canonical=UNKNOWN,
        label=title,
        confidence=0.3,
        description="Could not determine role type",
    )


def _infer_from_context(desc: str, skills: set[str]) -> str | None:
    """Infer role type from description text and skill tags."""
    # Strong backend indicators
    if any(s in desc for s in ["backend", "back end", "server side", "server-side", "microservice"]):
        return BACKEND_ENGINEER
    if skills & BACKEND_SKILLS:
        return BACKEND_ENGINEER
    if any(p in desc for p in ["spring boot", "hibernate", "jpa", "jdbc", "servlet"]):
        return BACKEND_ENGINEER
    if "api" in desc and any(s in desc for s in ["rest", "graphql", "endpoint", "service", "http"]):
        return BACKEND_ENGINEER

    # Strong frontend indicators
    if any(s in desc for s in ["frontend", "front end", "client side", "ui", "user interface"]):
        return FRONTEND_ENGINEER
    if skills & FRONTEND_SKILLS:
        return FRONTEND_ENGINEER

    # DevOps indicators
    if any(s in desc for s in ["devops", "ci/cd", "deployment", "infrastructure", "cloud infrastructure"]):
        return DEVOPS_ENGINEER
    if skills & {"docker", "kubernetes", "terraform", "jenkins", "aws", "gcp", "azure"}:
        return DEVOPS_ENGINEER

    # Data/ML indicators
    if any(s in desc for s in ["data pipeline", "etl", "data warehouse", "big data"]):
        return DATA_ENGINEER
    if skills & {"tensorflow", "pytorch", "langchain", "nlp"}:
        return ML_ENGINEER

    return None


def seniority_level(title: str) -> str:
    """Extract seniority level from title."""
    t = title.lower()
    if any(w in t for w in ["intern", "trainee", "apprentice"]):
        return "intern"
    if any(w in t for w in ["junior", "jr", "fresher", "entry", "graduate", "new grad", "campus"]):
        return "junior"
    if any(w in t for w in ["senior", "sr", "staff", "principal", "lead", "architect"]):
        return "senior"
    if any(w in t for w in ["manager", "director", "head", "vp", "chief"]):
        return "manager"
    if any(w in t for w in ["mid", "intermediate", "level ii", "level 2", "sde ii", "sde 2"]):
        return "mid"
    return "mid"


SENIORITY_MULTIPLIER: dict[str, float] = {
    "intern": 1.0,
    "junior": 1.0,
    "mid": 0.9,
    "senior": 0.3,
    "manager": 0.1,
}
