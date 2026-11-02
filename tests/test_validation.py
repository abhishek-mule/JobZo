import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ats.validation import (
    has_role_info,
    is_job_like_url,
    is_job_title,
    validate_candidate,
    validate_candidates,
)


# ── URL gate ──────────────────────────────────────────────────────────────

def test_job_like_url_accepts_at_board_urls():
    ok = [
        "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4716521005",
        "https://boards.greenhouse.io/razorpay/jobs/123456",
        "https://jobs.lever.co/cred/09022710-e79a-4cf0-8060-df0e1cd64",
        "https://jobs.smartrecruiters.com/freshworks/744000144161379",
        "https://jobs.ashbyhq.com/notion/297b4ece-765f-4eea-b1b8-4605",
        "https://visa.wd5.myworkdayjobs.com/job/SG---Singapore/Technical-Program-Manager",
        "https://razorpay.com/jobs/12345",
        "https://example.com/careers/backend-engineer",
    ]
    for url in ok:
        assert is_job_like_url(url), f"should accept job URL: {url}"


def test_job_like_url_rejects_nav_and_junk():
    bad = [
        "https://razorpay.com/accept-international-payments/",
        "https://stripe.dev/",
        "https://example.com/about",
        "https://example.com/products",
        "https://example.com/blog",
        "https://nvidia.com/en-us/networking/products/data-processing",
        "",
        None,
    ]
    for url in bad:
        assert not is_job_like_url(url), f"should reject non-job URL: {url}"


# ── Title gate ─────────────────────────────────────────────────────────────

def test_job_title_accepts_real_titles():
    ok = [
        "Software Engineer, Early Career",
        "Business Development & Partnerships",
        "Full Stack Builder",
        "Associate Technical Program Analyst",
        "Intern - Data Science",
    ]
    for t in ok:
        assert is_job_title(t), f"should accept title: {t}"


def test_job_title_rejects_junk():
    bad = [
        "International Payments",
        "Developer blog",
        "Simplify SaaS billing",
        "B2B SaaS & Software",
        "Read more",
        "Careers",
        "View all",
    ]
    for t in bad:
        assert not is_job_title(t), f"should reject junk title: {t}"


def test_job_title_rejects_overlong():
    assert not is_job_title("x" * 70)


# ── Role info ──────────────────────────────────────────────────────────────

def test_has_role_info_short_desc_needs_role_title():
    assert has_role_info("", "Backend Engineer")
    assert not has_role_info("", "Finance Team")


# ── Full candidate gate ────────────────────────────────────────────────────

def test_validate_candidate_accepts_api_job():
    job = {"title": "Software Engineer", "url": "https://jobs.lever.co/cred/abc"}
    ok, reasons = validate_candidate(job, source_kind="api")
    assert ok, reasons


def test_validate_candidate_rejects_html_junk():
    job = {
        "title": "International Payments",
        "url": "https://razorpay.com/accept-international-payments/",
        "description": "",
    }
    ok, reasons = validate_candidate(job, source_kind="html")
    assert not ok
    assert reasons


def test_validate_candidates_returns_valid_and_rejected():
    jobs = [
        {"title": "Backend Engineer", "url": "https://example.com/careers/backend", "description": ""},
        {"title": "Developer blog", "url": "https://stripe.dev/", "description": ""},
    ]
    valid, rejected = validate_candidates(jobs, source_kind="html")
    assert len(valid) == 1
    assert len(rejected) == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))