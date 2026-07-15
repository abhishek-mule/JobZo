"""Tests for Phase 3.0 — domain model, providers, planner, execution, registry, simulation."""

from datetime import date
from domain.models import (
    DependencyKind, Dependency, TaskNode, Mission, MissionContext,
    OpportunitySnapshot, ProviderResult,
)
from domain.providers import ApplyTaskProvider
from domain.planner import GreedyPlanner
from domain.execution import MissionExecution
from domain.registry import TaskProviderRegistry
from domain.simulation import simulate, generate_task_pool, SimulationConfig


def test_task_node_lifecycle():
    task = TaskNode(id="t1", kind="apply", title="Test", description="", source="test")
    assert task.state == "pending"
    assert task.is_pending
    assert task.is_actionable

    task.execute()
    assert task.state == "active"

    task.complete()
    assert task.is_completed
    assert not task.is_actionable


def test_task_skip_and_defer():
    task = TaskNode(id="t1", kind="apply", title="Test", description="", source="test")
    task.skip("Not interested")
    assert task.state == "skipped"
    assert not task.is_actionable

    task2 = TaskNode(id="t2", kind="apply", title="Test", description="", source="test")
    task2.defer()
    assert task2.state == "deferred"
    assert task2.is_actionable


def test_task_value_density():
    t1 = TaskNode(id="t1", kind="apply", title="A", description="", source="test",
                  expected_value=100.0, estimated_minutes=20)
    t2 = TaskNode(id="t2", kind="apply", title="B", description="", source="test",
                  expected_value=50.0, estimated_minutes=5)
    assert t1.value_density == 5.0
    assert t2.value_density == 10.0
    assert t2.value_density > t1.value_density


def test_task_dependencies():
    t1 = TaskNode(id="t1", kind="apply", title="Base", description="", source="test")
    t2 = TaskNode(id="t2", kind="apply", title="Depends", description="", source="test",
                  dependencies=[Dependency(task_id="t1", kind=DependencyKind.REQUIRES)])

    assert not t2.can_execute()
    t1.complete()
    t2._completed_ids = {"t1"}
    assert t2.can_execute()


def test_task_why():
    t = TaskNode(id="t1", kind="apply", title="Apply to Acme", description="", source="test",
                 expected_value=14.5)
    t.add_why("Score: 82/100")
    t.add_why("Interview probability: 45%")
    lines = t.why()
    assert len(lines) == 2
    assert "Score: 82/100" in lines


def test_task_serialization_roundtrip():
    t = TaskNode(
        id="t1", kind="apply", title="Apply to Acme", description="Submit app",
        source="test", opportunity_id="opp-1", estimated_minutes=15, expected_value=14.5,
        uncertainty=3.0, urgency="high",
        dependencies=[Dependency(task_id="t2", kind=DependencyKind.REQUIRES)],
        metadata={"company": "Acme"},
    )
    t.add_why("Good match")
    data = t.to_dict()
    restored = TaskNode.from_dict(data)
    assert restored.id == t.id
    assert restored.kind == t.kind
    assert restored.expected_value == t.expected_value
    assert len(restored.dependencies) == 1
    assert restored.dependencies[0].task_id == "t2"
    assert restored._why_lines == ["Good match"]


def test_mission_progress():
    t1 = TaskNode(id="t1", kind="apply", title="A", description="", source="test")
    t2 = TaskNode(id="t2", kind="apply", title="B", description="", source="test")
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=30, expected_gain=50.0,
                confidence="High", tasks=[t1, t2])
    assert m.progress == 0.0
    t1.complete()
    assert m.progress == 0.5
    t2.complete()
    assert m.progress == 1.0


def test_mission_serialization():
    t1 = TaskNode(id="t1", kind="apply", title="A", description="", source="test")
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=15, expected_gain=20.0,
                confidence="High", tasks=[t1])
    data = m.to_dict()
    restored = Mission.from_dict(data)
    assert restored.id == m.id
    assert restored.objective == m.objective
    assert len(restored.tasks) == 1
    assert restored.tasks[0].id == "t1"


def test_provider_result():
    t1 = TaskNode(id="t1", kind="apply", title="A", description="", source="test",
                  expected_value=15.0)
    result = ProviderResult(
        provider="apply", provider_version="1",
        tasks=[t1], warnings=["low confidence"],
        statistics={"created": 1},
    )
    assert result.task_count == 1
    assert result.total_estimated_value == 15.0
    assert "low confidence" in result.warnings


def test_provider_result_merge():
    t1 = TaskNode(id="t1", kind="apply", title="A", description="", source="test")
    t2 = TaskNode(id="t2", kind="apply", title="B", description="", source="test")
    r1 = ProviderResult(provider="apply", provider_version="1", tasks=[t1])
    r2 = ProviderResult(provider="apply", provider_version="1", tasks=[t2])
    merged = r1.merge(r2)
    assert len(merged.tasks) == 2


