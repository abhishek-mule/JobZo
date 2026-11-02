import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.trust import (
    TIER_SUSPICIOUS,
    TIER_TRUSTED,
    TIER_UNVERIFIED,
    TIER_VERIFIED,
    TrustEvidence,
    calculate_trust,
    collect_evidence,
)


def _evidence(**overrides) -> TrustEvidence:
    base = dict(
        company_name="Pune Tech",
        company_in_registry=False,
        company_status="",
        company_quality="",
        company_verified=False,
        source="rss",
        url_domain="punetech.com",
        ats_verified=False,
        free_domain=False,
        company_domain_match=None,
        name_domain_consistent=True,
        title="Software Engineer",
        description="We are hiring a software engineer with 800+ characters of "
                    "genuine job description, responsibilities, requirements and "
                    "qualifications plus more detail to make the description long "
                    "enough to be scored as a detailed posting for trust reasons "
                    "which is the point of this test fixture right here.",
        location="Pune, India",
        salary="8 LPA",
        skills_count=5,
        scam_flags=[],
    )
    base.update(overrides)
    return TrustEvidence(**base)


def test_critical_scam_flag_is_suspicious():
    """Any critical keyword (candidate money out) → suspicious regardless of score."""
    from database.models import Job
    job = Job(
        company="FakeJobs",
        title="Software Engineer",
        description="Great opportunity! Pay a registration fee of Rs 500 to apply.",
        url="https://fakejobs.example.com/apply",
        source="rss",
    )
    ev = collect_evidence(job)
    assert any(f["level"] == "critical" for f in ev.scam_flags)
    result = calculate_trust(ev)
    assert result.tier == TIER_SUSPICIOUS, f"expected suspicious, got {result.tier}"
    assert result.score <= 40


def test_two_strong_flags_is_suspicious():
    """whatsapp-only + no interview = process smells → suspicious."""
    from database.models import Job
    job = Job(
        company="FastCash",
        title="Easy Money Job",
        description="Apply on WhatsApp only. No interview needed.",
        url="https://fastcash.example.com/jobs",
        source="rss",
    )
    result = calculate_trust(collect_evidence(job))
    assert result.tier == TIER_SUSPICIOUS
    assert len(result.risks) >= 2


def test_single_weak_flag_is_not_suspicious():
    """One weak keyword (e.g. data entry) must NOT get a company blocked."""
    from database.models import Job
    job = Job(
        company="LegitData",
        title="Data Entry Associate",
        description="Data entry role at a real company. 0-1 years experience welcome.",
        url="https://legitdata.example.com/careers/apply",
        source="company_pages",
        location="Bangalore",
    )
    result = calculate_trust(collect_evidence(job))
    assert result.tier != TIER_SUSPICIOUS
    assert result.tier in (TIER_VERIFIED, TIER_TRUSTED, TIER_UNVERIFIED)


def test_free_mail_domain_is_suspicious():
    """Job hosted on a gmail/telegram/shortener domain is a red flag."""
    from database.models import Job
    job = Job(
        company="FakeTech",
        title="Work From Home",
        description="Anyone can apply. Easy money, part time.",
        url="https://gmail.com/jobs/xyz",
        source="rss",
    )
    result = calculate_trust(collect_evidence(job))
    assert result.tier == TIER_SUSPICIOUS


def test_free_domain_alone_is_suspicious():
    """Even a perfectly worded posting on a free domain is blocked."""
    ev = _evidence(
        url_domain="gmail.com",
        free_domain=True,
        name_domain_consistent=False,
        company_domain_match=None,
    )
    result = calculate_trust(ev)
    assert result.tier == TIER_SUSPICIOUS
    assert result.score <= 40


def test_unknown_local_startup_is_unverified_not_suspicious():
    """The critical case: local startup not in registry → unverified, still diggable."""
    ev = _evidence(company_in_registry=False, ats_verified=False,
                   company_domain_match=None, name_domain_consistent=True)
    result = calculate_trust(ev)
    assert result.tier == TIER_UNVERIFIED, f"expected unverified, got {result.tier}"
    assert result.tier != TIER_SUSPICIOUS


