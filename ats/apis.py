"""Structured ATS API adapters.

These hit the public JSON endpoints that Greenhouse, Lever, and
SmartRecruiters expose for their job boards. Structured endpoints give us
exactly what JobZo needs (title, description, location, posted date, apply
URL) without scraping rendered HTML — and they are trusted input for the
validation gate (source_kind="api").

Endpoints (all public, no auth):
  Greenhouse       GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  Lever            GET https://api.lever.co/v0/postings/<company>?mode=json
  SmartRecruiters  GET https://api.smartrecruiters.com/v1/companies/<c>/postings
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("jobzo.ats.apis")

TIMEOUT = httpx.Timeout(20.0)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

LEVER_BASE = "https://api.lever.co/v0/postings/{company}"
SMARTRECRUITERS_BASE = "https://api.smartrecruiters.com/v1/companies/{company}/postings"


def _strip_html(html: str) -> str:
    if not html:
        return ""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)


# ── Greenhouse ───────────────────────────────────────────────────────────────

async def greenhouse_jobs(token: str, company_name: str = "", api_url: str = "") -> list[dict]:
    """Fetch from Greenhouse's documented public API.

    Uses boards-api.greenhouse.io/v1/boards/<token>/jobs, which works for
    standard boards (boards.greenhouse.io) AND APAC/EU boards hosted on
    job-boards.greenhouse.io (e.g. razorpaysoftwareprivatelimited).
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    async with _client() as client:
        try:
            resp = await client.get(url, params={"content": "true"})
        except httpx.HTTPError as e:
            logger.warning("Greenhouse API failed for %s: %s", token, e)
            return []
    if resp.status_code != 200:
        logger.warning("Greenhouse API %s → HTTP %s", token, resp.status_code)
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("Greenhouse API %s returned non-JSON", token)
        return []
    if not isinstance(payload, dict):
        return []

    jobs = []
    for j in payload.get("jobs") or []:
        title = (j.get("title") or "").strip()
        if not title:
            continue
        location = ""
        loc = j.get("location") or {}
        if isinstance(loc, dict):
            location = loc.get("name", "") or ""
        offices = []
        for dc in j.get("data_compliance") or []:
            if dc.get("type") == "office" and dc.get("name"):
                offices.append(dc["name"])
        if offices:
            location = f"{location} · {', '.join(offices)}".strip(" ·")
        internal_url = f"https://job-boards.greenhouse.io/{token}/jobs/{j.get('id', '')}"
        jobs.append({
            "company": company_name or token.replace("-", " ").title(),
            "title": title,
            "description": _strip_html(j.get("content") or ""),
            "location": location,
            "salary": "",
            "experience_required": "",
            "skills": [],
            "url": j.get("absolute_url") or internal_url,
            "posted_at": _iso_to_dt(j.get("updated_at")),
            "remote": "remote" in (location or "").lower(),
        })
    logger.info("Greenhouse %s: %d jobs via API", token, len(jobs))
    return jobs


# ── Lever ────────────────────────────────────────────────────────────────────

async def lever_jobs(company: str, company_name: str = "", api_url: str = "") -> list[dict]:
    url = LEVER_BASE.format(company=company)
    async with _client() as client:
        try:
            resp = await client.get(url, params={"mode": "json"})
        except httpx.HTTPError as e:
            logger.warning("Lever API failed for %s: %s", company, e)
            return []
    if resp.status_code != 200:
        logger.warning("Lever API %s → HTTP %s", company, resp.status_code)
        return []

    data = resp.json()
    if not isinstance(data, list):
        return []

    jobs = []
    for j in data:
        title = (j.get("text") or "").strip()
        if not title:
            continue
        cats = j.get("categories") or {}
        location = cats.get("location", "") or ""
        commitment = cats.get("commitment", "") or ""
        if commitment and commitment.lower() not in ("fulltime", "full-time"):
            location = f"{location} · {commitment}".strip(" ·")
        salary_range = j.get("salaryRange") or {}
        salary = ""
        if salary_range and salary_range.get("min") and salary_range.get("max"):
            salary = f"{salary_range['min']}-{salary_range['max']} {salary_range.get('currency', '')}".strip()

        jobs.append({
            "company": company_name or company.replace("-", " ").title(),
            "title": title,
            "description": j.get("descriptionPlain") or _strip_html(j.get("description") or ""),
            "location": location,
            "salary": salary,
            "experience_required": "",
            "skills": [],
            "url": j.get("hostedUrl") or f"https://jobs.lever.co/{company}",
            "posted_at": _ms_to_dt(j.get("createdAt")),
            "remote": "remote" in (location or "").lower(),
        })
    logger.info("Lever %s: %d jobs via API", company, len(jobs))
    return jobs


# ── SmartRecruiters ──────────────────────────────────────────────────────────

_SR_SENIOR_TITLE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|head|director|manager|vp|chief|architect)\b",
    re.I,
)


