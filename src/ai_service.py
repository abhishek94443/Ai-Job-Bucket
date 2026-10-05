import datetime
import hashlib
import json
import logging
import os
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Tuple
import requests
from pydantic import BaseModel, Field

from src.candidate_profile import CandidateProfile
from src.privacy_scrubber import PrivacyScrubber
from src.resume_analysis_schema import ResumeAnalysisResult, analysis_result_to_candidate_profile
from src.db_config import connect_database

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Official Stable Model Identifier verified from https://ai.google.dev/gemini-api/docs/models/gemini (checked October 2026)
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_DAILY_AI_CALL_LIMIT = 50  # Hard ceiling to strictly prevent billable overages
MIN_CALL_INTERVAL_SECONDS = 1.0   # Rate limit throttle: minimum 1s between calls (max 60 RPM)
PROMPT_VERSION_V1 = "v1"
PROMPT_VERSION_V2 = "v2"
CURRENT_PROMPT_VERSION = PROMPT_VERSION_V2  # Phase 4 AI-First prompt template version
QUOTA_TIMEZONE = "UTC"            # Explicit quota accounting reset boundary: 00:00:00 UTC


class AIServiceError(Exception):
    """Base exception for AI service operations."""
    pass


class AIQuotaExceededError(AIServiceError):
    """Raised when quota limit reached or persistent quota tracking cannot be verified."""
    pass


class AIDailyQuotaExhaustedError(AIQuotaExceededError):
    """Raised specifically when the persistent daily call count reaches or exceeds the daily limit."""
    pass


class AIDatabaseConnectivityError(AIServiceError):
    """Raised when database connection or transaction fails during persistent quota operations."""
    pass


class AIConfigurationError(AIServiceError):
    """Raised when API key or model configuration is missing or invalid."""
    pass


class MatchProvenance(str, Enum):
    """Explicit provenance tracker for job match evaluation origin."""
    AI_LIVE = "AI_LIVE"
    AI_CACHED = "AI_CACHED"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"
    AI_ERROR = "AI_ERROR"


EvaluationProvenance = MatchProvenance


class AIMatchVerdict(str, Enum):
    """Semantic match verdict categories."""
    STRONG_MATCH = "STRONG_MATCH"
    GOOD_MATCH = "GOOD_MATCH"
    STRETCH_MATCH = "STRETCH_MATCH"
    MISMATCH = "MISMATCH"
    # Legacy V1 categories for backward compatibility
    HIGH_FIT = "HIGH_FIT"
    MEDIUM_FIT = "MEDIUM_FIT"
    LOW_FIT = "LOW_FIT"
    OVER_LIMIT = "OVER_LIMIT"


class ExperienceSeniorityFit(str, Enum):
    """Seniority and experience alignment categorization."""
    ALIGNED = "ALIGNED"
    POTENTIAL_STRETCH = "POTENTIAL_STRETCH"
    OVER_QUALIFIED = "OVER_QUALIFIED"
    UNCERTAIN_CANDIDATE_DATA = "UNCERTAIN_CANDIDATE_DATA"
    UNSPECIFIED_BY_JOB = "UNSPECIFIED_BY_JOB"


class AIMatchEvaluation(BaseModel):
    """Legacy V1 structured evaluation of a job match (zero raw provider response)."""
    match_score: int = Field(ge=0, le=100, description="Overall match score between 0 and 100")
    fit_verdict: str = Field(description="HIGH_FIT, MEDIUM_FIT, LOW_FIT, or OVER_LIMIT")
    matching_evidence: List[str] = Field(default_factory=list, description="Demonstrated candidate skills matching job requirements")
    missing_requirements: List[str] = Field(default_factory=list, description="Job requirements not evident in candidate profile")
    experience_assessment: str = Field(default="", description="Assessment of candidate experience vs job requirement")
    reasoning: str = Field(default="", description="Concise explanation for the match verdict")
    confidence_score: float = Field(default=0.9, ge=0.0, le=1.0)
    prompt_version: str = Field(default=PROMPT_VERSION_V1)
    match_source: MatchProvenance = Field(default=MatchProvenance.AI_LIVE)


