"""BambooHR ATS parser — handles {company}.bamboohr.com/jobs/.

Job listings are in <tr> rows within a <table> or <tbody> element.
"""

import logging
import re

from ats.base import ATSParser, ParsedDocument, ParseResult
from ats._base import make_soup, absolute_url, is_engineering_role

logger = logging.getLogger("jobzo.ats.bamboohr")


class BambooHRParser(ATSParser):
    name = "bamboohr"
    confidence = 0.80

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = make_soup(doc.html)
        jobs: list[dict] = []
        seen: set[str] = set()
        company = self._extract_company(doc.url)

        # BambooHR lists jobs in <tr> with job links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            # BambooHR job URLs contain /jobs/view.php?id=
            if "/jobs/view.php" not in href and "bamboohr" not in href and "job" not in href.lower():
                continue
            if not is_engineering_role(text):
                continue

            full_url = absolute_url(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            row = a.find_parent("tr")
            if row:
                cells = row.find_all("td")
                for cell in cells:
                    cell_text = cell.get_text(strip=True)
                    if cell_text and len(cell_text) < 100 and not cell_text.startswith("$") and cell_text != text:
                        if any(c.isupper() for c in cell_text[:3]):
                            location = cell_text[:100]
                            break

            job = {
                "title": text[:200],
                "url": full_url,
                "location": location,
                "description": row.get_text(separator=" ", strip=True)[:1000] if row else "",
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
        m = re.search(r"//([^.]+)\.bamboohr", url)
        if m:
            return m.group(1).replace("-", " ").title().strip()
        return ""