def test_verified_employer_plus_official_domain_is_verified():
    ev = _evidence(
        company_in_registry=True,
        company_status="active",
        company_quality="verified",
        company_verified=True,
        source="company_pages",
        company_domain_match=True,
        ats_verified=False,
        name_domain_consistent=False,
    )
    result = calculate_trust(ev)
    assert result.tier == TIER_VERIFIED
    assert result.score >= 80


def test_ats_board_is_verified():
    ev = _evidence(
        company_in_registry=True,
        company_status="active",
        company_quality="verified",
        company_verified=True,
        source="company_pages",
        ats_verified=True,
        company_domain_match=False,
        name_domain_consistent=False,
    )
    result = calculate_trust(ev)
    assert result.tier == TIER_VERIFIED


def test_high_score_unknown_employer_stays_unverified():
    """Even a polished posting cannot push an unverified employer to 'trusted'."""
    ev = _evidence(company_in_registry=False, company_domain_match=True,
                   ats_verified=False)
    result = calculate_trust(ev)
    assert result.score >= 60
    assert result.tier == TIER_UNVERIFIED
    assert result.tier != TIER_SUSPICIOUS


def test_known_employer_high_score_is_trusted():
    """A registry-known employer (community/manual quality) with solid evidence → trusted."""
    ev = _evidence(
        company_in_registry=True,
        company_status="active",
        company_quality="community",
        company_verified=False,
        source="company_pages",
        company_domain_match=True,
        ats_verified=False,
        name_domain_consistent=False,
    )
    result = calculate_trust(ev)
    assert result.score >= 60
    assert result.tier == TIER_TRUSTED


def test_name_domain_consistency_detected():
    from database.models import Job
    job = Job(
        company="PuneTech",
        title="Backend Developer",
        description="Backend developer role at PuneTech. Strong written description "
                    "with enough content to be plausible, plus responsibilities and "
                    "requirements sections typical of a real job posting here.",
        url="https://punetech.com/careers/backend",
        source="rss",
        location="Pune",
        skills=["Java", "Spring Boot"],
    )
    ev = collect_evidence(job)
    assert ev.name_domain_consistent is True
    result = calculate_trust(ev)
    assert result.tier != TIER_SUSPICIOUS


def test_ats_url_detected():
    from database.models import Job
    job = Job(
        company="Acme",
        title="Engineer",
        description="A real job description with plenty of detail about the role.",
        url="https://boards.greenhouse.io/acme/jobs/123",
        source="company_pages",
    )
    ev = collect_evidence(job)
    assert ev.ats_verified is True


def test_why_not_100_is_explanatory():
    ev = _evidence(company_in_registry=False, salary="", source="rss")
    result = calculate_trust(ev)
    assert result.why_not_100, "expected 'why not 100' gaps to be non-empty"
    assert any("verified" in g.lower() for g in result.why_not_100)


def test_score_always_in_range():
    for _ in range(50):
        result = calculate_trust(_evidence())
        assert 0.0 <= result.score <= 100.0


if __name__ == "__main__":
    test_critical_scam_flag_is_suspicious()
    test_two_strong_flags_is_suspicious()
    test_single_weak_flag_is_not_suspicious()
    test_free_mail_domain_is_suspicious()
    test_free_domain_alone_is_suspicious()
    test_unknown_local_startup_is_unverified_not_suspicious()
    test_verified_employer_plus_official_domain_is_verified()
    test_ats_board_is_verified()
    test_high_score_unknown_employer_stays_unverified()
    test_known_employer_high_score_is_trusted()
    test_name_domain_consistency_detected()
    test_ats_url_detected()
    test_why_not_100_is_explanatory()
    test_score_always_in_range()
    print("\nAll trust tests passed!")