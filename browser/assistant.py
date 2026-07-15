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
    "ycombinator.com", "teamtailor.com", "personio.com",
    "personio.de",
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
    "current_company": {
        "label": "Current company",
        "aria": ["current-company", "current-company-name", "employer"],
        "name": ["current-company", "company", "employer", "current-company-name"],
        "placeholder": ["Current company", "Company", "Employer", "Current employer"],
    },
    "current_role": {
        "label": "Current role",
        "aria": ["current-role", "job-title", "current-title"],
        "name": ["current-role", "title", "job-title", "current-title", "position", "org"],
        "placeholder": ["Current role", "Job title", "Title", "Position"],
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
    "current_ctc": {
        "label": "Current CTC",
        "aria": ["current-ctc", "current-cost-to-company", "present-ctc", "current-salary"],
        "name": ["current-ctc", "current_cost_to_company", "present-ctc", "current_ctc", "current-annual-salary"],
        "placeholder": ["Current CTC", "Current Cost to Company", "Current salary", "Present CTC"],
    },
    "expected_ctc": {
        "label": "Expected CTC",
        "aria": ["expected-ctc", "expected-cost-to-company", "expected-salary", "desired-ctc"],
        "name": ["expected-ctc", "expected_cost_to_company", "expected_ctc", "desired-ctc", "expected-salary", "desired-salary"],
        "placeholder": ["Expected CTC", "Expected Cost to Company", "Expected salary", "Desired CTC"],
    },
    "notice_period": {
        "label": "Notice period",
        "aria": ["notice-period", "notice", "notice-period-duration"],
        "name": ["notice-period", "notice_period", "notice", "notice-period-duration"],
        "placeholder": ["Notice period", "Notice"],
    },
    "cover": {
        "label": "Cover letter",
        "aria": ["cover-letter", "cover", "additional-info"],
        "name": ["cover", "cover-letter", "message", "additional-info", "comments", "notes"],
        "placeholder": ["Cover letter", "Cover", "Additional information", "Message"],
    },
    # Application-specific fields (select, radio, checkbox)
    "country": {
        "label": "Country of residence",
        "select_options": ["country", "residence", "citizenship"],
        "aria": ["country", "residence", "citizenship", "country-of-residence"],
        "name": ["country", "country-of-residence", "citizenship_country"],
    },
    "state": {
        "label": "State",
        "select_options": ["state", "province", "region"],
        "aria": ["state", "province", "region"],
        "name": ["state", "province", "region"],
    },
    "city": {
        "label": "City",
        "aria": ["city", "town", "current-city"],
        "name": ["city", "town", "current-city"],
        "placeholder": ["City", "Town"],
    },
    "visa_sponsorship": {
        "label": "Sponsorship",
        "radio_group": ["sponsor", "visa", "authorization", "work-permit"],
        "aria": ["sponsor", "visa", "require-sponsorship", "sponsorship"],
        "name": ["sponsor", "visa", "require_sponsorship", "requires_visa", "work_authorization"],
    },
    "employment_restrictions": {
        "label": "Employment restrictions",
        "radio_group": ["restriction", "agreement", "non-compete", "employment-agreement"],
        "aria": ["restrictions", "employment-agreement", "non-compete"],
        "name": ["restrictions", "employment_agreement", "non_compete", "employment_restrictions"],
    },
    "accommodations": {
        "label": "Accommodations",
        "aria": ["accommodation", "accessibility", "adjustment", "disability-accommodation"],
        "name": ["accommodation", "accommodations", "accessibility", "adjustment", "interview_accommodation"],
        "placeholder": ["Accommodations", "Accessibility", "Adjustments"],
    },
    "gender": {
        "label": "Gender",
        "select_group": ["gender", "sex"],
        "aria": ["gender", "sex"],
        "name": ["gender", "sex"],
    },
    "veteran_status": {
        "label": "Veteran status",
        "radio_group": ["veteran", "military", "protected-veteran"],
        "aria": ["veteran", "military-service", "protected-veteran"],
        "name": ["veteran", "veteran_status", "military_service", "protected_veteran"],
    },
    "disability_status": {
        "label": "Disability status",
        "radio_group": ["disability", "disabled", "disability-status"],
        "aria": ["disability", "disability-status", "have-disability"],
        "name": ["disability", "disability_status", "disability_status"],
    },
    "pronouns": {
        "label": "Pronouns",
        "aria": ["pronoun", "pronouns", "preferred-pronouns"],
        "name": ["pronoun", "pronouns", "preferred_pronouns"],
        "placeholder": ["Pronouns", "Preferred pronouns"],
    },
    "ethnicity": {
        "label": "Ethnicity",
        "select_group": ["ethnicity", "race", "ethnic-group"],
        "aria": ["ethnicity", "race", "ethnic-group"],
        "name": ["ethnicity", "race", "ethnic_group", "race_ethnicity"],
    },
    "previously_employed": {
        "label": "Previously employed",
        "radio_group": ["previously-employed", "former-employee", "rehire", "previously-worked"],
        "aria": ["previously-employed", "former-employee", "previously-worked-here"],
        "name": ["previously_employed", "former_employee", "rehire_eligible"],
    },
    "website": {
        "label": "Website",
        "aria": ["website", "portfolio", "personal-website", "url"],
        "name": ["website", "portfolio", "personal_website", "url", "personal_url"],
        "placeholder": ["Website", "Portfolio", "Personal website"],
    },
    "linkedin_headline": {
        "label": "Headline",
        "aria": ["headline", "professional-headline", "tagline"],
        "name": ["headline", "professional_headline", "tagline"],
        "placeholder": ["Headline", "Professional headline"],
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

        # Determine input type from FIELD_MAP hints
        has_select = "select_options" in rules or "select_group" in rules
        has_radio = "radio_group" in rules

        # For select/radio fields, use dedicated handlers first
        if has_select:
            if await self._fill_select(p, rules, value):
                return True
        if has_radio:
            if await self._fill_radio(p, rules, value):
                return True

        # For text-like fields, use existing fill strategies
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
                for tag in ["input", "textarea", "select"]:
                    try:
                        loc = p.locator(f"{tag}[name*='{n}' i], {tag}[id*='{n}' i], {tag}[data-testid*='{n}' i]")
                        count = await loc.count()
                        for i in range(count):
                            el = loc.nth(i)
                            if await el.is_visible():
                                tag_name = await el.evaluate("el => el.tagName.toLowerCase()") if True else ""
                                # Use select_option for <select>, fill for others
                                if tag_name == "select":
                                    await el.select_option(value)
                                else:
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

    async def _fill_select(self, page, rules: dict, value: str) -> bool:
        """Fill a <select> dropdown by matching label text to option value."""
        label_keywords = rules.get("select_options", []) or rules.get("select_group", []) or rules.get("name", [])
        value_lower = value.lower()

        # Strategy 1: find <select> by label text
        for keyword in label_keywords:
            try:
                loc = page.locator(f"label:has-text('{keyword}')")
                count = await loc.count()
                for i in range(count):
                    label_el = loc.nth(i)
                    for_id = await label_el.get_attribute("for")
                    if for_id:
                        select = page.locator(f"select#{for_id}")
                    else:
                        select = label_el.locator("xpath=following-sibling::select")
                    if await select.count():
                        options = await select.locator("option").all_inner_texts()
                        option_values = await select.locator("option").evaluate_all(
                            "els => els.map(el => el.value)"
                        )
                        for opt_text, opt_val in zip(options, option_values):
                            if value_lower in opt_text.lower() or value_lower == opt_val.lower()[:len(value_lower)]:
                                await select.first.select_option(opt_val or opt_text)
                                return True
            except Exception:
                continue

        # Strategy 2: find <select> by aria-label or name
        for attr in ["aria-label", "name", "id"]:
            for keyword in label_keywords:
                try:
                    select = page.locator(f"select[{attr}*='{keyword}' i]")
                    count = await select.count()
                    for i in range(count):
                        el = select.nth(i)
                        if await el.is_visible():
                            options = await el.locator("option").all_inner_texts()
                            option_values = await el.locator("option").evaluate_all(
                                "els => els.map(el => el.value)"
                            )
                            for opt_text, opt_val in zip(options, option_values):
                                if value_lower in opt_text.lower() or value_lower == opt_val.lower()[:len(value_lower)]:
                                    await el.select_option(opt_val or opt_text)
                                    return True
                except Exception:
                    continue

        return False

    async def _fill_radio(self, page, rules: dict, value: str) -> bool:
        """Fill a radio button group by matching label/name to value."""
        label_keywords = rules.get("radio_group", []) or rules.get("name", [])
        value_lower = value.lower().strip()

        # Determine the target option: Yes/No or specific value
        yes_no = value_lower in ("yes", "no", "true", "false")
        target = "yes" if value_lower in ("yes", "true") else "no" if value_lower in ("no", "false") else value

        def _find_radio_by_name(name_val: str):
            return page.locator(f"input[type='radio'][name='{name_val}']")

        # Strategy 1: find radio group by label containing keyword
        for keyword in label_keywords:
            try:
                # Look for a label containing the keyword
                label_loc = page.locator(f"label:has-text('{keyword}')")
                label_count = await label_loc.count()
                for li in range(label_count):
                    label_el = label_loc.nth(li)
                    label_text = (await label_el.inner_text()).lower()
                    # Try to find radio inputs within the same container or following
                    parent = label_el.locator("xpath=ancestor::fieldset | ancestor::div[contains(@class,'field')] | ancestor::li")
                    if await parent.count():
                        parent = parent.first
                    else:
                        parent = label_el.locator("xpath=..")
                    radios = parent.locator("input[type='radio']")
                    radio_count = await radios.count()
                    for ri in range(radio_count):
                        radio = radios.nth(ri)
                        radio_label = (await page.locator(f"label[for='{(await radio.get_attribute('id')) or ''}']").inner_text()).lower() if await radio.get_attribute('id') else ""
                        radio_value = (await radio.get_attribute("value")) or ""
                        radio_text = radio_label or radio_value.lower()
                        if yes_no:
                            if target in radio_text or (target == "yes" and radio_value.lower() in ("yes", "true", "1")):
                                await radio.check()
                                return True
                            if target == "no" and radio_value.lower() in ("no", "false", "0"):
                                await radio.check()
                                return True
                        else:
                            if target.lower() in radio_text:
                                await radio.check()
                                return True
            except Exception:
                continue

        # Strategy 2: find radio group by name attribute
        for keyword in label_keywords:
            try:
                radios = page.locator(f"input[type='radio'][name*='{keyword}' i]")
                count = await radios.count()
                for ri in range(count):
                    radio = radios.nth(ri)
                    radio_value = (await radio.get_attribute("value")) or ""
                    # Find associated label
                    radio_id = await radio.get_attribute("id")
                    label_text = ""
                    if radio_id:
                        try:
                            label_text = (await page.locator(f"label[for='{radio_id}']").inner_text()).lower()
                        except Exception:
                            pass
                    match_text = label_text or radio_value.lower()
                    if yes_no:
                        if target in match_text:
                            await radio.check()
                            return True
                    else:
                        if target.lower() in match_text:
                            await radio.check()
                            return True
            except Exception:
                continue

        return False

    async def _fill_checkbox(self, page, rules: dict, value: bool | str) -> bool:
        """Check or uncheck a checkbox by label."""
        label_keywords = rules.get("label", "")
        should_check = value is True or str(value).lower() in ("yes", "true", "1")
        for keyword in [label_keywords] if label_keywords else rules.get("name", []):
            try:
                cb = page.get_by_label(keyword, exact=False)
                if await cb.count():
                    if should_check:
                        await cb.first.check()
                    else:
                        await cb.first.uncheck()
                    return True
            except Exception:
                continue
        return False

    async def _upload_resume(self, resume_path: str) -> bool:
        # Look up the resume in the registry to get the actual file path
        from resumes.registry import get_registry
        registry = get_registry()
        meta = registry.get(resume_path)
        if meta:
            file_path = meta.file
            if Path(file_path).is_absolute():
                full_path = file_path
            else:
                full_path = str(RESUME_DIR.parent / file_path)
        else:
            # Fallback: try adding .pdf extension
            for candidate in [resume_path, resume_path + ".pdf"]:
                fp = str(RESUME_DIR / candidate)
                if Path(fp).exists():
                    full_path = fp
                    break
            else:
                self._out(f"  -> Resume not found: {resume_path}")
                return False

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
            ("website", profile.get("website", "") or profile.get("portfolio", "")),
            ("current_company", profile.get("current_company", "")),
            ("current_role", profile.get("current_role", "")),
            ("years_experience", str(profile.get("years_experience", ""))),
            ("degree", profile.get("degree", "")),
            ("college", profile.get("college", "")),
            ("graduation_year", profile.get("graduation_year", "")),
            ("current_ctc", str(profile.get("current_ctc", ""))),
            ("expected_ctc", str(profile.get("expected_ctc", ""))),
            ("notice_period", profile.get("notice_period", "")),
            ("country", profile.get("country", "")),
            ("state", profile.get("state", "")),
            ("city", profile.get("city", "")),
            ("visa_sponsorship", profile.get("requires_sponsorship", profile.get("authorized_to_work", ""))),
            ("employment_restrictions", profile.get("employment_restrictions", "")),
            ("accommodations", profile.get("accommodations", "")),
            ("gender", profile.get("gender", "")),
            ("veteran_status", profile.get("veteran_status", "")),
            ("disability_status", profile.get("disability_status", "")),
            ("pronouns", profile.get("pronouns", "")),
            ("ethnicity", profile.get("ethnicity", "")),
            ("previously_employed", profile.get("previously_employed_by", "")),
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
