import logging
import re
import asyncio

import httpx
from bs4 import BeautifulSoup

from providers.base import RawJob

logger = logging.getLogger("jobzo.hn_scraper")

YC_PATTERNS = [
    re.compile(r"ycombinator\.com/companies/[^/]+/jobs/\S+"),
    re.compile(r"ycombinator\.com/jobs/\S+"),
]


def is_yc_job(url: str) -> bool:
    return any(p.search(url) for p in YC_PATTERNS)


async def scrape_yc_job(url: str) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            title_el = soup.find("h1") or soup.find("title")
            title = title_el.get_text(strip=True) if title_el else ""

            desc_section = (
                soup.find("div", class_=re.compile(r"(description|content|job-detail)", re.I))
                or soup.find("article")
                or soup.find("main")
                or soup
            )
            description = desc_section.get_text(strip=True, separator="\n")[:3000] if desc_section else ""

            meta = {}
            for li in soup.find_all("li"):
                text = li.get_text(strip=True)
                if "location" in text.lower():
                    meta["location"] = text.split(":", 1)[-1].strip() if ":" in text else text
                if "salary" in text.lower() or "compensation" in text.lower():
                    meta["salary"] = text.split(":", 1)[-1].strip() if ":" in text else text
                if "experience" in text.lower() or "years" in text.lower():
                    meta["experience"] = text.split(":", 1)[-1].strip() if ":" in text else text

            skill_section = soup.find(string=re.compile(r"(skills|requirements|technologies)", re.I))
            skills = []
            if skill_section:
                parent = skill_section.find_parent(["div", "section"])
                if parent:
                    for tag in parent.find_all(["li", "span", "code"]):
                        skill_text = tag.get_text(strip=True)
                        if skill_text and len(skill_text) < 50:
                            skills.append(skill_text)

            return {
                "title": title or "",
                "description": description,
                "location": meta.get("location", ""),
                "salary": meta.get("salary", ""),
                "experience_required": meta.get("experience", ""),
                "skills": skills[:20],
            }
    except Exception as e:
        logger.debug("Failed to scrape YC job page %s: %s", url, e)
        return None


async def enrich_yc_jobs(raw_jobs: list[RawJob]) -> list[RawJob]:
    enriched = []
    scrape_tasks = []

    for raw in raw_jobs:
        url = raw.data.get("url", "")
        if is_yc_job(url):
            scrape_tasks.append(_enrich_single(raw, url))
        else:
            enriched.append(raw)

    if scrape_tasks:
        results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, RawJob):
                enriched.append(r)
            elif isinstance(r, Exception):
                logger.debug("Scrape error: %s", r)

    return enriched


async def _enrich_single(raw: RawJob, url: str) -> RawJob:
    scraped = await scrape_yc_job(url)
    if scraped:
        raw.data.update(scraped)
        raw.raw_html = scraped.get("description", raw.raw_html)
        logger.info("Enriched YC job: %s (%s)", scraped.get("title"), url)
    return raw
