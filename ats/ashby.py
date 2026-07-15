"""Ashby ATS parser — handles ashbyhq.com job boards.

Job listings are typically in <a data-ashby-job-id="..."> or <li data-ashby-job="...">.
"""

import logging
import re

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role, parent_text

logger = logging.getLogger("jobzo.ats.ashby")


class AshbyParser(ATSParser):
    name = "ashby"
    confidence = 0.90

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # Ashby uses data-ashby-job-id or data-ashby-job-category attributes
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if not is_engineering_role(text):
                continue
            # Ashby job links contain /jobs/ or a query param with job ID
            if "/jobs/" not in href and "ashbyhq.com" not in href and not href.startswith("/"):
                continue
            if "ashbyhq.com" not in href and not href.startswith("/"):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent_container = a.find_parent(["div", "li", "section", "ul"])
            if parent_container:
                block = parent_container.get_text(separator=" ", strip=True)
                loc_match = re.search(r"(?:^|\s)((?:Remote|Hybrid|On.?site)?\s*[A-Z][a-zA-Z\s,]*(?:India|United\s+States|Bangalore|Mumbai|Remote))", block)
                if loc_match:
                    location = loc_match.group(1).strip()[:100]

            job = {
                "title": text[:200],
                "url": full_url,
                "location": location,
                "description": parent_container.get_text(separator=" ", strip=True)[:1000] if parent_container else "",
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
        m = re.search(r"ashbyhq\.com/(?:jobs/)?@?([^/?#]+)", url)
        if m:
            return m.group(1).replace("-", " ").replace("_", " ").title().strip()
        m = re.search(r"//([^.]+)\.ashbyhq", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        return ""
