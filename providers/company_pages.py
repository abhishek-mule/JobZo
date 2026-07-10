import asyncio
import logging
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from providers.base import JobProvider, RawJob
from services.config import Config
from services.company_registry import get_all
from services.http_cache import get_cache, is_fresh, set_cache, html_unchanged
from ats import detect
from ats.base import ParsedDocument

logger = logging.getLogger("jobzo.company")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


class CompanyPagesProvider(JobProvider):
    name = "company_pages"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        cfg = Config.provider_config("company_pages")
        companies = cfg.get("companies", get_all())
        concurrency = cfg.get("concurrency", 5)
        sem = asyncio.Semaphore(concurrency)

        async def fetch(company: dict) -> tuple[list[RawJob], dict]:
            async with sem:
                return await self._fetch_company(company, keywords)

        tasks = [fetch(c) for c in companies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: list[RawJob] = []
        health_report: list[dict] = []

        for company, result in zip(companies, results):
            if isinstance(result, Exception):
                logger.warning("Company %s failed: %s", company["name"], result)
                health_report.append({"company": company["name"], "status": "error", "error": str(result), "jobs": 0})
                continue
            jobs, report = result
            all_jobs.extend(jobs)
            health_report.append(report)

        self._print_health(health_report)
        logger.info("Company pages: %d jobs from %d companies", len(all_jobs), len(companies))
        return all_jobs

    async def _fetch_company(
        self, company: dict, keywords: list[str] | None
    ) -> tuple[list[RawJob], dict]:
        name = company["name"]
        target_url = company.get("job_listing_url") or company["careers_url"]
        company_keywords = company.get("tags", [])

        start = time.time()

        if is_fresh(target_url):
            logger.debug("Cache fresh for %s, skipping fetch", name)
            return [], {"company": name, "status": "cached", "jobs": 0, "time": 0}

        html, status_code, headers = await self._fetch_html(target_url)

        elapsed = int((time.time() - start) * 1000)

        if status_code != 200 or not html or len(html) < 500:
            html = await self._fetch_with_playwright(target_url)
            if not html or len(html) < 500:
                logger.warning("Empty page for %s", name)
                return [], {"company": name, "status": "empty", "jobs": 0, "time": elapsed}

        parser = detect(target_url)
        doc = ParsedDocument(html=html, url=target_url)
        result = await parser.parse(doc)

        matched_jobs = self._filter_jobs(result.jobs, company_keywords, keywords)

        set_cache(
            url=target_url,
            html=html,
            etag=headers.get("etag", ""),
            last_modified=headers.get("last_modified", ""),
            status=status_code,
            parser=parser.name,
            jobs_found=len(matched_jobs),
        )

        raw_jobs = []
        for j in matched_jobs:
            j.setdefault("company", name)
            raw_jobs.append(RawJob(
                source=f"company:{name}",
                data=j,
                raw_html=html,
            ))

        return raw_jobs, {
            "company": name,
            "status": "ok",
            "jobs": len(matched_jobs),
            "parser": parser.name,
            "confidence": result.confidence,
            "time": elapsed,
        }

    async def _fetch_html(self, url: str) -> tuple[str, int, dict]:
        headers = {"User-Agent": USER_AGENT}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                return resp.text, resp.status_code, dict(resp.headers)
        except Exception as e:
            logger.debug("httpx fetch failed for %s: %s", url, e)
            return "", 0, {}

    async def _fetch_with_playwright(self, url: str) -> str:
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            html = await page.content()
            await browser.close()
            await pw.stop()
            logger.info("Playwright fallback succeeded for %s", url)
            return html
        except Exception as e:
            logger.debug("Playwright fallback failed for %s: %s", url, e)
            return ""

    def _filter_jobs(
        self,
        jobs: list[dict],
        company_keywords: list[str],
        global_keywords: list[str] | None,
    ) -> list[dict]:
        if not company_keywords and not global_keywords:
            return jobs

        all_kw = set(k.lower() for k in (company_keywords or []))
        if global_keywords:
            all_kw.update(k.lower() for k in global_keywords)

        filtered = []
        for j in jobs:
            text = (j.get("title", "") + " " + j.get("description", "")).lower()
            if any(kw in text for kw in all_kw):
                filtered.append(j)

        return filtered

    def _print_health(self, report: list[dict]):
        logger.info("--- Company Health ---")
        for r in report:
            status_icon = {"ok": "OK", "cached": "CA", "empty": "EM", "error": "ER"}.get(r["status"], "??")
            jobs = r.get("jobs", 0)
            parser = r.get("parser", "")
            confidence = r.get("confidence", "")
            time_ms = r.get("time", 0)
            logger.info(
                "  %s %-20s %2d jobs  %-10s  conf=%s  %dms",
                status_icon, r["company"], jobs, parser, str(confidence), time_ms,
            )
