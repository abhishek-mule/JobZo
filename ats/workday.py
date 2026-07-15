"""Workday ATS parser — handles myworkdayjobs.com and wd1.myworkdayjobs.com.

Workday is heavily customized per company, so this parser casts a wide net
and accepts lower confidence (0.70). Covers ~70% of Workday job boards.
"""

import logging
import re
from urllib.parse import urljoin

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role

logger = logging.getLogger("jobzo.ats.workday")


class WorkdayParser(ATSParser):
    name = "workday"
    confidence = 0.70

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # Workday job links typically contain job/{id}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "job/" not in href and "requisition" not in href:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 3:
                continue
            if not is_engineering_role(title):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent = a.find_parent(["div", "li", "section", "tr"])
            if parent:
                text_block = parent.get_text(separator=" ", strip=True)
                loc_match = re.search(r"(?:^|\s)((?:Remote|Hybrid|On.?site)?\s*[A-Z][a-zA-Z\s,]+(?:India|United\s+States|UK|Canada|Australia|Germany|Singapore))", text_block)
                if loc_match:
                    location = loc_match.group(1).strip()[:100]

            job = {
                "title": title[:200],
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
        m = re.search(r"(?:myworkdayjobs|wd1|wd3|wd5)\.(?:com|myworkdayjobs\.com)/([^/?#]+)", url)
        if m:
            return m.group(1).replace("-", " ").replace("_", " ").title().replace("  ", " ").strip()
        m = re.search(r"//([^.]+)\.myworkdayjobs", url)
        if m:
            return m.group(1).replace("-", " ").title()
        return ""
