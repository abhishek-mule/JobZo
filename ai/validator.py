from pydantic import BaseModel, Field, field_validator
from typing import Literal


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100)
    strategy: Literal["apply_now", "get_referral", "cold_email", "skip", "watch"]
    reasoning: str = Field(min_length=10)
    missing_skills: list[str] = Field(default_factory=list)
    recommended_resume: str = ""

    @field_validator("score")
    @classmethod
    def score_range(cls, v):
        if not 0 <= v <= 100:
            raise ValueError("Score must be 0-100")
        return v


class ResumeSelection(BaseModel):
    resume_name: str
    reasoning: str = Field(min_length=10)


class CoverLetterResult(BaseModel):
    cover_letter: str = Field(min_length=50)


class EmailDraft(BaseModel):
    subject: str = Field(min_length=5)
    body: str = Field(min_length=50)


class EmailParseResult(BaseModel):
    company: str = ""
    status: Literal["interview", "rejection", "oa", "other"] = "other"
    interview_date: str = ""
    meeting_link: str = ""
    notes: str = ""


class SkillExtraction(BaseModel):
    skills: list[str] = Field(min_length=1)


VALIDATORS = {
    "score": ScoreResult,
    "resume_select": ResumeSelection,
    "cover_letter": CoverLetterResult,
    "email_draft": EmailDraft,
    "gmail_parse": EmailParseResult,
    "skill_extract": SkillExtraction,
}
