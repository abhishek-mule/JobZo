"""TaskProviderRegistry — central registry for task providers.

Mission engine never imports providers directly. It asks the registry:
  registry.build_all(context, opportunities) -> list[ProviderResult]

New providers are registered in one place and discovered automatically.
"""

from __future__ import annotations
from typing import Sequence

from domain.models import MissionContext, OpportunitySnapshot, ProviderResult
from domain.providers import TaskProvider


class TaskProviderRegistry:
    """Registry of TaskProvider instances.

    Providers can be registered, unregistered, queried by kind, and
    filtered by context capability. The mission engine calls build_all()
    which iterates applicable providers in priority order.
    """

    def __init__(self) -> None:
        self._providers: dict[str, TaskProvider] = {}

    def register(self, provider: TaskProvider) -> None:
        """Register a provider. Replaces any existing provider with the same kind."""
        self._providers[provider.kind()] = provider

    def unregister(self, kind: str) -> None:
        """Remove a provider by kind."""
        self._providers.pop(kind, None)

    def get(self, kind: str) -> TaskProvider | None:
        """Get a provider by kind string, or None."""
        return self._providers.get(kind)

    def all(self) -> Sequence[TaskProvider]:
        """Return all registered providers."""
        return list(self._providers.values())

    def applicable(self, context: MissionContext) -> list[TaskProvider]:
        """Return providers that support the given context, sorted by priority."""
        providers = [p for p in self._providers.values() if p.supports(context)]
        providers.sort(key=lambda p: p.priority())
        return providers

    def build_all(
        self,
        context: MissionContext,
        opportunities: list[OpportunitySnapshot],
    ) -> list[ProviderResult]:
        """Run all applicable providers and return results.

        Each provider receives the full opportunities list and decides
        which ones to create tasks for. This lets providers inspect
        the full landscape before generating tasks.
        """
        results: list[ProviderResult] = []
        for provider in self.applicable(context):
            result = provider.build(context, opportunities)
            results.append(result)
        return results

    @property
    def count(self) -> int:
        return len(self._providers)

    @property
    def kinds(self) -> list[str]:
        return list(self._providers.keys())
