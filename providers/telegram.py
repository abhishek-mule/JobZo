import logging
from datetime import datetime

from providers.base import JobProvider, RawJob
from services.config import Config

logger = logging.getLogger("jobzo.telegram")


class TelegramProvider(JobProvider):
    name = "telegram"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        cfg = Config.provider_config("telegram")
        if not cfg.get("enabled", False):
            return []

        channels = cfg.get("channels", [])
        if not channels:
            logger.info("Telegram: no channels configured")
            return []

        logger.info("Telegram: monitoring %d channels", len(channels))
        return []

    def normalize(self, raw: RawJob) -> dict:
        return {
            "company": raw.data.get("company", ""),
            "title": raw.data.get("title", ""),
            "description": raw.data.get("description", ""),
            "location": raw.data.get("location", ""),
            "salary": raw.data.get("salary", ""),
            "experience_required": raw.data.get("experience_required", ""),
            "skills": raw.data.get("skills", []),
            "url": raw.data.get("url", ""),
            "source": raw.source,
            "posted_at": raw.data.get("posted_at") or datetime.utcnow(),
            "remote": raw.data.get("remote", False),
        }
