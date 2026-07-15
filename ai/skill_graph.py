"""Skill relationship graph — expand, analyze, and score skill sets.

Uses config/skill_graph.yaml to define weighted relationships between skills.
The graph supports:
  - Expansion: given a skill, find related skills (parents, complements)
  - Gap analysis: find missing prerequisite skills
  - Similarity: compute skill set overlap with relationship awareness
"""

from pathlib import Path
from typing import Any
import yaml

from skills import canonical_name, resolve_skill

GRAPH_PATH = Path(__file__).parent.parent / "config" / "skill_graph.yaml"

_graph: dict[str, dict] | None = None
_parent_cache: dict[str, list[tuple[str, float]]] = {}
_child_cache: dict[str, list[tuple[str, float]]] = {}
_complement_cache: dict[str, list[tuple[str, float]]] = {}


def _load_graph() -> dict[str, dict]:
    global _graph
    if _graph is not None:
        return _graph
    if not GRAPH_PATH.exists():
        _graph = {}
        return _graph
    with open(GRAPH_PATH) as f:
        data = yaml.safe_load(f)
    _graph = {k: v for k, v in data.items()}
    return _graph


def _canonical_key(skill: str) -> str:
    """Get canonical name for graph lookup."""
    cn = canonical_name(skill)
    return cn


def parents(skill: str) -> list[tuple[str, float]]:
    """Return list of (parent_skill, strength) for a given skill."""
    sk = _canonical_key(skill)
    if sk in _parent_cache:
        return _parent_cache[sk]
    g = _load_graph()
    entry = g.get(sk, {})
    result = [(name, strength) for name, strength in entry.get("parents", {}).items()]
    _parent_cache[sk] = result
    return result


def complements(skill: str) -> list[tuple[str, float]]:
    """Return list of (complement_skill, strength) for a given skill."""
    sk = _canonical_key(skill)
    if sk in _complement_cache:
        return _complement_cache[sk]
    g = _load_graph()
    entry = g.get(sk, {})
    result = [(name, strength) for name, strength in entry.get("complements", {}).items()]
    _complement_cache[sk] = result
    return result


def all_related(skill: str, min_strength: float = 0.0) -> list[tuple[str, float]]:
    """Return all related skills (parents + complements) with their strengths.

    Filters by minimum strength threshold.
    """
    p = parents(skill)
    c = complements(skill)
    combined: dict[str, float] = {}
    for name, strength in p + c:
        if strength >= min_strength:
            combined[name] = max(combined.get(name, 0.0), strength)
    return sorted(combined.items(), key=lambda x: -x[1])


def expand(skills: list[str], max_depth: int = 2, min_strength: float = 0.3) -> dict[str, float]:
    """Expand a set of skills to include related skills with decay weights.

    Args:
        skills: The known skills.
        max_depth: How many levels of relationships to follow (1 = immediate only).
        min_strength: Minimum edge strength to follow.

    Returns:
        dict of {skill: aggregate_weight} where weight incorporates decay.
    """
    result: dict[str, float] = {}
    seen: set[str] = set()

    def _dfs(current: str, depth: int, decay: float):
        key = current.lower()
        if key in seen or depth > max_depth:
            return
        seen.add(key)
        for related, strength in all_related(current, min_strength):
            weight = decay * strength
            if weight > result.get(related, 0.0):
                result[related] = weight
            _dfs(related, depth + 1, weight)

    for skill in skills:
        cn = _canonical_key(skill)
        result[cn] = 1.0  # known skills get full weight
        _dfs(cn, 1, 0.75)  # first-level decay = 0.75

    return result


def skill_similarity(skills_a: list[str], skills_b: list[str], min_strength: float = 0.3) -> float:
    """Compute similarity between two skill sets using the relationship graph.

    Returns a score 0-1 representing how related the two sets are.
    A match via the graph counts partially (strength-weighted) vs. exact match.
    """
    expanded_a = expand(skills_a, max_depth=1, min_strength=min_strength)
    expanded_b = expand(skills_b, max_depth=1, min_strength=min_strength)

    if not expanded_a or not expanded_b:
        return 0.0

    set_b_names = set(expanded_b.keys())
    total_a = 0.0
    matched = 0.0
    for skill_a, weight_a in expanded_a.items():
        total_a += weight_a
        if skill_a in set_b_names:
            # Exact match: full weight
            matched += weight_a
        else:
            # Check relationship: if skill_a is a parent/complement of anything in B
            for skill_b, weight_b in expanded_b.items():
                relations = dict(all_related(skill_b, min_strength))
                if skill_a in relations:
                    matched += weight_a * relations[skill_a]
                    break

    return min(matched / max(total_a, 1.0), 1.0)


def missing_skills(resume_skills: list[str], job_skills: list[str], min_strength: float = 0.4) -> list[tuple[str, float, str]]:
    """Find skills the job requires but the resume lacks.

    Returns list of (skill_name, gap_score, reason) where gap_score is 0-1
    and reason is 'missing' (completely absent) or 'weak' (only distantly related).
    """
    expanded_resume = expand(resume_skills, max_depth=2, min_strength=min_strength)
    resume_names = set(expanded_resume.keys())

    gaps: list[tuple[str, float, str]] = []
    for js in job_skills:
        cn = _canonical_key(js)
        if cn in resume_names:
            continue  # skill or relative is known
        # Check if it's weakly related (distance > 2)
        gaps.append((cn, 1.0, "missing"))

    return gaps


def unlocked_companies(resume_skills: list[str], companies_db) -> list[tuple[str, float]]:
    """Estimate how many more companies become accessible by learning each missing skill.

    Args:
        resume_skills: Current skills.
        companies_db: A callable that returns companies with backend_stack.

    Returns:
        List of (skill_name, new_company_score) pairs.
    """
    from services.company_registry import get_by_technology
    current = set(resume_skills)
    results: list[tuple[str, float]] = []

    # Get a broad sample of relevant skills from the graph
    expanded = expand(resume_skills, max_depth=1, min_strength=0.3)
    candidate_skills = set(k for k, v in expanded.items() if v >= 0.5)

    for skill in sorted(candidate_skills - set(s.lower() for s in resume_skills)):
        companies_with = get_by_technology(skill)
        if companies_with:
            current_count = sum(
                1 for c in companies_with
                if any(s in (getattr(c, 'backend_stack', None) or []) for s in resume_skills)
            )
            new_count = len(companies_with) - current_count
            if new_count > 0:
                results.append((skill, new_count))

    return sorted(results, key=lambda x: -x[1])[:10]
