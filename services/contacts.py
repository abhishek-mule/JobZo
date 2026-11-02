"""Verified contact discovery — evidence first, never guessed addresses.

Design rules:
  * Only public, attributable sources are used. No LinkedIn scraping.
  * A valid email is not a recruiter. Identity, domain, mailbox and hiring
    relevance are separate booleans, each with evidence.
  * JobZo never invents an address from a name pattern. Guessed addresses
    damage deliverability and the user's reputation.
  * The company's official domain is the anchor for domain verification.

Typical flow:
  official careers page / official website / manual LinkedIn URL
      → contact record
      → evidence rows (URL + excerpt + confidence)
      → verification dimensions
      → confidence + status
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.connection import get_session
from database.models import Contact, ContactEvidence, ContactVerification

FREE_MAIL_DOMAINS = {
    "gmail.com", "gmail.co.uk", "googlemail.com", "yahoo.com", "yahoo.in",
    "yahoo.co.uk", "yahoo.co.in", "hotmail.com", "hotmail.co.uk",
    "outlook.com", "outlook.in", "rediffmail.com", "ymail.com",
    "protonmail.com", "proton.me", "zoho.com", "zoho.in", "icloud.com",
    "aol.com", "msn.com", "live.com",
}

NON_COMPANY_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com",
    "workday.com", "myworkdayjobs.com", "linkedin.com", "naukri.com",
    "indeed.com", "wellfound.com", "ycombinator.com", "github.com",
}

IGNORE_EMAIL_DOMAINS = {
    "example.com", "domain.com", "email.com", "sentry.io", "wixpress.com",
    "w3.org", "schema.org", "googlemail.com",
}

# Public suffixes with two labels. Without this, "careers.acme.co.in" would
# collapse to "co.in" and any .co.in address would look like the same company.
MULTI_PART_SUFFIXES = {
    "co.in", "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au",
    "co.jp", "co.nz", "co.za", "com.br", "com.mx", "com.sg", "com.my",
    "com.hk", "com.tr", "com.pk", "com.bd", "com.np", "com.lk", "com.eg",
    "com.sa", "com.ae", "co.il", "co.id", "co.ke", "com.ng",
}

# Evidence kinds that can establish identity.
IDENTITY_EVIDENCE = {
    "official_careers_page", "official_website", "company_directory",
    "job_posting", "linkedin_profile", "user_confirmed",
}

# Evidence kinds that can establish mailbox existence.
MAILBOX_EVIDENCE = {"mailbox_check", "user_confirmed_mailbox"}

RELEVANT_ROLE = re.compile(
    r"\b(recruit|talent|hiring|hr\b|human resources|people ops|"
    r"founder|co-?founder|engineering manager|hiring manager|"
    r"head of engineering|director of engineering)\b",
    re.IGNORECASE,
)

GENERIC_LOCAL_PARTS = {
    "careers", "career", "jobs", "job", "hiring", "recruiting", "recruitment",
    "talent", "hr", "hello", "info", "contact", "people", "team", "apply",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class ExtractedEmail:
    email: str
    context: str = ""
    kind: str = "official_careers_page"


@dataclass
class ContactRecord:
    contact: Contact
    verification: ContactVerification | None = None

    @property
    def status(self) -> str:
        return self.verification.status if self.verification else "unverified"

    @property
    def confidence(self) -> float:
        return self.verification.confidence if self.verification else 0.0

    @property
    def is_verified(self) -> bool:
        return self.status in ("verified", "verified_domain")

    @property
    def levels(self) -> list[str]:
        if not self.verification:
            return []
        levels = []
        if self.verification.identity_verified:
            levels.append("identity")
        if self.verification.domain_verified:
            levels.append("domain")
        if self.verification.mailbox_verified:
            levels.append("mailbox")
        if self.verification.relevance_verified:
            levels.append("hiring_relevance")
        return levels

    def as_dict(self) -> dict[str, Any]:
        c = self.contact
        v = self.verification
        return {
            "id": c.id,
            "company": c.company,
            "name": c.name,
            "role": c.role,
            "email": c.email,
            "linkedin": c.linkedin,
            "source": c.source,
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "levels": self.levels,
            "domain_verified": bool(v and v.domain_verified),
            "identity_verified": bool(v and v.identity_verified),
            "mailbox_verified": bool(v and v.mailbox_verified),
            "relevance_verified": bool(v and v.relevance_verified),
            "reasons": json.loads(v.reasons_json) if v and v.reasons_json else [],
            "last_verified_at": v.verified_at.isoformat() if v and v.verified_at else "",
        }


def _domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "@" in value:
        return value.split("@", 1)[1]
    if "://" not in value:
        value = "https://" + value
    try:
        return (urlparse(value).hostname or "").lower()
    except Exception:
        return ""


def base_domain(value: str) -> str:
    host = _domain(value)
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def official_company_domain(company_name: str, careers_url: str = "") -> str:
    """Resolve the employer's real domain from the registry or careers URL."""
    from services.company_registry import get_by_alias

    candidates = []
    try:
        company = get_by_alias(company_name)
        if company is not None:
            candidates.extend([
                getattr(company, "careers_url", "") or "",
                getattr(company, "job_listing_url", "") or "",
            ])
    except Exception:
        pass
    if careers_url:
        candidates.insert(0, careers_url)

    for candidate in candidates:
        domain = base_domain(candidate)
        if domain and domain not in NON_COMPANY_DOMAINS and domain not in FREE_MAIL_DOMAINS:
            return domain
    return ""


