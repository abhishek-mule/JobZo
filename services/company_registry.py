"""Company Registry — query, validate, and manage company knowledge."""

from datetime import datetime
from typing import Any

from database.connection import get_session
from database.models import Company, CompanyAlias
from services.config import Config
from sqlalchemy import func


REGISTRY_DIR = Config._data.get("registry", {})


def get_all(active_only: bool = True) -> list[Company]:
    """Return all companies from DB, optionally only active ones."""
    session = get_session()
    try:
        q = session.query(Company)
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_by_id(company_id: str) -> Company | None:
    session = get_session()
    try:
        return session.get(Company, company_id)
    finally:
        session.close()


def get_by_alias(name: str) -> Company | None:
    """Find a company by any of its aliases (case-insensitive)."""
    session = get_session()
    try:
        alias_rec = (
            session.query(CompanyAlias)
            .filter(func.lower(CompanyAlias.alias) == name.lower())
            .first()
        )
        if alias_rec:
            return session.get(Company, alias_rec.company_id)
        # Fallback: try exact name match
        return (
            session.query(Company)
            .filter(func.lower(Company.name) == name.lower())
            .first()
        )
    finally:
        session.close()


def get_by_category(category: str, active_only: bool = True) -> list[Company]:
    """Find companies matching a primary or secondary category."""
    session = get_session()
    try:
        q = session.query(Company).filter(
            (Company.primary_category == category)
            | Company.secondary_categories.contains(category)
        )
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_by_ats(ats_type: str, active_only: bool = True) -> list[Company]:
    """Find companies using a specific ATS."""
    session = get_session()
    try:
        q = session.query(Company).filter(Company.ats.contains(ats_type))
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_fresher_friendly(active_only: bool = True) -> list[Company]:
    session = get_session()
    try:
        q = session.query(Company).filter(Company.fresher_friendly == True)
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_by_technology(tech: str, active_only: bool = True) -> list[Company]:
    """Find companies whose backend_stack includes a technology."""
    session = get_session()
    try:
        q = session.query(Company).filter(Company.backend_stack.contains(tech))
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_by_office(city: str, active_only: bool = True) -> list[Company]:
    """Find companies with an office in a given city."""
    session = get_session()
    try:
        q = session.query(Company).filter(Company.offices.contains(city))
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def count_by_category(active_only: bool = True) -> dict[str, int]:
    """Return count of companies per primary category."""
    session = get_session()
    try:
        q = session.query(Company.primary_category, func.count(Company.id))
        if active_only:
            q = q.filter(Company.is_active == True)
        rows = q.group_by(Company.primary_category).all()
        return {row[0]: row[1] for row in rows}
    finally:
        session.close()


def get_all_aliases() -> list[tuple[str, str]]:
    """Return all (alias, company_id) pairs."""
    session = get_session()
    try:
        rows = session.query(CompanyAlias.alias, CompanyAlias.company_id).all()
        return [(r[0], r[1]) for r in rows]
    finally:
        session.close()


def get_stats() -> dict:
    """Return aggregate registry statistics."""
    session = get_session()
    try:
        total = session.query(func.count(Company.id)).scalar() or 0
        active = session.query(func.count(Company.id)).filter(Company.is_active == True).scalar() or 0
        aliases = session.query(func.count(CompanyAlias.id)).scalar() or 0
        categories = session.query(Company.primary_category).distinct().count()
        return {
            "companies": total,
            "active": active,
            "aliases": aliases,
            "categories": categories,
        }
    finally:
        session.close()


# ── Validation ───────────────────────────────────────────────────────────────

VALIDATORS = {}


def validator(name: str):
    """Decorator to register a registry validator."""
    def wrap(fn):
        VALIDATORS[name] = fn
        return fn
    return wrap


def validate_registry() -> list[dict]:
    """Run all registered validators and return results."""
    results = []
    for name, fn in VALIDATORS.items():
        try:
            issues = fn()
            results.append({
                "check": name,
                "status": "PASS" if not issues else "FAIL",
                "issues": issues,
            })
        except Exception as e:
            results.append({
                "check": name,
                "status": "ERROR",
                "issues": [str(e)],
            })
    return results


@validator("duplicate_ids")
def _check_duplicate_ids() -> list[str]:
    companies = Config.registry("companies", {})
    seen = set()
    issues = []
    for cid in companies:
        if cid in seen:
            issues.append(f"Duplicate company id: {cid}")
        seen.add(cid)
    return issues


@validator("duplicate_aliases")
def _check_duplicate_aliases() -> list[str]:
    aliases = Config.registry("aliases", {}).get("aliases", {})
    seen = set()
    issues = []
    for cid, names in aliases.items():
        for alias in names:
            key = alias.lower().strip()
            if key in seen:
                issues.append(f"Duplicate alias '{alias}' (company: {cid})")
            seen.add(key)
    return issues


@validator("invalid_ats")
def _check_invalid_ats() -> list[str]:
    companies = Config.registry("companies", {})
    valid_ats = set(Config.registry("ats", {}).get("ats_types", {}).keys())
    issues = []
    for cid, data in companies.items():
        for ats_type in data.get("ats", []):
            if ats_type not in valid_ats:
                issues.append(f"{cid}: unknown ATS '{ats_type}'")
    return issues


