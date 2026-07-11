COMPANIES = [
    {
        "name": "Razorpay",
        "careers_url": "https://razorpay.com/careers/",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "java", "spring", "fullstack", "software engineer"],
    },
    {
        "name": "Postman",
        "careers_url": "https://www.postman.com/careers/",
        "job_listing_url": "https://job-boards.greenhouse.io/postman",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "platform", "software engineer", "fullstack"],
    },
    {
        "name": "BrowserStack",
        "careers_url": "https://www.browserstack.com/careers/",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "sdet"],
    },
    {
        "name": "Juspay",
        "careers_url": "https://juspay.in/careers/",
        "job_listing_url": "",
        "priority": "medium",
        "country": "IN",
        "tags": ["backend", "java", "spring", "software engineer"],
    },
    {
        "name": "Atlassian",
        "careers_url": "https://www.atlassian.com/company/careers/",
        "job_listing_url": "https://www.atlassian.com/company/careers/all-jobs",
        "priority": "high",
        "country": "GLOBAL",
        "tags": ["backend", "java", "spring", "software engineer", "fullstack"],
    },
    {
        "name": "Stripe",
        "careers_url": "https://stripe.com/jobs",
        "job_listing_url": "https://stripe.com/jobs/search",
        "priority": "high",
        "country": "GLOBAL",
        "tags": ["backend", "software engineer", "infrastructure", "api"],
    },
    {
        "name": "GitLab",
        "careers_url": "https://about.gitlab.com/jobs/",
        "job_listing_url": "https://about.gitlab.com/jobs/all/",
        "priority": "high",
        "country": "GLOBAL",
        "tags": ["backend", "software engineer", "fullstack", "platform"],
    },
    {
        "name": "Grafana Labs",
        "careers_url": "https://grafana.com/careers/",
        "job_listing_url": "",
        "priority": "medium",
        "country": "GLOBAL",
        "tags": ["backend", "software engineer", "platform", "sre"],
    },

    # ── Indian product startups with fresher/graduate hiring ──
    {
        "name": "Flipkart",
        "careers_url": "https://www.flipkart.com/careers",
        "job_listing_url": "https://www.flipkartcareers.com/#!/joblist",
        "priority": "high",
        "country": "IN",
        "tags": ["software engineer", "sde", "backend", "graduate", "campus"],
    },
    {
        "name": "PhonePe",
        "careers_url": "https://www.phonepe.com/careers/",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "java", "fullstack", "graduate"],
    },
    {
        "name": "Groww",
        "careers_url": "https://groww.in/careers",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "java", "graduate"],
    },
    {
        "name": "Zomato",
        "careers_url": "https://www.zomato.com/careers",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "graduate"],
    },
    {
        "name": "CRED",
        "careers_url": "https://cred.club/careers",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "platform"],
    },
    {
        "name": "Dream11",
        "careers_url": "https://www.dream11.com/careers",
        "job_listing_url": "",
        "priority": "high",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "graduate"],
    },
    {
        "name": "Urban Company",
        "careers_url": "https://careers.urbancompany.com/",
        "job_listing_url": "",
        "priority": "medium",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "graduate"],
    },
    {
        "name": "Nykaa",
        "careers_url": "https://www.nykaa.com/careers",
        "job_listing_url": "",
        "priority": "medium",
        "country": "IN",
        "tags": ["backend", "software engineer", "fullstack", "graduate"],
    },
]


def get_all() -> list[dict]:
    return COMPANIES


def get_by_name(name: str) -> dict | None:
    for c in COMPANIES:
        if c["name"].lower() == name.lower():
            return c
    return None


def get_by_priority(priority: str = "high") -> list[dict]:
    return [c for c in COMPANIES if c.get("priority") == priority]