def email_domain(email: str) -> str:
    return _domain(email)


def is_free_mail(email: str) -> bool:
    return email_domain(email) in FREE_MAIL_DOMAINS


def domain_matches(email: str, company_domain: str) -> bool:
    ed = email_domain(email)
    cd = base_domain(company_domain)
    if not ed or not cd:
        return False
    return ed == cd or ed.endswith("." + cd)


def looks_relevant(role: str, name: str = "", email: str = "") -> bool:
    text = " ".join(filter(None, [role, name, email.split("@", 1)[0] if email else ""]))
    if RELEVANT_ROLE.search(text):
        return True
    local = email.split("@", 1)[0].lower() if email else ""
    return local in GENERIC_LOCAL_PARTS


def _clean_email(value: str) -> str:
    return (value or "").strip().strip(".,;:()[]<>\"'").lower()


def parse_public_emails(html: str, page_url: str = "",
                        kind: str = "official_careers_page") -> list[ExtractedEmail]:
    """Extract public emails and a short context snippet from one page."""
    if not html:
        return []

    found: dict[str, ExtractedEmail] = {}

    # mailto links first — they carry the strongest intent signal.
    for match in re.finditer(r"mailto:([^\"'?\s>]+)", html, re.IGNORECASE):
        email = _clean_email(match.group(1))
        if _acceptable_email(email):
            found[email] = ExtractedEmail(email=email, context="mailto link", kind=kind)

    # Then plain text emails.
    for match in EMAIL_RE.finditer(html):
        email = _clean_email(match.group(0))
        if not _acceptable_email(email) or email in found:
            continue
        start = max(0, match.start() - 90)
        end = min(len(html), match.end() + 90)
        context = re.sub(r"<[^>]+>", " ", html[start:end])
        context = re.sub(r"\s+", " ", context).strip()
        found[email] = ExtractedEmail(email=email, context=context[:180], kind=kind)

    return list(found.values())


def _acceptable_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        return False
    if domain in IGNORE_EMAIL_DOMAINS:
        return False
    if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", domain):
        return False
    if email.startswith(("noreply", "no-reply", "donotreply")):
        return False
    return len(email) <= 254


def _name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    if local.lower() in GENERIC_LOCAL_PARTS:
        return "Hiring Team"
    return " ".join(part.capitalize() for part in re.split(r"[._\-+]", local) if part)


def verification_for(
    *,
    company: str,
    name: str,
    role: str,
    email: str,
    evidence_kinds: Iterable[str],
    company_domain: str = "",
) -> dict[str, Any]:
    """Pure verification: evidence kinds → dimensions, status, confidence.

    Kept separate from the DB so it can be tested and audited directly.
    """
    kinds = set(evidence_kinds or [])
    official = base_domain(company_domain) if company_domain else official_company_domain(company)
    ed = email_domain(email)

    identity = bool(kinds & IDENTITY_EVIDENCE)
    domain_ok = bool(email) and domain_matches(email, official) and not is_free_mail(email)
    mailbox = bool(kinds & MAILBOX_EVIDENCE)
    relevance = looks_relevant(role, name, email)

    reasons: list[str] = []
    if identity:
        reasons.append("Person/team tied to an official company source")
    else:
        reasons.append("No official source proves the company association")
    if domain_ok:
        reasons.append(f"Email domain matches official domain ({official})")
    elif email:
        reasons.append("Email domain is not the company's official domain")
    if relevance:
        reasons.append("Role is hiring-relevant")
    else:
        reasons.append("Role is not clearly hiring-relevant")
    if mailbox:
        reasons.append("Mailbox existence confirmed")
    else:
        reasons.append("Mailbox existence not independently checked")

    confidence = 0.0
    confidence += 0.35 if identity else 0.0
    confidence += 0.30 if domain_ok else 0.0
    confidence += 0.25 if relevance else 0.0
    confidence += 0.10 if mailbox else 0.0
    # Corroborating sources add a little confidence, never enough to fake a level.
    confidence += min(0.05, max(0, len(kinds) - 1) * 0.01)

    risky = bool(email) and is_free_mail(email)
    if risky:
        confidence = min(confidence, 0.45)
        status = "risky"
        reasons.append("Free-mail address — deliverability/reputation risk")
    elif identity and domain_ok and relevance and email:
        status = "verified"
    elif domain_ok and relevance and email:
        status = "verified_domain"
    elif identity and not email:
        status = "verified_identity"
    elif identity and relevance:
        status = "verified_identity"
    else:
        status = "unverified"

    return {
        "official_domain": official,
        "email_domain": ed,
        "identity_verified": identity,
        "domain_verified": domain_ok,
        "mailbox_verified": mailbox,
        "relevance_verified": relevance,
        "status": status,
        "confidence": round(min(1.0, confidence), 3),
        "reasons": reasons,
    }


