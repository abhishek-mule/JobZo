"""Resume Intelligence Engine — Phase 2 of JobZo Career OS.

Modules:
  registry    — Resume metadata loaded from YAML files
  jd_analyzer — Three-layer extraction of skills, domains, experience from JD
  scorer      — 7-dimension resume scoring against JD
  optimizer   — Actionable suggestions to improve resume-JD alignment
  fit_report  — On-demand human-readable fit report
  skill_roadmap — Aggregate skill demand from recommended jobs
  feed        — Opportunity Feed (default jobzo output)
"""

from resumes.registry import ResumeMeta, ResumeRegistry, get_registry, reload_registry
from resumes.jd_analyzer import JDAnalysis, analyze as analyze_jd
from resumes.scorer import (
    ResumeScore,
    DimensionScore,
    score_resumes,
    best_resume,
    DEFAULT_WEIGHTS,
)
from resumes.optimizer import Optimization, Suggestion, optimize
from resumes.fit_report import FitReport, generate as generate_fit_report
from resumes.skill_roadmap import SkillDemand, SkillRoadmap, build_roadmap
from resumes.feed import OpportunityFeed, build_feed
from resumes.prepare import PreparationPlan, StudySection, prepare