def test_apply_provider_basic():
    context = MissionContext(time_budget=60, goal="Get placed ASAP")
    opp = OpportunitySnapshot(
        snapshot_id="s1", opportunity_id="o1", job_id="j1",
        company="Acme", title="Engineer", url="https://acme.com/job",
        score=82, tier="strong_match", interview_probability=45,
        confidence="High", risk="Medium", effort_minutes=15,
        canonical_role="BACKEND_ENGINEER", seniority="mid",
        matched_skills=["python", "java"], missing_skills=["k8s"],
    )
    provider = ApplyTaskProvider()
    result = provider.build(context, [opp])
    assert len(result.tasks) == 1
    t = result.tasks[0]
    assert t.kind == "apply"
    assert "Acme" in t.title
    assert t.estimated_minutes == 15
    assert t.expected_value == 45.0
    assert t.urgency == "high"
    assert result.statistics["tasks_created"] == 1


def test_apply_provider_filters_low_score():
    context = MissionContext(time_budget=60)
    opp = OpportunitySnapshot(
        snapshot_id="s1", opportunity_id="o1", job_id="j1",
        company="Acme", title="Engineer", url="https://acme.com/job",
        score=30, tier="stretch", interview_probability=10,
        confidence="Low", risk="Hard", effort_minutes=15,
        canonical_role="BACKEND_ENGINEER", seniority="mid",
    )
    provider = ApplyTaskProvider()
    result = provider.build(context, [opp])
    assert len(result.tasks) == 0


def test_greedy_planner():
    context = MissionContext(time_budget=30, goal="Get placed ASAP")
    results = [
        ProviderResult(provider="apply", provider_version="1", tasks=[
            TaskNode(id="t1", kind="apply", title="High", description="", source="test",
                     expected_value=50.0, estimated_minutes=10),
            TaskNode(id="t2", kind="apply", title="Medium", description="", source="test",
                     expected_value=30.0, estimated_minutes=10),
            TaskNode(id="t3", kind="apply", title="Low", description="", source="test",
                     expected_value=10.0, estimated_minutes=10),
        ]),
    ]
    planner = GreedyPlanner()
    mission = planner.plan(results, context)
    assert len(mission.tasks) == 3
    assert mission.tasks[0].id == "t1"
    assert mission.estimated_minutes == 30
    assert mission.expected_gain == 90.0
    assert len(mission.provider_results) == 1
    assert "planner" in mission.plan_provenance


def test_greedy_planner_budget_limit():
    context = MissionContext(time_budget=15)
    results = [
        ProviderResult(provider="apply", provider_version="1", tasks=[
            TaskNode(id="t1", kind="apply", title="A", description="", source="test",
                     expected_value=50.0, estimated_minutes=10),
            TaskNode(id="t2", kind="apply", title="B", description="", source="test",
                     expected_value=40.0, estimated_minutes=10),
        ]),
    ]
    planner = GreedyPlanner()
    mission = planner.plan(results, context)
    assert len(mission.tasks) == 1
    assert len(mission.rejected_tasks) == 1
    assert mission.rejected_tasks[0].id == "t2"


def test_planner_dependency_resolution():
    tasks = [
        TaskNode(id="t2", kind="apply", title="Depends", description="", source="test",
                 expected_value=50.0, estimated_minutes=10,
                 dependencies=[Dependency(task_id="t1", kind=DependencyKind.REQUIRES)]),
        TaskNode(id="t1", kind="apply", title="Base", description="", source="test",
                 expected_value=10.0, estimated_minutes=10),
    ]
    context = MissionContext(time_budget=30)
    planner = GreedyPlanner()
    mission = planner.plan(
        [ProviderResult(provider="apply", provider_version="1", tasks=tasks)],
        context,
    )
    assert len(mission.tasks) == 2
    assert mission.tasks[0].id == "t1"
    assert mission.tasks[1].id == "t2"


def test_mission_execution_basic():
    t1 = TaskNode(id="t1", kind="apply", title="Task 1", description="", source="test")
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=10, expected_gain=20.0,
                confidence="High", tasks=[t1])
    exec_engine = MissionExecution(m)

    assert exec_engine.start().state == "active"
    assert exec_engine.execute_task("t1")
    assert t1.state == "active"
    assert exec_engine.complete_task("t1")
    assert t1.is_completed
    assert len(exec_engine.next_actionable()) == 0


def test_mission_execution_pause_resume():
    t1 = TaskNode(id="t1", kind="apply", title="Task 1", description="", source="test")
    t1.execute()
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=10, expected_gain=20.0,
                confidence="High", tasks=[t1])
    exec_engine = MissionExecution(m)

    exec_engine.pause()
    assert m.state == "paused"
    assert t1.state == "deferred"

    exec_engine.resume()
    assert m.state == "active"


