"""OutreachTaskProvider — generates outreach tasks from opportunities + contacts.

Phase 3.2 — Part of the TaskProviderRegistry family.
"""

from __future__ import annotations
import logging

from domain.models import TaskNode, MissionContext, OpportunitySnapshot, ProviderResult
from domain.outreach import (
    Contact, ContactRole, ContactRanking, ContactSource,
    CompanyTier, Relationship, DEFAULT_STRATEGIES,
)

logger = logging.getLogger("jobzo.outreach_provider")


class OutreachTaskProvider:
    """Creates 'outreach' tasks for opportunities that benefit from human contact.

    Generates ranked contact suggestions based on company tier, role,
    and relationship metadata. Never executes — the planner decides.
    """

    def kind(self) -> str:
        return "outreach"

    def version(self) -> str:
        return "1"

    def priority(self) -> int:
        return 20  # After apply (10), before learning (30)

    def supports(self, context: MissionContext) -> bool:
        goal = context.goal
        if goal == "Get placed ASAP":
            return True
        if goal == "Maximize salary":
            return True
        if goal in ("Crack product companies", "Build network"):
            return True
        return True

    def build(
        self,
        context: MissionContext,
        opportunities: list[OpportunitySnapshot],
        contacts: list[Contact] | None = None,
    ) -> ProviderResult:
        result = ProviderResult(
            provider=self.kind(),
            provider_version=self.version(),
        )

        for opp in opportunities:
            tasks = self._build_tasks(opp, context, contacts or [])
            result.tasks.extend(tasks)

        result.statistics = {
            "opportunities_scanned": len(opportunities),
            "tasks_created": len(result.tasks),
            "total_value": round(result.total_estimated_value, 1),
        }
        return result

    def _build_tasks(
        self,
        opp: OpportunitySnapshot,
        context: MissionContext,
        contacts: list[Contact],
    ) -> list[TaskNode]:
        """Generate outreach tasks for a single opportunity."""
        strategy = self._strategy_for(opp)
        if not strategy:
            return []

        applicable = self._relevant_contacts(opp, contacts, strategy)
        rankings = self._rank_contacts(applicable, opp, strategy)

        if not rankings:
            return []

        # Take top 1-2 ranked contacts
        return [r.to_task_node() for r in rankings[:2]]

    def _strategy_for(self, opp: OpportunitySnapshot) -> object:
        """Determine outreach strategy based on company characteristics."""
        from domain.outreach import CompanyTier

        company_lower = opp.company.lower()
        tier = CompanyTier.UNKNOWN

        faang_keywords = {"google", "meta", "amazon", "apple", "netflix", "microsoft"}
        mnc_keywords = {"oracle", "ibm", "salesforce", "adobe", "sap", "vmware", "cisco"}
        growth_keywords = {"stripe", "datadog", "databricks", "canva", "deel", "notion", "figma"}

        if any(k in company_lower for k in faang_keywords):
            tier = CompanyTier.FAANG
        elif opp.seniority == "Senior" and any(k in company_lower for k in mnc_keywords):
            tier = CompanyTier.LARGE_MNC
        elif any(k in company_lower for k in growth_keywords):
            tier = CompanyTier.GROWTH
        elif opp.risk == "Hard":
            tier = CompanyTier.MID_SIZE
        elif opp.effort_minutes <= 10:
            tier = CompanyTier.STARTUP

        return DEFAULT_STRATEGIES.get(tier)

    def _relevant_contacts(
        self,
        opp: OpportunitySnapshot,
        contacts: list[Contact],
        strategy: object,
    ) -> list[Contact]:
        """Filter contacts relevant to this opportunity."""
        if not strategy:
            return []
        strat = strategy
        relevant = [c for c in contacts if c.company.lower() == opp.company.lower()]
        if not relevant:
            return []
        return relevant

    def _rank_contacts(
        self,
        contacts: list[Contact],
        opp: OpportunitySnapshot,
        strategy: object,
    ) -> list[ContactRanking]:
        """Score each contact for this opportunity."""
        if not strategy:
            return []
        strat = strategy

        rankings = []
        for c in contacts:
            relevance = self._relevance(c, opp, strat)
            value = self._estimated_value(c, opp, relevance)
            minutes = self._estimated_minutes(c)
            ranking = ContactRanking(
                contact=c,
                opportunity_id=opp.opportunity_id,
                relevance_score=relevance,
                estimated_value=value,
                estimated_minutes=minutes,
                strategy=strat,
                why_lines=self._why_lines(c, opp, relevance, value),
            )
            rankings.append(ranking)

        rankings.sort(key=lambda r: r.estimated_value, reverse=True)
        return rankings

    def _relevance(self, contact: Contact, opp: OpportunitySnapshot, strategy: object) -> float:
        """How relevant is this contact for this opportunity? 0.0 - 1.0."""
        score = 0.0
        strat = strategy
        primary_role = strat.primary_contact.value if strat else ""
        secondary_role = strat.secondary_contact.value if strat and strat.secondary_contact else ""

        if contact.role.value == primary_role:
            score += 0.5
        elif contact.role.value == secondary_role:
            score += 0.3

        if contact.hiring_authority:
            score += 0.3

        if contact.relationship == Relationship.EXISTING:
            score += 0.2
        elif contact.relationship == Relationship.REFERRAL:
            score += 0.15

        if contact.team and opp.canonical_role:
            team_lower = contact.team.lower()
            role_lower = opp.canonical_role.lower()
            if "backend" in team_lower and "backend" in role_lower:
                score += 0.1
            elif "frontend" in team_lower and "frontend" in role_lower:
                score += 0.1

        return min(score, 1.0)

    def _estimated_value(self, contact: Contact, opp: OpportunitySnapshot, relevance: float) -> float:
        """Expected value of reaching out to this contact."""
        base = opp.interview_probability / 100.0 * 30  # Base: 30 points at 100% interview chance
        if contact.hiring_authority:
            base *= 1.3
        if contact.role == ContactRole.FOUNDER:
            base *= 1.2
        if contact.role == ContactRole.EMPLOYEE:
            base *= 0.8  # referral, less direct
        return round(base * relevance, 1)

    def _estimated_minutes(self, contact: Contact) -> int:
        """Time estimate for finding + drafting + sending."""
        minutes = 6
        if contact.role == ContactRole.FOUNDER:
            minutes += 2  # More research needed
        if not contact.email:
            minutes += 3  # Need to find email
        return minutes

    def _why_lines(self, contact: Contact, opp: OpportunitySnapshot, relevance: float, value: float) -> list[str]:
        lines = []
        lines.append(f"{contact.name} ({contact.role.value}) at {contact.company}")
        lines.append(f"Relevance: {relevance:.0%}")
        lines.append(f"Estimated value: {value:.1f}")
        if contact.hiring_authority:
            lines.append("Has hiring authority")
        if contact.relationship != Relationship.NONE:
            lines.append(f"Relationship: {contact.relationship.value}")
        return lines
