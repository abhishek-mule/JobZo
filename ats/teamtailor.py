"""Teamtailor ATS parser — handles jobs.teamtailor.com and careers.teamtailor.com.

Job listings use data-controller="jobs" or <a class="job__link">.
"""

import logging
import re

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role

logger = logging.getLogger("jobzo.ats.teamtailor")


class TeamtailorParser(ATSParser):
    name = "teamtailor"
    confidence = 0.80

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # Teamtailor uses various job listing patterns
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            # Teamtailor job links typically contain "jobs/" or "positions/"
            if "/jobs/" not in href and "/positions/" not in href:
                continue
            if not is_engineering_role(text):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent = a.find_parent(["div", "li", "section"])
            if parent:
                block = parent.get_text(separator=" ", strip=True)
                loc_match = re.search(r"(?:^|\s)((?:Remote|Hybrid|On.?site)?\s*[A-Z][a-zA-Z\s]*(?:India|United\s+States|Stockholm|Berlin|London|Remote))", block)
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
        m = re.search(r"(?:jobs|careers)\.(?:teamtailor|teamtaylor)\.com/([^/?#]+)", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        m = re.search(r"//([^.]+)\.teamtailor", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        return ""
