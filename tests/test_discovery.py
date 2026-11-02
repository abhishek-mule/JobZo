import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ats.discovery import (
    discover,
    discover_from_html,
    discover_from_url,
    registry_hint,
    load_discovery,
)


def test_url_rule_greenhouse():
    d = discover_from_url("https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs")
    assert d is not None
    assert d.ats == "greenhouse"
    assert d.board_token == "razorpaysoftwareprivatelimited"


def test_url_rule_workday():
    d = discover_from_url("https://visa.wd5.myworkdayjobs.com/en-US/home")
    assert d is not None
    assert d.ats == "workday"
    assert d.board_token == "visa"


def test_html_marker_lever():
    html = '<html><body><a href="https://jobs.lever.co/cred/123">jobs</a></body></html>' * 3
    d = discover_from_html(html)
    assert d is not None
    assert d.ats == "lever"
    assert d.board_token == "cred"


def test_html_marker_workday_cxs_builds_api_url():
    html = '<a href="https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/visa/jobs">apply</a>' * 3
    d = discover_from_html(html)
    assert d is not None
    assert d.ats == "workday"
    assert d.api_url == "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/visa/jobs"
    assert d.has_api


def test_html_marker_greenhouse():
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=phonepe"></script>' * 3
    d = discover_from_html(html)
    assert d is not None
    assert d.ats == "greenhouse"
    assert d.board_token == "phonepe"


def test_registry_hint():
    d = registry_hint("greenhouse", "Razorpay")
    assert d is not None
    assert d.ats == "greenhouse"
    assert d.board_token == "razorpay"
    assert d.method == "registry_hint"


def test_registry_hint_ignores_generic():
    assert registry_hint("generic", "Razorpay") is None
    assert registry_hint("", "Razorpay") is None


def test_discover_full_cascade():
    d = discover("https://razorpay.com/careers", "Razorpay",
                 html='<a href="https://boards.greenhouse.io/razorpay/jobs">x</a>' * 3,
                 ats_hint="greenhouse")
    assert d.ats == "greenhouse"
    assert d.board_token == "razorpay"


def test_discover_falls_back_to_unknown():
    d = discover("https://example.com/", "Example")
    assert d.ats == "unknown"
    assert not d.has_api


def test_load_discovery_missing_returns_none():
    assert load_discovery("https://this-url-was-never-discovered.example/") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))