from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class ApplicationType(str, Enum):
    EASY_APPLY = "EASY_APPLY"
    EXTERNAL = "EXTERNAL"
    COMPANY_DIRECT = "COMPANY_DIRECT"
    UNKNOWN = "UNKNOWN"


class UserApplicationStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    SHORTLISTED = "SHORTLISTED"
    APPLIED = "APPLIED"
    INTERVIEWING = "INTERVIEWING"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    STALE = "STALE"


class RawJob(BaseModel):
    platform: str
    raw_payload: Dict[str, Any]
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationEvidence(BaseModel):
    job_status: JobStatus = JobStatus.UNKNOWN
    application_type: ApplicationType = ApplicationType.UNKNOWN
    evidence_source: str
    evidence_notes: str
    last_checked_at: datetime = Field(default_factory=datetime.utcnow)
    application_url: Optional[str] = None


class ExperienceLevel(str, Enum):
    ENTRY_LEVEL = "ENTRY_LEVEL"
    JUNIOR = "JUNIOR"
    MID_LEVEL = "MID_LEVEL"
    SENIOR = "SENIOR"
    UNKNOWN = "UNKNOWN"


class ExperienceEligibility(str, Enum):
    MATCH = "MATCH"              # Clearly compatible with ~2 YOE (0-3 years)
    STRETCH = "STRETCH"          # Borderline or ambiguous (e.g. 4 years, 3-4 years)
    OVER_LIMIT = "OVER_LIMIT"    # Exceeds 4 years (>4 years)
    UNKNOWN = "UNKNOWN"          # Missing / unreliable experience requirements


class FreshnessStatus(str, Enum):
    CONFIRMED_FRESH = "CONFIRMED_FRESH"        # Confirmed posted in preceding 24h
    RECENT = "RECENT"                          # Posted within recent window (e.g. 2-7 days)
    STALE = "STALE"                            # Older than target window or known stale
    CONTRADICTORY = "CONTRADICTORY"            # Contradictory signals (e.g. reposted vs original date)
    UNKNOWN_FRESHNESS = "UNKNOWN_FRESHNESS"    # Missing or unparseable original posting date


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FitEligibility(str, Enum):
    HIGH_FIT = "HIGH_FIT"
    MEDIUM_FIT = "MEDIUM_FIT"
    PROVISIONAL_NEEDS_REVIEW = "PROVISIONAL_NEEDS_REVIEW"
    LOW_FIT = "LOW_FIT"


class EnrichmentStatus(str, Enum):
    STAGED_DETERMINISTIC = "STAGED_DETERMINISTIC"
    AI_QUEUED = "AI_QUEUED"
    AI_IN_FLIGHT = "AI_IN_FLIGHT"
    AI_ENRICHED = "AI_ENRICHED"
    AI_QUOTA_HELD = "AI_QUOTA_HELD"


class DeterministicDecision(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class DeterministicEligibility(BaseModel):
    decision: DeterministicDecision = DeterministicDecision.NEEDS_REVIEW
    is_eligible: bool = False
    reasons: List[str] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    criteria_breakdown: Dict[str, str] = Field(default_factory=dict)


class RelevanceScore(BaseModel):
    total_score: int = Field(ge=0, le=100)
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    experience_eligibility: ExperienceEligibility = ExperienceEligibility.UNKNOWN
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    eligibility: FitEligibility = FitEligibility.LOW_FIT
    eligibility_reason: str = ""
    is_provisional: bool = False
    unverified_components: List[str] = Field(default_factory=list)
    available_fields: List[str] = Field(default_factory=list)
    score_breakdown: Dict[str, int] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    matched_skills: List[str] = Field(default_factory=list)
    missing_critical_skills: List[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    platform: str
    platform_job_id: str
    title: str
    company: str
    location: Optional[str] = None
    url: str
    normalized_url: str
    canonical_key: Optional[str] = None
    posted_date: Optional[str] = None
    updated_date: Optional[str] = None
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN_FRESHNESS
    work_mode: Optional[str] = None  # "remote", "hybrid", "onsite", or None
    description_snippet: Optional[str] = None
    source_experience_years_min: Optional[int] = None
    structured_requirements: Optional[List[Dict[str, Any]]] = None
    deterministic_eligibility: Optional[DeterministicEligibility] = None
    validation: ValidationEvidence
    relevance: Optional[RelevanceScore] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    is_partial_description: bool = False
    ai_score: Optional[int] = None
    ai_verdict: Optional[str] = None
    evaluation_source: Optional[str] = None  # "AI_LIVE", "AI_CACHED", "DETERMINISTIC_FALLBACK", "AI_ERROR"
    ai_evaluation: Optional[Dict[str, Any]] = None
    enrichment_status: str = EnrichmentStatus.STAGED_DETERMINISTIC.value
    char_count: int = 0
    estimated_tokens: int = 0
    canonical_hash: Optional[str] = None
    user_status: UserApplicationStatus = UserApplicationStatus.DISCOVERED
    applied_at: Optional[datetime] = None
    user_notes: Optional[str] = None
    contact_person: Optional[str] = None
    last_status_change: Optional[datetime] = None

    @property
    def description(self) -> Optional[str]:
        return self.description_snippet

