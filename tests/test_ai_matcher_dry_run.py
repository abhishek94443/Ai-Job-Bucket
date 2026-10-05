from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime
import pytest

from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    DeterministicDecision,
    DeterministicEligibility,
    FitEligibility,
    ExperienceLevel,
    ConfidenceLevel,
    RelevanceScore,
)
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.ai_matcher import AIMatcher, AIMatchDryRunReport
from src.ai_service import (
    GeminiAIService,
    AIQuotaExceededError,
    AIDatabaseConnectivityError,
    AIServiceError,
    compute_profile_hash,
    compute_job_hash,
    CURRENT_PROMPT_VERSION,
)
from src.pipeline import run_pipeline
from src.discovery import JobDiscoveryService


def make_test_job(
    platform: str = "freehire",
    platform_job_id: str = "job-1",
    title: str = "Java Backend Engineer",
    company: str = "Tech Corp",
    location: str = "Bengaluru",
    description: str = "Java, Spring Boot, REST APIs.",
    decision: DeterministicDecision = DeterministicDecision.ELIGIBLE,
    rejection_reasons: list = None,
    review_reasons: list = None,
) -> JobRecord:
    url = f"https://example.com/jobs/{platform_job_id}"
    return JobRecord(
        platform=platform,
        platform_job_id=platform_job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        normalized_url=url,
        description_snippet=description,
        deterministic_eligibility=DeterministicEligibility(
            decision=decision,
            is_eligible=(decision != DeterministicDecision.REJECTED),
            reasons=["Stack compatible"] if decision == DeterministicDecision.ELIGIBLE else [],
            rejection_reasons=rejection_reasons or ([] if decision != DeterministicDecision.REJECTED else ["Exceeds hard cutoff"]),
            review_reasons=review_reasons or ([] if decision == DeterministicDecision.NEEDS_REVIEW else []),
            criteria_breakdown={"experience": decision.value},
        ),
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="unit_test",
            evidence_notes="mock test listing",
            application_url=url,
        ),
        relevance=RelevanceScore(
            total_score=80,
            experience_level=ExperienceLevel.JUNIOR,
            eligibility=FitEligibility.HIGH_FIT if decision == DeterministicDecision.ELIGIBLE else FitEligibility.LOW_FIT,
            eligibility_reason="Deterministic alignment verified",
            confidence_level=ConfidenceLevel.HIGH,
            confidence_score=0.9,
            matched_skills=["java", "spring boot"],
        ),
    )


