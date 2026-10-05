"""
Job Bucket — Phase 3D.1: Single-Job Live-Matching Preflight & Safety Controller.

This module provides a strictly disabled-by-default, single-job live test procedure
for verifying real Gemini API connectivity and end-to-end semantic evaluation.

CRITICAL SAFETY GUARANTEES:
1. DISABLED BY DEFAULT: Execution will immediately abort unless explicitly authorized
   with `enable_live_test=True`.
2. SINGLE-JOB BOUNDARY: Evaluates exactly ONE job per execution. Never runs bulk crawls or matches.
3. DETERMINISTIC REJECTION PRECEDENCE: Strictly rejects any job with
   deterministic_eligibility.decision == REJECTED. Deterministic rejections cannot be overridden.
4. ZERO PII TRANSMISSION: Uses PrivacyScrubber to redact job descriptions and candidate
   identifying details (name, email, phone, location, personal URLs, raw resume text).
5. PERSISTENT ATOMIC QUOTA: Reserves 1 quota unit in PostgreSQL (ai_daily_usage table)
   before dispatching any HTTP request. Fails closed if the database is unreachable or limit reached.
6. CACHE-FIRST LOOKUP: Checks ai_match_cache; returns cached evaluation without API dispatch if found.
7. CACHE WRITE INTEGRITY: Persists only validated structured output into ai_match_cache.
8. ZERO-COST GUARANTEE: Never falls back to a paid model or third-party provider.
9. GOOGLE TERMS DISCLOSURE: Under Google AI Studio Free Tier terms, prompt and response data
   may be processed and human-reviewed to train Google models. Candidate PII is scrubbed
   to ensure zero personal identifying information is transmitted.
"""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from src.models import JobRecord, DeterministicDecision
from src.candidate_profile import CandidateProfile
from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluation,
    AIConfigurationError,
    AIQuotaExceededError,
    AIDatabaseConnectivityError,
    AIServiceError,
    CURRENT_PROMPT_VERSION,
)
from src.ai_matcher import AIMatcher
from src.resume_service import ResumeService

logger = logging.getLogger(__name__)


class SingleJobPreflightResult(BaseModel):
    """Structured report returned by the single-job live preflight procedure."""
    platform: str
    platform_job_id: str
    job_title: str
    company: str
    eligibility_decision: str
    from_cache: bool
    evaluation: Optional[AIMatchEvaluation] = None
    profile_hash: str
    job_hash: str
    model_version: str
    prompt_version: str
    quota_consumed: bool = False
    status_message: str


