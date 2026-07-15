"""Shared HTML parsing helpers for ATS parsers.

Every parser uses these to avoid duplicating BeautifulSoup boilerplate.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ENGINEERING_KEYWORDS = [
    "engineer", "developer", "sde", "intern", "software",
    "backend", "full.?stack", "frontend", "platform",
    "java", "python", "devops", "data", "ml",
]


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text(soup: BeautifulSoup, selector: str, default: str = "") -> str:
    el = soup.select_one(selector)
    return el.get_text(strip=True) if el else default


def texts(soup: BeautifulSoup, selector: str) -> list[str]:
    return [el.get_text(strip=True) for el in soup.select(selector)]


def link_urls(soup: BeautifulSoup, selector: str | None = None) -> list[tuple[str, str]]:
    """Return [(url, text), ...] for matching links."""
    links = soup.select(selector) if selector else soup.find_all("a", href=True)
    results = []
    for a in links:
        href = a.get("href", "")
        t = a.get_text(strip=True)
        if href and t:
            results.append((href, t))
    return results


def absolute_url(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(base, href)


def parent_text(element, depth: int = 3) -> str:
    parent = element
    for _ in range(depth):
        if parent is None or parent.name in ("html", "body", "document"):
            break
        parent = parent.parent
    return parent.get_text(separator=" ", strip=True) if parent else ""


def is_engineering_role(text_str: str) -> bool:
    t = text_str.lower()
    for kw in ENGINEERING_KEYWORDS:
        if re.search(kw, t):
            return True
    return False


def extract_company_from_url(url: str, pattern: str, group: int = 1) -> str:
    m = re.search(pattern, url)
    if m:
        name = m.group(group).replace("-", " ").replace("_", " ").title()
        name = re.sub(r"\s+", " ", name).strip()
        return name
    return ""


def find_element_by_text(soup: BeautifulSoup, tag: str, text_pattern: str) -> object | None:
    """Find an element by tag name and text content pattern."""
    for el in soup.find_all(tag):
        if re.search(text_pattern, el.get_text(strip=True), re.I):
            return el
    return None
