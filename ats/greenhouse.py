import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ats.base import ATSParser, ParsedDocument, ParseResult

logger = logging.getLogger("jobzo.ats.greenhouse")


class GreenhouseParser(ATSParser):
    name = "greenhouse"
    confidence = 0.98

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = BeautifulSoup(doc.html, "lxml")
        jobs: list[dict] = []
        seen: set[str] = set()

        base = doc.url.rstrip("/")

        # Greenhouse boards list jobs in <section> with job postings
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if not re.search(r"engineer|developer|sde|intern|software|backend|full.?stack", text, re.I):
                continue
            if not re.search(r"/jobs/\d+", href):
                continue

            full_url = href if href.startswith("http") else urljoin(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            dept = ""
            parent = link.find_parent(["div", "li", "section", "tr"])
            if parent:
                text_block = parent.get_text(separator=" ", strip=True)
                loc_match = re.search(r"(?:^|\s)([A-Z][a-zA-Z\s,]+(?:United States|India|UK|Canada|Australia|Germany|Singapore|Remote))", text_block)
                if loc_match:
                    location = loc_match.group(1).strip()[:100]

            job = {
                "title": text[:200],
                "url": full_url,
                "location": location,
                "description": parent.get_text(separator=" ", strip=True)[:1000] if parent else "",
                "company": self._extract_company(doc.url),
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
        m = re.search(r"greenhouse\.io/([^/]+)", url)
        if m:
            return m.group(1).replace("-", " ").replace("_", " ").title()
        return ""
