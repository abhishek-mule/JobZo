import logging
import re
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

from providers.base import JobProvider, RawJob
from services.config import Config

logger = logging.getLogger("jobzo.company")


class CompanyPagesProvider(JobProvider):
    name = "company_pages"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        cfg = Config.provider_config("company_pages")
        targets = cfg.get("targets", [])
        jobs: list[RawJob] = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for url in targets:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    html = resp.text
                    parsed = self._parse_page(url, html)
                    jobs.extend(parsed)
                except Exception as e:
                    logger.warning("Company page error for %s: %s", url, e)

        logger.info("Company pages: %d jobs from %d targets", len(jobs), len(targets))
        return jobs

    def _parse_page(self, base_url: str, html: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs: list[RawJob] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)

            if not self._is_job_link(href, text):
                continue

            full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            data = {
                "company": self._extract_company_name(base_url),
                "title": text,
                "description": self._get_sibling_description(link),
                "location": "",
                "salary": "",
                "experience_required": "",
                "skills": [],
                "url": full_url,
                "posted_at": None,
                "remote": "remote" in text.lower(),
            }

            jobs.append(RawJob(source=f"company:{base_url}", data=data, raw_html=html))

        return jobs[:30]

    def _is_job_link(self, href: str, text: str) -> bool:
        if not text or len(text) < 5:
            return False
        job_keywords = [
            "engineer", "developer", "sde", "intern", "backend",
            "frontend", "fullstack", "full stack", "software",
            "spring", "java", "react",
        ]
        text_lower = text.lower()
        if not any(kw in text_lower for kw in job_keywords):
            return False
        exclude = ["about", "login", "sign", "blog", "team"]
        if any(x in href.lower() for x in exclude):
            return False
        return True

    def _get_sibling_description(self, link) -> str:
        parent = link.find_parent(["div", "li", "section"])
        if parent:
            return parent.get_text(strip=True)[:1000]
        return ""

    def _extract_company_name(self, url: str) -> str:
        match = re.search(r"https?://(?:www\.)?([^.]+)", url)
        if match:
            return match.group(1).capitalize()
        return ""