def _find_existing(session: Session, company: str, name: str, email: str,
                   linkedin: str) -> Contact | None:
    if email:
        found = session.query(Contact).filter(
            func.lower(Contact.email) == email.lower()
        ).first()
        if found:
            return found
    if linkedin:
        found = session.query(Contact).filter(
            func.lower(Contact.linkedin) == linkedin.lower()
        ).first()
        if found:
            return found
    return session.query(Contact).filter(
        func.lower(Contact.company) == company.lower().strip(),
        func.lower(Contact.name) == name.lower().strip(),
    ).first()


def save_contact(
    *,
    company: str,
    name: str,
    role: str = "",
    email: str = "",
    linkedin: str = "",
    source: str = "manual",
    evidence_kind: str = "user_confirmed",
    source_url: str = "",
    excerpt: str = "",
    evidence_confidence: float = 0.8,
    company_domain: str = "",
    session: Session | None = None,
) -> ContactRecord:
    """Create or update a contact, attach evidence, and re-verify it."""
    if not company.strip() or not name.strip():
        raise ValueError("company and name are required")

    own_session = session is None
    session = session or get_session()
    try:
        contact = _find_existing(session, company, name, email, linkedin)
        if contact is None:
            contact = Contact(
                company=company.strip(),
                name=name.strip(),
                role=role.strip(),
                email=_clean_email(email),
                linkedin=linkedin.strip(),
                source=source,
            )
            session.add(contact)
            session.flush()
        else:
            contact.company = company.strip() or contact.company
            contact.name = name.strip() or contact.name
            contact.role = role.strip() or contact.role
            contact.email = _clean_email(email) or contact.email
            contact.linkedin = linkedin.strip() or contact.linkedin
            contact.source = source or contact.source

        if source_url or excerpt:
            already = session.query(ContactEvidence).filter(
                ContactEvidence.contact_id == contact.id,
                ContactEvidence.kind == evidence_kind,
                ContactEvidence.url == source_url,
            ).first()
            if not already:
                session.add(ContactEvidence(
                    contact_id=contact.id,
                    kind=evidence_kind,
                    url=source_url,
                    excerpt=excerpt[:2000],
                    confidence=evidence_confidence,
                ))
        session.flush()

        record = verify_contact(contact.id, company_domain=company_domain, session=session)
        if own_session:
            # The returned record must stay readable after this session closes.
            session.expire_on_commit = False
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def add_evidence(contact_id: str, kind: str, url: str = "", excerpt: str = "",
                 confidence: float = 0.8, session: Session | None = None) -> ContactEvidence:
    own_session = session is None
    session = session or get_session()
    try:
        evidence = ContactEvidence(
            contact_id=contact_id,
            kind=kind,
            url=url,
            excerpt=excerpt[:2000],
            confidence=confidence,
        )
        session.add(evidence)
        session.flush()
        if own_session:
            session.commit()
        return evidence
    finally:
        if own_session:
            session.close()


