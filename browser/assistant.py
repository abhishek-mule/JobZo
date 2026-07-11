import logging
import asyncio
import sys
from urllib.parse import urlparse
from pathlib import Path
from services.config import Config

logger = logging.getLogger("jobzo.browser")

RESUME_DIR = Path(__file__).parent.parent / "resumes"


def _is_tty() -> bool:
    return sys.stdin.isatty()


async def _input_or_skip(prompt: str) -> str:
    """Read user input; if not a TTY, return empty string to continue."""
    if not _is_tty():
        logger.info("Non-interactive mode — proceeding automatically")
        return ""
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: input(prompt))
    return response.strip().lower()

KNOWN_ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "breezy.hr", "recruitee.com", "smartrecruiters.com", "icims.com",
    "jazzhr.com", "bamboohr.com", "paycom.com", "workday.com",
    "myworkdayjobs.com", "jobs.workday.com", "successfactors.com",
    "sap.com", "linkedin.com", "indeed.com", "glassdoor.com",
    "ziprecruiter.com", "monster.com", "careerbuilder.com",
    "ycombinator.com",
}

FIELD_MAP = {
    "name": {
        "label": "Full name",
        "autocomplete": ["name"],
        "aria": ["full-name", "fullname", "candidate-name", "name"],
        "name": ["name", "full-name", "fullname", "candidate-name", "firstname", "lastname"],
        "placeholder": ["Full name", "Name", "First name", "Last name"],
    },
    "email": {
        "label": "Email",
        "autocomplete": ["email"],
        "aria": ["email", "e-mail", "email-address"],
        "name": ["email", "e-mail", "email-address"],
        "placeholder": ["Email", "E-mail", "Email address"],
    },
    "phone": {
        "label": "Phone",
        "autocomplete": ["tel", "mobile"],
        "aria": ["phone", "mobile", "telephone", "phone-number"],
        "name": ["phone", "phone-number", "mobile", "telephone", "cell"],
        "placeholder": ["Phone", "Mobile", "Phone number"],
    },
    "location": {
        "label": "Location",
        "autocomplete": ["address-level2", "city"],
        "aria": ["location", "city", "current-city"],
        "name": ["location", "city", "current-city", "candidate-location"],
        "placeholder": ["Location", "City", "Current location"],
    },
    "linkedin": {
        "label": "LinkedIn",
        "aria": ["linkedin", "linkedin-url", "linkedin_profile"],
        "name": ["linkedin", "linkedin-url", "linkedin_profile", "linkedinurl"],
        "placeholder": ["LinkedIn", "LinkedIn URL", "LinkedIn profile"],
    },
    "github": {
        "label": "GitHub",
        "aria": ["github", "github-url", "github_profile"],
        "name": ["github", "github-url", "github_profile", "githuburl"],
        "placeholder": ["GitHub", "GitHub URL", "GitHub profile"],
    },
    "current_role": {
        "label": "Current role",
        "aria": ["current-role", "job-title", "current-title", "current-company"],
        "name": ["current-role", "title", "job-title", "current-title", "position", "org"],
        "placeholder": ["Current role", "Job title", "Title", "Position", "Current company"],
    },
    "years_experience": {
        "label": "Years of experience",
        "aria": ["experience", "years-experience"],
        "name": ["experience", "years-experience", "years_of_experience", "yoe"],
        "placeholder": ["Years of experience", "Experience"],
    },
    "degree": {
        "label": "Degree",
        "aria": ["degree", "education", "qualification"],
        "name": ["degree", "education", "qualification", "education_level"],
        "placeholder": ["Degree", "Education", "Qualification"],
    },
    "college": {
        "label": "College",
        "aria": ["college", "university", "school", "institution"],
        "name": ["college", "university", "school", "institution", "college_name"],
        "placeholder": ["College", "University", "School", "Institution"],
    },
    "graduation_year": {
        "label": "Graduation year",
        "autocomplete": ["graduation-year", "grad-year"],
        "name": ["graduation-year", "grad-year", "graduation_year", "graduation_date"],
        "placeholder": ["Graduation year", "Grad year", "Year of graduation"],
    },
    "cover": {
        "label": "Cover letter",
        "aria": ["cover-letter", "cover", "additional-info"],
        "name": ["cover", "cover-letter", "message", "additional-info", "comments", "notes"],
        "placeholder": ["Cover letter", "Cover", "Additional information", "Message"],
    },
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
        self._results = {}

    def _out(self, msg: str):
        print(msg)
        sys.stdout.flush()

    async def start(self):
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        launch_args = {"headless": self.headless, "slow_mo": self.slow_mo}
        cfg = Config.browser_config()
        exec_path = cfg.get("executable_path", "")
        if exec_path:
            launch_args["executable_path"] = exec_path
        self._browser = await self._pw.chromium.launch(**launch_args)
        self._page = await self._browser.new_page()
        logger.info("Browser started")
        self._out("  -> Browser launched")

    async def close(self):
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        logger.info("Browser closed")

    async def navigate(self, url: str):
        if not self._page:
            await self.start()
        self._out(f"  -> Navigating to {url}")
        await self._page.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(3000)
        self._out("  -> Page loaded")

    async def _find_iframes(self):
        frames = self._page.frames
        if len(frames) <= 1:
            return None
        for f in frames[1:]:
            try:
                inputs = await f.locator("input, textarea, select").count()
                if inputs >= 2:
                    logger.info("Found iframe with %d input fields", inputs)
                    return f
            except Exception:
                continue
        return None

    async def detect_form(self) -> str | None:
        page = self._page

        if await page.locator("form").count():
            self._out("  -> Form tag detected")
            return "form"

        inputs = await page.locator(
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "textarea, select:not([size='1'])"
        ).count()
        if inputs >= 2:
            self._out(f"  -> {inputs} input fields detected")
            return "inputs_present"

        iframe = await self._find_iframes()
        if iframe:
            self._page = iframe
            self._out("  -> Form found inside iframe")
            return "inputs_present"

        btns = 0
        for name in ["Apply Now", "Apply", "Submit"]:
            btns += await page.get_by_role("button", name=name, exact=False).count()
            btns += await page.get_by_role("link", name=name, exact=False).count()
        if btns:
            self._out("  -> Apply button detected")
            return "apply_button"

        self._out("  -> No form detected")
        return None

    async def step_forward(self) -> bool:
        for name in ["Continue", "Next", "Next Step", "Review", "Submit"]:
            for role in ["button", "link"]:
                count = await self._page.get_by_role(role, name=name, exact=False).count()
                if count:
                    self._out(f"  -> '{name}' button detected (multi-step form)")
                    return True
        return False

    async def click_apply(self) -> bool:
        for name in ["Apply Now", "Apply", "Submit"]:
            for role in ["button", "link"]:
                count = await self._page.get_by_role(role, name=name, exact=False).count()
                if count:
                    await self._page.get_by_role(role, name=name, exact=False).first.click()
                    await self._page.wait_for_timeout(3000)
                    logger.info("Clicked '%s' %s", name, role)
                    self._out(f"  -> Clicked '{name}'")
                    return True
        self._out("  -> Could not find Apply button")
        return False

    async def _fill_one(self, field_type: str, value: str) -> bool:
        if not value:
            return False

        rules = FIELD_MAP.get(field_type)
        if not rules:
            return False

        p = self._page

        async def try_label():
            label = rules.get("label", "")
            if not label:
                return False
            try:
                loc = p.get_by_label(label, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.fill(value)
                    return True
            except Exception:
                pass
            return False

        async def try_autocomplete():
            for ac in rules.get("autocomplete", []):
                try:
                    loc = p.locator(f"[autocomplete='{ac}']")
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.fill(value)
                        return True
                except Exception:
                    continue
            return False

        async def try_aria():
            for aria in rules.get("aria", []):
                try:
                    loc = p.locator(f"[aria-label*='{aria}' i]")
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.fill(value)
                        return True
                except Exception:
                    continue
            return False

        async def try_name():
            for n in rules.get("name", []):
                for tag in ["input", "textarea"]:
                    try:
                        loc = p.locator(f"{tag}[name*='{n}' i], {tag}[id*='{n}' i], {tag}[data-testid*='{n}' i]")
                        count = await loc.count()
                        for i in range(count):
                            el = loc.nth(i)
                            if await el.is_visible():
                                await el.fill(value)
                                return True
                    except Exception:
                        continue
            return False

        async def try_placeholder():
            for ph in rules.get("placeholder", []):
                try:
                    loc = p.get_by_placeholder(ph)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.fill(value)
                        return True
                except Exception:
                    continue
            return False

        async def try_role_textbox():
            label = rules.get("label", "")
            try:
                loc = p.get_by_role("textbox", name=label, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.fill(value)
                    return True
            except Exception:
                pass
            return False

        for attempt in [try_label, try_autocomplete, try_aria, try_name, try_placeholder, try_role_textbox]:
            if await attempt():
                return True

        return False

    async def _upload_resume(self, resume_path: str) -> bool:
        full_path = str(RESUME_DIR / resume_path)
        if not Path(full_path).exists():
            self._out(f"  -> Resume not found: {resume_path}")
            return False

        for sel in [
            "input[type='file']",
            "[class*='upload'] input[type='file']",
            "[class*='resume'] input[type='file']",
            "[class*='file-input'] input[type='file']",
        ]:
            try:
                loc = self._page.locator(sel)
                count = await loc.count()
                if count:
                    await loc.first.set_input_files(full_path)
                    return True
            except Exception:
                continue

        return False

    async def autofill(self, resume_path: str, cover_letter: str, url: str = ""):
        if not self._page:
            logger.error("Page not initialized")
            return
        if url and not await self._confirm_domain(url):
            self._out("  -> Autofill cancelled")
            return

        profile = {k: v for k, v in self.profile.items() if v}
        self._results = {}

        FIELDS = [
            ("name", profile.get("name", "")),
            ("email", profile.get("email", "")),
            ("phone", profile.get("phone", "")),
            ("location", profile.get("location", "")),
            ("linkedin", profile.get("linkedin", "")),
            ("github", profile.get("github", "")),
            ("current_role", profile.get("current_role", "")),
            ("years_experience", str(profile.get("years_experience", ""))),
            ("degree", profile.get("degree", "")),
            ("college", profile.get("college", "")),
            ("graduation_year", profile.get("graduation_year", "")),
        ]

        for field_type, value in FIELDS:
            ok = await self._fill_one(field_type, value)
            self._results[field_type] = ok

        resume_ok = await self._upload_resume(resume_path)
        self._results["resume"] = resume_ok

        cover_ok = False
        if cover_letter:
            cover_ok = await self._fill_one("cover", cover_letter)
            if not cover_ok:
                for sel in ["textarea", "[name*='cover']", "[name*='message']", "[name*='notes']"]:
                    try:
                        loc = self._page.locator(sel)
                        count = await loc.count()
                        for i in range(count):
                            el = loc.nth(i)
                            if await el.is_visible():
                                await el.fill(cover_letter)
                                cover_ok = True
                                break
                    except Exception:
                        continue
        self._results["cover"] = cover_ok

        await self._print_report()

    async def _print_report(self):
        self._out("")
        self._out("  === Autofill Summary ===")
        filled = 0
        total = 0
        for key, ok in self._results.items():
            total += 1
            if ok:
                filled += 1
            label = key.replace("_", " ").title()
            icon = "[green]✓[/green]" if ok else "[yellow]⚠[/yellow]"
            self._out(f"  {icon} {label}")
        self._out("")
        self._out(f"  Completed: {filled}/{total} fields")
        if filled < total:
            self._out("  Missing fields will need manual entry")
        self._out("")

    async def wait_for_confirmation(self) -> bool:
        has_next = await self.step_forward()
        if has_next:
            self._out("  -> This form has multiple steps. Click Continue/Next manually.")
            self._out("  -> After the final step, click Submit manually.")

        self._out("  " + "=" * 56)
        self._out("    BROWSER: Application form is ready for review")
        self._out("    Check all fields, then click Submit manually")
        self._out("  " + "=" * 56)
        response = await _input_or_skip("    Press Enter when done, or 'skip' to cancel: ")
        return response != "skip"

    async def _confirm_domain(self, url: str) -> bool:
        domain = urlparse(url).hostname or ""
        known = any(d in domain for d in KNOWN_ATS_DOMAINS)
        if known:
            return True
        self._out(f"\n  -> Unknown domain: {domain}")
        response = await _input_or_skip("  -> Press Enter to continue, or type 'skip': ")
        return response != "skip"
