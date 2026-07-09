import logging
import re
from datetime import datetime, timezone
import feedparser
import httpx

from providers.base import JobProvider, RawJob
from services.config import Config

logger = logging.getLogger("jobzo.rss")


def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class RSSProvider(JobProvider):
    name = "rss"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        cfg = Config.provider_config("rss")
        urls = cfg.get("urls", [])
        jobs: list[RawJob] = []
        errors: int = 0

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                    for entry in feed.entries[:30]:
                        raw = self._entry_to_raw(url, entry)
                        if raw:
                            jobs.append(raw)
                except Exception as e:
                    logger.warning("RSS error for %s: %s", url, e)
                    errors += 1

        logger.info("RSS: %d jobs from %d sources (%d errors)", len(jobs), len(urls), errors)
        return jobs

    def _entry_to_raw(self, feed_url: str, entry: dict) -> RawJob | None:
        title = entry.get("title", "")
        link = entry.get("link", "")
        if not title or not link:
            return None

        summary = _strip_html(entry.get("summary", entry.get("description", "")))
        published = entry.get("published_parsed")
        posted_at = None
        if published:
            posted_at = datetime(*published[:6], tzinfo=timezone.utc)

        # Extract article URL from description if present (HN RSS pattern)
        article_url = link
        article_match = re.search(r"Article URL:\s*(https?://\S+)", summary)
        if article_match:
            article_url = article_match.group(1)

        company, clean_title = self._extract_company_and_title(title)

        data = {
            "company": company,
            "title": clean_title,
            "description": summary,
            "location": "",
            "salary": "",
            "experience_required": "",
            "skills": [],
            "url": article_url,
            "posted_at": posted_at,
            "remote": "remote" in title.lower() or "remote" in summary.lower(),
        }

        return RawJob(source=f"rss:{feed_url}", data=data, raw_html=summary)

    def _extract_company_and_title(self, title: str) -> tuple[str, str]:
        company = ""
        clean = title.strip()

        # Pattern: "Company (YC Year) – Job Title – Location"
        for sep in [" – ", " — ", " - ", " | "]:
            if sep in title:
                parts = title.split(sep)
                company = parts[0].strip()
                clean = parts[1].strip() if len(parts) > 1 else clean
                break

        # Clean YC batch suffix from company name
        company = re.sub(r"\s*\(YC\s*\w+\d*\)", "", company).strip()

        # Remove "Is Hiring" suffix from clean title
        clean = re.sub(r"\s+Is\s+Hiring.*", "", clean, flags=re.IGNORECASE).strip()

        # If no separator found and title is "Company (YC Batch) Is Hiring", use company as fallback
        if not company:
            match = re.match(r"^(.+?)\s*(?:\(YC\s*\w+\d*\)|Is\s+Hiring)", title, re.IGNORECASE)
            if match:
                company = match.group(1).strip()

        # If still empty company, use first meaningful word cluster
        if not company:
            company = clean.split()[0] if clean.split() else ""

        return company, clean
