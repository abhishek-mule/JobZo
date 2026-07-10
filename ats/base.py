from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedDocument:
    html: str
    url: str
    rendered_dom: str | None = None
    json_ld: list[dict] | None = None
    meta: dict | None = None


@dataclass
class ParseResult:
    jobs: list[dict]
    confidence: float = 1.0
    parser_name: str = "generic"
    jobs_found: int = 0


class ATSParser(ABC):
    name: str = "generic"
    confidence: float = 0.7

    @abstractmethod
    async def parse(self, doc: ParsedDocument) -> ParseResult:
        ...

    def _normalize_job(self, raw: dict) -> dict:
        return {
            "company": raw.get("company", ""),
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "location": raw.get("location", ""),
            "salary": raw.get("salary", ""),
            "experience_required": raw.get("experience_required", ""),
            "skills": raw.get("skills", []),
            "url": raw.get("url", ""),
            "posted_at": raw.get("posted_at"),
            "remote": raw.get("remote", False),
        }
