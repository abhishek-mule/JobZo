"""JobCandidate validation — the hard gate that keeps junk out of the DB.

A link on a careers page is NOT a job until it survives this gate. Bad data
at 1,000 companies is worse than good data at 100.

  Candidate URL ─► job-like URL? ─NO─► reject
                      │YES
                      ▼
                 title looks like a job? ─NO─► reject
                      │YES
                      ▼
                 description has role info? ─NO─► reject
                      │YES
                      ▼
                    Create Job

Structured API sources (Greenhouse/Lever/SmartRecruiters JSON) are trusted:
they only require non-empty title + URL. HTML-scraped candidates must pass
the strict gate, because HTML scrapers grab nav links as "jobs".
"""

import re

# ── Job-like URL patterns ────────────────────────────────────────────────────
# These are the shapes real job posting URLs take.
JOB_URL_PATTERNS = [
    r"/jobs?/\d+",                    # /jobs/12345
    r"/jobs?/[a-z0-9-]+",             # /job/backend-engineer
    r"/job-(?:openings?|postings?|listings?)",
    r"/positions?/",
    r"/careers?/",                    # /career/... or /careers/
    r"/career-pages/",
    r"/requisition",
    r"/apply(?:/|$|\?)",
    r"\?gh_jid=",                     # Greenhouse single-job link
    r"\?lever-origin=",               # Lever single-job link
    r"/job-posting",
    r"/open-position",
    r"/jobAdId=",                     # Workday-style
    r"/recruiting/",
    r"/apply/",                       # iCIMS-style
    # Legit ATS board posting URLs (job + company slug + id)
    r"jobs\.lever\.co/[a-z0-9-]+/",
    r"jobs\.smartrecruiters\.com/[a-z0-9-]+/",
    r"jobs\.ashbyhq\.com/[a-z0-9-]+/",
    r"(?:job-)?boards\.(?:eu\.)?greenhouse\.io/",
    r"myworkdayjobs\.com/job/",
]

# Links that are almost never a job posting.
NAV_URL_PATTERNS = [
    r"/about", r"/login", r"/sign\b", r"/blog", r"/team", r"/press",
    r"/contact", r"/culture", r"/benefits", r"/faq", r"/terms", r"/privacy",
    r"/products?", r"/services?", r"/solutions?", r"/pricing",
]

# Link texts that are page/nav labels, not job titles.
NAV_TITLE_PATTERNS = [
    r"^read\s+more", r"^learn\s+more", r"^view\s+all", r"^see\s+all",
    r"^apply\s+now$", r"^careers?$", r"^jobs?$", r"^open\s+positions?$",
    r"^our\s+(?:products?|services?|solutions?|story|culture)$",
    r"^life\s+at\s+", r"^meet\s+the\s+", r"^join\s+our\s+",
    r"^why\s+", r"^work\s+with\s+us$", r"^perks?\s*$", r"^benefits\s*$",
]

# Product/landing-style titles that slipped in before (e.g. "International
# Payments", "Developer blog", "Simplify SaaS billing").
JUNK_TITLE_PATTERNS = [
    r"(?:product|developer|engineering)\s*(?:page|blog|hub|home|overview)$",
    r"^simplif", r"^automate\s+", r"^b2b\s+", r"^seats,\s*tiers",
    r"^-{0,2}\s*(?:international|global)\s+payments?$",
    r"^.{60,}",                       # over-long "titles" are usually page text
]

MIN_TITLE_LEN = 4
MAX_TITLE_LEN = 120


def is_job_like_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    if any(re.search(p, low) for p in NAV_URL_PATTERNS):
        return False
    return any(re.search(p, low) for p in JOB_URL_PATTERNS)


def is_job_title(title: str) -> bool:
    t = (title or "").strip()
    if not (MIN_TITLE_LEN <= len(t) <= MAX_TITLE_LEN):
        return False
    low = t.lower()
    if any(re.search(p, low) for p in NAV_TITLE_PATTERNS):
        return False
    if any(re.search(p, low) for p in JUNK_TITLE_PATTERNS):
        return False
    if not re.search(r"[a-z]", low):
        return False
    return True


def has_role_info(description: str, title: str = "") -> bool:
    desc = (description or "").strip()
    if len(desc) >= 40:
        return True
    # Short descriptions are acceptable only if the title itself carries role
    # signals (e.g. a full ATS listing that collapsed to just a heading).
    return bool(title) and bool(re.search(
        r"engineer|developer|analyst|intern|associate|trainee|specialist|"
        r"consultant|architect|designer|manager|scientist|lead|executive|"
        r"software|sde|full.?stack|backend|frontend|devops|data\b",
        title, re.I,
    ))


def validate_candidate(job: dict, *, source_kind: str = "html") -> tuple[bool, list[str]]:
    """Validate one candidate job dict before it becomes a Job row.

    source_kind:
      "api"  → structured JSON from a known ATS board (lenient gate)
      "html" → scraped from rendered HTML (strict gate)
    Returns (valid, reasons). Reasons are non-empty when rejected.
    """
    title = (job.get("title") or "").strip()
    url = (job.get("url") or "").strip()
    description = job.get("description") or ""

    if not title:
        return False, ["missing title"]
    if not url:
        return False, ["missing URL"]

    if source_kind == "api":
        if not is_job_title(title):
            return False, [f"title not job-like: {title[:60]!r}"]
        return True, []

    # ── Strict HTML gate ────────────────────────────────────────────────
    if not is_job_like_url(url):
        return False, [f"URL is not job-like: {url[:80]}"]
    if not is_job_title(title):
        return False, [f"title not job-like: {title[:60]!r}"]
    if not has_role_info(description, title):
        return False, ["description lacks role information"]
    return True, []


def validate_candidates(jobs: list[dict], *, source_kind: str = "html") -> tuple[list[dict], list[str]]:
    """Validate a batch. Returns (accepted, rejection_reasons)."""
    accepted: list[dict] = []
    rejected: list[str] = []
    for job in jobs:
        ok, reasons = validate_candidate(job, source_kind=source_kind)
        if ok:
            accepted.append(job)
        else:
            rejected.append(f"{job.get('title', '?')[:40]} — {'; '.join(reasons)}")
    return accepted, rejected