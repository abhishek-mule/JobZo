import logging

from services.config import Config

logger = logging.getLogger("jobzo.cover_letter")


def generate_cover_letter(
    company: str,
    role: str,
    description: str,
    resume_type: str = "",
) -> str:
    try:
        from ai.llm import ask
        result = ask("cover_letter", f"""Company: {company}
Role: {role}
Description: {description[:1500]}
Resume type: {resume_type}""")
        if isinstance(result, dict):
            text = result.get("cover_letter", "")
        else:
            text = str(result)
        if text and len(text) > 50:
            return text
    except Exception as e:
        logger.debug("LLM cover letter failed: %s", e)

    return _template_fallback(company, role, description, resume_type)


def _template_fallback(
    company: str,
    role: str,
    description: str,
    resume_type: str = "",
) -> str:
    profile = _get_profile_summary()
    skills_text = ", ".join(profile.get("skills", ["software engineering"]))
    experience_years = profile.get("years_experience", "1+")

    letter = f"""Dear Hiring Manager,

I am excited to apply for the {role} position at {company}. As a backend-focused Software Engineer with {experience_years} year(s) of experience in {skills_text}, I am confident that my technical skills and problem-solving approach align well with the requirements of this role.

Through my work on real-time systems, distributed backend services, and API design, I have developed a strong foundation in building scalable, production-ready applications. My experience with Java, Spring Boot, and Python has equipped me to contribute effectively to engineering teams building complex systems.

I thrive in remote, collaborative environments and am committed to writing clean, maintainable code that delivers business value. I would welcome the opportunity to discuss how my background and skills can contribute to the success of {company}.

Thank you for your time and consideration.

Best regards,
Abhishek Mule"""

    return letter


def _get_profile_summary() -> dict:
    import json as _json
    from pathlib import Path as _Path

    cfg = Config.browser_config()
    profile = cfg.get("profile", {})

    resume_cfg = Config.resume_config()
    all_skills = set()
    for name, info in resume_cfg.get("resumes", {}).items():
        if info.get("active", False):
            meta_path = _Path(info.get("metadata", ""))
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        meta = _json.load(f)
                        all_skills.update(meta.get("skills", []))
                except Exception:
                    continue

    return {
        "name": profile.get("name", ""),
        "years_experience": profile.get("years_experience", 1),
        "skills": sorted(all_skills) if all_skills else ["Java", "Spring Boot", "Python", "PostgreSQL", "Docker"],
    }
