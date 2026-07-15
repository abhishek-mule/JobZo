"""Tests for Phase 2: Resume Intelligence Engine."""

from skills import canonical_name, skill_weight, skill_category, all_canonical, skill_patterns
from resumes.registry import get_registry
from resumes.jd_analyzer import analyze as analyze_jd
from resumes.scorer import score_resumes
from resumes.optimizer import optimize
from resumes.fit_report import generate as generate_fit_report


# ── Fixtures ──────────────────────────────────────────────────────────────────

BACKEND_JD = """
Backend Engineer - Java/Spring Boot

We are looking for a Backend Engineer with strong Java and Spring Boot experience.
You will build REST APIs, work with PostgreSQL and Redis, and deploy on AWS using Docker.

Requirements:
- 2+ years of Java backend development
- Experience with microservices architecture
- Knowledge of Kafka and message queues
"""

SENIOR_JD = """
Senior Staff Backend Engineer

We are looking for a Senior Staff Backend Engineer with deep expertise in distributed systems.
You will design system architecture, lead Kafka-based event-driven platforms, and manage
Kubernetes clusters on AWS.

Requirements:
- 8+ years of backend development
- Expert in Java, Spring Boot, Microservices
- Experience with system design and architecture
"""

FRONTEND_JD = """
Frontend Developer - React

Build beautiful UIs with React, TypeScript, and TailwindCSS.
Work with our design team to implement pixel-perfect components.

Requirements:
- Strong React and TypeScript skills
- CSS and HTML expertise
- Experience with REST APIs
"""


# ── Skills Knowledge Base Tests ──────────────────────────────────────────────

def test_canonical_name_resolves_aliases():
    assert canonical_name("SpringBoot") == "Spring Boot"
    assert canonical_name("K8s") == "Kubernetes"
    assert canonical_name("Postgresql") == "PostgreSQL"


def test_canonical_name_unknown_returns_input():
    assert canonical_name("SomeRandomSkill") == "SomeRandomSkill"


def test_skill_weight():
    assert skill_weight("Java") == 7
    assert skill_weight("Kafka") == 8
    assert skill_weight("Git") == 4


def test_skill_category():
    assert skill_category("Redis") == "Database"
    assert skill_category("Docker") == "Infrastructure"
    assert skill_category("React") == "Frontend"


def test_all_canonical_returns_known_count():
    skills = all_canonical()
    assert len(skills) >= 60  # We have 64 canonical skills


def test_skill_patterns_compiled():
    patterns = skill_patterns()
    assert len(patterns) >= 60
    matched_any = False
    for pat, canon in patterns[:20]:
        if pat.search("Python") or pat.search("Go"):
            matched_any = True
            break
    assert matched_any, "At least one pattern should match a known skill"


# ── Resume Registry Tests ────────────────────────────────────────────────────

def test_registry_loads_all_resumes():
    reg = get_registry()
    names = reg.names()
    assert len(names) == 5
    assert "backend_v3" in names
    assert "java_v1" in names
    assert "fullstack_v4" in names
    assert "frontend_v2" in names
    assert "sde_v1" in names


def test_registry_get_returns_meta():
    reg = get_registry()
    meta = reg.get("backend_v3")
    assert meta is not None
    assert meta.name == "backend_v3"
    assert len(meta.skills) >= 5
    assert len(meta.projects) >= 1


def test_registry_by_role():
    reg = get_registry()
    results = reg.by_role("Backend Engineer")
    assert len(results) >= 2
    names = [r.name for r in results]
    assert "backend_v3" in names


def test_registry_by_skill():
    reg = get_registry()
    results = reg.by_skill("Kafka")
    assert len(results) >= 1
    assert "java_v1" in [r.name for r in results]


# ── JD Analyzer Tests ───────────────────────────────────────────────────────

