"""Pydantic schemas shared by the LLM calls (structured outputs) and the rest of the app."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------- Resume ----------

class Link(BaseModel):
    label: str = Field(description="e.g. LinkedIn, GitHub, Portfolio")
    url: str


class SkillGroup(BaseModel):
    category: str = Field(description="e.g. Languages, Frameworks, Cloud & DevOps")
    items: list[str]


class Experience(BaseModel):
    company: str
    title: str = Field(description="Job title only, e.g. 'IT Analyst (SDE-2 Equivalent)'")
    team: str | None = Field(default=None, description="Team / product / platform line shown under the title, if any")
    location: str | None
    start: str = Field(description="As written on the resume, e.g. 'Jan 2023'")
    end: str = Field(description="As written, e.g. 'Present'")
    bullets: list[str]


class Project(BaseModel):
    name: str
    tech: list[str]
    url: str | None
    bullets: list[str]


class Education(BaseModel):
    institution: str
    degree: str
    start: str | None
    end: str | None
    details: str | None = Field(description="GPA, honours, relevant coursework")


class Resume(BaseModel):
    name: str
    headline: str | None = Field(default=None, description="Tagline under the name, e.g. 'Software Engineer - Java, Spring Boot'")
    email: str
    phone: str | None
    location: str | None
    links: list[Link]
    summary: str | None
    skills: list[SkillGroup]
    experience: list[Experience]
    projects: list[Project]
    education: list[Education]
    certifications: list[str]
    achievements: list[str]


# ---------- Candidate profile (India-first) ----------

class SchoolDetail(BaseModel):
    """One education milestone, e.g. Class X, Class XII, B.Tech."""
    level: str = Field(description="e.g. 'Class 10', 'Class 12', 'B.Tech', 'M.Tech', 'Diploma'")
    board_or_university: str | None = None
    institution: str | None = None
    year_of_passing: int | None = None
    cgpa: float | None = Field(default=None, description="On its native scale (usually /10)")
    cgpa_scale: float = 10.0
    percentage: float | None = Field(default=None, description="Percentage marks, 0-100")


class CandidateProfile(BaseModel):
    """Structured candidate profile - the single source of truth for eligibility and answers."""
    # identity + contact
    full_name: str = ""
    email: str = ""
    phone: str = ""
    current_location: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    # academics
    education: list[SchoolDetail] = Field(default_factory=list)
    graduation_year: int | None = Field(default=None, description="Year of the HIGHEST qualifying degree, e.g. 2027")
    degree: str = ""                       # e.g. 'B.Tech'
    branch: str = ""                       # e.g. 'Computer Science and Engineering (AI & ML)'
    college: str = ""
    university: str = ""
    cgpa: float | None = None
    cgpa_scale: float = 10.0
    # Phase 18.3: India fresher form fields - 10th/12th schooling details
    school_tenth_name: str = ""            # e.g. 'Delhi Public School, Hyderabad'
    board_tenth: str = ""                  # e.g. 'CBSE'
    school_twelfth_name: str = ""          # e.g. 'Narayana Junior College'
    board_twelfth: str = ""                # e.g. 'Telangana State Board / CBSE'
    tenth_percentage: float | None = None
    twelfth_percentage: float | None = None
    intermediate_qualification: str = ""   # e.g. 'MPC (Maths, Physics, Chemistry)'
    diploma_percentage: float | None = None
    diploma_details: str = ""              # e.g. 'Diploma in CME, 2021-2024' ("" when none)
    active_backlogs: int = 0
    previous_backlogs: int = 0
    education_gap_months: int = 0
    # work
    candidate_type: str = "fresher"        # fresher | entry_level | experienced
    total_experience_years: float = 0.0
    internship_experience: str = ""        # e.g. '2 internships (8 months total): ML at XYZ'
    work_authorization: str = ""           # e.g. 'Indian citizen; authorized to work in India'
    # skills come from the master resume; keep a short list here for quick answers
    skills: list[str] = Field(default_factory=list)
    # preferences
    preferred_roles: list[str] = Field(default_factory=list)
    secondary_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    open_to_relocation: bool = True
    open_to_remote: bool = True
    expected_ctc_lpa: float | None = None
    expected_stipend_inr: int | None = None
    notice_period: str = ""
    current_ctc_lpa: float | None = None
    # extra facts used when filling forms (free-form key/values)
    extra: dict[str, str] = Field(default_factory=dict)


# ---------- Enrichment (deterministic, from the job text) ----------

class JobFacts(BaseModel):
    """Deterministically extracted facts about a job posting. No LLM involved."""
    salary_min_lpa: float | None = None
    salary_max_lpa: float | None = None
    salary_text: str = ""
    stipend_inr: int | None = None
    employment_type: str = ""              # internship | full_time | part_time | contract | apprentice | trainee | ''
    remote_type: str = ""                  # remote | hybrid | onsite | ''
    experience_min: float | None = None
    experience_max: float | None = None
    batch_years: list[int] = Field(default_factory=list)
    min_cgpa: float | None = None
    min_percentage: float | None = None
    backlog_policy: str = ""               # 'no_active_backlogs' | 'no_backlogs' | ''
    deadline: str | None = None            # ISO date when parseable, else ''
    fresher_flag: bool = False
    walk_in: bool = False
    off_campus: bool = False
    fraud_flags: list[str] = Field(default_factory=list)


# ---------- Eligibility (deterministic, candidate vs job) ----------

EligibilityStatus = Literal["eligible", "likely_eligible", "needs_verification", "not_eligible"]


class EligibilityCheck(BaseModel):
    criterion: str = Field(description="e.g. 'graduation_year', 'cgpa', 'experience'")
    required: str = Field(description="What the job asks for")
    candidate: str = Field(description="What the candidate has (or 'unknown')")
    result: Literal["pass", "fail", "unknown"]
    note: str = ""


class EligibilityResult(BaseModel):
    status: EligibilityStatus
    checks: list[EligibilityCheck] = Field(default_factory=list)

    def worst_check(self) -> EligibilityCheck | None:
        return next((c for c in self.checks if c.result == "fail"), None)


# ---------- Matching ----------

class MatchResult(BaseModel):
    """Kept for compatibility with stored match_json written before v2."""
    score: int = Field(description="0-100 fit of the candidate for this job")
    verdict: Literal["apply", "maybe", "skip"]
    reasons: list[str] = Field(description="2-4 short reasons for the score")
    missing_requirements: list[str] = Field(description="Hard requirements the candidate lacks")


class MatchResultV2(BaseModel):
    """Explainable match: AI semantic judgement merged with deterministic eligibility."""
    score: int = Field(description="0-100 overall fit")
    verdict: Literal["apply", "maybe", "skip"]
    skills_score: int = Field(ge=0, le=100, description="Technical skills overlap with the requirements")
    education_score: int = Field(ge=0, le=100)
    experience_score: int = Field(ge=0, le=100)
    location_score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(description="2-4 short reasons for the score")
    missing_skills: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list, description="Hard requirements the candidate lacks")
    disqualifiers: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# ---------- Tailoring ----------

class TailoredResume(BaseModel):
    resume: Resume
    changes: list[str] = Field(description="Short list of what was changed and why")
    added_skills: list[str] = Field(default_factory=list, description="Skills put on this resume that are NOT in the master resume")
    cover_letter: str = Field(description="Plain-text cover letter, 150-250 words, no placeholders")


# ---------- Form filling ----------

class FieldAnswer(BaseModel):
    field_id: str
    value: str = Field(
        description="Text to type; for select/radio the exact option label; for checkbox 'true'/'false'; "
        "for file inputs 'resume' or 'cover_letter'"
    )


class FormAnswers(BaseModel):
    answers: list[FieldAnswer]
    needs_human: list[str] = Field(
        description="field_ids that are required but cannot be answered truthfully from the provided facts"
    )


# ---------- AI services ----------

class InterviewPrep(BaseModel):
    likely_questions: list[str] = Field(description="5-8 technical + HR questions this company/role will probably ask")
    talking_points: list[str] = Field(description="Candidate-specific answers built ONLY from the profile")
    questions_to_ask: list[str] = Field(default_factory=list, description="Good questions for the candidate to ask")
    topics_to_revise: list[str] = Field(default_factory=list)


class FollowUpMessage(BaseModel):
    subject: str
    body: str = Field(description="Short, professional follow-up email in the candidate's voice")


class SkillGapReport(BaseModel):
    missing_skills: list[str] = Field(description="Skills the job needs that the candidate lacks")
    strengths: list[str] = Field(default_factory=list)
    learning_plan: list[str] = Field(default_factory=list, description="Concrete 2-4 week plan to close the top gaps")


class ApplicationQualityCheck(BaseModel):
    score: int = Field(ge=0, le=100, description="Quality of the tailored resume + cover letter for THIS job")
    issues: list[str] = Field(default_factory=list, description="Concrete problems found")
    suggestions: list[str] = Field(default_factory=list)


class ScreeningAnswer(BaseModel):
    answer: str = Field(description="Grounded answer to an application question, within the character limit")
    used_saved_answer: bool = Field(default=False, description="True when a verified saved answer was reused")
    grounded: bool = Field(default=True, description="False when the profile has no facts for this question")