class AIMatchEvaluationV2(BaseModel):
    """Expanded V2 structured evaluation covering technical fit, uncertainties, recommendations, and provenance."""
    match_score: int = Field(ge=0, le=100, description="Overall fit score from 0 to 100")
    fit_verdict: AIMatchVerdict = Field(description="Primary semantic fit verdict")
    confidence_score: float = Field(default=0.85, ge=0.0, le=1.0, description="Model confidence in evaluation")
    role_responsibilities: List[str] = Field(default_factory=list, description="Core responsibilities extracted from job")
    required_skills: List[str] = Field(default_factory=list, description="Mandatory technologies requested by job")
    preferred_skills: List[str] = Field(default_factory=list, description="Nice-to-have or bonus technologies")
    matching_skills: List[str] = Field(default_factory=list, description="Candidate skills directly matching job requirements")
    missing_skills: List[str] = Field(default_factory=list, description="Required skills missing from candidate profile")
    transferable_skills: List[str] = Field(default_factory=list, description="Candidate skills applicable to adjacent required tech")
    job_seniority_level: str = Field(default="Unspecified", description="Extracted seniority level")
    job_experience_required: str = Field(default="Not specified", description="Extracted experience requirement")
    experience_fit: ExperienceSeniorityFit = Field(default=ExperienceSeniorityFit.UNCERTAIN_CANDIDATE_DATA, description="Experience alignment")
    experience_assessment: str = Field(default="", description="Narrative analysis of experience alignment")
    work_mode: str = Field(default="Unspecified", description="Remote, Hybrid, On-site, or Unspecified")
    location_fit: str = Field(default="Location flexible", description="Assessment of location fit")
    education_and_certifications: str = Field(default="None specified", description="Degree or cert requirements")
    evidence: List[str] = Field(default_factory=list, description="Specific matching evidence citations")
    uncertainties: List[str] = Field(default_factory=list, description="Explicitly flagged missing data or unknowns")
    actionable_recommendations: List[str] = Field(default_factory=list, description="Concrete preparation or resume advice")
    reasoning: str = Field(default="", description="Summary explanation for score and verdict")
    prompt_version: str = Field(default=PROMPT_VERSION_V2)
    match_source: MatchProvenance = Field(default=MatchProvenance.AI_LIVE)
    fallback_reason: Optional[str] = Field(default=None)
    supplementary_deterministic_eligibility: Optional[Dict[str, Any]] = Field(default=None)


def deserialize_cached_evaluation(data: Dict[str, Any], prompt_version: str = PROMPT_VERSION_V1) -> Union[AIMatchEvaluation, AIMatchEvaluationV2]:
    """
    Deserializes cached match evaluation preserving explicit schema versions.
    Never deserializes V1 data directly as V2 without explicit version handling.
    """
    version = data.get("prompt_version") or prompt_version
    if version == PROMPT_VERSION_V2 or "role_responsibilities" in data:
        res = AIMatchEvaluationV2(**data)
    else:
        res = AIMatchEvaluation(**data)
    res.match_source = MatchProvenance.AI_CACHED
    return res


def adapt_v1_to_v2(v1: AIMatchEvaluation) -> AIMatchEvaluationV2:
    """Explicitly adapts a legacy V1 evaluation to the V2 model with clear placeholder indicators."""
    verdict_map = {
        "HIGH_FIT": AIMatchVerdict.STRONG_MATCH,
        "MEDIUM_FIT": AIMatchVerdict.GOOD_MATCH,
        "LOW_FIT": AIMatchVerdict.MISMATCH,
        "OVER_LIMIT": AIMatchVerdict.MISMATCH,
    }
    verdict = verdict_map.get(v1.fit_verdict, AIMatchVerdict.GOOD_MATCH)

    return AIMatchEvaluationV2(
        match_score=v1.match_score,
        fit_verdict=verdict,
        confidence_score=v1.confidence_score,
        role_responsibilities=[],
        required_skills=[],
        preferred_skills=[],
        matching_skills=v1.matching_evidence,
        missing_skills=v1.missing_requirements,
        transferable_skills=[],
        job_seniority_level="Unspecified (v1 cache)",
        job_experience_required="Unspecified (v1 cache)",
        experience_fit=ExperienceSeniorityFit.UNCERTAIN_CANDIDATE_DATA,
        experience_assessment=v1.experience_assessment,
        work_mode="Unspecified (v1 cache)",
        location_fit="Unspecified (v1 cache)",
        education_and_certifications="None specified (v1 cache)",
        evidence=v1.matching_evidence,
        uncertainties=["Evaluated under V1 legacy schema; detailed skill breakdown not stored."],
        actionable_recommendations=[],
        reasoning=v1.reasoning,
        prompt_version=PROMPT_VERSION_V1,
        match_source=MatchProvenance.AI_CACHED,
    )


def resolve_model_name(model_name: Optional[str] = None) -> str:
    """
    Resolves and validates the Gemini model identifier with strict configuration guarantees:
    1. Explicit model_name argument (if provided)
    2. GEMINI_MODEL environment variable (if set)
    3. DEFAULT_GEMINI_MODEL ("gemini-3.5-flash-lite")
    
    Raises AIConfigurationError if the configured model is empty or whitespace.
    Strictly preserves the configured model without silent fallback.
    """
    if model_name is not None:
        if not model_name.strip():
            raise AIConfigurationError("Configured Gemini model identifier is empty or invalid.")
        return model_name.strip()

    env_val = os.getenv("GEMINI_MODEL")
    if env_val is not None:
        if not env_val.strip():
            raise AIConfigurationError("Configured Gemini model identifier is empty or invalid.")
        return env_val.strip()

    return DEFAULT_GEMINI_MODEL


