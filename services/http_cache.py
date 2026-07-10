import hashlib
import logging
from datetime import datetime
from sqlalchemy import select

from database.connection import get_session
from database.models import PageCache

logger = logging.getLogger("jobzo.cache")


def _html_hash(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()[:16]


def get_cache(url: str) -> PageCache | None:
    session = get_session()
    try:
        return session.execute(
            select(PageCache).where(PageCache.url == url)
        ).scalar_one_or_none()
    finally:
        session.close()


def set_cache(
    url: str,
    html: str,
    etag: str = "",
    last_modified: str = "",
    status: int = 200,
    parser: str = "",
    jobs_found: int = 0,
):
    session = get_session()
    try:
        entry = session.execute(
            select(PageCache).where(PageCache.url == url)
        ).scalar_one_or_none()
        h = _html_hash(html)
        if entry:
            entry.etag = etag
            entry.last_modified = last_modified
            entry.html_hash = h
            entry.status = status
            entry.fetched_at = datetime.utcnow()
            entry.parser = parser
            entry.jobs_found = jobs_found
            entry.html = html
        else:
            entry = PageCache(
                url=url,
                etag=etag,
                last_modified=last_modified,
                html_hash=h,
                status=status,
                fetched_at=datetime.utcnow(),
                parser=parser,
                jobs_found=jobs_found,
                html=html,
            )
            session.add(entry)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning("Cache write failed for %s: %s", url, e)
    finally:
        session.close()


def is_fresh(url: str, ttl_hours: int = 6) -> bool:
    entry = get_cache(url)
    if not entry:
        return False
    age = (datetime.utcnow() - entry.fetched_at).total_seconds()
    return age < ttl_hours * 3600


def html_unchanged(url: str, html: str) -> bool:
    entry = get_cache(url)
    if not entry:
        return False
    return entry.html_hash == _html_hash(html)
