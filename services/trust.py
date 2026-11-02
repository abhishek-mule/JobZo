"""Trust — evidence-first legitimacy engine for job opportunities.

Two-layer model:
  Employer legitimacy — is the hiring entity real / known?
  Job legitimacy      — is THIS particular posting likely legitimate?

Evidence is collected first, then a score is derived from it. The score is a
byproduct; the evidence is the source of truth. This keeps the system
explainable ("why is this trusted?") and testable.

Tiers:
  🛡 verified    — known employer + official source/domain + no risk signals
  ✓ trusted      — no critical risk and overall score >= 60
  ⚠ unverified   — no critical risk, but not enough evidence to confirm.
                   NOT bad — most local/startup openings land here.
  🚨 suspicious  — critical scam signals present. Blocked from feed/dig.

`unverified` is intentionally never removed from the feed. Freshers are
exactly the people who need access to small startups, GCCs, and college
hiring that JobZo has not independently verified yet.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from services.config import Config

# ── Tiers ────────────────────────────────────────────────────────────────────

TIER_VERIFIED = "verified"
TIER_TRUSTED = "trusted"
TIER_UNVERIFIED = "unverified"
TIER_SUSPICIOUS = "suspicious"

TIER_EMOJI = {
    TIER_VERIFIED: "🛡",
    TIER_TRUSTED: "✓",
    TIER_UNVERIFIED: "⚠",
    TIER_SUSPICIOUS: "🚨",
}

TIER_LABELS = {
    TIER_VERIFIED: "Verified",
    TIER_TRUSTED: "Trusted",
    TIER_UNVERIFIED: "Unverified",
    TIER_SUSPICIOUS: "Suspicious",
}

# ── Scam signals ─────────────────────────────────────────────────────────────
# Three levels, so we don't false-positive on harmless words:
#   critical → immediate suspicious (money out of the candidate's pocket)
#   strong   → large penalty (process smells)
#   weak     → small penalty (may be legitimate in context)

CRITICAL_SCAM_KEYWORDS = [
    "registration fee",
    "processing fee",
    "candidate pays",
    "applicant pays",
    "deposit required",
    "security deposit",
    "token money",
    "advance fee",
    "refundable fee",
    "pay to apply",
    "application fee",
    "money for job",
    "job in exchange for money",
    "pay us to get",
    "guaranteed job after payment",
]

STRONG_SCAM_KEYWORDS = [
    "whatsapp only",
    "telegram only",
    "direct whatsapp",
    "no interview",
    "easy money",
    "100% placement",
    "guaranteed placement",
    "guaranteed selection",
    "assured job",
    "assured selection",
    "anyone can apply",
    "copy paste job",
    "form filling job",
    "earn per form",
    "no experience needed",
    "work and earn",
]

WEAK_SCAM_KEYWORDS = [
    "data entry",
    "work from home typing",
    "paid training",
    "training fee",
    "part time",
]

# ── Domains ──────────────────────────────────────────────────────────────────

FREE_MAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.in", "hotmail.com", "outlook.com",
    "rediffmail.com", "ymail.com", "protonmail.com", "zoho.com", "zoho.in",
    "icloud.com", "aol.com", "msn.com", "live.com",
}

URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "shorturl.at", "rb.gy",
    "cutt.ly", "is.gd", "buff.ly", "ow.ly",
}

HIGH_RISK_DOMAINS = {
    "telegram.me", "t.me", "whatsapp.com", "wa.me",
    "docs.google.com", "forms.gle", "sites.google.com", "medium.com",
}


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return (url or "").lower()


def _base_domain(host: str) -> str:
    """Last two labels, e.g. jobs.browserstack.com → browserstack.com."""
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _company_domain_hints(company_name: str) -> list[str]:
    """Plausible domains derived from a company name (punetech.com → punetech)."""
    norm = re.sub(r"[^a-z0-9]", "", company_name.lower().strip())
    if not norm:
        return []
    hints = [norm]
    for suffix in ("tech", "labs", "solutions", "services", "soft", "systems"):
        if norm.endswith(suffix) and len(norm) > len(suffix) + 3:
            hints.append(norm[: -len(suffix)])
    return hints


def _name_domain_consistent(company_name: str, base: str) -> bool:
    """Unknown-company heuristic: does the URL domain plausibly belong to the company?"""
    if not base:
        return False
    main_label = base.split(".")[0]
    hints = _company_domain_hints(company_name)
    for hint in hints:
        if hint in main_label or main_label in hint:
            return True
    # e.g. company "BrowserStack" vs domain "browserstack.com"
    words = re.sub(r"[^a-z0-9]", "", company_name.lower())
    if words and (words in base or base.replace(".", "") in words.replace(" ", "")):
        return True
    return False


def _ats_verified(url: str) -> bool:
    """Does the URL match a known ATS board pattern (Greenhouse, Lever, ...)?"""
    if not url:
        return False
    ats_types = Config.registry("ats", {}).get("ats_types", {})
    for cfg in ats_types.values():
        pat = cfg.get("url_pattern")
        if pat and re.search(pat, url, re.IGNORECASE):
            return True
    return False


def _free_or_shortener_domain(host: str) -> bool:
    base = _base_domain(host)
    return base in FREE_MAIL_DOMAINS or base in URL_SHORTENERS or base in HIGH_RISK_DOMAINS


def _scam_flags(job) -> list[dict]:
    text = " ".join(filter(None, [
        getattr(job, "title", ""),
        getattr(job, "description", ""),
        getattr(job, "location", ""),
        getattr(job, "experience_required", ""),
        getattr(job, "salary", ""),
    ])).lower()

    flags = []
    for kw in CRITICAL_SCAM_KEYWORDS:
        if kw in text:
            flags.append({"level": "critical", "keyword": kw})
    for kw in STRONG_SCAM_KEYWORDS:
        if kw in text:
            flags.append({"level": "strong", "keyword": kw})
    for kw in WEAK_SCAM_KEYWORDS:
        if kw in text:
            flags.append({"level": "weak", "keyword": kw})
    return flags


# ── Evidence model ───────────────────────────────────────────────────────────

@dataclass
class TrustEvidence:
    company_name: str = ""

    # Employer legitimacy
    company_in_registry: bool = False
    company_status: str = ""
    company_quality: str = ""
    company_verified: bool = False

    # Source authenticity
    source: str = ""

    # Domain authenticity
    url_domain: str = ""
    ats_verified: bool = False
    free_domain: bool = False
    company_domain_match: bool | None = None
    name_domain_consistent: bool = False

    # Job-posting consistency / description quality
    title: str = ""
    description: str = ""
    location: str = ""
    salary: str = ""
    skills_count: int = 0

    # Scam indicators
    scam_flags: list[dict] = field(default_factory=list)


# ── Evidence collection ──────────────────────────────────────────────────────

def collect_evidence(job) -> TrustEvidence:
    """Gather every trust signal from a Job without scoring it."""
    from services.company_registry import get_by_alias

    company = None
    try:
        company = get_by_alias(getattr(job, "company", ""))
    except Exception:
        company = None

    url = getattr(job, "url", "") or ""
    host = _host_of(url)
    base = _base_domain(host)
    free_domain = _free_or_shortener_domain(host)

    company_domain_match: bool | None = None
    name_domain_consistent = False

    if company is not None:
        official = getattr(company, "careers_url", "") or getattr(company, "job_listing_url", "")
        if official:
            company_domain_match = bool(base) and _base_domain(_host_of(official)) == base
        else:
            name_domain_consistent = _name_domain_consistent(getattr(job, "company", ""), base)
    else:
        name_domain_consistent = _name_domain_consistent(getattr(job, "company", ""), base)

    return TrustEvidence(
        company_name=getattr(job, "company", "") or "",
        company_in_registry=company is not None,
        company_status=getattr(company, "status", "") if company else "",
        company_quality=getattr(company, "registry_quality", "") if company else "",
        company_verified=(company is not None
                          and getattr(company, "status", "active") not in ("broken", "archived")
                          and getattr(company, "registry_quality", "") == "verified"),
        source=getattr(job, "source", "") or "",
        url_domain=host,
        ats_verified=_ats_verified(url),
        free_domain=free_domain,
        company_domain_match=company_domain_match,
        name_domain_consistent=name_domain_consistent,
        title=getattr(job, "title", "") or "",
        description=getattr(job, "description", "") or "",
        location=getattr(job, "location", "") or "",
        salary=getattr(job, "salary", "") or "",
        skills_count=len(getattr(job, "skills", []) or []),
        scam_flags=_scam_flags(job),
    )


# ── Scoring ──────────────────────────────────────────────────────────────────

@dataclass
class TrustResult:
    score: float
    tier: str
    reasons: list[str] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    why_not_100: list[str] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return TIER_EMOJI.get(self.tier, "?")

    @property
    def label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)


def _employer_score(ev: TrustEvidence) -> tuple[int, list[str]]:
    if not ev.company_in_registry:
        if ev.company_domain_match is True or ev.name_domain_consistent:
            return 16, ["Company not in registry, but domain is self-consistent"]
        return 8, []
    if ev.company_status not in ("active", ""):
        return 10, [f"Company status in registry: {ev.company_status}"]
    if ev.company_quality == "verified":
        return 30, ["Company independently verified in registry"]
    if ev.company_quality in ("community", "manual"):
        return 24, ["Company present in JobZo registry"]
    if ev.company_quality in ("auto_discovered", "deprecated"):
        return 14, [f"Registry quality: {ev.company_quality}"]
    return 20, ["Company present in JobZo registry"]


def _source_score(ev: TrustEvidence) -> tuple[int, list[str]]:
    src = (ev.source or "").lower()
    if src.startswith("company:"):
        return 20, ["Official company career page"]
    if src in ("yc_workatastartup", "wellfound") or src.startswith("yc:"):
        return 18, ["Curated startup board (YC / Wellfound)"]
    if src.startswith("rss"):
        return 12, ["Aggregated feed (RSS)"]
    if src.startswith("telegram"):
        return 4, ["Telegram channel — low source authenticity"]
    if src == "manual":
        return 8, ["Manually imported posting"]
    return 8, []


def _domain_score(ev: TrustEvidence) -> tuple[int, list[str]]:
    if ev.free_domain:
        return 0, ["Suspicious domain (free mail / shortener / sharing service)"]
    if ev.ats_verified:
        return 20, ["Verified ATS board (Greenhouse / Lever / Ashby / ...)"]
    if ev.company_domain_match is True:
        return 20, ["URL on the company's official domain"]
    if ev.name_domain_consistent:
        return 14, ["URL domain consistent with company name"]
    if ev.company_in_registry:
        # Known employer, clean external board (e.g. aggregated RSS/YC listings).
        # The employer is already vetted; this specific URL just isn't theirs.
        return 12, ["Known employer — posting on a clean external board"]
    return 6, ["Posting domain not confirmed against the employer"]


def _description_score(ev: TrustEvidence) -> tuple[int, list[str]]:
    n = len((ev.description or "").strip())
    pts = 0
    reasons = []
    if n >= 800:
        pts += 12
        reasons.append("Detailed job description")
    elif n >= 300:
        pts += 8
    elif n >= 100:
        pts += 4
    elif n > 0:
        pts += 1
    if ev.salary:
        pts += 6
        reasons.append("Salary disclosed")
    if ev.location:
        pts += 5
        reasons.append("Location disclosed")
    if ev.skills_count > 0:
        pts += 7
        reasons.append("Structured skill list")
    return min(30, pts), reasons


def calculate_trust(evidence: TrustEvidence) -> TrustResult:
    """Evidence → score + tier. Pure function; no DB access."""
    emp, emp_reasons = _employer_score(evidence)
    src, src_reasons = _source_score(evidence)
    dom, dom_reasons = _domain_score(evidence)
    dq, dq_reasons = _description_score(evidence)

    score = float(min(100, emp + src + dom + dq))

    critical = [f for f in evidence.scam_flags if f["level"] == "critical"]
    strong = [f for f in evidence.scam_flags if f["level"] == "strong"]
    weak = [f for f in evidence.scam_flags if f["level"] == "weak"]
    risks = list(evidence.scam_flags)
    if evidence.free_domain:
        strong = strong + [{"level": "strong", "keyword": "suspicious domain",
                            "detail": evidence.url_domain}]
        risks.append({"level": "strong", "keyword": "suspicious domain",
                      "detail": evidence.url_domain})

    reasons = []
    for group in (emp_reasons, src_reasons, dom_reasons, dq_reasons):
        reasons.extend(group)

    # Penalties (strong flags are loud, weak ones barely move the needle)
    score -= 20.0 * len(strong) + 5.0 * len(weak)
    score = max(0.0, min(100.0, round(score, 1)))

    if critical or len(strong) >= 2 or evidence.free_domain:
        tier = TIER_SUSPICIOUS
        score = min(score, 40.0)
        if critical:
            reasons = []
    elif evidence.company_in_registry and evidence.company_status not in ("broken", "archived") \
            and evidence.company_verified \
            and (evidence.ats_verified or evidence.company_domain_match is True):
        tier = TIER_VERIFIED
    elif evidence.company_in_registry and evidence.company_status not in ("broken", "archived") \
            and score >= 60:
        tier = TIER_TRUSTED
    else:
        # Employer not independently verified → cap at unverified. Not a
        # rejection: local/startup/GCC openings live here and stay visible.
        tier = TIER_UNVERIFIED

    why_not_100 = _why_not_100(evidence)

    return TrustResult(score=score, tier=tier, reasons=reasons,
                       risks=risks, why_not_100=why_not_100)


def _why_not_100(evidence: TrustEvidence) -> list[str]:
    gaps = []
    if not evidence.company_verified:
        gaps.append("Company has not been independently verified")
    if not (evidence.ats_verified or evidence.company_domain_match is True or evidence.name_domain_consistent):
        gaps.append("Posting domain is not confirmed against the employer")
    if not evidence.salary:
        gaps.append("No salary information posted")
    if evidence.source.startswith(("rss", "telegram")) or evidence.source == "manual":
        gaps.append("Source is not an official company/ATS channel")
    if evidence.scam_flags:
        gaps.append(f"{len(evidence.scam_flags)} flagged keyword(s) in the posting")
    return gaps[:4]


def trust_for_job(job) -> TrustResult:
    """Convenience: collect evidence + score in one call."""
    return calculate_trust(collect_evidence(job))