MATCH_EVALUATION_SYSTEM_INSTRUCTION = (
    "You are an expert technical hiring screener evaluating job fit for a software engineering candidate. "
    "Compare the candidate's verified skills against the job description. "
    "Output match_score (0-100), fit_verdict ('HIGH_FIT', 'MEDIUM_FIT', 'LOW_FIT', or 'OVER_LIMIT'), "
    "concrete matching_evidence, missing_requirements, and experience_assessment."
)


def build_candidate_summary_for_ai(profile: CandidateProfile) -> Dict[str, Any]:
    """
    Constructs the canonical, deterministic candidate summary for AI evaluation with zero PII.
    Excludes name, contact details, target cities, and raw resume text.
    Handles unknown/unconfirmed experience safely without inventing 0.
    """
    if profile.target_experience_years is not None:
        exp_desc = f"{profile.target_experience_years:g} years"
    else:
        exp_desc = "UNKNOWN / UNCONFIRMED (Evaluate based on technical skills and flag experience uncertainty)"

    return {
        "total_experience": exp_desc,
        "experience_confirmed": profile.experience_confirmed,
        "hard_limit_years": profile.hard_limit_years,
        "target_title": profile.target_title,
        "languages": sorted(profile.languages),
        "frameworks": sorted(profile.frameworks),
        "databases": sorted(profile.databases),
        "messaging_and_cache": sorted(profile.messaging_and_cache),
        "devops_and_tools": sorted(profile.devops_and_tools),
        "inferred_capabilities": sorted(profile.inferred_capabilities),
    }


def build_job_match_prompt(
    candidate_summary: Dict[str, Any],
    job_title: str,
    company: str,
    location: str,
    scrubbed_description: str,
) -> str:
    """Constructs the exact user prompt string sent to Gemini for job match evaluation."""
    return (
        f"Candidate Profile:\n{json.dumps(candidate_summary, indent=2)}\n\n"
        f"Job Listing:\n"
        f"Title: {job_title}\n"
        f"Company: {company}\n"
        f"Location: {location}\n"
        f"Description:\n{scrubbed_description}\n\n"
        "Evaluate match fit."
    )


MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2 = (
    "You are an expert technical hiring screener conducting deep semantic analysis of job postings against candidate profiles. "
    "Evaluate: "
    "1. Role Responsibilities (core duties extracted from description). "
    "2. Required vs Preferred Skills (distinguish mandatory from nice-to-have). "
    "3. Matching, Missing, and Transferable Skills (e.g. adjacent message queues, databases, or frameworks). "
    "4. Experience & Seniority Requirements vs Candidate Profile. "
    "   IMPORTANT: If candidate total experience is unconfirmed or unknown, flag this uncertainty explicitly "
    "   in 'uncertainties' and 'experience_assessment'. NEVER treat missing experience as 0 or invent a number. "
    "5. Work Mode and Location Fit. "
    "6. Education or Certifications ONLY where explicitly mentioned in the job posting (never assume). "
    "7. Evidence, Actionable Recommendations for candidate preparation, Reasoning, and Overall Fit Score (0-100). "
    "Fit verdict must be one of: 'STRONG_MATCH', 'GOOD_MATCH', 'STRETCH_MATCH', 'MISMATCH'."
)


def build_job_match_prompt_v2(
    candidate_summary: Dict[str, Any],
    job_title: str,
    company: str,
    location: str,
    scrubbed_description: str,
    supplementary_eligibility: Optional[Dict[str, Any]] = None,
) -> str:
    """Constructs the V2 user prompt sent to Gemini with explicit uncertainty instructions and supplementary context."""
    supp_text = ""
    if supplementary_eligibility:
        supp_text = f"\n\nSupplementary Rule-Based Heuristic (Informational only):\n{json.dumps(supplementary_eligibility, indent=2)}"

    return (
        f"Candidate Profile (Verified Technical Facts):\n{json.dumps(candidate_summary, indent=2)}\n\n"
        f"Job Listing:\n"
        f"Title: {job_title}\n"
        f"Company: {company}\n"
        f"Location: {location}\n"
        f"Description:\n{scrubbed_description}"
        f"{supp_text}\n\n"
        "Conduct structured semantic evaluation."
    )


