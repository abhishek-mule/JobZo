"""ATS discovery — resolve "what ATS does this company use?" from a career URL.

The registry stores human-friendly career URLs (razorpay.com/careers), not
machine-friendly ATS endpoints. This module discovers the ATS + board token
so the collector can hit the right API/board.

  Company ─► Career URL ─► ATS Discovery ─► ATS Provider ─► real jobs
              │
              ├─ URL pattern match (fast, no fetch)
              ├─ HTML marker scan (careers page references its board)
              └─ registry `ats` hint as a fallback

The discovered endpoint is cached separately from the registry YAML (see
ATSDiscovery in database.models) so the YAML stays clean.
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("jobzo.ats.discovery")


@dataclass
class Discovery:
    ats: str = "unknown"
    board_token: str = ""
    api_url: str = ""
    method: str = "none"          # url_pattern | html_marker | registry_hint
    confidence: float = 0.0
    note: str = ""
    markers: list[str] = field(default_factory=list)

    @property
    def has_api(self) -> bool:
        # Workday is always worth an API attempt (adapter guesses the CXS
        # path if none was discovered); greenhouse/lever/smartrecruiters
        # need a concrete board token.
        return self.ats in ("greenhouse", "lever", "smartrecruiters", "workday")


# URL patterns: (regex, ats, token_group)
URL_RULES = [
    (r"(?:job-)?boards\.(?:eu\.)?greenhouse\.io/([^/?#]+)", "greenhouse", 1),
    (r"greenhouse\.io/([^/?#]+)/jobs", "greenhouse", 1),
    (r"jobs\.lever\.co/([^/?#]+)", "lever", 1),
    (r"api\.lever\.co/v0/postings/([^/?#]+)", "lever", 1),
    (r"jobs\.smartrecruiters\.com/([^/?#]+)", "smartrecruiters", 1),
    (r"api\.smartrecruiters\.com/v1/companies/([^/?#]+)", "smartrecruiters", 1),
    (r"smartrecruiters\.com/([^/?#]+)", "smartrecruiters", 1),
    (r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby", 1),
    (r"apply\.workable\.com/([^/?#]+)", "workable", 1),
    (r"jobs\.teamtailor\.com/([^/?#]+)", "teamtailor", 1),
    (r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", "workday", 1),
    (r"myworkdayjobs\.com/([^/?#]+)", "workday", 1),
    (r"oraclecloud\.com", "oracle_cloud", 0),
    (r"icims\.com/jobs", "icims", 0),
]

# HTML markers: (regex, ats, token_group)
# Workday CXS API path — the only reliable way to know tenant + company slug.
HTML_MARKERS = [
    (r"https?://([a-z0-9.-]+?\.myworkdayjobs\.com)/wday/cxs/([^/\"'\s?#]+)/([^/\"'\s?#]+)/jobs", "workday_cxs", 0),
    (r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/([A-Za-z0-9_-]+)(?:[/\"'\s?#]|$)", "workday_board", 0),
    (r"boards\.(?:eu\.)?greenhouse\.io/embed[^\"'\s]*[?&]for=([a-z0-9-]+)", "greenhouse", 1),
    (r"(?:job-)?boards\.(?:eu\.)?greenhouse\.io/([^/\"'\s?#]+)", "greenhouse", 1),
    (r"greenhouse\.io/([^/\"'\s?#]+)", "greenhouse", 1),
    (r"jobs\.lever\.co/([^/\"'\s?#]+)", "lever", 1),
    (r"api\.lever\.co/v0/postings/([^/\"'\s?#]+)", "lever", 1),
    (r"jobs\.smartrecruiters\.com/([^/\"'\s?#]+)", "smartrecruiters", 1),
    (r"api\.smartrecruiters\.com/v1/companies/([^/\"'\s?#]+)", "smartrecruiters", 1),
    (r"jobs\.ashbyhq\.com/([^/\"'\s?#]+)", "ashby", 1),
    (r"apply\.workable\.com/([^/\"'\s?#]+)", "workable", 1),
    (r"jobs\.teamtailor\.com/([^/\"'\s?#]+)", "teamtailor", 1),
    (r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", "workday", 1),
    (r"myworkdayjobs\.com/([^/\"'\s?#]+)", "workday", 1),
    (r"icims\.com/jobs", "icims", 0),
    (r"jobs\.personio\.", "personio", 0),
    (r"jobs\.bamboohr\.", "bamboohr", 0),
]

_TOKEN_CLEAN = re.compile(r"[^a-z0-9-]")


def _clean_token(tok: str) -> str:
    return _TOKEN_CLEAN.sub("", tok.lower()).strip("-") or ""


def discover_from_url(url: str) -> Discovery | None:
    if not url:
        return None
    for pattern, ats, group in URL_RULES:
        m = re.search(pattern, url, re.I)
        if m:
            token = _clean_token(m.group(group)) if group else ""
            return Discovery(ats=ats, board_token=token, method="url_pattern",
                             confidence=0.95 if ats != "workday" else 0.8)
    return None


def discover_from_html(html: str) -> Discovery | None:
    if not html or len(html) < 100:
        return None
    for pattern, ats, group in HTML_MARKERS:
        m = re.search(pattern, html, re.I)
        if m:
            if ats == "workday_cxs":
                host, tenant, company = m.group(1), m.group(2), m.group(3)
                api_url = f"https://{host}/wday/cxs/{tenant}/{company}/jobs"
                return Discovery(ats="workday", board_token=_clean_token(tenant),
                                 api_url=api_url, method="html_marker",
                                 confidence=0.9, note="workday cxs path")
            if ats == "workday_board":
                host, company = m.group(1), m.group(2)
                tenant = host.split(".")[0]
                api_url = f"https://{host}/wday/cxs/{tenant}/{company}/jobs"
                return Discovery(ats="workday", board_token=_clean_token(tenant),
                                 api_url=api_url, method="html_marker",
                                 confidence=0.9, note="workday board host")
            token = _clean_token(m.group(group)) if group else ""
            return Discovery(ats=ats, board_token=token, method="html_marker",
                             confidence=0.9)
    return None


def registry_hint(ats: str, company_name: str) -> Discovery | None:
    """Derive a token guess from the registry's ATS field + company name slug."""
    if not ats or ats in ("generic", "unknown", ""):
        return None
    slug = _clean_token(company_name or "")
    if not slug:
        return None
    return Discovery(ats=ats, board_token=slug, method="registry_hint",
                     confidence=0.4, note="token guessed from company name")