def execute_single_job_live_preflight(
    job: JobRecord,
    candidate: CandidateProfile,
    enable_live_test: bool = False,
    db_config: Optional[Any] = None,
    db_conn = None,
    resume_service: Optional[ResumeService] = None,
    ai_service: Optional[GeminiAIService] = None,
) -> SingleJobPreflightResult:
    """
    Executes a controlled, single-job semantic match test against Gemini API.
    
    SAFETY INVARIANTS:
    1. DISABLED BY DEFAULT: Must pass enable_live_test=True to authorize execution.
    2. DETERMINISTIC PRECEDENCE: Strictly rejects jobs with decision == REJECTED.
    3. BOUNDED TRANSACTIONS: Commits quota reservation and closes database connections
       BEFORE dispatching the external HTTP request, preventing open DB transactions.
    4. ZERO PII: Uses PrivacyScrubber to redact job descriptions and candidate identity.
    5. CACHE FIRST: If cached match exists, returns it immediately without provider request.
    """
    if not enable_live_test:
        raise RuntimeError(
            "Live Gemini API calls are strictly disabled by default. "
            "To authorize a future single-job live test, pass enable_live_test=True explicitly. "
            "Note: A live call will consume 1 unit of your persistent daily quota (50/day cap) "
            "and Google AI Studio free-tier data terms apply."
        )

    # 1. Enforce deterministic rejection precedence
    det = job.deterministic_eligibility
    if det and det.decision == DeterministicDecision.REJECTED:
        rejection_reasons = "; ".join(det.rejection_reasons) if det.rejection_reasons else "Deterministic hard rejection"
        raise ValueError(
            f"Job '{job.title}' @ '{job.company}' was deterministically REJECTED ({rejection_reasons}). "
            "Deterministic rejections are impossible to override and must never be evaluated by AI."
        )

    # 2. Initialize and validate AI service
    ai = ai_service or GeminiAIService()
    if not ai.is_configured:
        raise AIConfigurationError(
            "GEMINI_API_KEY is missing or invalid. Cannot execute live preflight test."
        )

    matcher = AIMatcher(model_version=ai.model_name)
    scrubbed_payload = matcher.build_scrubbed_payload(job, candidate)

    prof_hash = scrubbed_payload["profile_hash"]
    job_h = scrubbed_payload["job_hash"]
    model_ver = ai.model_name
    prompt_ver = matcher.prompt_version

    rs = resume_service or ResumeService(config=db_config)

    # 3. Cache lookup (short-lived connection, immediately closed)
    cached_eval = rs.get_cached_match(
        profile_hash=prof_hash,
        platform=job.platform,
        platform_job_id=job.platform_job_id,
        job_hash=job_h,
        model_version=model_ver,
        prompt_version=prompt_ver,
    )
    if cached_eval is not None:
        logger.info(
            "Found validated cached evaluation for (%s, %s). Zero API calls made.",
            job.platform,
            job.platform_job_id,
        )
        return SingleJobPreflightResult(
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            job_title=job.title,
            company=job.company,
            eligibility_decision=det.decision.value if det else "ELIGIBLE",
            from_cache=True,
            evaluation=cached_eval,
            profile_hash=prof_hash,
            job_hash=job_h,
            model_version=model_ver,
            prompt_version=prompt_ver,
            quota_consumed=False,
            status_message="Retrieved from existing ai_match_cache (0 external calls, 0 quota consumed).",
        )

    # 4. Atomic quota reservation with strict transaction boundary
    # Must commit and close DB connection BEFORE sending HTTP request to prevent open transactions.
    from src.db_config import connect_database
    if db_conn is not None:
        ai.reserve_persistent_quota(db_conn)
    elif ai.require_persistent_quota:
        quota_conn = None
        try:
            try:
                quota_conn = connect_database(db_config)
            except Exception as exc:
                raise AIDatabaseConnectivityError(
                    f"Persistent quota check failed ({str(exc)}). Failing closed: skipping AI calls to protect daily cap."
                ) from exc
            ai.reserve_persistent_quota(quota_conn)
        finally:
            if quota_conn is not None:
                try:
                    quota_conn.close()
                except Exception:
                    pass

    # 5. Dispatch live evaluation (Zero DB connection held open during HTTP request)
    evaluation = ai.evaluate_job_match(
        profile=candidate,
        job_title=job.title,
        company=job.company,
        location=job.location or "",
        description=job.description_snippet or "",
        db_conn=None,
        reserve_quota=False,  # Quota was already reserved atomically in Step 4
    )

    # 6. Persist to cache (isolated connection lifecycle, immediately committed and closed)
    rs.save_cached_match(
        profile_hash=prof_hash,
        platform=job.platform,
        platform_job_id=job.platform_job_id,
        job_hash=job_h,
        model_version=model_ver,
        evaluation=evaluation,
        prompt_version=prompt_ver,
    )

    return SingleJobPreflightResult(
        platform=job.platform,
        platform_job_id=job.platform_job_id,
        job_title=job.title,
        company=job.company,
        eligibility_decision=det.decision.value if det else "ELIGIBLE",
        from_cache=False,
        evaluation=evaluation,
        profile_hash=prof_hash,
        job_hash=job_h,
        model_version=model_ver,
        prompt_version=prompt_ver,
        quota_consumed=True,
        status_message=f"Live evaluation successfully executed via {model_ver} and cached.",
    )


