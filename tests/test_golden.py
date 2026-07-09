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
    from ai.llm import ask
    from ai.scorer import _keyword_pre_score, _skill_overlap, _experience_match, _location_match
    from services.freshness import freshness_score
    from datetime import datetime
    from dataclasses import dataclass

    @dataclass
    class FakeJob:
        company: str
        title: str
        description: str
        location: str
        salary: str
        experience_required: str
        skills: list
        url: str
        source: str
        posted_at: datetime | None
        remote: bool

    tests = load_golden_tests()

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
            elif line.startswith("Location:"):
                pass
            elif line.startswith("Experience:"):
                pass
            elif line.startswith("---"):
                in_desc = True
            elif in_desc or line.startswith("We're") or line.startswith("Requirements") or line.startswith("Google") or line.startswith("Stripe"):
                in_desc = True

        if not in_desc:
            first_blank = job_text.find("\n\n")
            if first_blank > 0:
                desc_text = job_text[first_blank:].strip()
            else:
                desc_text = job_text
        else:
            desc_text = job_text

        kw_score = _keyword_pre_score(FakeJob(
            company=company, title=title, description=desc_text,
            location="", salary="", experience_required="",
            skills=[], url="", source="test",
            posted_at=datetime.utcnow(), remote=False,
        ))

        overlap, matched = _skill_overlap(FakeJob(
            company=company, title=title, description=desc_text,
            location="", salary="", experience_required="",
            skills=[], url="", source="test",
            posted_at=datetime.utcnow(), remote=False,
        ), ["spring boot", "java", "react", "typescript", "postgresql", "docker"])

        validity = "skip" if kw_score < 20 else "review"

        print(f"\n=== {name} ===")
        print(f"  Company: {company} | Title: {title}")
        print(f"  Keyword score: {kw_score}")
        print(f"  Skill overlap: {overlap:.2f} (matched: {matched})")
        print(f"  Filter verdict: {validity}")
        print(f"  Expected score: {expected['score']}")
        print(f"  Expected strategy: {expected['strategy']}")

        if validity == "skip":
            print(f"  VERDICT: Filtered out (below keyword threshold)")
        else:
            print(f"  VERDICT: Passes filters, would be sent to LLM")

        print(f"  Keyword filter {'PASS' if (kw_score >= 20) == (expected['score'] >= 30) else 'CHECK'}")

    print("\n--- Golden test summary ---")
    print(f"{len(tests)} tests loaded")


if __name__ == "__main__":
    test_golden_scores()
