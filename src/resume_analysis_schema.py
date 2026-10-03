"""
Structured schema models for AI-first semantic resume analysis.
Enforces strict type validation, evidence tracking, uncertainty reporting,
and clear separation between verified career facts and suggested search preferences.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator


class WorkExperienceItem(BaseModel):

    """Structured work experience record evidenced in the resume."""
    employer: str = Field(description="Company or employer name.")
    job_title: str = Field(description="Position or job title held.")
    start_date: Optional[str] = Field(default=None, description="Start date as stated (e.g. 'Jan 2022', '2022', '06/2021').")
    end_date: Optional[str] = Field(default=None, description="End date as stated (e.g. 'Present', 'Dec 2023', '2023').")
    duration_years: Optional[float] = Field(default=None, description="Calculated duration in years for this position if dates are stated.")
    responsibilities: List[str] = Field(default_factory=list, description="Core responsibilities performed.")
    technologies_used: List[str] = Field(default_factory=list, description="Technologies and tools explicitly used in this role.")
    evidence: str = Field(default="", description="Snippet or proof from the resume supporting this entry.")


class ExperienceCalculation(BaseModel):
    """
    Detailed, verifiable calculation of total professional engineering experience.
    Explicitly reports calculation method, assumptions, and uncertainties.
    """
    total_experience_years: Optional[float] = Field(
        default=None,
        description="Total professional experience in years, or null if unknown, missing, or ambiguous.",
    )
    calculation_method: str = Field(
        default="unknown",
        description="Method used to calculate experience (e.g., 'sum_of_stated_roles', 'explicit_resume_claim', 'date_range_overlap_adjusted').",
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Assumptions made during calculation (e.g., 'treated Present as current year', 'excluded internship').",
    )
    is_ambiguous: bool = Field(
        default=False,
        description="True if employment dates are missing, overlapping, contradictory, or difficult to ascertain.",
    )
    conflicts_or_gaps: List[str] = Field(
        default_factory=list,
        description="Any identified date conflicts, unstated gaps, or ambiguous claims in the resume.",
    )


class TechnicalSkillsBreakdown(BaseModel):
    """
    Comprehensive categorization of technical skills actually supported by the resume.
    Zero hallucination: only includes technologies evidenced in the document.
    """
    languages: List[str] = Field(default_factory=list, description="Programming and query languages (e.g. Java, Python, SQL, Go).")
    frameworks: List[str] = Field(default_factory=list, description="Frameworks and core libraries (e.g. Spring Boot, Django, FastAPI).")
    databases: List[str] = Field(default_factory=list, description="Databases and stores (e.g. PostgreSQL, MySQL, Redis, MongoDB).")
    cloud_and_devops: List[str] = Field(default_factory=list, description="Cloud, CI/CD, and DevOps tools (e.g. Docker, Kubernetes, AWS, Jenkins).")
    messaging_and_cache: List[str] = Field(default_factory=list, description="Messaging brokers and caching layers (e.g. Kafka, RabbitMQ, Redis).")
    apis_and_architecture: List[str] = Field(default_factory=list, description="APIs, protocols, and architectural patterns (e.g. REST, GraphQL, Microservices, gRPC).")
    testing_and_tools: List[str] = Field(default_factory=list, description="Testing libraries and developer tooling (e.g. JUnit, Mockito, Git, Postman, Maven).")
    other_technologies: List[str] = Field(default_factory=list, description="Other specialized technical tools or domain technologies.")


class EducationItem(BaseModel):
    """Education record evidenced in the resume."""
    degree: str = Field(description="Degree name (e.g. 'Bachelor of Technology', 'B.S. in Computer Science').")
    institution: str = Field(description="University, college, or school name.")
    graduation_year: Optional[str] = Field(default=None, description="Year of graduation or attendance period.")
    field_of_study: Optional[str] = Field(default=None, description="Major, department, or field of study.")


class CertificationItem(BaseModel):
    """Certification evidenced in the resume."""
    name: str = Field(description="Certification title (e.g. 'AWS Certified Solutions Architect').")
    issuing_organization: Optional[str] = Field(default=None, description="Issuing body (e.g. 'Amazon Web Services', 'Oracle').")
    year: Optional[str] = Field(default=None, description="Year obtained or validity period.")


class ProjectHighlight(BaseModel):
    """Key project demonstrated in the resume."""
    title: str = Field(description="Project title or system name.")
    description: str = Field(description="Summary of project purpose and architecture.")
    technologies_used: List[str] = Field(default_factory=list, description="Technologies used in this project.")
    demonstrated_capabilities: List[str] = Field(default_factory=list, description="Engineering competencies demonstrated (e.g. high-throughput processing).")


class ResumeAnalysisResult(BaseModel):
    """
    Versioned, schema-validated result of AI-first semantic resume analysis.
    Distinguishes:
    1. Verified Career Facts (work history, skills, education, certifications).
    2. Evidence-backed Experience Calculation & Ambiguities.
    3. AI Suggestions & Inferences (suggested titles, inferred capabilities).
    """
    schema_version: str = "v2"
    provenance: str = "AI_LIVE"  # 'AI_LIVE', 'DETERMINISTIC_FALLBACK', 'USER_EDITED'
    candidate_name: Optional[str] = None
    professional_summary: str = Field(default="", description="Objective professional summary grounded in the resume.")

    @model_validator(mode="before")
    @classmethod
    def _handle_legacy_or_flat_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If flat legacy fields are provided instead of nested skills/experience_calculation
            if "skills" not in data and ("languages" in data or "frameworks" in data or "databases" in data):
                data["skills"] = {
                    "languages": data.get("languages") or [],
                    "frameworks": data.get("frameworks") or [],
                    "databases": data.get("databases") or [],
                    "cloud_and_devops": data.get("devops_and_tools") or [],
                    "messaging_and_cache": data.get("messaging_and_cache") or [],
                    "apis_and_architecture": data.get("apis_and_auth") or [],
                    "testing_and_tools": [],
                    "other_technologies": [],
                }
            if "experience_calculation" not in data:
                exp_years = data.get("total_experience_years")
                data["experience_calculation"] = {
                    "total_experience_years": exp_years,
                    "calculation_method": "Extracted from resume or profile",
                    "assumptions": [],
                    "is_ambiguous": exp_years is None,
                    "conflicts_or_gaps": [],
                }
            if not data.get("professional_summary"):
                data["professional_summary"] = data.get("target_title") or "Candidate profile"
            if "suggested_role_titles" not in data and data.get("target_title"):
                data["suggested_role_titles"] = [data["target_title"]]
            if "demonstrated_capabilities" not in data and data.get("inferred_capabilities"):
                data["demonstrated_capabilities"] = data.get("inferred_capabilities") or []
        return data
    
    # 1. Career Facts
    work_history: List[WorkExperienceItem] = Field(default_factory=list)
    experience_calculation: ExperienceCalculation = Field(default_factory=ExperienceCalculation)
    skills: TechnicalSkillsBreakdown = Field(default_factory=TechnicalSkillsBreakdown)
    education: List[EducationItem] = Field(default_factory=list)
    certifications: List[CertificationItem] = Field(default_factory=list)
    projects: List[ProjectHighlight] = Field(default_factory=list)

    # 2. Inferences & Suggestions (Explicitly marked as AI interpretations, NOT candidate facts)
    demonstrated_capabilities: List[str] = Field(
        default_factory=list,
        description="High-level engineering capabilities inferred from demonstrated projects.",
    )
    suggested_role_titles: List[str] = Field(
        default_factory=list,
        description="Suggested target job titles based on demonstrated strengths (does not overwrite user preferences).",
    )
    suggested_role_families: List[str] = Field(
        default_factory=list,
        description="Broad role families suitable for the candidate (e.g. 'Backend Engineering', 'Cloud / Platform').",
    )

    # 3. Uncertainties and Missing Information
    uncertainties: List[str] = Field(
        default_factory=list,
        description="Explicit uncertainties, ambiguous timelines, missing dates, or unverified claims.",
    )

    @property
    def all_skills_list(self) -> List[str]:
        """Flattens all categorized skills into a unique, sorted list."""
        s = set()
        for group in [
            self.skills.languages,
            self.skills.frameworks,
            self.skills.databases,
            self.skills.cloud_and_devops,
            self.skills.messaging_and_cache,
            self.skills.apis_and_architecture,
            self.skills.testing_and_tools,
            self.skills.other_technologies,
        ]:
            for item in group:
                if item and item.strip():
                    s.add(item.strip())
        return sorted(list(s))


def analysis_result_to_candidate_profile(
    analysis: ResumeAnalysisResult,
    candidate_name: str = "",
    existing_profile: Optional[Any] = None,
) -> Any:
    """
    Converts a rich ResumeAnalysisResult into a CandidateProfile model,
    preserving all structured data in candidate_profile.resume_analysis
    while populating standard relational fields.
    If existing_profile has user-configured preferences, preserves those preferences.
    """
    from src.candidate_profile import CandidateProfile, DEFAULT_TARGET_CITIES

    total_exp = analysis.experience_calculation.total_experience_years
    name = candidate_name.strip() or analysis.candidate_name or "Candidate"

    # Separate verified facts from search preferences
    if existing_profile and getattr(existing_profile, "preferences_source", "") == "user_configured":
        target_title = existing_profile.target_title
        target_cities = existing_profile.target_cities
        hard_limit = existing_profile.hard_limit_years
        exp_min = existing_profile.experience_range_min
        exp_max = existing_profile.experience_range_max
        remote_pref = existing_profile.remote_preference
        pref_source = "user_configured"
    else:
        # Initial suggested search preferences (editable defaults, not confirmed choices)
        target_title = analysis.suggested_role_titles[0] if analysis.suggested_role_titles else "Software Engineer"
        target_cities = DEFAULT_TARGET_CITIES
        exp_min = max(0.0, total_exp - 1.0) if total_exp is not None else 0.0
        exp_max = total_exp + 1.0 if total_exp is not None else 3.0
        hard_limit = total_exp + 2.0 if total_exp is not None else 4.0
        remote_pref = "flexible"
        pref_source = "editable_default"

    return CandidateProfile(
        name=name,
        total_experience_years=total_exp,
        experience_confirmed=False,  # Unconfirmed until user explicitly reviews and confirms
        languages=analysis.skills.languages,
        frameworks=analysis.skills.frameworks,
        databases=analysis.skills.databases,
        messaging_and_cache=analysis.skills.messaging_and_cache,
        devops_and_tools=analysis.skills.cloud_and_devops,
        apis_and_auth=analysis.skills.apis_and_architecture,
        inferred_capabilities=analysis.demonstrated_capabilities,
        target_title=target_title,
        experience_range_min=exp_min,
        experience_range_max=exp_max,
        hard_limit_years=hard_limit,
        target_cities=target_cities,
        remote_preference=remote_pref,
        preferences_source=pref_source,
        analysis_provenance=analysis.provenance,
        resume_analysis=analysis.model_dump(),
    )
