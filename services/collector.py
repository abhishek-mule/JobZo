import logging
import hashlib
import re
from datetime import datetime
from typing import Sequence

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from database.models import Job
from database.connection import get_session
from providers import PROVIDERS
from providers.base import RawJob
from services.config import Config

logger = logging.getLogger("jobzo.collector")


def _make_dedup_key(company: str, title: str, location: str = "") -> str:
    def norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[^a-z0-9\s]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s[:60]

    raw = f"{norm(company)}|{norm(title)}|{norm(location)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def collect_all(keywords: list[str] | None = None) -> int:
    total = 0
    errors = 0

    for name, provider_cls in PROVIDERS.items():
        cfg = Config.provider_config(name)
        if not cfg.get("enabled", True):
            logger.debug("Provider %s disabled, skipping", name)
            continue

        provider = provider_cls()
        logger.info("Collecting from %s...", name)

        try:
            raw_jobs = await provider.search(keywords)
        except Exception as e:
            logger.error("Provider %s failed: %s", name, e)
            errors += 1
            continue

        # Enrich YC job entries by scraping their pages
        from providers.hn_scraper import enrich_yc_jobs
        raw_jobs = await enrich_yc_jobs(raw_jobs)

        saved = _store_jobs(raw_jobs, provider, keywords)
        total += saved
        logger.info("%s: %d new jobs", name, saved)

    logger.info("Collection complete: %d new jobs (%d errors)", total, errors)
    return total


def _store_jobs(raw_jobs: list[RawJob], provider, keywords: list[str] | None = None) -> int:
    session: Session = get_session()
    saved = 0

    try:
        for raw in raw_jobs:
            normalized = provider.normalize(raw)
            dedup_key = _make_dedup_key(
                normalized["company"],
                normalized["title"],
                normalized.get("location", ""),
            )

            existing = session.execute(
                select(Job).where(
                    or_(
                        Job.url == normalized["url"],
                        Job.dedup_key == dedup_key,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                continue

            job = Job(
                company=normalized["company"],
                title=normalized["title"],
                description=normalized["description"],
                location=normalized.get("location", ""),
                salary=normalized.get("salary", ""),
                experience_required=normalized.get("experience_required", ""),
                skills=normalized.get("skills", []),
                url=normalized["url"],
                source=normalized["source"],
                raw_html=raw.raw_html,
                posted_at=normalized.get("posted_at"),
                remote=normalized.get("remote", False),
                dedup_key=dedup_key,
            )
            session.add(job)
            saved += 1

        session.commit()
        from tracker.events import record_event, SYNC_RUN
        record_event(SYNC_RUN, "system", "collector", actor="system", metadata={
            "jobs_saved": saved,
            "keywords": keywords,
        })
    except Exception as e:
        session.rollback()
        logger.error("Error storing jobs: %s", e)
        raise
    finally:
        session.close()

    return saved