def test_mission_execution_skip():
    t1 = TaskNode(id="t1", kind="apply", title="Task 1", description="", source="test")
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=10, expected_gain=20.0,
                confidence="High", tasks=[t1])
    exec_engine = MissionExecution(m)
    exec_engine.skip_task("t1", "Not relevant")
    assert t1.state == "skipped"
    assert "Not relevant" in t1.why()[-1]


def test_opportunity_snapshot_from_snapshot():
    class _Mock:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    mock_snapshot = _Mock(
        id="s1", application_id="a1",
        composite_score=82, tier="strong_match",
        interview_probability=45, confidence="High", risk="Medium",
        effort_minutes=15, canonical_role="BACKEND_ENGINEER", seniority="mid",
        details_json='{"matched_skills": ["python"], "missing_skills": [["k8s", 0.5, "missing"]]}',
    )
    mock_app = _Mock(id="a1")
    mock_job = _Mock(company="Acme Corp", title="Backend Engineer",
                     url="https://acme.com/job", source="company_pages")

    opp = OpportunitySnapshot.from_snapshot(mock_snapshot, mock_app, mock_job)
    assert opp.company == "Acme Corp"
    assert opp.score == 82
    assert opp.interview_probability == 45
    assert opp.matched_skills == ["python"]
    assert opp.missing_skills == ["k8s"]


# ── Registry tests ──────────────────────────────────────────────────────────

def test_registry_register_and_get():
    registry = TaskProviderRegistry()
    provider = ApplyTaskProvider()
    registry.register(provider)
    assert registry.get("apply") is provider
    assert registry.count == 1
    assert "apply" in registry.kinds


def test_registry_unregister():
    registry = TaskProviderRegistry()
    registry.register(ApplyTaskProvider())
    registry.unregister("apply")
    assert registry.get("apply") is None


def test_registry_applicable_filters_by_context():
    registry = TaskProviderRegistry()
    registry.register(ApplyTaskProvider())

    # ApplyTaskProvider always supports any context
    context = MissionContext(goal="Get placed ASAP")
    applicable = registry.applicable(context)
    assert len(applicable) == 1


def test_registry_build_all():
    registry = TaskProviderRegistry()
    registry.register(ApplyTaskProvider())

    context = MissionContext(time_budget=60)
    opp = OpportunitySnapshot(
        snapshot_id="s1", opportunity_id="o1", job_id="j1",
        company="Acme", title="Engineer", url="https://acme.com/job",
        score=82, tier="strong_match", interview_probability=45,
        confidence="High", risk="Medium", effort_minutes=15,
        canonical_role="BACKEND_ENGINEER", seniority="mid",
    )

    results = registry.build_all(context, [opp])
    assert len(results) == 1
    assert results[0].provider == "apply"
    assert len(results[0].tasks) == 1


def test_registry_priority_ordering():
    """Test that providers are sorted by priority()."""
    registry = TaskProviderRegistry()

    class LowPrioProvider:
        def kind(self): return "low"
        def version(self): return "1"
        def priority(self): return 100
        def supports(self, ctx): return True
        def build(self, ctx, opps):
            return ProviderResult(provider="low", provider_version="1")

    class HighPrioProvider:
        def kind(self): return "high"
        def version(self): return "1"
        def priority(self): return 5
        def supports(self, ctx): return True
        def build(self, ctx, opps):
            return ProviderResult(provider="high", provider_version="1")

    registry.register(LowPrioProvider())
    registry.register(HighPrioProvider())

    context = MissionContext()
    applicable = registry.applicable(context)
    assert applicable[0].kind() == "high"
    assert applicable[1].kind() == "low"


# ── Simulation tests ────────────────────────────────────────────────────────

def test_simulation_generates_tasks():
    config = SimulationConfig(seed=42)
    pool = generate_task_pool(10, config)
    assert len(pool) == 10
    for t in pool:
        assert t.expected_value > 0
        assert t.estimated_minutes > 0


def test_simulation_runs():
    planner = GreedyPlanner()
    config = SimulationConfig(days=10, seed=42, daily_budget=30)
    pool = generate_task_pool(20, config)
    result = simulate(planner, config, pool)
    assert result.total_applications > 0
    assert len(result.daily_logs) == 10
    assert 0 <= result.budget_utilization <= 1.0


def test_simulation_higher_budget_yields_more_applications():
    planner = GreedyPlanner()
    pool = generate_task_pool(20, SimulationConfig(seed=99))

    low = simulate(planner, SimulationConfig(days=5, seed=99, daily_budget=15), pool)
    high = simulate(planner, SimulationConfig(days=5, seed=99, daily_budget=60), pool)

    assert high.total_applications >= low.total_applications


def test_simulation_summary():
    planner = GreedyPlanner()
    config = SimulationConfig(days=5, seed=42)
    pool = generate_task_pool(10, config)
    result = simulate(planner, config, pool)
    summary = result.summary()
    assert "days" in summary
    assert "applications" in summary
    assert "offers" in summary
    assert "budget_utilization" in summary