async def _sr_fetch_description(client: httpx.AsyncClient, company: str,
                                posting_id: str) -> str:
    """Per-posting detail call — SmartRecruiters' list API omits the JD body."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        d = resp.json()
        sections = (d.get("jobAd") or {}).get("sections") or {}
        desc = (sections.get("jobDescription") or {}).get("text") or ""
        return _strip_html(desc)
    except Exception as e:
        logger.debug("SmartRecruiters detail %s/%s failed: %s", company, posting_id, e)
        return ""


async def smartrecruiters_jobs(company: str, company_name: str = "", api_url: str = "") -> list[dict]:
    url = SMARTRECRUITERS_BASE.format(company=company)
    async with _client() as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            logger.warning("SmartRecruiters API failed for %s: %s", company, e)
            return []
    if resp.status_code != 200:
        logger.warning("SmartRecruiters API %s → HTTP %s", company, resp.status_code)
        return []

    data = resp.json()
    content = data.get("content") or []
    jobs = []
    # Descriptions only matter where the title is ambiguous (a "Senior Manager"
    # JD won't change the fresher verdict). Bounded to avoid N+1 on everything.
    detail_sem = asyncio.Semaphore(4)
    detail_cap = 40

    async def with_description(j, client):
        raw = j.get("_posting") or {}
        posting_id = raw.get("id", "")
        title = (raw.get("name") or "").strip()
        if not posting_id or _SR_SENIOR_TITLE.search(title) or detail_cap_reached():
            return j, ""
        async with detail_sem:
            return j, await _sr_fetch_description(client, company, posting_id)

    cap = {"used": 0}

    def detail_cap_reached() -> bool:
        if cap["used"] >= detail_cap:
            return True
        cap["used"] += 1
        return False

    for j in content:
        title = (j.get("name") or "").strip()
        if not title:
            continue
        loc = j.get("location") or {}
        city = (loc.get("city") or "")
        country = (loc.get("country") or "")
        location = ", ".join(x for x in [city, country] if x)
        remote_flag = bool(loc.get("remote")) or "remote" in (location or "").lower()

        jobs.append({
            "company": company_name or company.replace("-", " ").title(),
            "title": title,
            "description": "",
            "location": location,
            "salary": "",
            "experience_required": "",
            "skills": [],
            "url": f"https://jobs.smartrecruiters.com/{company}/{j.get('id', '')}",
            "posted_at": _iso_to_dt(j.get("releasedDate")),
            "remote": remote_flag,
            "_posting": j,
        })

    # Fill descriptions for junior-ambiguous postings (bounded, concurrent).
    async with _client() as client:
        tasks = [with_description(j, client) for j in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for j, res in zip(jobs, results):
        if isinstance(res, Exception):
            continue
        posting, desc = res
        if desc:
            j["description"] = desc
        j.pop("_posting", None)

    logger.info("SmartRecruiters %s: %d jobs via API", company, len(jobs))
    return jobs


# ── Workday (CXS API, best-effort) ───────────────────────────────────────────

async def _guess_workday_cxs(tenant: str) -> str:
    """Guess the CXS endpoint from the tenant name. Rarely works (Visa does),
    so this is a last-ditch best-effort before giving up on Workday."""
    for sub in (5, 3, 1):
        host = f"{tenant}.wd{sub}.myworkdayjobs.com"
        url = f"https://{host}/wday/cxs/{tenant}/{tenant}/jobs"
        try:
            async with _client() as client:
                r = await client.post(url, json={"limit": 1, "offset": 0, "appliedFacets": {}})
            if r.status_code == 200:
                return url
        except httpx.HTTPError:
            continue
    return ""

async def workday_jobs(board_token: str, company_name: str = "", api_url: str = "") -> list[dict]:
    """Fetch jobs from Workday's CXS JSON API.

    Workday tenants are host-specific (accenture.wd3.myworkdayjobs.com,
    visa.wd5.myworkdayjobs.com) and the API path carries a company slug that
    is NOT derivable from the tenant name. So we require the concrete
    api_url discovered from the tenant page (/wday/cxs/<tenant>/<company>/jobs);
    a guessed URL almost never works. Best-effort by design.
    """
    if not api_url:
        api_url = await _guess_workday_cxs(board_token)
    if not api_url:
        return []
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    async with _client() as client:
        try:
            resp = await client.post(api_url, json=body)
        except httpx.HTTPError as e:
            logger.warning("Workday API failed for %s: %s", board_token, e)
            return []
    if resp.status_code != 200:
        logger.warning("Workday API %s → HTTP %s", board_token, resp.status_code)
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("Workday API %s returned non-JSON", board_token)
        return []

    jobs = []
    import urllib.parse as _up
    host = _up.urlparse(api_url).netloc
    for p in payload.get("jobPostings") or []:
        title = (p.get("jobPostingTitle") or p.get("title") or "").strip()
        if not title:
            continue
        loc = p.get("locationsText") or p.get("location") or ""
        path = p.get("externalPath") or ""
        if path.startswith("/"):
            url = f"https://{host}{path}"
        else:
            url = path
        jobs.append({
            "company": company_name or board_token.replace("-", " ").title(),
            "title": title,
            "description": "",
            "location": str(loc),
            "salary": "",
            "experience_required": "",
            "skills": [],
            "url": url,
            "posted_at": _iso_to_dt(p.get("postedOn")),
            "remote": "remote" in str(loc).lower(),
        })
    logger.info("Workday %s: %d jobs via API", board_token, len(jobs))
    return jobs


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _ms_to_dt(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


API_ADAPTERS = {
    "greenhouse": greenhouse_jobs,
    "lever": lever_jobs,
    "smartrecruiters": smartrecruiters_jobs,
    "workday": workday_jobs,
}


async def fetch_api_jobs(ats: str, board_token: str, company_name: str = "",
                         api_url: str = "") -> list[dict]:
    """Fetch from a structured ATS API. Returns [] if ATS has no adapter or fails."""
    adapter = API_ADAPTERS.get((ats or "").lower())
    if adapter is None:
        return []
    try:
        return await adapter(board_token, company_name, api_url)
    except Exception as e:
        logger.warning("API adapter %s failed: %s", ats, e)
        return []