class TestAIMatcherDryRun:
    """
    Phase 3D Test Suite: AI semantic matching integration audit & dry run.
    Verifies dry-run isolation, eligibility filtering, privacy scrubbing,
    cache key versioning, quota fail-closed behavior, and malformed output handling.
    """

    @pytest.fixture
    def candidate(self) -> CandidateProfile:
        return CandidateProfile(
            name="Abhishek Dwivedi",
            total_experience_years=2.0,
            experience_confirmed=True,
            hard_limit_years=4.0,
            target_title="Java Backend Developer",
            languages=["Java", "SQL"],
            frameworks=["Spring Boot", "Hibernate"],
            databases=["PostgreSQL"],
            messaging_and_cache=["Kafka"],
            devops_and_tools=["Docker"],
            inferred_capabilities=["REST API design"],
            target_cities=["Bengaluru"],
            remote_preference="flexible",
        )

    @pytest.fixture
    def matcher(self) -> AIMatcher:
        return AIMatcher()

    @patch("src.ai_service.requests.post")
    def test_dry_run_isolation_makes_zero_api_calls_and_zero_writes(self, mock_post, candidate, matcher):
        """Dry-run must strictly make ZERO LLM API calls, ZERO DB writes, and reserve ZERO quota."""
        jobs = [
            make_test_job(platform="freehire", platform_job_id="fh-1", decision=DeterministicDecision.ELIGIBLE),
            make_test_job(platform="linkedin", platform_job_id="li-2", decision=DeterministicDecision.NEEDS_REVIEW),
        ]

        report = matcher.run_dry_run(jobs, candidate)

        # Invariant checks
        assert report.is_dry_run is True
        assert report.llm_calls_made == 0
        assert report.quota_reserved == 0
        assert report.db_writes_made == 0
        mock_post.assert_not_called()

        # Output formatting check
        summary = report.format_summary()
        assert "LLM API Calls Dispatched:  0" in summary
        assert "Quota Units Reserved:      0" in summary
        assert "Database Writes:           0" in summary

    def test_ai_first_candidate_selection_includes_all_jobs(self, candidate, matcher):
        """
        In AI-First architecture, deterministic rejection is not a gate that excludes
        jobs from AI matching. All discovered jobs (ELIGIBLE, NEEDS_REVIEW, REJECTED)
        reach AI matching, with deterministic eligibility preserved as supplementary context.
        """
        job_eligible = make_test_job(
            platform="freehire", platform_job_id="fh-1", title="Java Backend Engineer", decision=DeterministicDecision.ELIGIBLE
        )
        job_review = make_test_job(
            platform="indeed", platform_job_id="ind-2", title="Java Software Engineer", decision=DeterministicDecision.NEEDS_REVIEW
        )
        job_rejected = make_test_job(
            platform="linkedin", platform_job_id="li-3", title="Staff Architect (8+ YOE)", decision=DeterministicDecision.REJECTED
        )

        report = matcher.run_dry_run([job_eligible, job_review, job_rejected], candidate)

        assert report.total_discovered == 3
        assert report.ai_candidates_count == 3
        assert report.excluded_count == 0

        candidate_ids = [c.platform_job_id for c in report.ai_candidates]
        assert "fh-1" in candidate_ids
        assert "ind-2" in candidate_ids
        assert "li-3" in candidate_ids  # Rejected job IS in AI candidates in AI-First mode

        # Verify deterministic eligibility is preserved as supplementary information
        cand_map = {c.platform_job_id: c for c in report.ai_candidates}
        assert cand_map["li-3"].eligibility_decision == DeterministicDecision.REJECTED.value
        assert "Deterministic REJECTED" in cand_map["li-3"].selection_reason

    def test_privacy_scrubbing_in_payload_construction(self, candidate, matcher):
        """
        Job descriptions must have recruiter emails, phone numbers, and profile URLs redacted.
        Candidate summary must NOT contain personal name, email, or unredacted contact info.
        """
        raw_description = (
            "Looking for Java developer. Contact recruiter at hiring@bigcorp.com or call +91-9876543210. "
            "LinkedIn: https://linkedin.com/in/recruiter-jane. Abhishek Dwivedi please apply."
        )
        job = make_test_job(platform="indeed", platform_job_id="ind-10", description=raw_description)

        payload = matcher.build_scrubbed_payload(job, candidate)

        # 1. Job description scrubbing assertions
        preview = payload["description_preview"]
        assert "hiring@bigcorp.com" not in preview
        assert "[REDACTED_EMAIL]" in preview
        assert "9876543210" not in preview
        assert "[REDACTED_PHONE]" in preview
        assert "recruiter-jane" not in preview
        assert "[REDACTED_PROFILE_URL]" in preview
        assert "Abhishek Dwivedi" not in preview
        assert "[CANDIDATE_NAME]" in preview

        # 2. Candidate summary assertions (technical data only, zero PII)
        cand_summary = payload["candidate_summary"]
        assert "name" not in cand_summary
        assert "email" not in cand_summary
        assert "phone" not in cand_summary
        assert "Java" in cand_summary["languages"]
        assert "Spring Boot" in cand_summary["frameworks"]

    def test_cache_key_versioning_and_invalidation(self, candidate):
        """
        Profile hash must change when candidate skills change.
        Job hash must change when title, company, or description change.
        """
        hash1 = compute_profile_hash(candidate)

        # Modify candidate skills
        candidate_modified = candidate.model_copy(update={"languages": ["Java", "SQL", "Go"]})
        hash2 = compute_profile_hash(candidate_modified)
        assert hash1 != hash2

        # Verify job hash stability and sensitivity
        j_hash1 = compute_job_hash("freehire", "101", "Java Dev", "Acme", "Spring Boot required")
        j_hash2 = compute_job_hash("freehire", "101", "Java Dev", "Acme", "Spring Boot required")
        assert j_hash1 == j_hash2

        # Change description
        j_hash3 = compute_job_hash("freehire", "101", "Java Dev", "Acme", "Spring Boot & Kafka required")
        assert j_hash1 != j_hash3

    def test_quota_fail_closed_when_neon_unavailable(self):
        """GeminiAIService must fail closed if persistent quota cannot be verified."""
        service = GeminiAIService(api_key="test-key", require_persistent_quota=True)

        # 1. Missing connection when Neon is unreachable
        with patch("src.ai_service.connect_database", side_effect=Exception("Neon unreachable")):
            with pytest.raises(AIDatabaseConnectivityError, match="Persistent quota tracking cannot be confirmed"):
                service.reserve_persistent_quota(conn=None)

        # 2. Broken connection
        broken_conn = MagicMock()
        broken_conn.cursor.side_effect = RuntimeError("Neon database connection dropped")
        with pytest.raises(AIDatabaseConnectivityError, match="Failing closed: skipping AI calls"):
            service.reserve_persistent_quota(conn=broken_conn)

    @patch("src.ai_service.requests.post")
    def test_malformed_ai_output_handling(self, mock_post):
        """Malformed JSON or missing candidates from Gemini must raise AIServiceError safely."""
        service = GeminiAIService(api_key="test-key", require_persistent_quota=False)

        # Case A: Non-JSON response
        mock_resp_a = MagicMock()
        mock_resp_a.status_code = 200
        mock_resp_a.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "THIS IS NOT VALID JSON"}]}}]
        }
        mock_post.return_value = mock_resp_a

        with pytest.raises(AIServiceError, match="Failed to parse structured JSON"):
            service.generate_json("test prompt", {})

        # Case B: Empty candidates
        mock_resp_b = MagicMock()
        mock_resp_b.status_code = 200
        mock_resp_b.json.return_value = {"candidates": []}
        mock_post.return_value = mock_resp_b

        with pytest.raises(AIServiceError, match="Gemini response contained no candidates"):
            service.generate_json("test prompt", {})

    @pytest.mark.asyncio
    async def test_pipeline_dry_run_integration(self, candidate):
        """Verifies run_pipeline(..., ai_dry_run=True) runs end-to-end without DB writes or AI calls."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "data": [
                {
                    "public_slug": "dry-run-job-1",
                    "external_id": "dry-101",
                    "title": "Java Backend Developer",
                    "company": "Infosys",
                    "cities": ["Bengaluru"],
                    "url": "https://infosys.com/jobs/101",
                    "posted_at": "2026-10-11",
                    "description": "Java and Spring Boot. 2 years experience.",
                    "enrichment": {"experience_years_min": 2},
                    "reality": {"class": "fresh"},
                }
            ]
        }

        with patch("src.pipeline.JobDiscoveryService") as MockService, \
             patch("src.ai_service.requests.post") as mock_post:
            mock_service_instance = JobDiscoveryService(mcp_client=mock_mcp)
            MockService.return_value = mock_service_instance

            jobs = await run_pipeline(
                profile=candidate,
                skill_queries=["Java Spring Boot"],
                location="Bengaluru",
                limit_per_source=1,
                persist_to_db=False,
                ai_dry_run=True,
            )

            assert len(jobs) >= 1
            mock_post.assert_not_called()

    def test_dry_run_and_live_service_share_identical_candidate_summary(self, candidate):
        """Phase 3D.4: Validates that dry-run payload and live Gemini service assemble identical candidate summary."""
        from src.ai_service import build_candidate_summary_for_ai
        job = make_test_job()
        matcher = AIMatcher()
        payload = matcher.build_scrubbed_payload(job, candidate)

        expected_summary = build_candidate_summary_for_ai(candidate)
        assert payload["candidate_summary"] == expected_summary
        assert payload["candidate_summary"]["languages"] == ["Java", "SQL"]
        assert payload["candidate_summary"]["frameworks"] == ["Hibernate", "Spring Boot"]

    def test_unknown_experience_retains_uncertainty_and_avoids_zero(self):
        """
        Phase 3D.4: Verifies unknown/unconfirmed experience:
        1. Never defaults to 0.0 years or treats search preference (2-3 YOE) as resume facts.
        2. Evaluates as NEEDS_REVIEW under DeterministicJobEvaluator for 1-3 YOE jobs.
        3. Deterministic rejection strictly takes precedence for jobs exceeding hard cutoff (> 4 YOE).
        """
        from src.eligibility import DeterministicJobEvaluator
        from src.ai_service import build_candidate_summary_for_ai

        candidate = CandidateProfile(
            name="Abhishek Dwivedi",
            total_experience_years=None,
            experience_confirmed=False,
            experience_range_min=2.0,
            experience_range_max=3.0,
            hard_limit_years=4.0,
            target_title="Java Developer",
            languages=["Java", "Python"],
            frameworks=["Spring Boot"],
        )

        # 1. Summary verification
        summary = build_candidate_summary_for_ai(candidate)
        assert "UNKNOWN / UNCONFIRMED" in summary["total_experience"]
        assert "0" not in summary["total_experience"]
        assert "2" not in summary["total_experience"]
        assert summary["experience_confirmed"] is False

        evaluator = DeterministicJobEvaluator()

        # 2. Job with 1-3 years requirement -> NEEDS_REVIEW (never assumed ELIGIBLE)
        job_1_3 = make_test_job(
            description="Looking for Java developer with 1-3 years of experience. Spring Boot required."
        )
        det_1_3 = evaluator.evaluate(job_1_3, candidate)
        assert det_1_3.decision == DeterministicDecision.NEEDS_REVIEW
        assert det_1_3.is_eligible is True
        assert any("unconfirmed or unknown" in r for r in det_1_3.review_reasons)

        # 3. Job with 5+ years requirement -> REJECTED (exceeds hard cutoff of 4.0)
        job_5_plus = make_test_job(
            description="Looking for Senior Java developer with 5+ years of experience."
        )
        det_5_plus = evaluator.evaluate(job_5_plus, candidate)
        assert det_5_plus.decision == DeterministicDecision.REJECTED
        assert det_5_plus.is_eligible is False
        assert any("exceeding candidate hard cutoff of 4 years" in r for r in det_5_plus.rejection_reasons)

    def test_build_effective_prompt_matches_live_service_prompt(self, candidate):
        """Phase 3D.4: Verifies build_effective_prompt produces identical prompt as GeminiAIService."""
        from src.ai_service import (
            GeminiAIService,
            MATCH_EVALUATION_SYSTEM_INSTRUCTION,
            build_job_match_prompt,
            build_candidate_summary_for_ai,
        )

        job = make_test_job(
            description="Hiring Java developer. Contact hr@corp.com or call +91-9876543210."
        )
        matcher = AIMatcher()
        system_inst, effective_prompt = matcher.build_effective_prompt(candidate, job)

        assert system_inst == MATCH_EVALUATION_SYSTEM_INSTRUCTION
        assert "hr@corp.com" not in effective_prompt
        assert "[REDACTED_EMAIL]" in effective_prompt
        assert "+91-9876543210" not in effective_prompt
        assert "[REDACTED_PHONE]" in effective_prompt
        assert "Candidate Profile:" in effective_prompt
        assert "Job Listing:" in effective_prompt
        assert "Evaluate match fit." in effective_prompt
