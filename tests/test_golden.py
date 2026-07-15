import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.validator import ScoreResult


def load_golden_tests():
    golden_dir = Path(__file__).parent / "golden"
    tests = []

    for md_file in sorted(golden_dir.glob("*.md")):
        json_file = md_file.with_suffix(".json")
        if json_file.exists():
            with open(md_file) as f:
                job_text = f.read()
            with open(json_file) as f:
                expected = json.load(f)
            tests.append((md_file.stem, job_text, expected))

    return tests


def test_golden_scores():
    from ai.retriever import retrieve, _extract_skills, _match_skills, _experience_match, _location_match
    from ai.skill_graph import expand
    from datetime import datetime
    from dataclasses import dataclass

    @dataclass
    class FakeJob:
        id: str = ""
        company: str = ""
        title: str = ""
        description: str = ""
        location: str = ""
        salary: str = ""
        experience_required: str = ""
        skills: list = None
        url: str = ""
        source: str = ""
        posted_at: datetime | None = None
        remote: bool = False
        raw_html: str = ""
        is_active: bool = True
        eligible: bool = True

    tests = load_golden_tests()
    user_skills = ["spring boot", "java", "react", "typescript", "postgresql", "docker"]

    for name, job_text, expected in tests:
        lines = job_text.strip().split("\n")
        company = ""
        title = ""
        desc_lines = []
        in_desc = False

        for line in lines:
            if line.startswith("Company:"):
                company = line.split(":", 1)[1].strip()
            elif line.startswith("Role:"):
                title = line.split(":", 1)[1].strip()
            elif line.startswith("---"):
                in_desc = True
            elif in_desc or line.startswith("We're") or line.startswith("Requirements"):
                in_desc = True

        if not in_desc:
            first_blank = job_text.find("\n\n")
            if first_blank > 0:
                desc_text = job_text[first_blank:].strip()
            else:
                desc_text = job_text
        else:
            desc_text = job_text

        fj = FakeJob(
            company=company, title=title, description=desc_text,
            location="", salary="",
            experience_required=expected.get("experience_required", ""),
            skills=[], url="", source="test",
            posted_at=datetime.utcnow(), remote=False,
        )

        opp = retrieve(fj, user_skills, 1)
        retrieved = opp is not None
        score = opp.composite_score() if opp else 0
        tier = opp.tier() if opp else "ignore"

        validity = "skip" if not retrieved else "review"

        print(f"\n=== {name} ===")
        print(f"  Company: {company} | Title: {title}")
        print(f"  Retrieved: {retrieved}")
        print(f"  Score: {score} | Tier: {tier}")
        print(f"  Filter verdict: {validity}")
        print(f"  Expected score: {expected['score']}")
        print(f"  Expected strategy: {expected['strategy']}")

        if retrieved:
            print(f"  Matched skills: {opp.matched_skills}")
        else:
            print(f"  VERDICT: Filtered out")

    print("\n--- Golden test summary ---")
    print(f"{len(tests)} tests loaded")


if __name__ == "__main__":
    test_golden_scores()
