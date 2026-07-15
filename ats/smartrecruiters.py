"""SmartRecruiters ATS parser — handles jobs.smartrecruiters.com.

Job listings are in <li class="job-listing"> or <div class="job-listing-item">
containing an <a> with the title and URL.
"""

import logging
import re

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role

logger = logging.getLogger("jobzo.ats.smartrecruiters")


class SmartRecruitersParser(ATSParser):
    name = "smartrecruiters"
    confidence = 0.85

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # SmartRecruiters lists jobs in container <li>/<div> with an <a> inside,
        # OR directly as <a> tags with job-listing class
        containers = soup.find_all(["li", "div", "a"], class_=re.compile(r"job.?listing", re.I))
        if not containers:
            containers = soup.find_all("a", href=True)

        for el in containers:
            if isinstance(el, str):
                continue

            if el.name == "a" and el.get("href"):
                link = el
            else:
                link = el.find("a", href=True)

            if not link or not link.get("href"):
                continue

            href = link["href"]
            text = link.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if not is_engineering_role(text):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent = el.find_parent(["div", "li", "section"])
            if parent:
                block = parent.get_text(separator=" ", strip=True)
                loc_match = re.search(
                    r"(?:^|\s)((?:Remote|Hybrid)?\s*[A-Z][a-zA-Z\s,]*(?:India|United\s+States|Bangalore|Mumbai|Pune|Remote))",
                    block,
                )
                if loc_match:
                    location = loc_match.group(1).strip()[:100]

            job = {
                "title": text[:200],
                "url": full_url,
                "location": location,
                "description": parent.get_text(separator=" ", strip=True)[:1000] if parent else "",
                "company": company,
            }
            jobs.append(self._normalize_job(job))

        return ParseResult(
            jobs=jobs,
            confidence=self.confidence,
            parser_name=self.name,
            jobs_found=len(jobs),
            collected=len(seen),
            valid=len(jobs),
        )

    def _extract_company(self, url: str) -> str:
        m = re.search(r"smartrecruiters\.com/([^/?#]+)", url)
        if m:
            return m.group(1).replace("-", " ").replace("_", " ").title().strip()
        return ""