def discover(careers_url: str, company_name: str = "", html: str | None = None,
             ats_hint: str = "") -> Discovery:
    """Full discovery cascade. Returns the best Discovery (never None)."""
    d = discover_from_url(careers_url)
    if d and d.ats != "generic":
        return d
    if html:
        d = discover_from_html(html)
        if d:
            return d
    d = registry_hint(ats_hint, company_name)
    if d:
        return d
    return Discovery(note="no ATS discovered")


# ── Persistence (separate from registry YAML) ────────────────────────────────

def load_discovery(careers_url: str) -> Discovery | None:
    """Load a cached discovery for a career URL, if present and active."""
    from database.connection import get_session
    from database.models import ATSDiscovery
    session = get_session()
    try:
        row = (
            session.query(ATSDiscovery)
            .filter(ATSDiscovery.careers_url == careers_url, ATSDiscovery.is_active == True)
            .order_by(ATSDiscovery.updated_at.desc())
            .first()
        )
        if not row:
            return None
        return Discovery(ats=row.ats, board_token=row.board_token,
                         api_url=row.api_url, method=row.method,
                         confidence=row.confidence, note=row.note or "")
    finally:
        session.close()


def save_discovery(careers_url: str, company_id: str, company_name: str,
                   discovery: Discovery) -> None:
    """Upsert a discovery result so we don't re-scan on every poll."""
    from datetime import datetime
    from database.connection import get_session
    from database.models import ATSDiscovery
    session = get_session()
    try:
        row = (
            session.query(ATSDiscovery)
            .filter(ATSDiscovery.careers_url == careers_url)
            .first()
        )
        if not row:
            row = ATSDiscovery(company_id=company_id, company_name=company_name,
                               careers_url=careers_url)
            session.add(row)
        row.company_name = company_name
        row.ats = discovery.ats
        row.board_token = discovery.board_token
        row.api_url = discovery.api_url
        row.method = discovery.method
        row.confidence = discovery.confidence
        row.note = discovery.note or ""
        row.is_active = True
        row.updated_at = datetime.utcnow()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.debug("Discovery store failed for %s: %s", careers_url, e)
    finally:
        session.close()