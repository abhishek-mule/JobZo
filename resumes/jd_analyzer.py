"""JD Analyzer — three-layer extraction of skills, domains, and experience from job descriptions.

Layer 1: Regex — fast keyword extraction using the Skills Knowledge Base
Layer 2: Dictionary Normalization — map aliases to canonical names
Layer 3: LLM Enrichment (optional) — semantic structure extraction
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from skills import skill_patterns, canonical_name, skill_category, all_canonical

logger = logging.getLogger("jobzo.jd_analyzer")

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "fintech": ["fintech", "financial", "payment", "banking", "pay", "transaction", "ledger",
                "wallet", "lending", "credit", "finance", "accounting"],
    "saas": ["saas", "subscription", "multi-tenant", "billing", "cloud software", "as a service"],
    "ecommerce": ["ecommerce", "e-commerce", "retail", "marketplace", "shop", "store", "inventory"],
    "healthcare": ["healthcare", "health", "medical", "hospital", "clinical", "health tech", "medtech"],
    "devtools": ["developer tools", "devtools", "dev tool", "ide", "cli", "sdk", "api platform"],
    "automation": ["automation", "automated", "workflow", "pipeline", "orchestration", "ci/cd"],
    "data": ["data engineering", "data pipeline", "analytics", "big data", "data platform", "etl"],
    "infrastructure": ["infrastructure", "platform", "sre", "devops", "cloud infra", "reliability"],
    "security": ["security", "cybersecurity", "infosec", "application security", "appsec"],
    "ai": ["artificial intelligence", "machine learning", "ml", "ai", "deep learning", "llm", "nlp"],
}

SENIORITY_PATTERNS: list[tuple[str, str]] = [
    (r"\b(intern|internship|trainee)\b", "intern"),
    (r"\b(junior|entry.level|jr\.?|associate|graduate|fresher)\b", "junior"),
    (r"\b(mid.level|mid.?senior|intermediate)\b", "mid"),
    (r"\b(senior|sr\.?|staff|lead|principal|architect)\b", "senior"),
    (r"\b(head|director|vp|vice president|chief|cto|manager)\b", "staff"),
]


@dataclass
class JDAnalysis:
    skills: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    experience_level: str = ""
    experience_years: str = ""
    responsibilities: list[str] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 1.0


def _extract_skills_regex(text: str) -> list[str]:
    """Layer 1 + 2: Extract canonical skill names using regex + dictionary."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern, canonical in skill_patterns():
        if pattern.search(text):
            if canonical not in seen:
                seen.add(canonical)
                found.append(canonical)
    return found


def _extract_domains(text: str) -> list[str]:
    text_lower = text.lower()
    matched: list[str] = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                if domain not in matched:
                    matched.append(domain)
                break
    return matched


def _extract_experience_level(text: str) -> str:
    text_lower = text.lower()
    for pattern, level in SENIORITY_PATTERNS:
        if re.search(pattern, text_lower):
            return level
    return ""


def _llm_enrich(text: str) -> dict[str, Any] | None:
    """Layer 3: LLM enrichment — optional semantic extraction."""
    try:
        from services.llm import llm
        prompt = (
            "Analyze this job description. Return ONLY a JSON object with keys: "
            "skills (list of strings), domains (list), experience_level (string), "
            "responsibilities (list of strings), confidence (0.0-1.0). "
            "No explanation, no markdown.\n\n"
            f"JD:\n{text[:3000]}"
        )
        response = llm(prompt, max_tokens=500)
        import json
        data = json.loads(response.strip())
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.debug("LLM enrichment failed: %s", e)
    return None


def analyze(text: str, use_llm: bool = False) -> JDAnalysis:
    """Analyze a job description.

    Args:
        text: Raw job description text
        use_llm: If True, augment with LLM enrichment (Layer 3)

    Returns:
        JDAnalysis with extracted fields
    """
    if not text or not text.strip():
        return JDAnalysis(raw_text=text, confidence=0.0)

    analysis = JDAnalysis(raw_text=text)

    # Layer 1 + 2: Regex extraction + dictionary normalization
    analysis.skills = _extract_skills_regex(text)
    analysis.domains = _extract_domains(text)
    analysis.experience_level = _extract_experience_level(text)

    # Layer 3: LLM enrichment (optional)
    if use_llm:
        try:
            enrichment = _llm_enrich(text)
            if enrichment:
                existing = set(analysis.skills)
                for s in enrichment.get("skills", []):
                    canon = canonical_name(s)
                    if canon and canon not in existing:
                        existing.add(canon)
                        analysis.skills.append(canon)

                for d in enrichment.get("domains", []):
                    if d not in analysis.domains:
                        analysis.domains.append(d)

                if enrichment.get("experience_level") and not analysis.experience_level:
                    analysis.experience_level = enrichment["experience_level"]

                analysis.responsibilities.extend(enrichment.get("responsibilities", []))
                analysis.confidence = enrichment.get("confidence", 0.7)
        except Exception as e:
            logger.warning("LLM enrichment failed: %s", e)
            analysis.confidence = 0.5

    return analysis