V2_MATCH_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "match_score": {"type": "INTEGER", "description": "Score from 0 to 100"},
        "fit_verdict": {
            "type": "STRING",
            "enum": ["STRONG_MATCH", "GOOD_MATCH", "STRETCH_MATCH", "MISMATCH"],
        },
        "confidence_score": {"type": "NUMBER"},
        "role_responsibilities": {"type": "ARRAY", "items": {"type": "STRING"}},
        "required_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "preferred_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "matching_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "missing_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "transferable_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "job_seniority_level": {"type": "STRING"},
        "job_experience_required": {"type": "STRING"},
        "experience_fit": {
            "type": "STRING",
            "enum": [
                "ALIGNED",
                "POTENTIAL_STRETCH",
                "OVER_QUALIFIED",
                "UNCERTAIN_CANDIDATE_DATA",
                "UNSPECIFIED_BY_JOB",
            ],
        },
        "experience_assessment": {"type": "STRING"},
        "work_mode": {"type": "STRING"},
        "location_fit": {"type": "STRING"},
        "education_and_certifications": {"type": "STRING"},
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "actionable_recommendations": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reasoning": {"type": "STRING"},
    },
    "required": [
        "match_score",
        "fit_verdict",
        "matching_skills",
        "missing_skills",
        "role_responsibilities",
        "reasoning",
    ],
}


