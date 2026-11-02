from __future__ import annotations
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).parent.parent / "resumes" / "master" / "profile.yaml"

DEFAULT_PROFILE = {
    "name": "",
    "email": "",
    "location": "",
    "career_stage": "student",
    "graduation_year": None,
    "experience_years": 0,
    "internships": 0,
    "strategy": "balanced",
    "resume": "",
    "declared_skills": [],
    "desired_role_families": ["software", "backend", "devops"],
    "observed_preferences": {},
}


def load_profile() -> dict[str, Any]:
    import yaml
    if not PROFILE_PATH.exists():
        return dict(DEFAULT_PROFILE)
    with open(PROFILE_PATH) as f:
        data = yaml.safe_load(f) or {}
    profile = dict(DEFAULT_PROFILE)
    profile.update(data)
    return profile


def save_profile(updates: dict[str, Any]):
    import yaml
    current = load_profile()
    current.update(updates)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_PATH, "w") as f:
        yaml.dump(current, f, default_flow_style=False, sort_keys=False)


def is_complete(profile: dict[str, Any] | None = None) -> bool:
    if profile is None:
        profile = load_profile()
    required = ["career_stage", "experience_years"]
    for key in required:
        if key not in profile or profile[key] is None:
            return False
    if profile.get("career_stage") == "student" and profile.get("graduation_year") is None:
        return False
    return True


def prompt_setup(console) -> dict[str, Any]:
    from rich.prompt import Prompt, IntPrompt

    console.print("[bold cyan]Let's set up your profile[/bold cyan]")
    console.print()

    stages = {"1": "student", "2": "graduate", "3": "junior", "4": "mid", "5": "senior"}
    console.print("What's your career stage?")
    for k, v in stages.items():
        console.print(f"  [{k}] {v.title()}")
    stage_choice = Prompt.ask("  →", default="1")
    career_stage = stages.get(stage_choice, "student")

    graduation_year = None
    if career_stage == "student":
        graduation_year = IntPrompt.ask("  Graduation year", default=2027)

    experience_years = IntPrompt.ask("  Years of experience", default=0)
    internships = IntPrompt.ask("  Internships completed", default=0)

    strategies = {"1": "safe", "2": "balanced", "3": "aggressive", "4": "dream"}
    console.print("\nWhat's your application strategy?")
    console.print("  [1] Safe — only high-probability roles")
    console.print("  [2] Balanced — includes stretch roles")
    console.print("  [3] Aggressive — includes ambitious targets")
    console.print("  [4] Dream — everything")
    strat_choice = Prompt.ask("  →", default="2")
    strategy = strategies.get(strat_choice, "balanced")

    console.print("\nYour core skills (comma-separated, e.g. Java, Spring Boot, React):")
    skills_input = Prompt.ask("  Skills", default="Java, Spring Boot")
    declared_skills = [s.strip() for s in skills_input.split(",") if s.strip()]

    console.print("\nDesired role families (comma-separated, e.g. software, backend, devops):")
    families_input = Prompt.ask("  Families", default="software, backend, devops")
    desired_role_families = [f.strip() for f in families_input.split(",") if f.strip()]

    profile = {
        "career_stage": career_stage,
        "graduation_year": graduation_year,
        "experience_years": experience_years,
        "internships": internships,
        "strategy": strategy,
        "declared_skills": declared_skills,
        "desired_role_families": desired_role_families,
    }
    save_profile(profile)
    return load_profile()


def strategy_multiplier(strategy: str) -> float:
    return {"safe": 0.7, "balanced": 1.0, "aggressive": 1.3, "dream": 1.5}.get(strategy, 1.0)
