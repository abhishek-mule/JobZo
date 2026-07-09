import logging
import asyncio
from urllib.parse import urlparse
from pathlib import Path
from typing import Callable

from services.config import Config

logger = logging.getLogger("jobzo.browser")

RESUME_DIR = Path(__file__).parent.parent / "resumes"

KNOWN_ATS_DOMAINS = {
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "breezy.hr",
    "recruitee.com",
    "smartrecruiters.com",
    "icims.com",
    "jazzhr.com",
    "bamboohr.com",
    "paycom.com",
    "workday.com",
    "myworkdayjobs.com",
    "jobs.workday.com",
    "successfactors.com",
    "sap.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "careerbuilder.com",
    "ycombinator.com",
}


class BrowserAssistant:
    def __init__(self):
        cfg = Config.browser_config()
        self.headless = cfg.get("headless", False)
        self.slow_mo = cfg.get("slow_mo", 500)
        self.timeout = cfg.get("timeout", 30000)
        self.profile = cfg.get("profile", {})
        self._browser = None
        self._page = None

    async def start(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        launch_args = {
            "headless": self.headless,
            "slow_mo": self.slow_mo,
        }
        cfg = Config.browser_config()
        exec_path = cfg.get("executable_path", "")
        if exec_path:
            launch_args["executable_path"] = exec_path
        self._browser = await self._pw.chromium.launch(**launch_args)
        self._page = await self._browser.new_page()
        logger.info("Browser started")

    async def close(self):
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        logger.info("Browser closed")

    async def navigate(self, url: str):
        if not self._page:
            await self.start()
        await self._page.goto(url, timeout=self.timeout, wait_until="networkidle")
        logger.info("Navigated to %s", url)

    async def detect_form(self) -> str | None:
        form = await self._page.query_selector("form")
        if form:
            return "form"

        apply_btn = await self._page.query_selector(
            "button:has-text('Apply'), a:has-text('Apply'), "
            "button:has-text('Submit'), a:has-text('Submit')"
        )
        if apply_btn:
            return "apply_button"

        input_fields = await self._page.query_selector_all(
            "input[type='text'], input[type='email'], textarea, select"
        )
        if len(input_fields) >= 3:
            return "inputs_present"

        return None

    async def _confirm_domain(self, url: str) -> bool:
        domain = urlparse(url).hostname or ""
        known = any(d in domain for d in KNOWN_ATS_DOMAINS)
        if known:
            return True
        print(f"\n  [yellow]Unknown domain: {domain}[/yellow]")
        print("  This might not be a job application form.")
        print("  Press Enter to continue, or type 'skip': ", end="")
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, input)
        return response.strip().lower() != "skip"

    async def autofill(self, resume_path: str, cover_letter: str, url: str = ""):
        if not self._page:
            logger.error("Page not initialized")
            return
        if url and not await self._confirm_domain(url):
            print("  [yellow]Autofill cancelled by user[/yellow]")
            return

        profile = {k: v for k, v in self.profile.items() if v}
        fields = {
            "name": (profile.get("name", ""), ["name", "full-name", "fullname", "candidate-name"]),
            "email": (profile.get("email", ""), ["email", "e-mail", "email-address"]),
            "phone": (profile.get("phone", ""), ["phone", "phone-number", "mobile", "telephone"]),
            "linkedin": (profile.get("linkedin", ""), ["linkedin", "linkedin-url", "linkedin_profile"]),
            "github": (profile.get("github", ""), ["github", "github-url", "github_profile"]),
            "resume": ("", ["resume", "file", "upload-resume", "attachment", "cv"]),
            "cover": ("", ["cover", "cover-letter", "message", "additional-info", "comments"]),
        }

        for field_type, (value, selectors) in fields.items():
            for selector in selectors:
                try:
                    el = await self._page.query_selector(
                        f"input[name*='{selector}'], "
                        f"input[id*='{selector}'], "
                        f"textarea[name*='{selector}'], "
                        f"textarea[id*='{selector}'], "
                        f"input[type='file'][name*='{selector}']"
                    )
                    if el:
                        is_visible = await el.is_visible()
                        if not is_visible:
                            continue

                        if field_type == "resume":
                            full_path = str(RESUME_DIR / resume_path)
                            if Path(full_path).exists():
                                await el.set_input_files(full_path)
                                logger.info("Uploaded resume: %s", resume_path)
                        elif field_type == "cover" and cover_letter:
                            await el.fill(cover_letter)
                            logger.info("Filled cover letter")
                        elif value:
                            await el.fill(value)
                            logger.info("Filled %s", field_type)
                        break
                except Exception as e:
                    logger.debug("Field %s (%s): %s", field_type, selector, e)

        logger.info("Autofill complete")

    async def wait_for_confirmation(self) -> bool:
        print("\n" + "=" * 60)
        print("  BROWSER: Application form is ready for review")
        print("  Check: resume, cover letter, salary, location, visa")
        print("  Then click Submit manually in the browser")
        print("=" * 60)
        print("  Press Enter when done, or type 'skip' to cancel: ", end="")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, input)
        return response.strip().lower() != "skip"
