"""
Phase 4A: Tests for AI-First Matching Architecture & V2 Evaluation Schema
Verifies:
1. AI evaluation of deterministically rejected jobs.
2. Eligible and needs-review jobs reaching AI evaluation.
3. Cache hits bypassing quota and provider calls.
4. Quota exhaustion triggering correctly labeled deterministic fallback.
5. All other AI failures failing closed (zero silent fallback).
6. V2 schema validation: role responsibilities, required/preferred skills,
   missing skills, transferable skills, experience uncertainty.
7. Exactly one quota reservation per uncached evaluation.
8. V1/V2 cache-version separation and safe adaptation.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
import requests

from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluation,
    AIMatchEvaluationV2,
    AIMatchVerdict,
    ExperienceSeniorityFit,
    MatchProvenance,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    CURRENT_PROMPT_VERSION,
    AIDailyQuotaExhaustedError,
    AIDatabaseConnectivityError,
    AIConfigurationError,
    AIQuotaExceededError,
    AIServiceError,
    deserialize_cached_evaluation,
    adapt_v1_to_v2,
    build_candidate_summary_for_ai,
    build_job_match_prompt_v2,
    MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2,
)
from src.ai_matcher import AIMatcher
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.models import (
    JobRecord,
    ValidationEvidence,
    DeterministicDecision,
    DeterministicEligibility,
    RelevanceScore,
    FitEligibility,
    JobStatus,
    ApplicationType,
)


def make_test_job(
    platform: str = "linkedin",
    platform_job_id: str = "job-401",
    title: str = "Staff Backend Engineer",
    company: str = "CloudScale Tech",
    location: str = "Bengaluru",
    description: str = "Looking for a Staff Backend Engineer with 8+ years of Java, Kafka, Kubernetes.",
    decision: DeterministicDecision = DeterministicDecision.REJECTED,
    rejection_reasons: list = None,
    review_reasons: list = None,
) -> JobRecord:
    url = f"https://example.com/jobs/{platform_job_id}"
    det = DeterministicEligibility(
        decision=decision,
        is_eligible=(decision != DeterministicDecision.REJECTED),
        reasons=["Deterministic rule evaluated"],
        rejection_reasons=rejection_reasons or (["Experience exceeds hard cutoff of 4.0 years"] if decision == DeterministicDecision.REJECTED else []),
        review_reasons=review_reasons or (["Needs review: ambiguous title"] if decision == DeterministicDecision.NEEDS_REVIEW else []),
    )
    return JobRecord(
        platform=platform,
        platform_job_id=platform_job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        normalized_url=url,
        description_snippet=description,
        job_status=JobStatus.ACTIVE,
        application_type=ApplicationType.EASY_APPLY,
        deterministic_eligibility=det,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="unit_test",
            evidence_notes="mock test listing",
            application_url=url,
        ),
        relevance=RelevanceScore(
            total_score=60,
            experience_level="SENIOR",
            eligibility=FitEligibility.LOW_FIT if decision == DeterministicDecision.REJECTED else FitEligibility.HIGH_FIT,
            eligibility_reason="Deterministic test record",
            confidence_level="HIGH",
            confidence_score=0.9,
            matched_skills=["Java"],
        ),
    )


def sample_v2_response_dict() -> dict:
    return {
        "match_score": 78,
        "fit_verdict": "GOOD_MATCH",
        "confidence_score": 0.85,
        "role_responsibilities": [
            "Architect and build high-throughput distributed microservices",
            "Design Kafka event streaming pipelines",
        ],
        "required_skills": ["Java", "Spring Boot", "Kafka"],
        "preferred_skills": ["Kubernetes", "AWS", "gRPC"],
        "matching_skills": ["Java", "Spring Boot", "Kafka"],
        "missing_skills": ["Kubernetes", "gRPC"],
        "transferable_skills": [
            "Docker skills transferable to Kubernetes container orchestration"
        ],
        "job_seniority_level": "Senior / Staff",
        "job_experience_required": "8+ years",
        "experience_fit": "POTENTIAL_STRETCH",
        "experience_assessment": "Job seeks 8+ YOE; candidate brings 2 YOE with strong core backend fundamentals.",
        "work_mode": "Hybrid",
        "location_fit": "Match (Bengaluru)",
        "education_and_certifications": "B.Tech or equivalent",
        "evidence": [
            "Demonstrated mastery of Java 17 and Spring Boot microservices in production",
            "Production experience with Kafka event-driven architectures",
        ],
        "uncertainties": [
            "Candidate total experience is unconfirmed in profile",
            "Job posting does not specify strict minimum degree requirement",
        ],
        "actionable_recommendations": [
            "Highlight Kafka throughput and concurrency projects during interview",
            "Upskill on Kubernetes workload deployments",
        ],
        "reasoning": "Strong technical alignment with modern backend stack, though seniority level is a stretch.",
    }


class TestAIEvaluationOfRejectedAndAllJobs:
    """Requirement 1 & 2: Deterministic rejection does not gate AI; all jobs reach AI."""

    def test_deterministic_rejected_job_reaches_and_is_evaluated_by_ai(self):
        """Proves that a deterministically rejected job reaches AI matching and receives AI evaluation."""
        rejected_job = make_test_job(
            platform="linkedin",
            platform_job_id="rej-001",
            title="Senior Architect (8+ YOE)",
            decision=DeterministicDecision.REJECTED,
        )
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        # Step 1: select_ai_candidates must NOT exclude rejected job
        candidates, excluded = matcher.select_ai_candidates([rejected_job])
        assert len(candidates) == 1
        assert len(excluded) == 0
        assert candidates[0].platform_job_id == "rej-001"

        # Step 2: evaluate_job_ai_first evaluates the rejected job
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.evaluate_job_match_v2.return_value = AIMatchEvaluationV2(
            **sample_v2_response_dict(),
            match_source=MatchProvenance.AI_LIVE,
        )

        result = matcher.evaluate_job_ai_first(
            job=rejected_job,
            candidate=candidate,
            ai_service=mock_ai,
            resume_service=None,
        )

        assert isinstance(result, AIMatchEvaluationV2)
        assert result.match_source == MatchProvenance.AI_LIVE
        assert result.fit_verdict == AIMatchVerdict.GOOD_MATCH
        assert result.match_score == 78
        # Deterministic rejection was NOT an overriding blocker
        mock_ai.evaluate_job_match_v2.assert_called_once()

    def test_eligible_and_needs_review_jobs_both_reach_ai(self):
        """Proves that ELIGIBLE and NEEDS_REVIEW jobs both reach AI evaluation."""
        job_eligible = make_test_job(
            platform="freehire",
            platform_job_id="el-001",
            title="Java Engineer",
            decision=DeterministicDecision.ELIGIBLE,
        )
        job_review = make_test_job(
            platform="indeed",
            platform_job_id="rev-002",
            title="Full Stack Developer",
            decision=DeterministicDecision.NEEDS_REVIEW,
        )
        matcher = AIMatcher()

        candidates, excluded = matcher.select_ai_candidates([job_eligible, job_review])
        assert len(candidates) == 2
        assert len(excluded) == 0
        cand_ids = [c.platform_job_id for c in candidates]
        assert "el-001" in cand_ids
        assert "rev-002" in cand_ids


class TestCacheHitsAndQuotaInvariants:
    """Requirement 3 & 7: Cache hits bypass quota/provider; uncached evaluation reserves quota at most once."""

    def test_cache_hit_bypasses_quota_and_provider_call(self):
        """Proves that a cache hit reserves 0 quota and executes 0 provider calls."""
        job = make_test_job(platform="linkedin", platform_job_id="hit-001")
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        cached_v2 = AIMatchEvaluationV2(
            **sample_v2_response_dict(),
            match_source=MatchProvenance.AI_CACHED,
            prompt_version=PROMPT_VERSION_V2,
        )
        mock_resume_svc = MagicMock()
        mock_resume_svc.get_cached_match.return_value = cached_v2

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"

        result = matcher.evaluate_job_ai_first(
            job=job,
            candidate=candidate,
            ai_service=mock_ai,
            resume_service=mock_resume_svc,
        )

        assert result.match_source == MatchProvenance.AI_CACHED
        assert result.match_score == 78
        # Strict zero call invariants on cache hit
        mock_ai.reserve_persistent_quota.assert_not_called()
        mock_ai.evaluate_job_match_v2.assert_not_called()

    def test_single_quota_reservation_per_uncached_evaluation(self):
        """Proves that an uncached evaluation reserves quota exactly ONCE."""
        job = make_test_job(platform="indeed", platform_job_id="uncached-001")
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_resume_svc = MagicMock()
        mock_resume_svc.get_cached_match.return_value = None  # Cache miss

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.return_value = 1
        mock_ai.evaluate_job_match_v2.return_value = AIMatchEvaluationV2(
            **sample_v2_response_dict(),
            match_source=MatchProvenance.AI_LIVE,
        )

        result = matcher.evaluate_job_ai_first(
            job=job,
            candidate=candidate,
            ai_service=mock_ai,
            resume_service=mock_resume_svc,
            db_conn="mock-conn",
        )

        assert result.match_source == MatchProvenance.AI_LIVE
        # Exactly one reservation
        assert mock_ai.reserve_persistent_quota.call_count == 1
        assert mock_ai.evaluate_job_match_v2.call_count == 1
        # Saved to cache
        mock_resume_svc.save_cached_match.assert_called_once()


class TestQuotaExhaustionAndDeterministicFallback:
    """Requirement 4 & 5: Only quota exhaustion triggers fallback; all other errors fail closed."""

    def test_daily_quota_exhaustion_triggers_deterministic_fallback(self):
        """Proves that explicit AIDailyQuotaExhaustedError triggers DETERMINISTIC_FALLBACK."""
        job = make_test_job(
            platform="freehire",
            platform_job_id="quota-ex-001",
            title="Java Backend Engineer",
            description="Looking for Java Backend Developer with 2 years of Java, Spring Boot, REST APIs, Microservices, PostgreSQL.",
            decision=DeterministicDecision.ELIGIBLE,
        )
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.side_effect = AIDailyQuotaExhaustedError(
            "Job Bucket persistent daily AI call limit of 50 calls/day reached."
        )

        result = matcher.evaluate_job_ai_first(
            job=job,
            candidate=candidate,
            ai_service=mock_ai,
            resume_service=None,
            db_conn="mock-conn",
        )

        assert result.match_source == MatchProvenance.DETERMINISTIC_FALLBACK
        assert result.fit_verdict in (AIMatchVerdict.STRONG_MATCH, AIMatchVerdict.GOOD_MATCH)
        assert result.fallback_reason is not None
        assert "50 calls/day reached" in result.fallback_reason
        # LLM provider call was NOT executed
        mock_ai.evaluate_job_match_v2.assert_not_called()

    def test_daily_quota_exhaustion_on_rejected_job_produces_mismatch_fallback(self):
        """Proves fallback reflects deterministic rejection when quota is exhausted."""
        job = make_test_job(
            platform="linkedin",
            platform_job_id="rej-fallback-001",
            title="Staff Architect (8+ YOE)",
            description="Requires minimum 8+ years of Java backend experience.",
            decision=DeterministicDecision.REJECTED,
            rejection_reasons=["Exceeds 4.0 YOE cutoff"],
        )
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.side_effect = AIDailyQuotaExhaustedError("Quota cap 50 reached.")

        result = matcher.evaluate_job_ai_first(
            job=job,
            candidate=candidate,
            ai_service=mock_ai,
            resume_service=None,
            db_conn="mock-conn",
        )

        assert result.match_source == MatchProvenance.DETERMINISTIC_FALLBACK
        assert result.fit_verdict == AIMatchVerdict.MISMATCH
        assert result.match_score < 50

    def test_database_connectivity_error_fails_closed_without_fallback(self):
        """Proves DB connectivity failure fails closed and NEVER triggers fallback."""
        job = make_test_job()
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.side_effect = AIDatabaseConnectivityError(
            "Neon connection timed out."
        )

        with pytest.raises(AIDatabaseConnectivityError):
            matcher.evaluate_job_ai_first(
                job=job,
                candidate=candidate,
                ai_service=mock_ai,
                resume_service=None,
            )
        # Provider never called
        mock_ai.evaluate_job_match_v2.assert_not_called()

    def test_missing_api_key_fails_closed_without_fallback(self):
        """Proves missing API key fails closed and NEVER triggers fallback."""
        job = make_test_job()
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.return_value = 1
        mock_ai.evaluate_job_match_v2.side_effect = AIConfigurationError("GEMINI_API_KEY missing.")

        with pytest.raises(AIConfigurationError):
            matcher.evaluate_job_ai_first(
                job=job,
                candidate=candidate,
                ai_service=mock_ai,
                resume_service=None,
                db_conn="mock-conn",
            )

    def test_provider_timeout_and_http_error_fails_closed_without_fallback(self):
        """Proves network/HTTP errors fail closed and NEVER trigger fallback."""
        job = make_test_job()
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.return_value = 1
        mock_ai.evaluate_job_match_v2.side_effect = AIServiceError("Gemini HTTP 503 Service Unavailable")

        with pytest.raises(AIServiceError):
            matcher.evaluate_job_ai_first(
                job=job,
                candidate=candidate,
                ai_service=mock_ai,
                resume_service=None,
                db_conn="mock-conn",
            )

    def test_schema_validation_error_fails_closed_without_fallback(self):
        """Proves malformed LLM response fails closed and NEVER triggers fallback."""
        job = make_test_job()
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.reserve_persistent_quota.return_value = 1
        mock_ai.evaluate_job_match_v2.side_effect = AIServiceError(
            "Gemini response did not conform to required AIMatchEvaluationV2 schema"
        )

        with pytest.raises(AIServiceError):
            matcher.evaluate_job_ai_first(
                job=job,
                candidate=candidate,
                ai_service=mock_ai,
                resume_service=None,
                db_conn="mock-conn",
            )


class TestV2SchemaAndUncertainties:
    """Requirement 6: V2 schema validation, missing/transferable skills, experience uncertainty."""

    def test_v2_schema_field_validation(self):
        """Validates all required and extended V2 fields and enum constraints."""
        data = sample_v2_response_dict()
        eval_v2 = AIMatchEvaluationV2(**data)

        assert eval_v2.match_score == 78
        assert eval_v2.fit_verdict == AIMatchVerdict.GOOD_MATCH
        assert eval_v2.experience_fit == ExperienceSeniorityFit.POTENTIAL_STRETCH
        assert len(eval_v2.role_responsibilities) == 2
        assert len(eval_v2.required_skills) == 3
        assert len(eval_v2.preferred_skills) == 3
        assert len(eval_v2.matching_skills) == 3
        assert len(eval_v2.missing_skills) == 2
        assert len(eval_v2.transferable_skills) == 1
        assert "Docker" in eval_v2.transferable_skills[0]
        assert len(eval_v2.evidence) == 2
        assert len(eval_v2.uncertainties) == 2
        assert len(eval_v2.actionable_recommendations) == 2
        assert eval_v2.confidence_score == 0.85
        assert eval_v2.prompt_version == PROMPT_VERSION_V2

    def test_unknown_experience_remains_strictly_unknown(self):
        """
        Proves that when total_experience_years is None (and experience_confirmed=False),
        the candidate summary explicitly notes UNKNOWN / UNCONFIRMED, never zero,
        and never infers experience from search preferences.
        """
        cand_unknown = CandidateProfile(
            name="Unconfirmed Candidate",
            total_experience_years=None,
            experience_confirmed=False,
            experience_range_min=2.0,
            experience_range_max=3.0,
            hard_limit_years=4.0,
            languages=["Java"],
            frameworks=["Spring Boot"],
        )
        summary = build_candidate_summary_for_ai(cand_unknown)

        assert summary["experience_confirmed"] is False
        assert "UNKNOWN / UNCONFIRMED" in summary["total_experience"]
        assert summary["hard_limit_years"] == 4.0

        # Verify prompt builder includes explicit instruction regarding unconfirmed experience
        prompt = build_job_match_prompt_v2(
            candidate_summary=summary,
            job_title="Software Engineer",
            company="Tech Corp",
            location="Bengaluru",
            scrubbed_description="Looking for Java developer.",
        )
        assert "UNKNOWN / UNCONFIRMED" in prompt
        assert "NEVER treat missing experience as 0" in MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2

    def test_supplementary_eligibility_included_in_prompt_without_overriding(self):
        """Verifies deterministic eligibility is included in prompt as supplementary context."""
        job = make_test_job(
            decision=DeterministicDecision.REJECTED,
            rejection_reasons=["Exceeds 4.0 YOE limit"],
        )
        candidate = DEFAULT_CANDIDATE_PROFILE
        matcher = AIMatcher()

        system_inst, effective_prompt = matcher.build_effective_prompt_v2(candidate, job)

        assert "Supplementary Rule-Based Heuristic (Informational only):" in effective_prompt
        assert "REJECTED" in effective_prompt
        assert "Exceeds 4.0 YOE limit" in effective_prompt
        assert MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2 == system_inst


class TestV1V2CacheSeparationAndDeserialization:
    """Requirement 8: V1/V2 cache separation and safe deserialization."""

    def test_deserialize_cached_evaluation_distinguishes_v1_and_v2(self):
        """Proves deserialize_cached_evaluation preserves V1 and V2 instances accurately."""
        v1_row = {
            "match_score": 85,
            "fit_verdict": "HIGH_FIT",
            "matching_evidence": ["Java", "Spring Boot"],
            "missing_requirements": ["Docker"],
            "experience_assessment": "Matches 2 YOE",
            "reasoning": "Solid fit",
            "confidence_score": 0.9,
            "prompt_version": "v1",
        }
        res_v1 = deserialize_cached_evaluation(v1_row)
        assert isinstance(res_v1, AIMatchEvaluation)
        assert res_v1.prompt_version == PROMPT_VERSION_V1
        assert res_v1.fit_verdict == "HIGH_FIT"

        v2_row = sample_v2_response_dict()
        v2_row["prompt_version"] = "v2"
        res_v2 = deserialize_cached_evaluation(v2_row)
        assert isinstance(res_v2, AIMatchEvaluationV2)
        assert res_v2.prompt_version == PROMPT_VERSION_V2
        assert res_v2.fit_verdict == AIMatchVerdict.GOOD_MATCH

    def test_adapt_v1_to_v2_preserves_compatibility_without_corrupting_schema(self):
        """Proves explicit adaptation converts V1 evaluation into valid V2 structure."""
        v1_eval = AIMatchEvaluation(
            match_score=85,
            fit_verdict="HIGH_FIT",
            matching_evidence=["Java", "Spring Boot"],
            missing_requirements=["Docker"],
            experience_assessment="Matches 2 YOE",
            reasoning="Strong technical alignment",
            confidence_score=0.9,
            prompt_version=PROMPT_VERSION_V1,
        )

        adapted_v2 = adapt_v1_to_v2(v1_eval)
        assert isinstance(adapted_v2, AIMatchEvaluationV2)
        assert adapted_v2.match_score == 85
        assert adapted_v2.fit_verdict == AIMatchVerdict.STRONG_MATCH  # HIGH_FIT mapped to STRONG_MATCH
        assert adapted_v2.matching_skills == ["Java", "Spring Boot"]
        assert adapted_v2.missing_skills == ["Docker"]
        assert adapted_v2.prompt_version == PROMPT_VERSION_V1  # Explicit provenance preserved
        assert adapted_v2.match_source == MatchProvenance.AI_CACHED
        assert "Evaluated under V1 legacy schema" in adapted_v2.uncertainties[0]
        assert adapted_v2.reasoning == v1_eval.reasoning
