import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ats.base import ATSParser, ParsedDocument, ParseResult

logger = logging.getLogger("jobzo.ats.generic")


class GenericParser(ATSParser):
    name = "generic"
    confidence = 0.70

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = BeautifulSoup(doc.html, "lxml")
        jobs: list[dict] = []
        seen: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True)
            if not text or len(text) < 5:
                continue
            if not re.search(r"engineer|developer|sde|intern|software|backend|full.?stack|java|spring", text, re.I):
                continue
            if re.search(r"about|login|sign|blog|team|press|contact", href, re.I):
                continue

            full_url = href if href.startswith("http") else urljoin(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            location = ""
            parent = link.find_parent(["div", "li", "section", "tr", "article"])
            description = parent.get_text(separator=" ", strip=True)[:1000] if parent else ""

            job = {
                "title": text[:200],
                "url": full_url,
                "location": location,
                "description": description,
                "company": self._extract_company(doc.url),
                "remote": "remote" in text.lower(),
            }
            jobs.append(self._normalize_job(job))

        html_quality = 0.6 if len(doc.html) < 5000 else 1.0
        extraction_quality = min(1.0, len(jobs) / 10) if jobs else 0.3
        final_confidence = self.confidence * html_quality * extraction_quality

        return ParseResult(
            jobs=jobs,
            confidence=round(final_confidence, 2),
            parser_name=self.name,
            jobs_found=len(jobs),
        )

    def _extract_company(self, url: str) -> str:
        m = re.search(r"https?://(?:www\.)?([^.]+)", url)
        if m:
            return m.group(1).capitalize()
        return ""
