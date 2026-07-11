import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ats.base import ATSParser, ParsedDocument, ParseResult

logger = logging.getLogger("jobzo.ats.lever")


class LeverParser(ATSParser):
    name = "lever"
    confidence = 0.95

    async def parse(self, doc: ParsedDocument) -> ParseResult:
        soup = BeautifulSoup(doc.html, "lxml")
        jobs: list[dict] = []
        seen: set[str] = set()

        postings = soup.find_all("a", class_=re.compile(r"posting", re.I))
        if not postings:
            postings = soup.find_all("a", href=True)

        for link in postings:
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if not re.search(r"engineer|developer|sde|intern|software|backend|full.?stack", text, re.I):
                continue
            if "lever.co" not in href and not href.startswith("/"):
                continue

            full_url = href if href.startswith("http") else urljoin(doc.url, href)
            if full_url in seen:
                continue
            seen.add(full_url)

            title_el = link.find(["h2", "h3", "h4", "h5", "h6", "span", "div"], class_=re.compile(r"title|role|position", re.I))
            if not title_el:
                title_el = link.find(["h2", "h3", "h4", "h5", "h6"])
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            title = re.sub(r"\s+", " ", title).partition("Hybrid")[0].partition("Remote")[0].partition("On-site")[0].partition("—")[0].partition("–")[0].partition("/")[0].strip()[:200]

            location = ""
            dept = ""
            for cls_pattern in [r"location", r"office", r"commitment", r"workplace"]:
                el = link.find(class_=re.compile(cls_pattern, re.I))
                if el:
                    loc_text = el.get_text(strip=True)
                    if loc_text and len(loc_text) < 100 and not loc_text.startswith("$"):
                        location = loc_text
                        break
            dept_el = link.find(class_=re.compile(r"department|team|category|division", re.I))
            if dept_el:
                dept = dept_el.get_text(strip=True)[:100]

            description = ""
            parent = link.find_parent(["div", "li", "section", "tr"])
            if parent:
                description = parent.get_text(separator=" ", strip=True)[:1000]

            company = self._extract_company(doc.url)

            job = {
                "title": title,
                "url": full_url,
                "location": location,
                "description": description,
                "company": company,
                "department": dept,
            }
            jobs.append(self._normalize_job(job))

        return ParseResult(
            jobs=jobs,
            confidence=self.confidence,
            parser_name=self.name,
            jobs_found=len(jobs),
        )

    def _extract_company(self, url: str) -> str:
        m = re.search(r"(?:jobs\.)?lever\.co/([^/?#]+)", url)
        if m:
            name = m.group(1).replace("-", " ").replace("_", " ").title()
            name = re.sub(r"\b\d+\b", "", name).strip()
            name = re.sub(r"\s+", " ", name).strip()
            known_mappings = {
                "Levelai": "Level AI",
                "Leverdemo ": "Lever ",
            }
            return known_mappings.get(name, name)
        return ""
