import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ats.apis import (
    _guess_workday_cxs,
    _iso_to_dt,
    _ms_to_dt,
    API_ADAPTERS,
    fetch_api_jobs,
    greenhouse_jobs,
    lever_jobs,
    smartrecruiters_jobs,
)


def test_iso_to_dt_handles_z():
    dt = _iso_to_dt("2026-08-13T06:32:26Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_iso_to_dt_none():
    assert _iso_to_dt("") is None
    assert _iso_to_dt(None) is None
    assert _iso_to_dt("not-a-date") is None


def test_ms_to_dt():
    dt = _ms_to_dt(1_700_000_000_000)
    assert dt is not None
    assert dt.tzinfo is not None


def test_ms_to_dt_invalid():
    assert _ms_to_dt("x") is None
    assert _ms_to_dt(None) is None


def test_api_adapters_registered():
    for ats in ("greenhouse", "lever", "smartrecruiters", "workday"):
        assert ats in API_ADAPTERS


def test_fetch_api_jobs_unknown_ats_returns_empty():
    import asyncio
    result = asyncio.run(fetch_api_jobs("not_an_ats", "token", "Company"))
    assert result == []


def test_guess_workday_cxs_bad_tenant_returns_empty():
    # "zzzz-non-existent-tenant" won't resolve to a working CXS — expect ""
    import asyncio
    result = asyncio.run(_guess_workday_cxs("zzzznonexistenttenant12345"))
    assert result == ""


def test_adapters_accept_api_url_kwarg():
    # The public signature must tolerate the third api_url param
    import inspect
    for fn in (greenhouse_jobs, lever_jobs, smartrecruiters_jobs):
        sig = inspect.signature(fn)
        assert "api_url" in sig.parameters


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))