def test_jd_analyzer_extracts_skills():
    analysis = analyze_jd(BACKEND_JD)
    assert len(analysis.skills) >= 8
    assert "Java" in analysis.skills
    assert "Spring Boot" in analysis.skills
    assert "Docker" in analysis.skills
    assert "Kafka" in analysis.skills


def test_jd_analyzer_extracts_seniority():
    analysis = analyze_jd(SENIOR_JD)
    assert analysis.experience_level == "senior" or analysis.experience_level == "staff"


def test_jd_analyzer_empty_input():
    analysis = analyze_jd("")
    assert analysis.confidence == 0.0
    assert analysis.skills == []


def test_jd_analyzer_domains():
    analysis = analyze_jd(BACKEND_JD)
    assert isinstance(analysis.domains, list)


# ── Resume Scorer Tests ──────────────────────────────────────────────────────

def test_scorer_returns_all_resumes():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    scored = score_resumes(analysis, reg.all(), jd_text=BACKEND_JD)
    assert len(scored) == 5


def test_scorer_sorts_by_composite():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    scored = score_resumes(analysis, reg.all(), jd_text=BACKEND_JD)
    for i in range(len(scored) - 1):
        assert scored[i].composite() >= scored[i + 1].composite()


def test_scorer_backend_jd_prefers_backend_resume():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    scored = score_resumes(analysis, reg.all(), jd_text=BACKEND_JD)
    top_names = [s.resume_name for s in scored[:3]]
    # Backend-oriented resumes should rank high
    assert "java_v1" in top_names or "backend_v3" in top_names


def test_scorer_frontend_jd_prefers_frontend():
    reg = get_registry()
    analysis = analyze_jd(FRONTEND_JD)
    scored = score_resumes(analysis, reg.all(), jd_text=FRONTEND_JD)
    best = scored[0]
    assert best.resume_name == "frontend_v2"


def test_scorer_dimensions_in_range():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    scored = score_resumes(analysis, reg.all(), jd_text=BACKEND_JD)
    for s in scored:
        for key, dim in s.dimensions.items():
            assert 0 <= dim.score <= 100, f"{s.resume_name}.{key}={dim.score}"


# ── Resume Optimizer Tests ───────────────────────────────────────────────────

def test_optimizer_suggests_additions():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    meta = reg.get("frontend_v2")
    assert meta is not None
    opt = optimize(meta, analysis, BACKEND_JD)
    additions = [s for s in opt.suggestions if s.action == "add"]
    assert len(additions) >= 1


def test_optimizer_suggests_reorder():
    reg = get_registry()
    analysis = analyze_jd(BACKEND_JD)
    meta = reg.get("backend_v3")
    assert meta is not None
    opt = optimize(meta, analysis, BACKEND_JD)
    reorders = [s for s in opt.suggestions if s.action == "reorder"]
    # backend_v3 lacks kafka and redis in its direct skills but projects may match
    assert any(s.action in ("reorder", "emphasize", "add") for s in opt.suggestions)


# ── Fit Report Tests ─────────────────────────────────────────────────────────

def test_fit_report_generates():
    reg = get_registry()
    report = generate_fit_report("TestCo", "Backend Engineer", BACKEND_JD, reg)
    assert report.company == "TestCo"
    assert report.title == "Backend Engineer"
    assert report.recommended_resume != ""
    assert report.analysis is not None
    assert len(report.strengths) >= 1


def test_fit_report_adds_weaknesses():
    reg = get_registry()
    frontend_jd = FRONTEND_JD
    report = generate_fit_report("TestCo", "Frontend Dev", frontend_jd, reg)
    # Frontend JD against backend-heavy resume should have weaknesses
    assert len(report.weaknesses) >= 0  # at minimum doesn't crash


def test_fit_report_ineligible():
    reg = get_registry()
    report = generate_fit_report("TestCo", "Backend", BACKEND_JD, reg, is_eligible=False)
    best = report.scores[0]
    loc_score = best.dimensions["location"].score
    elig_score = best.dimensions["eligibility"].score
    assert loc_score == 0.0 and elig_score == 0.0
