import logging
import json
from pathlib import Path
from datetime import datetime

from providers.base import JobProvider, RawJob

logger = logging.getLogger("jobzo.manual")


class ManualProvider(JobProvider):
    name = "manual"
    _queue_path = Path(__file__).parent.parent / "cache" / "jobs" / "manual_queue.json"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        jobs: list[RawJob] = []
        if not self._queue_path.exists():
            return jobs

        try:
            with open(self._queue_path) as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read manual queue: %s", e)
            return jobs

        for entry in entries:
            data = {
                "company": entry.get("company", ""),
                "title": entry.get("title", ""),
                "description": entry.get("description", ""),
                "location": entry.get("location", ""),
                "salary": entry.get("salary", ""),
                "experience_required": entry.get("experience_required", ""),
                "skills": entry.get("skills", []),
                "url": entry.get("url", ""),
                "posted_at": entry.get("posted_at"),
                "remote": entry.get("remote", False),
            }
            jobs.append(RawJob(source="manual", data=data, raw_html=entry.get("raw_html", "")))

        self._queue_path.unlink(missing_ok=True)
        logger.info("Manual: %d jobs imported", len(jobs))
        return jobs

    @staticmethod
    def add_to_queue(
        url: str,
        company: str = "",
        title: str = "",
        description: str = "",
        location: str = "",
        salary: str = "",
        remote: bool = False,
    ):
        existing_urls = set()
        path = ManualProvider._queue_path
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            with open(path) as f:
                queue = json.load(f)
            existing_urls = {e.get("url", "") for e in queue}
        else:
            queue = []

        if url in existing_urls:
            logger.info("URL already in queue: %s", url)
            return

        entry = {
            "url": url,
            "company": company,
            "title": title,
            "description": description,
            "location": location,
            "salary": salary,
            "experience_required": "",
            "skills": [],
            "posted_at": datetime.utcnow().isoformat(),
            "remote": remote,
            "raw_html": "",
        }

        queue.append(entry)
        with open(path, "w") as f:
            json.dump(queue, f, indent=2)

        logger.info("Added job to manual queue: %s", url)
