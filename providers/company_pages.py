import asyncio
import logging
import os
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from database.connection import get_session
from database.models import PollRun, HiringChannel
from providers.base import JobProvider, RawJob
from services.config import Config
from services.company_registry import get_all, get_by_alias
from services.http_cache import get_cache, is_fresh, set_cache, html_unchanged
from ats import detect
from ats.apis import fetch_api_jobs
from ats.base import ParsedDocument
from ats.discovery import Discovery, discover, load_discovery, save_discovery
from ats.validation import validate_candidates

logger = logging.getLogger("jobzo.company")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def elapsed_sec(start: float) -> int:
    """Elapsed milliseconds since `start` (time.time())."""
    import time as _t
    return int((_t.time() - start) * 1000)


class CompanyPagesProvider(JobProvider):
    """Collection for companies defined in the registry.

    Pipeline per target:
      1. ATS discovery (cached in ats_discovery table, separate from YAML)
      2. Structured ATS API when available (greenhouse/lever/smartrecruiters/
         workday-CXS) — trusted input, source_kind="api"
      3. HTML parse otherwise — then strict validation gate (ats.validation)
         rejects nav-links/product pages/blog junk
      4. Playwright render only as a fallback for JS-heavy pages
    """

    _pw_lock = asyncio.Lock()
    name = "company_pages"

    async def search(self, keywords: list[str] | None = None) -> list[RawJob]:
        cfg = Config.provider_config("company_pages")
        companies_raw = cfg.get("companies", get_all())

        # Flatten companies into fetch targets (one per hiring channel)
        targets = []
        for c in companies_raw:
            cid = getattr(c, "id", "")
            name = getattr(c, "name", "")
            c_status = getattr(c, "status", "active")
            if c_status in ("broken", "paused", "archived"):
                logger.debug("Skipping %s (status=%s)", name, c_status)
                continue
            ctags = getattr(c, "tags", []) or []
            channels = getattr(c, "hiring_channels", []) or []
            if channels:
                for ch in channels:
                    if not ch.is_active:
                        continue
                    targets.append({
                        "company_id": cid,
                        "name": name,
                        "channel": ch.channel_name,
                        "url": ch.job_listing_url or ch.careers_url,
                        "ats": ch.ats or getattr(c, "ats", ""),
                        "tags": ctags,
                    })
            else:
                targets.append({
                    "company_id": cid,
                    "name": name,
                    "channel": None,
                    "url": getattr(c, "job_listing_url", None) or getattr(c, "careers_url", ""),
                    "ats": getattr(c, "ats", ""),
                    "tags": ctags,
                })

        concurrency = cfg.get("concurrency", 5)
        sem = asyncio.Semaphore(concurrency)

        async def fetch_one(target: dict) -> tuple[list[RawJob], dict]:
            async with sem:
                return await self._fetch_target(target, keywords)

        tasks = [fetch_one(t) for t in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: list[RawJob] = []
        health_report: list[dict] = []

        for target, result in zip(targets, results):
            label = f"{target['name']}/{target['channel']}" if target['channel'] else target['name']
            if isinstance(result, Exception):
                logger.warning("Target %s failed: %s", label, result)
                health_report.append({
                    "company": label, "status": "error", "jobs": 0,
                    "diagnostic": str(result)[:80], "suggestion": "Check logs for details",
                })
                continue
            jobs, report = result
            all_jobs.extend(jobs)
            health_report.append(report)

        self._print_health(health_report)
        logger.info("Company pages: %d jobs from %d targets", len(all_jobs), len(targets))
        return all_jobs

    async def _fetch_target(
        self, target: dict, keywords: list[str] | None
    ) -> tuple[list[RawJob], dict]:
        name = target["name"]
        company_id = target["company_id"]
        channel = target["channel"]
        target_url = target["url"]
        company_keywords = target.get("tags", [])

        label = f"{name}/{channel}" if channel else name

        start = time.time()
        poll_start = datetime.utcnow()

        if not target_url:
            return [], {"company": label, "status": "empty", "jobs": 0,
                         "diagnostic": "no URL configured", "time": 0}

        cache_entry = get_cache(target_url)
        if cache_entry and is_fresh(target_url):
            age_h = int((datetime.utcnow() - cache_entry.fetched_at).total_seconds() / 3600)
            diag = f"cached {age_h}h ago"
            if cache_entry.jobs_found == 0:
                diag += " (0 jobs — retry in 1h)"
            logger.debug("Cache fresh for %s, skipping fetch", label)
            self._record_poll(company_id, poll_start, "cached", 0, cache_entry.parser,
                              cache_entry.jobs_found, 0, 0, 0)
            return [], {
                "company": label, "status": "cached", "jobs": cache_entry.jobs_found,
                "parser": cache_entry.parser, "diagnostic": diag, "time": 0,
            }

        # ── 1. ATS discovery (cached) ───────────────────────────────────
        discovery = load_discovery(target_url)

        # ── 2. Structured API path (no HTML) ────────────────────────────
        html, status_code, headers = "", 200, {}
        used_playwright = False
        used_api = False
        jobs: list[dict] = []
        parse_errors: list[str] = []
        parser_name = "generic"
        confidence = 0.0

        if discovery and discovery.has_api and discovery.board_token:
            api_jobs = await fetch_api_jobs(discovery.ats, discovery.board_token, name,
                                       api_url=discovery.api_url)
            if api_jobs:
                jobs, used_api = api_jobs, True
                parser_name = discovery.ats
                confidence = 0.95

        # ── 3. HTML path (API missing/failed) ───────────────────────────
        if not jobs:
            if not html:
                html, status_code, headers = await self._fetch_html(target_url)
                if status_code != 200 or not html or len(html) < 500:
                    html = await self._fetch_with_playwright(target_url)
                    used_playwright = True
                    if not html or len(html) < 500:
                        reason = f"HTTP {status_code}" if status_code else "Network error"
                        if used_playwright:
                            reason += " (Playwright also failed)"
                        suggestion = "Check URL or add Playwright fallback"
                        if status_code in (403, 429):
                            suggestion = "Blocked by anti-bot (Cloudflare?) — needs Playwright or proxy"
                        elif status_code == 404:
                            suggestion = "URL changed — find new career page"
                        logger.warning("Empty page for %s: %s", label, reason)
                        self._record_poll(company_id, poll_start, "empty", elapsed_sec(start),
                                          "", 0, 0, 0, 0, error_message=reason)
                        return [], {
                            "company": label, "status": "empty", "jobs": 0,
                            "diagnostic": reason, "suggestion": suggestion, "time": elapsed_sec(start),
                        }

            # Discover ATS from the careers page if we still don't know it
            if not discovery or discovery.ats == "unknown":
                discovery = discover(target_url, name, html=html,
                                     ats_hint=target.get("ats", ""))
                save_discovery(target_url, company_id, name, discovery)

            # If discovery unlocked an API after all, try it
            if not used_api and discovery.has_api and discovery.board_token:
                api_jobs = await fetch_api_jobs(discovery.ats, discovery.board_token, name,
                                       api_url=discovery.api_url)
                if api_jobs:
                    jobs, used_api = api_jobs, True
                    parser_name = discovery.ats
                    confidence = 0.95

            if not jobs:
                parser = detect(target_url)
                doc = ParsedDocument(html=html, url=target_url)
                result = await parser.parse(doc)
                parse_errors = list(result.errors)
                parser_name = parser.name
                confidence = result.confidence
                jobs = result.jobs

                # 0 jobs on a JS-heavy page → render once with Playwright
                if not jobs and not used_playwright:
                    rendered = await self._fetch_with_playwright(target_url)
                    if rendered:
                        result2 = await parser.parse(
                            ParsedDocument(html=rendered, url=target_url))
                        if result2.jobs:
                            jobs = result2.jobs
                            used_playwright = True
                            parse_errors = list(result2.errors)

        # ── 4. Strict validation gate (junk never becomes a Job) ────────
        valid_jobs, rejected = validate_candidates(
            jobs, source_kind="api" if used_api else "html")
        matched_jobs = self._filter_jobs(valid_jobs, company_keywords, keywords)

        elapsed = elapsed_sec(start)
        html_kb = len(html) // 1024 if html else 0

        # ── 5. Dedupe + build RawJobs ────────────────────────────────────
        new_jobs = 0
        duplicate_jobs = 0
        raw_jobs = []
        for j in matched_jobs:
            j.setdefault("company", name)
            if channel:
                j["channel"] = channel
            url = j.get("url", "")
            is_new = not self._job_exists(url)
            if is_new:
                new_jobs += 1
            else:
                duplicate_jobs += 1
            raw_jobs.append(RawJob(
                source=f"company:{name}",
                data=j,
                raw_html=html,
            ))

        set_cache(
            url=target_url,
            html=html,
            etag=headers.get("etag", ""),
            last_modified=headers.get("last_modified", ""),
            status=status_code,
            parser=parser_name,
            jobs_found=len(matched_jobs),
        )

        # Collection uncertainty ≠ "company has 0 jobs"
        if matched_jobs:
            poll_status = "success"
        elif discovery.ats == "unknown":
            poll_status = "empty"
        else:
            poll_status = "empty"
        self._record_poll(company_id, poll_start, poll_status, elapsed,
                          parser_name, len(matched_jobs), new_jobs, duplicate_jobs,
                          len(parse_errors), confidence=confidence)

        diagnostic_parts = []
        if html_kb:
            diagnostic_parts.append(f"{html_kb}KB")
        if used_api:
            diagnostic_parts.append(f"API:{discovery.ats}")
        if used_playwright:
            diagnostic_parts.append("Playwright")
        if discovery.ats != "unknown":
            diagnostic_parts.append(f"ATS:{discovery.ats}")
        if parse_errors:
            diagnostic_parts.append(f"errors: {'; '.join(parse_errors[:2])}")
        if rejected:
            diagnostic_parts.append(f"rejected {len(rejected)} junk candidates")
        if not matched_jobs:
            diagnostic_parts.append("0 valid jobs — uncertain" if discovery.ats != "unknown" else "no ATS detected")
        elif len(matched_jobs) < len(jobs):
            diagnostic_parts.append(f"{len(matched_jobs)}/{len(jobs)} after validation+filter")

        return raw_jobs, {
            "company": name,
            "status": "ok" if matched_jobs else "empty",
            "jobs": len(matched_jobs),
            "total_found": len(jobs),
            "parser": parser_name,
            "confidence": confidence,
            "diagnostic": ", ".join(diagnostic_parts),
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
        if os.environ.get("JOBZO_NO_PLAYWRIGHT"):
            return ""
        async with self._pw_lock:
            try:
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    html = await page.content()
                finally:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    try:
                        await pw.stop()
                    except Exception:
                        pass
                if html:
                    logger.info("Playwright fallback succeeded for %s", url)
                return html
            except BaseException as e:
                logger.debug("Playwright fallback failed for %s: %s", url, e)
                return ""

    def _filter_jobs(
        self,
        jobs: list[dict],
        company_keywords: list[str],
        global_keywords: list[str] | None,
    ) -> list[dict]:
        """Apply user search keywords only.

        Company tags are descriptions of the business (e.g. "fintech",
        "payments"), NOT job keywords — filtering real roles like "Backend
        Engineer" against them was dropping legitimate jobs. The strict
        validation gate (ats.validation) is what keeps junk out now.
        """
        if not global_keywords:
            return jobs

        all_kw = set(k.lower() for k in global_keywords)

        filtered = []
        for j in jobs:
            text = (j.get("title", "") + " " + j.get("description", "")).lower()
            if any(kw in text for kw in all_kw):
                filtered.append(j)

        return filtered

    def _job_exists(self, url: str) -> bool:
        """Check if a job URL already exists in the database."""
        try:
            from database.connection import get_session
            from database.models import Job
            session = get_session()
            try:
                return session.query(Job.id).filter(Job.url == url).first() is not None
            finally:
                session.close()
        except Exception:
            return False

    def _record_poll(
        self, company_id: str, started_at: datetime, status: str,
        duration_ms: int, parser: str, jobs_found: int,
        jobs_new: int, jobs_duplicate: int, parse_errors: int,
        error_message: str = "", confidence: float = None,
    ):
        """Persist a PollRun event for this company poll."""
        if not company_id:
            return
        try:
            session = get_session()
            try:
                session.add(PollRun(
                    company_id=company_id,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    status=status,
                    duration_ms=duration_ms,
                    parser=parser,
                    confidence=confidence,
                    jobs_found=jobs_found,
                    jobs_new=jobs_new,
                    jobs_duplicate=jobs_duplicate,
                    parse_errors=parse_errors,
                    error_message=error_message,
                ))
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.debug("Failed to record poll for %s: %s", company_id, e)

    def _print_health(self, report: list[dict]):
        logger.info("--- Company Health ---")
        for r in report:
            status_icon = {"ok": "OK", "cached": "CA", "empty": "EM", "error": "ER"}.get(r["status"], "??")
            jobs = r.get("jobs", 0)
            parser = r.get("parser", "")
            confidence = r.get("confidence", "")
            time_ms = r.get("time", 0)
            diag = r.get("diagnostic", "")
            suggestion = r.get("suggestion", "")
            if suggestion:
                diag = f"{diag} — {suggestion}" if diag else suggestion
            logger.info(
                "  %s %-20s %2d jobs  %-12s conf=%-5s %5dms  %s",
                status_icon, r["company"], jobs, parser, str(confidence) if confidence != "" else "-",
                time_ms, diag,
            )