@validator("missing_categories")
def _check_missing_categories() -> list[str]:
    companies = Config.registry("companies", {})
    valid_cats = set(Config.registry("categories", {}).get("categories", {}).keys())
    issues = []
    for cid, data in companies.items():
        if data.get("primary_category") not in valid_cats:
            issues.append(f"{cid}: unknown primary_category '{data.get('primary_category')}'")
        for sc in data.get("secondary_categories", []):
            if sc not in valid_cats:
                issues.append(f"{cid}: unknown secondary_category '{sc}'")
    return issues


@validator("missing_offices")
def _check_unknown_offices() -> list[str]:
    companies = Config.registry("companies", {})
    valid_locs = set(Config.registry("locations", {}).get("locations", {}).keys())
    issues = []
    for cid, data in companies.items():
        for office in data.get("offices", []):
            if office not in valid_locs:
                issues.append(f"{cid}: unknown office '{office}'")
    return issues


@validator("invalid_technologies")
def _check_invalid_technologies() -> list[str]:
    companies = Config.registry("companies", {})
    valid_techs = set(Config.registry("technologies", {}).get("technologies", {}).keys())
    issues = []
    for cid, data in companies.items():
        for tech in data.get("backend_stack", []):
            if tech not in valid_techs:
                issues.append(f"{cid}: unknown technology '{tech}'")
    return issues


@validator("missing_salary")
def _check_missing_salary() -> list[str]:
    companies = Config.registry("companies", {})
    issues = []
    for cid, data in companies.items():
        salary = data.get("salary", {})
        fresher = salary.get("fresher") if isinstance(salary, dict) else None
        if not fresher:
            issues.append(f"{cid}: missing fresher salary")
    return issues


@validator("orphan_aliases")
def _check_orphan_aliases() -> list[str]:
    companies = Config.registry("companies", {})
    aliases = Config.registry("aliases", {}).get("aliases", {})
    issues = []
    for cid in aliases:
        if cid not in companies:
            issues.append(f"Orphan alias group for unknown company '{cid}'")
    return issues


@validator("broken_urls")
def _check_broken_urls() -> list[str]:
    companies = Config.registry("companies", {})
    issues = []
    for cid, data in companies.items():
        url = data.get("job_listing_url", "")
        if url and not url.startswith("http"):
            issues.append(f"{cid}: job_listing_url does not start with http")
        careers = data.get("careers_url", "")
        if careers and not careers.startswith("http"):
            issues.append(f"{cid}: careers_url does not start with http")
    return issues


# ── Sync ─────────────────────────────────────────────────────────────────────

def sync_companies_from_registry() -> dict:
    """Sync companies.yaml + aliases.yaml into the database.

    Returns a summary dict with counts of created/updated entries.
    """
    companies_data = Config.registry("companies", {})
    aliases_data = Config.registry("aliases", {}).get("aliases", {})
    if not companies_data:
        return {"created": 0, "updated": 0, "aliases_created": 0, "total": 0}

    created = 0
    updated = 0
    aliases_created = 0

    session = get_session()
    try:
        for cid, data in companies_data.items():
            existing = session.get(Company, cid)
            salary = data.get("salary", {})
            interview = data.get("interview", {})

            vals = {
                "name": data.get("name", cid),
                "primary_category": data.get("primary_category", ""),
                "secondary_categories": data.get("secondary_categories", []),
                "stage": data.get("stage", ""),
                "offices": data.get("offices", []),
                "hiring_regions": data.get("hiring_regions", []),
                "ats": data.get("ats", []),
                "careers_url": data.get("careers_url", ""),
                "job_listing_url": data.get("job_listing_url", ""),
                "fresher_friendly": data.get("fresher_friendly", False),
                "internship": data.get("internship", False),
                "remote_policy": data.get("remote_policy", ""),
                "backend_stack": data.get("backend_stack", []),
                "hiring_patterns": data.get("hiring_patterns", {}),
                "interview_difficulty": interview.get("difficulty") if isinstance(interview, dict) else None,
                "interview_oa": interview.get("oa", False) if isinstance(interview, dict) else False,
                "interview_system_design": interview.get("system_design", False) if isinstance(interview, dict) else False,
                "salary_fresher_min": salary.get("fresher", {}).get("min") if isinstance(salary, dict) else None,
                "salary_fresher_max": salary.get("fresher", {}).get("max") if isinstance(salary, dict) else None,
                "salary_intern_min": salary.get("intern", {}).get("min") if isinstance(salary, dict) else None,
                "salary_intern_max": salary.get("intern", {}).get("max") if isinstance(salary, dict) else None,
                "priority": data.get("priority", "medium"),
                "confidence": data.get("confidence", 0.5),
                "tags": data.get("tags", []),
            }

            if existing:
                for k, v in vals.items():
                    setattr(existing, k, v)
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                vals["id"] = cid
                session.add(Company(**vals))
                created += 1

        # Sync aliases
        for cid, alias_list in aliases_data.items():
            company = session.get(Company, cid)
            if not company:
                continue
            # Remove old aliases not in the current list
            existing_aliases = {a.alias.lower(): a for a in company.aliases}
            new_aliases_lower = {a.lower().strip() for a in alias_list}
            for alias_lower, alias_rec in existing_aliases.items():
                if alias_lower not in new_aliases_lower:
                    session.delete(alias_rec)
            # Add new aliases
            for alias in alias_list:
                alias_key = alias.lower().strip()
                if alias_key not in existing_aliases:
                    session.add(CompanyAlias(company_id=cid, alias=alias.strip()))
                    aliases_created += 1

        session.commit()
        total = session.query(func.count(Company.id)).scalar() or 0
        return {
            "created": created,
            "updated": updated,
            "aliases_created": aliases_created,
            "total": total,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