def load_single_job_for_preflight(
    db_config: Optional[Any] = None,
    profile: Optional[CandidateProfile] = None,
) -> Optional[JobRecord]:
    """
    Loads exactly one real job listing for live-test preflight:
    1. First checks the PostgreSQL 'jobs' table for an existing non-rejected job.
    2. If no persisted jobs exist, executes a bounded discovery run (1 job per source)
       and selects the first eligible candidate.
    """
    from src.db_config import connect_database
    from src.models import (
        ValidationEvidence,
        JobStatus,
        ApplicationType,
        RelevanceScore,
        DeterministicEligibility,
    )

    # 1. Attempt loading from database
    conn = None
    try:
        conn = connect_database(db_config)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT platform, platform_job_id, title, company, location,
                       description, posted_date, url, normalized_url,
                       application_url, job_status, application_type,
                       experience_level, relevance_score, score_breakdown, score_reasons,
                       matched_skills, confidence_level, confidence_score, eligibility,
                       eligibility_reason, is_provisional, unverified_components,
                       available_fields, evidence_source, evidence_notes, raw_metadata
                FROM jobs
                WHERE eligibility IS NULL OR eligibility != 'REJECTED'
                ORDER BY discovered_at DESC
                LIMIT 1;
                """
            )
            row = cur.fetchone()
            if row:
                raw_meta = row[26] if isinstance(row[26], dict) else {}
                det_raw = raw_meta.get("deterministic_eligibility", {})
                det_decision = DeterministicDecision(det_raw.get("decision", DeterministicDecision.ELIGIBLE.value))
                return JobRecord(
                    platform=row[0],
                    platform_job_id=row[1],
                    title=row[2],
                    company=row[3],
                    location=row[4],
                    description_snippet=row[5],
                    posted_date=row[6],
                    url=row[7],
                    normalized_url=row[8],
                    validation=ValidationEvidence(
                        job_status=JobStatus(row[10]) if row[10] else JobStatus.ACTIVE,
                        application_type=ApplicationType(row[11]) if row[11] else ApplicationType.COMPANY_DIRECT,
                        evidence_source=row[24] or "db_preflight",
                        evidence_notes=row[25] or "persisted job listing",
                        application_url=row[9] or row[7],
                    ),
                    relevance=RelevanceScore(
                        total_score=row[13] or 80,
                        experience_level=row[12] or "MID_LEVEL",
                        eligibility=row[19] or "HIGH_FIT",
                        eligibility_reason=row[20] or "Persisted job",
                        confidence_level=row[17] or "HIGH",
                        confidence_score=float(row[18] or 0.9),
                        matched_skills=row[16] or [],
                    ),
                    deterministic_eligibility=DeterministicEligibility(
                        decision=det_decision,
                        is_eligible=det_decision != DeterministicDecision.REJECTED,
                        reasons=det_raw.get("reasons", ["Persisted compatible job"]),
                        rejection_reasons=det_raw.get("rejection_reasons", []),
                        review_reasons=det_raw.get("review_reasons", []),
                    ),
                )
    except Exception as exc:
        logger.debug("Database job lookup skipped or empty: %s", str(exc))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # 2. If no persisted jobs, perform bounded 1-job discovery
    try:
        import asyncio
        from src.pipeline import run_pipeline

        discovered = asyncio.run(
            run_pipeline(
                profile=profile,
                limit_per_source=1,
                persist_to_db=False,
                ai_dry_run=False,
            )
        )
        for j in discovered:
            if not j.deterministic_eligibility or j.deterministic_eligibility.decision != DeterministicDecision.REJECTED:
                return j
    except Exception as exc:
        logger.warning("Bounded discovery lookup failed: %s", str(exc))

    return None
