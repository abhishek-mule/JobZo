"""Resume Metadata Registry — loads resume meta YAMLs and provides lookup by name, role, domain."""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RESUMES_DIR = Path(__file__).parent
META_DIR = RESUMES_DIR / "meta"


@dataclass
class ResumeMeta:
    name: str
    file: str
    skills: list[str] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    experience: str = "mid"
    education: str = ""
    target_roles: list[str] = field(default_factory=list)

    @property
    def all_skill_names(self) -> set[str]:
        return set(self.skills)

    @property
    def project_skills(self) -> set[str]:
        result: set[str] = set()
        for p in self.projects:
            result.update(p.get("skills", []))
        return result

    @property
    def project_domains(self) -> list[str]:
        return [p.get("domain", "") for p in self.projects if p.get("domain")]


class ResumeRegistry:
    def __init__(self) -> None:
        self._resumes: dict[str, ResumeMeta] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not META_DIR.exists():
            return
        for f in sorted(META_DIR.glob("*.yaml")):
            with open(f) as fh:
                data = yaml.safe_load(fh)
            if data and data.get("name"):
                meta = ResumeMeta(**data)
                self._resumes[meta.name] = meta

    def get(self, name: str) -> ResumeMeta | None:
        return self._resumes.get(name)

    def all(self) -> list[ResumeMeta]:
        return list(self._resumes.values())

    def names(self) -> list[str]:
        return list(self._resumes.keys())

    def by_role(self, role: str) -> list[ResumeMeta]:
        role_lower = role.lower()
        results = []
        for meta in self._resumes.values():
            if any(role_lower in t.lower() for t in meta.target_roles):
                results.append(meta)
        return results

    def by_domain(self, domain: str) -> list[ResumeMeta]:
        domain_lower = domain.lower()
        results = []
        for meta in self._resumes.values():
            if domain_lower in [d.lower() for d in meta.domains]:
                results.append(meta)
        return results

    def by_skill(self, skill: str) -> list[ResumeMeta]:
        skill_lower = skill.lower()
        results = []
        for meta in self._resumes.values():
            if any(s.lower() == skill_lower for s in meta.skills):
                results.append(meta)
        return results


_REGISTRY: ResumeRegistry | None = None


def get_registry() -> ResumeRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ResumeRegistry()
    return _REGISTRY


def reload_registry() -> ResumeRegistry:
    global _REGISTRY
    _REGISTRY = ResumeRegistry()
    return _REGISTRY
