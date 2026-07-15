"""Career Preparation Engine — generates study plans, likely questions, and prep checklists for interviews.

Usage: jobzo prepare <job-id>

Output:
- Company context (from JD)
- Tech stack analysis
- Likely interview questions by category
- Study plan with estimated prep time
- Revision checklist
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from resumes.jd_analyzer import JDAnalysis, analyze as analyze_jd, DOMAIN_KEYWORDS
from skills import skill_category

logger = logging.getLogger("jobzo.prepare")

# Question templates per category
QUESTION_TEMPLATES: dict[str, list[str]] = {
    "Java": [
        "Explain Java memory model and garbage collection.",
        "How do ConcurrentHashMap and synchronized collections differ?",
        "What are virtual threads (Project Loom)?",
        "Explain the difference between Comparable and Comparator.",
        "How does the JVM classloading work?",
        "What is the difference between fail-fast and fail-safe iterators?",
    ],
    "Spring Boot": [
        "How does Spring Boot auto-configuration work?",
        "Explain the Spring bean lifecycle.",
        "What is the difference between @Component, @Service, and @Repository?",
        "How do you handle transactions in Spring?",
        "Explain Spring Security filter chain.",
        "How does Spring Boot manage dependency injection?",
    ],
    "Microservices": [
        "How do you handle distributed transactions?",
        "Explain service discovery and circuit breaker patterns.",
        "How do you handle inter-service communication?",
        "What is the difference between orchestration and choreography?",
        "How do you monitor microservices?",
    ],
    "PostgreSQL": [
        "How do you optimize slow queries?",
        "Explain indexing strategies (B-tree, hash, GIN, GiST).",
        "What is the difference between JOIN types?",
        "How does MVCC work in PostgreSQL?",
        "Explain query planning and EXPLAIN ANALYZE.",
    ],
    "Redis": [
        "What data structures does Redis support?",
        "How does Redis handle persistence (RDB vs AOF)?",
        "Explain Redis cluster architecture.",
        "How do you handle cache invalidation?",
        "What is the difference between Redis and Memcached?",
    ],
    "Kafka": [
        "Explain Kafka topic partitioning and consumer groups.",
        "How does Kafka achieve high throughput?",
        "What is the difference between at-least-once and exactly-once semantics?",
        "How do you handle rebalancing in Kafka?",
        "Explain Kafka Streams vs Kafka Connect.",
    ],
    "Docker": [
        "How does Docker containerization differ from VMs?",
        "Explain Dockerfile multi-stage builds.",
        "What is the difference between CMD and ENTRYPOINT?",
        "How do you handle networking between containers?",
    ],
    "Kubernetes": [
        "Explain Pod, Deployment, StatefulSet, and DaemonSet.",
        "How does Kubernetes service discovery work?",
        "What is the difference between Ingress and LoadBalancer?",
        "How does horizontal pod autoscaling work?",
        "Explain ConfigMap and Secret use cases.",
    ],
    "AWS": [
        "Explain the difference between EC2, Lambda, and ECS.",
        "How does S3 consistency work?",
        "What is the difference between RDS and DynamoDB?",
        "How do you design a VPC with public and private subnets?",
        "Explain SQS vs SNS vs EventBridge.",
    ],
    "System Design": [
        "Design a URL shortener (tinyurl).",
        "Design a rate limiter.",
        "Design a distributed cache.",
        "Design a real-time chat system.",
        "Design a payment system.",
        "Design a notification service.",
    ],
    "SQL": [
        "Write a query to find duplicate rows.",
        "Explain window functions with examples.",
        "What is the difference between clustered and non-clustered indexes?",
        "How do you optimize a query using JOINs?",
        "Explain normalization and denormalization.",
    ],
    "OOP": [
        "Explain SOLID principles with examples.",
        "What is the difference between composition and inheritance?",
        "How do you design a parking lot (OO design)?",
        "Explain polymorphism and when to use interfaces vs abstract classes.",
    ],
    "Behavioral": [
        "Tell me about a time you handled a production incident.",
        "Describe a conflict with a teammate and how you resolved it.",
        "What project are you most proud of?",
        "Why do you want to work here?",
        "Where do you see yourself in 5 years?",
    ],
}

CONCEPT_CATEGORIES: dict[str, str] = {
    "Microservices": "Backend",
    "Kafka": "Infrastructure",
    "Docker": "Infrastructure",
    "Kubernetes": "Infrastructure",
    "System Design": "Concept",
    "AWS": "Cloud",
    "Redis": "Database",
    "PostgreSQL": "Database",
}


def _map_skill_to_question_category(skill: str) -> str | None:
    cat = skill_category(skill)
    if cat in ("Language",):
        return skill
    if cat == "Backend" and skill in ("Spring Boot", "Spring", "REST APIs", "Microservices"):
        return skill if skill in QUESTION_TEMPLATES else {
            "Spring Boot": "Spring Boot", "Spring": "Spring Boot",
            "Microservices": "Microservices", "REST APIs": "Spring Boot",
        }.get(skill)
    if skill in QUESTION_TEMPLATES:
        return skill
    if cat == "Database":
        if skill in ("PostgreSQL", "Redis", "MongoDB"):
            return skill
    if cat == "Infrastructure":
        return skill if skill in ("Docker", "Kubernetes", "Kafka") else None
    if cat == "Cloud":
        return skill if skill == "AWS" else None
    return None


@dataclass
class StudySection:
    topic: str
    estimated_minutes: int
    questions: list[str] = field(default_factory=list)

    def format_text(self) -> str:
        lines = [f"\n  {self.topic} (~{self.estimated_minutes} min)"]
        for q in self.questions[:3]:
            lines.append(f"    • {q}")
        if len(self.questions) > 3:
            lines.append(f"    ... and {len(self.questions) - 3} more")
        return "\n".join(lines)


@dataclass
class PreparationPlan:
    company: str = ""
    title: str = ""
    location: str = ""
    analysis: JDAnalysis | None = None
    sections: list[StudySection] = field(default_factory=list)
    total_estimated_minutes: int = 0
    checklist: list[str] = field(default_factory=list)

    def format_text(self) -> str:
        lines: list[str] = []
        lines.append(f"{'='*60}")
        lines.append(f"  Preparing for")
        lines.append(f"  {self.company}")
        lines.append(f"  Role: {self.title}")
        lines.append(f"{'='*60}")

        if self.analysis and self.analysis.skills:
            lines.append(f"\n  Tech Stack: {', '.join(self.analysis.skills[:8])}")

        if self.sections:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  Study Plan")
            lines.append(f"{'─'*60}")
            for section in self.sections:
                lines.append(section.format_text())

            lines.append(f"\n{'─'*60}")
            lines.append(f"  Estimated Prep Time: {self.total_estimated_minutes} min")
            lines.append(f"{'─'*60}")

        if self.checklist:
            lines.append(f"\n  Checklist")
            for item in self.checklist:
                lines.append(f"    ☐ {item}")

        lines.append(f"\n{'='*60}")
        return "\n".join(lines)


def prepare(
    company: str,
    title: str,
    jd_text: str,
    location: str = "",
    use_llm: bool = False,
) -> PreparationPlan:
    """Generate interview preparation plan from a job description."""
    analysis = analyze_jd(jd_text, use_llm=use_llm)
    plan = PreparationPlan(
        company=company,
        title=title,
        location=location,
        analysis=analysis,
    )

    if not analysis or not analysis.skills:
        # Fallback with generic backend questions
        plan.sections.append(StudySection(
            topic="General Backend",
            estimated_minutes=60,
            questions=QUESTION_TEMPLATES.get("System Design", [])[:3]
                         + QUESTION_TEMPLATES.get("Behavioral", [])[:3],
        ))
        plan.total_estimated_minutes = 60
        plan.checklist = ["Research company", "Review your resume"]
        return plan

    # Build study sections from JD skills
    seen_categories: set[str] = set()
    total_minutes = 0

    for skill in analysis.skills:
        qcat = _map_skill_to_question_category(skill)
        if qcat and qcat in QUESTION_TEMPLATES and qcat not in seen_categories:
            seen_categories.add(qcat)
            questions = QUESTION_TEMPLATES[qcat]
            minutes = min(len(questions) * 8, 45)  # 8 min per question, cap at 45
            if "System Design" in qcat or skill in ("Kubernetes", "Kafka", "Microservices"):
                minutes = 45  # harder topics need more time
            elif qcat in ("Java", "Spring Boot"):
                minutes = 35
            section = StudySection(
                topic=qcat,
                estimated_minutes=minutes,
                questions=questions,
            )
            plan.sections.append(section)
            total_minutes += minutes

    # Always add behavioral
    if "Behavioral" not in seen_categories:
        plan.sections.append(StudySection(
            topic="Behavioral",
            estimated_minutes=20,
            questions=QUESTION_TEMPLATES["Behavioral"],
        ))
        total_minutes += 20

    # Add system design for senior roles
    if analysis.experience_level in ("senior", "staff"):
        if "System Design" not in seen_categories:
            plan.sections.append(StudySection(
                topic="System Design",
                estimated_minutes=45,
                questions=QUESTION_TEMPLATES["System Design"],
            ))
            total_minutes += 45

    plan.total_estimated_minutes = total_minutes

    # Build checklist
    plan.checklist = [
        f"Review {', '.join([s.topic for s in plan.sections[:3]])}",
        "Research company products and engineering culture",
        "Review your relevant projects (JobZo, Billed-Core, vtracer)",
        "Prepare 2-3 questions to ask the interviewer",
        "Set up your development environment / whiteboard",
    ]

    return plan