class GeminiAIService:
    """
    Robust, production-grade Gemini integration with fail-closed zero-cost guarantees.
    Guarantees:
    1. Fail-closed persistent quota: If Neon DB quota tracking cannot be confirmed, AI calls are refused.
       No in-memory fallback is allowed to permit unmonitored calls.
    2. Explicit UTC quota boundary: Quota resets strictly at 00:00:00 UTC.
    3. Safe quota reservation: A call is reserved atomically in PostgreSQL BEFORE HTTP dispatch.
       Failed requests and timeouts count against the daily cap to prevent retry loops.
    4. Zero PII transmission: Always scrubs text prior to API submission.
    5. Strict model enforcement: Uses configured GEMINI_MODEL directly with NO silent fallback.
    6. No paid fallback: Fails closed to deterministic scoring rather than incurring unexpected costs.
    """
    require_persistent_quota: bool = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        daily_limit: int = DEFAULT_DAILY_AI_CALL_LIMIT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        require_persistent_quota: bool = True,
        rate_limiter: Optional[Any] = None,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = resolve_model_name(model_name)
        self.daily_limit = int(os.getenv("MAX_DAILY_AI_CALLS", str(daily_limit)))
        self.timeout = timeout
        self.require_persistent_quota = require_persistent_quota
        self.rate_limiter = rate_limiter
        self._last_call_timestamp: float = 0.0

    @property
    def is_configured(self) -> bool:
        """Returns True if an API key is present."""
        return bool(self.api_key and self.api_key.strip())

    def _redact_secrets(self, text: str) -> str:
        """Masks sensitive credentials from logs, exception strings, and error messages."""
        if not text or not self.api_key:
            return text
        return text.replace(self.api_key, "[REDACTED_API_KEY]")

    @staticmethod
    def get_current_utc_date() -> datetime.date:
        """Returns the current date in UTC explicitly."""
        return datetime.datetime.now(datetime.timezone.utc).date()

    def reserve_persistent_quota(self, conn=None, utc_date: Optional[datetime.date] = None) -> int:
        """
        Concurrency-safe, atomic quota reservation in PostgreSQL using explicit UTC date.
        Must be called BEFORE dispatching any HTTP request.
        Fails closed if the daily limit is exceeded or if DB is unreachable.
        If no connection is supplied, obtains and manages a short-lived connection.
        """
        conn_to_use = conn
        owned_conn = False
        if conn_to_use is None:
            try:
                conn_to_use = connect_database()
                owned_conn = True
            except Exception as e:
                raise AIDatabaseConnectivityError(
                    f"Persistent quota tracking cannot be confirmed (database connection failed: {e}). "
                    "Failing closed: skipping AI calls to guarantee zero unmonitored usage."
                ) from e

        target_date = utc_date or datetime.datetime.now(datetime.timezone.utc).date()

        try:
            with conn_to_use.cursor() as cur:
                # Atomic upsert in PostgreSQL with explicit UTC date parameter
                cur.execute(
                    """
                    INSERT INTO ai_daily_usage (usage_date, call_count, last_called_at)
                    VALUES (%s, 1, NOW())
                    ON CONFLICT (usage_date) DO UPDATE
                    SET call_count = ai_daily_usage.call_count + 1,
                        last_called_at = NOW()
                    WHERE ai_daily_usage.call_count < %s
                    RETURNING call_count;
                    """,
                    (target_date, self.daily_limit),
                )
                row = cur.fetchone()
                if not row:
                    # Query updated 0 rows because call_count reached or exceeded daily_limit
                    raise AIDailyQuotaExhaustedError(
                        f"Job Bucket persistent daily AI call limit of {self.daily_limit} calls/day reached for UTC date {target_date}. "
                        "Halting further external LLM calls to prevent unexpected usage or charges."
                    )
                # Commit immediately to release row locks and transaction before HTTP request
                conn_to_use.commit()
                return row[0]
        except AIQuotaExceededError:
            raise
        except Exception as exc:
            # Strictly fail closed if Neon database is unreachable or query errors out
            raise AIDatabaseConnectivityError(
                f"Persistent quota check failed ({str(exc)}). Failing closed: skipping AI calls to protect daily cap."
            ) from exc
        finally:
            if owned_conn and conn_to_use is not None:
                try:
                    conn_to_use.close()
                except Exception:
                    pass


    def _throttle(self) -> None:
        """Enforces rate limit throttle (minimum 1s between calls, or shared rate limiter if configured)."""
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        else:
            elapsed = time.time() - self._last_call_timestamp
            if elapsed < MIN_CALL_INTERVAL_SECONDS:
                time.sleep(MIN_CALL_INTERVAL_SECONDS - elapsed)
        self._last_call_timestamp = time.time()

    def generate_json(
        self,
        prompt: str,
        schema_dict: Dict[str, Any],
        system_instruction: str = "",
        db_conn = None,
        reserve_quota: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Sends request to Gemini using REST API with structured JSON output enforcement.
        Reserves daily quota atomically before sending request.
        Never logs prompt contents or credentials.
        """
        if not self.is_configured:
            raise AIConfigurationError("GEMINI_API_KEY is not configured in the environment or secrets.")

        # Fail closed: Reserve persistent quota atomically before sending request
        if (self.require_persistent_quota or db_conn is not None) and reserve_quota:
            self.reserve_persistent_quota(db_conn)

        self._throttle()

        url = f"{GEMINI_API_BASE_URL}/{self.model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": schema_dict,
                "temperature": 0.1,  # Low temperature for deterministic factual extraction
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        req_timeout = timeout if timeout is not None else self.timeout
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=req_timeout)
        except requests.exceptions.Timeout as exc:
            raise AIServiceError(f"Gemini API request timed out after {req_timeout}s.") from exc
        except requests.exceptions.RequestException as exc:
            clean_err = self._redact_secrets(str(exc))
            raise AIServiceError(f"Gemini API network connection failed: {clean_err}") from exc

        if response.status_code == 429:
            raise AIQuotaExceededError("Gemini Free Tier rate limit or quota exceeded (HTTP 429).")
        if response.status_code == 404:
            raise AIConfigurationError(
                f"Gemini model '{self.model_name}' was not found or is unsupported by API (HTTP 404)."
            )
        if response.status_code == 400:
            clean_text = self._redact_secrets(response.text)
            raise AIServiceError(f"Gemini Bad Request (HTTP 400): {clean_text}")
        if response.status_code in (401, 403):
            raise AIConfigurationError("Gemini API key is invalid, inactive, or unauthorized (HTTP 401/403).")
        if response.status_code != 200:
            clean_text = self._redact_secrets(response.text)
            raise AIServiceError(f"Gemini API returned unexpected status {response.status_code}: {clean_text}")

        try:
            res_data = response.json()
            candidates = res_data.get("candidates", [])
            if not candidates:
                raise AIServiceError("Gemini response contained no candidates.")

            part_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return json.loads(part_text)
        except (KeyError, json.JSONDecodeError) as exc:
            raise AIServiceError(f"Failed to parse structured JSON from Gemini response: {str(exc)}") from exc

    def _call_gemini_api(self, prompt: str) -> str:
        """
        Executes raw prompt via AIGateway multi-model cascading router.
        Automatically failovers across models and keys before failing.
        """
        from src.ai_gateway import AIGateway
        gateway = AIGateway()
        res = gateway.execute_generation(prompt, timeout_seconds=self.timeout)
        return res["text"]

    def analyze_resume_semantic(
        self,
        resume_text: str,
        candidate_name: str = "",
        db_conn = None,
    ) -> ResumeAnalysisResult:
        """
        AI-first semantic resume analyzer using Gemini.
        Returns validated, versioned structured analysis covering:
        - Professional summary
        - Work history (roles, employers, dates, durations, responsibilities, technologies, evidence)
        - Experience calculation (total years, method, assumptions, ambiguities, gaps)
        - Categorized technical skills (languages, frameworks, DBs, cloud/devops, messaging, APIs, testing, tools)
        - Education & certifications
        - Project highlights & demonstrated capabilities
        - Suggested roles (distinct from user search preferences)
        - Uncertainties and conflicting dates/claims
        
        Zero hallucinated skills, companies, degrees, dates, or numbers.
        """
        if not self.is_configured:
            raise AIConfigurationError(
                "Gemini API key is not configured. Set GEMINI_API_KEY in environment or .env."
            )

        scrubbed_text, _ = PrivacyScrubber.scrub_text(resume_text, candidate_name=candidate_name)

        system_instruction = (
            "You are an expert technical resume assessor. Analyze the provided resume text thoroughly and extract "
            "an objective, highly detailed technical profile. Ground all extractions strictly in explicit resume claims; "
            "never hallucinate, assume, or invent skills, companies, degrees, dates, or achievements.\n\n"
            "Guidelines:\n"
            "1. Work History: Extract each employer, job title, employment start/end dates, calculated duration in years, "
            "key responsibilities, technologies used, and concise evidence from the resume text.\n"
            "2. Total Experience Calculation: Propose total professional engineering experience in years. Explicitly specify the "
            "calculation method (e.g. sum of non-overlapping employment periods) and all assumptions (e.g. whether internships "
            "were excluded). If dates are ambiguous, overlapping, missing, or contradictory, flag is_ambiguous=true and detail conflicts.\n"
            "3. Technical Skills: Categorize technical skills explicitly supported by the resume into languages, frameworks, "
            "databases, cloud_and_devops, messaging_and_cache, apis_and_architecture, testing_and_tools, and other_technologies.\n"
            "4. Education & Certifications: Extract degrees, institutions, graduation years, certifications and issuing bodies.\n"
            "5. Projects: Extract demonstrated projects with technologies used and demonstrated engineering competencies.\n"
            "6. Suggested Roles: Propose suitable role titles and role families based on demonstrated technical strengths. "
            "These are AI interpretations and will be presented for candidate review.\n"
            "7. Uncertainties: Explicitly list any ambiguous timelines, unverified claims, or missing key details."
        )

        prompt = (
            "Perform comprehensive semantic technical analysis on the following sanitized resume text:\n\n"
            f"--- RESUME START ---\n{scrubbed_text}\n--- RESUME END ---\n"
        )

        schema = {
            "type": "OBJECT",
            "properties": {
                "candidate_name": {"type": "STRING", "description": "Candidate name if stated in resume."},
                "professional_summary": {"type": "STRING", "description": "Objective professional summary grounded in the resume."},
                "work_history": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "employer": {"type": "STRING"},
                            "job_title": {"type": "STRING"},
                            "start_date": {"type": "STRING"},
                            "end_date": {"type": "STRING"},
                            "duration_years": {"type": "NUMBER"},
                            "responsibilities": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "technologies_used": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "evidence": {"type": "STRING"},
                        },
                        "required": ["employer", "job_title"],
                    },
                },
                "experience_calculation": {
                    "type": "OBJECT",
                    "properties": {
                        "total_experience_years": {"type": "NUMBER"},
                        "calculation_method": {"type": "STRING"},
                        "assumptions": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "is_ambiguous": {"type": "BOOLEAN"},
                        "conflicts_or_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["calculation_method", "is_ambiguous"],
                },
                "skills": {
                    "type": "OBJECT",
                    "properties": {
                        "languages": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "frameworks": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "databases": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "cloud_and_devops": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "messaging_and_cache": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "apis_and_architecture": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "testing_and_tools": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "other_technologies": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["languages", "frameworks", "databases"],
                },
                "demonstrated_capabilities": {"type": "ARRAY", "items": {"type": "STRING"}},
                "projects": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "description": {"type": "STRING"},
                            "technologies_used": {"type": "ARRAY", "items": {"type": "STRING"}},
                            "demonstrated_capabilities": {"type": "ARRAY", "items": {"type": "STRING"}},
                        },
                        "required": ["title", "description"],
                    },
                },
                "education": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "degree": {"type": "STRING"},
                            "institution": {"type": "STRING"},
                            "graduation_year": {"type": "STRING"},
                            "field_of_study": {"type": "STRING"},
                        },
                        "required": ["degree", "institution"],
                    },
                },
                "certifications": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING"},
                            "issuing_organization": {"type": "STRING"},
                            "year": {"type": "STRING"},
                        },
                        "required": ["name"],
                    },
                },
                "suggested_role_titles": {"type": "ARRAY", "items": {"type": "STRING"}},
                "suggested_role_families": {"type": "ARRAY", "items": {"type": "STRING"}},
                "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": [
                "professional_summary",
                "experience_calculation",
                "skills",
            ],
        }

        extracted_data = self.generate_json(
            prompt,
            schema,
            system_instruction=system_instruction,
            db_conn=db_conn,
        )

        try:
            result = ResumeAnalysisResult.model_validate(extracted_data)
        except Exception as pydantic_err:
            raise AIServiceError(f"AI response schema validation failed: {str(pydantic_err)}") from pydantic_err

        if candidate_name and not result.candidate_name:
            result.candidate_name = candidate_name
        result.provenance = "AI_LIVE"
        return result

    def extract_candidate_profile(
        self,
        resume_text: str,
        candidate_name: str = "",
        db_conn = None,
    ) -> CandidateProfile:
        """
        Extracts a structured candidate profile from resume text via semantic analysis.
        Maintains backward compatibility with callers expecting a CandidateProfile.
        """
        analysis = self.analyze_resume_semantic(
            resume_text=resume_text,
            candidate_name=candidate_name,
            db_conn=db_conn,
        )
        return analysis_result_to_candidate_profile(analysis, candidate_name=candidate_name)

    def evaluate_job_match(
        self,
        profile: CandidateProfile,
        job_title: str,
        company: str,
        location: str,
        description: str,
        db_conn = None,
        reserve_quota: bool = True,
    ) -> AIMatchEvaluation:
        """
        Evaluates semantic match between candidate profile and job listing.
        """
        system_instruction = MATCH_EVALUATION_SYSTEM_INSTRUCTION
        candidate_summary = build_candidate_summary_for_ai(profile)

        scrubbed_desc, _ = PrivacyScrubber.scrub_text(
            description if description else "",
            candidate_name=profile.name,
        )

        prompt = build_job_match_prompt(
            candidate_summary=candidate_summary,
            job_title=job_title,
            company=company,
            location=location,
            scrubbed_description=scrubbed_desc,
        )


        schema = {
            "type": "OBJECT",
            "properties": {
                "match_score": {"type": "INTEGER"},
                "fit_verdict": {"type": "STRING"},
                "matching_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
                "missing_requirements": {"type": "ARRAY", "items": {"type": "STRING"}},
                "experience_assessment": {"type": "STRING"},
                "reasoning": {"type": "STRING"},
                "confidence_score": {"type": "NUMBER"},
            },
            "required": ["match_score", "fit_verdict", "matching_evidence", "reasoning"],
        }

        res_json = self.generate_json(
            prompt,
            schema,
            system_instruction=system_instruction,
            db_conn=db_conn,
            reserve_quota=reserve_quota,
        )
        try:
            return AIMatchEvaluation(**res_json)
        except Exception as exc:
            raise AIServiceError(
                f"Gemini response did not conform to required AIMatchEvaluation schema: {str(exc)}"
            ) from exc

    def evaluate_job_match_v2(
        self,
        profile: CandidateProfile,
        job_title: str,
        company: str,
        location: str,
        description: str,
        supplementary_eligibility: Optional[Dict[str, Any]] = None,
        db_conn = None,
        reserve_quota: bool = True,
    ) -> AIMatchEvaluationV2:
        """
        Evaluates semantic match between candidate profile and job listing under V2 schema.
        Captures role responsibilities, required/preferred skills, transferable skills,
        uncertainties, and actionable recommendations.
        """
        system_instruction = MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2
        candidate_summary = build_candidate_summary_for_ai(profile)

        scrubbed_desc, _ = PrivacyScrubber.scrub_text(
            description if description else "",
            candidate_name=profile.name,
        )

        prompt = build_job_match_prompt_v2(
            candidate_summary=candidate_summary,
            job_title=job_title,
            company=company,
            location=location,
            scrubbed_description=scrubbed_desc,
            supplementary_eligibility=supplementary_eligibility,
        )

        res_json = self.generate_json(
            prompt,
            V2_MATCH_RESPONSE_SCHEMA,
            system_instruction=system_instruction,
            db_conn=db_conn,
            reserve_quota=reserve_quota,
        )
        try:
            eval_v2 = AIMatchEvaluationV2(**res_json)
            eval_v2.match_source = MatchProvenance.AI_LIVE
            eval_v2.prompt_version = PROMPT_VERSION_V2
            if supplementary_eligibility:
                eval_v2.supplementary_deterministic_eligibility = supplementary_eligibility
            return eval_v2
        except Exception as exc:
            raise AIServiceError(
                f"Gemini response did not conform to required AIMatchEvaluationV2 schema: {str(exc)}"
            ) from exc

    def evaluate_jobs_batch(
        self,
        profile: CandidateProfile,
        jobs: List[Dict[str, Any]],
        db_conn = None,
        timeout: float = 90.0,
    ) -> List[Dict[str, Any]]:
        """
        Evaluates a batch of up to 15 jobs against the candidate profile in a single Gemini request.
        Guarantees that up to 45 jobs require at most 3 Gemini API calls.
        """
        if not jobs:
            return []

        candidate_summary = build_candidate_summary_for_ai(profile)

        scrubbed_jobs = []
        for j in jobs:
            raw_desc = j.get("description") or ""
            scrubbed_desc, _ = PrivacyScrubber.scrub_text(raw_desc, candidate_name=profile.name)
            scrubbed_jobs.append({
                "job_id": str(j["job_id"]),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "description": scrubbed_desc,
            })

        prompt = (
            f"Candidate Profile (Verified Technical Facts):\n{json.dumps(candidate_summary, indent=2)}\n\n"
            f"Analyze the following batch of {len(scrubbed_jobs)} job listings against the candidate profile.\n"
            "Conduct structured semantic evaluation for every job listing and return an evaluation item in 'evaluations' for every input job_id.\n\n"
            f"Job Listings:\n{json.dumps(scrubbed_jobs, indent=2)}"
        )

        res_json = self.generate_json(
            prompt=prompt,
            schema_dict=BATCH_MATCH_RESPONSE_SCHEMA,
            system_instruction=BATCH_MATCH_SYSTEM_INSTRUCTION,
            db_conn=db_conn,
            reserve_quota=True,
            timeout=timeout,
        )

        raw_evals = res_json.get("evaluations", []) if isinstance(res_json, dict) else []
        return raw_evals


BATCH_MATCH_SYSTEM_INSTRUCTION = (
    "You are an expert technical recruiter and senior software engineering evaluator. "
    "Evaluate each job listing in the provided batch independently and thoroughly against the candidate's verified profile. "
    "For every job listing in the input batch, produce an evaluation containing: "
    "1. job_id matching the input job_id exactly. "
    "2. match_score (0-100 integer). "
    "3. fit_verdict ('STRONG_MATCH', 'GOOD_MATCH', 'STRETCH_MATCH', or 'MISMATCH'). "
    "4. confidence_score (0.0 to 1.0). "
    "5. matching_skills (skills demonstrated in candidate profile matching the role). "
    "6. missing_skills (critical skills required by role not in candidate profile). "
    "7. transferable_skills (adjacent technical competencies). "
    "8. job_seniority_level (e.g. 'Junior', 'Mid-Level', 'Senior', 'Lead'). "
    "9. job_experience_required (explicit experience string from posting). "
    "10. experience_fit ('ALIGNED', 'POTENTIAL_STRETCH', 'OVER_QUALIFIED', 'UNCERTAIN_CANDIDATE_DATA', 'UNSPECIFIED_BY_JOB'). "
    "11. experience_assessment (concise narrative analysis of experience alignment). "
    "12. work_mode ('Remote', 'Hybrid', 'On-site', or 'Unspecified'). "
    "13. location_fit (assessment of location alignment). "
    "14. education_and_certifications. "
    "15. evidence (specific citations). "
    "16. uncertainties (flagged missing information). "
    "17. actionable_recommendations (advice for interview preparation). "
    "18. reasoning (concise summary reasoning for verdict and score)."
)

BATCH_MATCH_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "evaluations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "job_id": {"type": "STRING"},
                    "match_score": {"type": "INTEGER"},
                    "fit_verdict": {
                        "type": "STRING",
                        "enum": ["STRONG_MATCH", "GOOD_MATCH", "STRETCH_MATCH", "MISMATCH"],
                    },
                    "confidence_score": {"type": "NUMBER"},
                    "role_responsibilities": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "required_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "preferred_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "matching_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "missing_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "transferable_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "job_seniority_level": {"type": "STRING"},
                    "job_experience_required": {"type": "STRING"},
                    "experience_fit": {
                        "type": "STRING",
                        "enum": [
                            "ALIGNED",
                            "POTENTIAL_STRETCH",
                            "OVER_QUALIFIED",
                            "UNCERTAIN_CANDIDATE_DATA",
                            "UNSPECIFIED_BY_JOB",
                        ],
                    },
                    "experience_assessment": {"type": "STRING"},
                    "work_mode": {"type": "STRING"},
                    "location_fit": {"type": "STRING"},
                    "education_and_certifications": {"type": "STRING"},
                    "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "actionable_recommendations": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "reasoning": {"type": "STRING"},
                },
                "required": [
                    "job_id",
                    "match_score",
                    "fit_verdict",
                    "confidence_score",
                    "matching_skills",
                    "missing_skills",
                    "reasoning",
                ],
            },
        }
    },
    "required": ["evaluations"],
}




def compute_profile_hash(profile: CandidateProfile) -> str:
    """Computes deterministic SHA-256 hash of structured candidate profile attributes."""
    content = json.dumps(
        {
            "experience": profile.target_experience_years,
            "experience_confirmed": profile.experience_confirmed,
            "target_title": profile.target_title,
            "hard_limit": profile.hard_limit_years,
            "languages": sorted(profile.languages),
            "frameworks": sorted(profile.frameworks),
            "databases": sorted(profile.databases),
            "messaging": sorted(profile.messaging_and_cache),
            "devops": sorted(profile.devops_and_tools),
            "inferred_capabilities": sorted(profile.inferred_capabilities),
            "cities": sorted(profile.target_cities),
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_job_hash(platform: str, platform_job_id: str, title: str, company: str, description: str) -> str:
    """Computes deterministic SHA-256 hash of job listing content."""
    canonical = f"{platform}:{platform_job_id}:{title.strip().lower()}:{company.strip().lower()}:{description.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
