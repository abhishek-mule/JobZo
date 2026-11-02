"""YC Work at a Startup provider — scrapes Y Combinator's job board.

Fetches https://www.workatastartup.com/companies?layout=list-compact&sortBy=created_desc
and extracts the 30 most recent job listings from the Inertia page data.
"""

import logging
import re
from datetime import datetime, timezone

import httpx

from providers.base import JobProvider, RawJob

logger = logging.getLogger("jobzo.yc_waas")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

LISTING_URL = (
    "https://www.workatastartup.com/companies"
    "?layout=list-compact&sortBy=created_desc"
)

ROLE_TYPE_MAP = {
    "backend": "Backend",
    "frontend": "Frontend",
    "fullstack": "Full Stack",
    "machine learning": "ML",
    "data": "Data",
    "design": "Design",
    "product": "Product",
    "mobile": "Mobile",
    "devops": "DevOps",
    "security": "Security",
    "engineering": "Engineering",
    "sales": "Sales",
    "marketing": "Marketing",
    "operations": "Operations",
}


class YCWorkAtAStartupProvider(JobProvider):
    name = "yc_workatastartup"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
        jobs: list[RawJob] = []
        async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
            try:
                resp = await client.get(LISTING_URL)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("YC WaaS fetch failed: %s", e)
                return jobs

            page_data = self._extract_page_data(resp.text)
            if not page_data:
                logger.warning("YC WaaS: no page data found in HTML")
                return jobs

            raw_jobs = page_data.get("props", {}).get("jobs", [])
            logger.info("YC WaaS: %d jobs from page data", len(raw_jobs))
            for j in raw_jobs:
                parsed = self._parse_job(j)
                if parsed:
                    jobs.append(parsed)

            logger.info("YC WaaS: %d parsed jobs", len(jobs))
            return jobs

    def _extract_page_data(self, html: str) -> dict | None:
        import json as _json
        patterns = [
            r"data-page='({.*?})'",
            r'data-page="({.*?})"',
        ]
        for pat in patterns:
            match = re.search(pat, html, re.DOTALL)
            if match:
                raw = match.group(1)
                raw = raw.replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&")
                try:
                    return _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
        return None

    def _parse_job(self, j: dict) -> RawJob | None:
        title = j.get("title", "")
        company = j.get("companyName", "")
        if not title or not company:
            return None

        location = j.get("location", "")
        salary = j.get("salary", "")
        role_type = j.get("roleType", "")

        yc_batch = j.get("companyBatch", "")
        one_liner = j.get("companyOneLiner", "")
        description = f"{one_liner}\nBatch: {yc_batch}\nRole: {role_type}"

        skills = []
        title_lower = title.lower()
        for keyword, canonical in ROLE_TYPE_MAP.items():
            if keyword in title_lower:
                skills.append(canonical)
        if role_type and role_type.lower() not in title_lower:
            skills.append(role_type)

        apply_url = j.get("applyUrl", "")
        slug = j.get("companySlug", "")
        if not apply_url and slug:
            apply_url = f"https://www.workatastartup.com/companies/{slug}"

        raw_data = {
            "company": company,
            "title": title,
            "description": description,
            "location": location,
            "salary": salary,
            "skills": skills,
            "url": apply_url,
            "remote": "remote" in location.lower(),
            "source": self.name,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "id": j.get("id"),
            "yc_batch": yc_batch,
            "job_type": j.get("jobType", ""),
            "role_type": role_type,
            "company_slug": slug,
            "company_logo": j.get("companyLogoUrl", ""),
            "company_last_active": j.get("companyLastActiveAt", ""),
        }

        return RawJob(source=self.name, data=raw_data)