def verify_contact(contact_id: str, company_domain: str = "",
                   session: Session | None = None) -> ContactRecord:
    own_session = session is None
    session = session or get_session()
    try:
        contact = session.get(Contact, contact_id)
        if contact is None:
            raise ValueError(f"Contact not found: {contact_id}")
        evidence_rows = session.query(ContactEvidence).filter(
            ContactEvidence.contact_id == contact_id
        ).all()
        kinds = [e.kind for e in evidence_rows]
        result = verification_for(
            company=contact.company,
            name=contact.name,
            role=contact.role,
            email=contact.email or "",
            evidence_kinds=kinds,
            company_domain=company_domain,
        )

        verification = session.query(ContactVerification).filter(
            ContactVerification.contact_id == contact_id
        ).first()
        if verification is None:
            verification = ContactVerification(contact_id=contact_id)
            session.add(verification)
        verification.company_domain = result["official_domain"]
        verification.email_domain = result["email_domain"]
        verification.identity_verified = result["identity_verified"]
        verification.domain_verified = result["domain_verified"]
        verification.mailbox_verified = result["mailbox_verified"]
        verification.relevance_verified = result["relevance_verified"]
        verification.status = result["status"]
        verification.confidence = result["confidence"]
        verification.reasons_json = json.dumps(result["reasons"])
        verification.verified_at = datetime.utcnow()
        session.flush()
        if own_session:
            # The returned record must stay readable after this session closes.
            session.expire_on_commit = False
            session.commit()
        return ContactRecord(contact=contact, verification=verification)
    finally:
        if own_session:
            session.close()


def list_contacts(company: str = "", min_confidence: float = 0.0) -> list[ContactRecord]:
    session = get_session()
    try:
        query = session.query(Contact)
        if company:
            query = query.filter(func.lower(Contact.company) == company.lower().strip())
        contacts = query.order_by(Contact.company, Contact.name).all()
        records = []
        for contact in contacts:
            verification = session.query(ContactVerification).filter(
                ContactVerification.contact_id == contact.id
            ).first()
            record = ContactRecord(contact=contact, verification=verification)
            if record.confidence >= min_confidence:
                records.append(record)
        records.sort(key=lambda r: (-r.confidence, r.contact.company, r.contact.name))
        return records
    finally:
        session.close()


def best_contact_for(company: str, role_title: str = "",
                     allow_unverified: bool = False) -> ContactRecord | None:
    """Best contact for a specific company/role, verified by default."""
    records = [r for r in list_contacts(company) if r.contact.email]
    if not allow_unverified:
        records = [r for r in records if r.is_verified]
    if not records:
        return None

    def rank(r: ContactRecord):
        relevance = 1 if (r.verification and r.verification.relevance_verified) else 0
        title_bonus = 0
        text = f"{r.contact.role} {r.contact.email}".lower()
        if role_title:
            family = role_title.lower().split()[0]
            if family and family in text:
                title_bonus = 1
        return (-relevance, -title_bonus, -r.confidence, r.contact.name.lower())

    records.sort(key=rank)
    return records[0]


def discover_public_contacts(
    company: str,
    careers_url: str = "",
    extra_urls: list[str] | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Fetch official public pages and extract listed emails.

    This intentionally does not touch LinkedIn or third-party databases.
    Returns a summary; individual contact records are persisted.
    """
    domain = official_company_domain(company, careers_url)
    urls: list[str] = []
    if careers_url:
        urls.append(careers_url)
    if domain:
        urls.extend([
            f"https://{domain}/careers",
            f"https://{domain}/contact",
            f"https://{domain}/about",
        ])
        urls.append(f"https://{domain}")
    urls.extend(extra_urls or [])

    seen_urls: set[str] = set()
    fetched = 0
    created = 0
    emails: list[dict[str, Any]] = []
    errors: list[str] = []

    headers = {"User-Agent": "JobZo/0.1 (+personal job search assistant)"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for url in urls:
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                response = client.get(url)
                if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
                    continue
                fetched += 1
                page_host = base_domain(url)
                kind = "official_careers_page" if "career" in url.lower() else "official_website"
                for extracted in parse_public_emails(response.text, url, kind=kind):
                    # Only keep emails whose domain is the employer's domain.
                    if domain and not domain_matches(extracted.email, domain):
                        continue
                    name = _name_from_email(extracted.email)
                    role = "Hiring Team" if name == "Hiring Team" else "Contact"
                    record = save_contact(
                        company=company,
                        name=name,
                        role=role,
                        email=extracted.email,
                        source=kind,
                        evidence_kind=kind,
                        source_url=url,
                        excerpt=extracted.context,
                        company_domain=domain,
                    )
                    created += 1
                    emails.append({
                        "email": extracted.email,
                        "name": name,
                        "status": record.status,
                        "confidence": record.confidence,
                        "source_url": url,
                    })
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue

    # De-duplicate the report by email.
    unique = {e["email"]: e for e in emails}
    return {
        "company": company,
        "domain": domain,
        "pages_fetched": fetched,
        "emails_found": len(unique),
        "contacts": list(unique.values()),
        "errors": errors[:5],
    }
