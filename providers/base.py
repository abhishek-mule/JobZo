from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime


@dataclass
class RawJob:
    source: str
    data: dict
    raw_html: str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)


class JobProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        ...

    def normalize(self, raw: RawJob) -> dict:
        return {
            "company": raw.data.get("company", ""),
            "title": raw.data.get("title", ""),
            "description": raw.data.get("description", ""),
            "location": raw.data.get("location", ""),
            "salary": raw.data.get("salary", ""),
            "experience_required": raw.data.get("experience_required", ""),
            "skills": raw.data.get("skills", []),
            "url": raw.data.get("url", ""),
            "source": raw.source,
            "posted_at": raw.data.get("posted_at"),
            "remote": raw.data.get("remote", False),
        }
