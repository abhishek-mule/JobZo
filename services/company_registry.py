"""Company Registry — query, validate, and manage company knowledge."""

from datetime import datetime
from typing import Any

from database.connection import get_session
from database.models import Company, CompanyAlias, HiringChannel
from services.config import Config
from sqlalchemy import func


REGISTRY_DIR = Config._data.get("registry", {})


def get_all(active_only: bool = True) -> list[Company]:
    """Return all companies from DB, optionally only active ones."""
    from sqlalchemy.orm import subqueryload
    session = get_session()
    try:
        q = session.query(Company)
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.options(subqueryload(Company.hiring_channels)).order_by(Company.name).all()
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
        return (
            session.query(Company)
            .filter(func.lower(Company.name) == name.lower())
            .first()
        )
    finally:
        session.close()


def get_by_category(category: str, active_only: bool = True) -> list[Company]:
    """Find companies with a matching primary category."""
    session = get_session()
    try:
        q = session.query(Company).filter(Company.primary_category == category)
        if active_only:
            q = q.filter(Company.is_active == True)
        return q.order_by(Company.name).all()
    finally:
        session.close()


def get_by_ats(ats_type: str, active_only: bool = True) -> list[Company]:
    """Find companies using a specific ATS (exact match on name)."""
    session = get_session()
    try:
        q = session.query(Company).filter(Company.ats == ats_type)
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


# ── Sync ─────────────────────────────────────────────────────────────────────

def sync_companies_from_registry() -> dict:
    """Sync companies.yaml + aliases.yaml into the database.

    The YAML is identity-only. Fields not present get DB defaults.
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

            vals = {
                "name": data.get("name", cid),
                "primary_category": data.get("category", data.get("primary_category", "")),
                "ats": data.get("ats", ""),
                "careers_url": data.get("careers_url", ""),
                "job_listing_url": data.get("job_listing_url", ""),
                "fresher_friendly": data.get("fresher_friendly", False),
                "graduate_roles": data.get("graduate_roles", False),
                "internship": data.get("internship", False),
                "remote_policy": data.get("remote_policy", ""),
                "poll_interval_hours": data.get("poll_interval_hours", 24),
                "tags": data.get("tags", []),
                "status": data.get("status", "active"),
                "registry_source": data.get("registry_source", "manual"),
                "registry_quality": data.get("registry_quality", "manual"),
                "is_active": data.get("status", "active") not in ("archived",),
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

            # Sync hiring channels
            _sync_hiring_channels(session, cid, data.get("hiring_channels", {}))

        # Sync aliases
        for cid, alias_list in aliases_data.items():
            company = session.get(Company, cid)
            if not company:
                continue
            existing_aliases = {a.alias.lower(): a for a in company.aliases}
            new_aliases_lower = {a.lower().strip() for a in alias_list}
            for alias_lower, alias_rec in existing_aliases.items():
                if alias_lower not in new_aliases_lower:
                    session.delete(alias_rec)
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


def _sync_hiring_channels(session, company_id: str, channels: dict):
    """Sync hiring channels for a company from YAML data.

    Each channel is a dict keyed by channel name:
      campus:
        url: https://...
        ats: workday
        fresher_friendly: true
    """
    existing = {
        ch.channel_name: ch
        for ch in session.query(HiringChannel)
        .filter(HiringChannel.company_id == company_id)
        .all()
    }
    seen = set()
    for ch_name, ch_data in channels.items():
        seen.add(ch_name)
        url = ch_data.get("url", "") or ch_data.get("job_listing_url", "")
        if ch_name in existing:
            ch = existing[ch_name]
            ch.careers_url = ch_data.get("careers_url", "")
            ch.job_listing_url = url
            ch.ats = ch_data.get("ats", "")
            ch.fresher_friendly = ch_data.get("fresher_friendly", False)
            ch.graduate_roles = ch_data.get("graduate_roles", False)
            ch.poll_interval_hours = ch_data.get("poll_interval_hours")
            ch.is_active = True
        else:
            session.add(HiringChannel(
                company_id=company_id,
                channel_name=ch_name,
                careers_url=ch_data.get("careers_url", ""),
                job_listing_url=url,
                ats=ch_data.get("ats", ""),
                fresher_friendly=ch_data.get("fresher_friendly", False),
                graduate_roles=ch_data.get("graduate_roles", False),
                poll_interval_hours=ch_data.get("poll_interval_hours"),
                is_active=True,
            ))
    # Deactivate channels no longer in YAML
    for ch_name, ch in existing.items():
        if ch_name not in seen:
            ch.is_active = False
