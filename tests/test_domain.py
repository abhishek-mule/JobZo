"""Tests for Phase 3.0 — domain model, providers, planner, execution."""

from datetime import date
from domain.models import (
    DependencyKind, Dependency, TaskNode, Mission, MissionContext, OpportunityView,
)
from domain.providers import ApplyTaskProvider
from domain.planner import GreedyPlanner
from domain.execution import MissionExecution


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
    assert t1.value_density == 5.0  # 100/20
    assert t2.value_density == 10.0  # 50/5
    assert t2.value_density > t1.value_density  # B is denser


def test_task_dependencies():
    t1 = TaskNode(id="t1", kind="apply", title="Base", description="", source="test")
    t2 = TaskNode(id="t2", kind="apply", title="Depends", description="", source="test",
                  dependencies=[Dependency(task_id="t1", kind=DependencyKind.REQUIRES)])

    assert not t2.can_execute()  # t1 not completed

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
    assert restored.title == t.title
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


def test_apply_provider_basic():
    context = MissionContext(time_budget=60, goal="Get placed ASAP")
    opp = OpportunityView(
        snapshot_id="s1", opportunity_id="o1", job_id="j1",
        company="Acme", title="Engineer", url="https://acme.com/job",
        score=82, tier="strong_match", interview_probability=45,
        confidence="High", risk="Medium", effort_minutes=15,
        canonical_role="BACKEND_ENGINEER", seniority="mid",
        matched_skills=["python", "java"], missing_skills=["k8s"],
    )
    provider = ApplyTaskProvider()
    tasks = provider.build(context, [opp])
    assert len(tasks) == 1
    t = tasks[0]
    assert t.kind == "apply"
    assert "Acme" in t.title
    assert t.estimated_minutes == 15
    assert t.expected_value == 45.0  # interview_probability * 1.0 for ASAP
    assert t.urgency == "high"  # score >= 80
    assert len(t.why()) >= 2


def test_apply_provider_filters_low_score():
    context = MissionContext(time_budget=60)
    opp = OpportunityView(
        snapshot_id="s1", opportunity_id="o1", job_id="j1",
        company="Acme", title="Engineer", url="https://acme.com/job",
        score=30, tier="stretch", interview_probability=10,
        confidence="Low", risk="Hard", effort_minutes=15,
        canonical_role="BACKEND_ENGINEER", seniority="mid",
    )
    provider = ApplyTaskProvider()
    tasks = provider.build(context, [opp])
    assert len(tasks) == 0  # below min_score (60)


def test_greedy_planner():
    context = MissionContext(time_budget=30, goal="Get placed ASAP")
    tasks = [
        TaskNode(id="t1", kind="apply", title="High density", description="", source="test",
                 expected_value=50.0, estimated_minutes=10),
        TaskNode(id="t2", kind="apply", title="Medium density", description="", source="test",
                 expected_value=30.0, estimated_minutes=10),
        TaskNode(id="t3", kind="apply", title="Low density", description="", source="test",
                 expected_value=10.0, estimated_minutes=10),
    ]
    planner = GreedyPlanner()
    mission = planner.plan(tasks, context)
    assert len(mission.tasks) == 3  # all fit in 30m budget
    assert mission.tasks[0].id == "t1"  # highest value density first
    assert mission.estimated_minutes == 30
    assert mission.expected_gain == 90.0


def test_greedy_planner_budget_limit():
    context = MissionContext(time_budget=15, goal="Get placed ASAP")
    tasks = [
        TaskNode(id="t1", kind="apply", title="A", description="", source="test",
                 expected_value=50.0, estimated_minutes=10),
        TaskNode(id="t2", kind="apply", title="B", description="", source="test",
                 expected_value=40.0, estimated_minutes=10),
    ]
    planner = GreedyPlanner()
    mission = planner.plan(tasks, context)
    assert len(mission.tasks) == 1  # only 1 fits
    assert mission.tasks[0].id == "t1"  # highest value density


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
    mission = planner.plan(tasks, context)
    assert len(mission.tasks) == 2
    # t1 should come before t2 (dependency order)
    assert mission.tasks[0].id == "t1"
    assert mission.tasks[1].id == "t2"


def test_mission_execution_basic():
    t1 = TaskNode(id="t1", kind="apply", title="Task 1", description="", source="test")
    m = Mission(id="m1", generated_at=__import__("datetime").datetime.utcnow(),
                objective="Test", estimated_minutes=10, expected_gain=20.0,
                confidence="High", tasks=[t1])
    exec_engine = MissionExecution(m)

    # Start
    assert exec_engine.start().state == "active"

    # Execute task
    assert exec_engine.execute_task("t1")
    assert t1.state == "active"

    # Complete task
    assert exec_engine.complete_task("t1")
    assert t1.is_completed

    # Next actionable should be empty
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
    assert t1.state == "deferred"  # active tasks become deferred on pause

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


class _MockSnapshot:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _MockApp:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _MockJob:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_opportunity_view_from_snapshot():
    mock_snapshot = _MockSnapshot(
        id="s1", application_id="a1",
        composite_score=82, tier="strong_match",
        interview_probability=45, confidence="High", risk="Medium",
        effort_minutes=15, canonical_role="BACKEND_ENGINEER", seniority="mid",
        details_json='{"matched_skills": ["python"], "missing_skills": [["k8s", 0.5, "missing"]]}',
    )
    mock_app = _MockApp(id="a1")
    mock_job = _MockJob(company="Acme Corp", title="Backend Engineer",
                        url="https://acme.com/job", source="company_pages")

    opp = OpportunityView.from_snapshot(mock_snapshot, mock_app, mock_job)
    assert opp.company == "Acme Corp"
    assert opp.score == 82
    assert opp.interview_probability == 45
    assert opp.matched_skills == ["python"]
    assert opp.missing_skills == ["k8s"]
