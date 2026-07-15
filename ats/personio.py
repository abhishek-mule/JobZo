"""Personio ATS parser — handles jobs.personio.de and jobs.personio.com.

Job listings are in <a> elements with href containing "job/" or "position/".
Personio is common among European companies.
"""

import logging
import re

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role

logger = logging.getLogger("jobzo.ats.personio")


class PersonioParser(ATSParser):
    name = "personio"
    confidence = 0.80

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # Personio uses <a> tags with job/position in href
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if "/job/" not in href and "/position/" not in href and "/stellen" not in href.lower():
                continue
            if not is_engineering_role(text):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent = a.find_parent(["div", "li", "section", "tr"])
            if parent:
                block = parent.get_text(separator=" ", strip=True)
                loc_match = re.search(r"(?:^|\s)((?:Remote|Hybrid|On.?site)?\s*[A-Z][a-zA-Z\s]*(?:Germany|Berlin|Munich|Hamburg|Cologne|Frankfurt|Stuttgart|Remote|India|United\s+States|UK|London))", block)
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
        m = re.search(r"personio\.(?:de|com)/(?:jobs/)?(?:find\?)?[^/?#]*?([^/?#]+)", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        m = re.search(r"//([^.]+)\.personio", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        return ""
