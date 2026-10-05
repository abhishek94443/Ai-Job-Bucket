import json
import os
from unittest.mock import MagicMock, patch
import pytest
import requests

from src.ai_service import (
    GeminiAIService,
    DEFAULT_GEMINI_MODEL,
    CURRENT_PROMPT_VERSION,
    QUOTA_TIMEZONE,
    resolve_model_name,
    AIConfigurationError,
    AIQuotaExceededError,
    AIServiceError,
    AIMatchEvaluation,
    compute_profile_hash,
    compute_job_hash,
)
from src.ai_matcher import AIMatcher
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
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
from src.ai_live_preflight import (
    execute_single_job_live_preflight,
    SingleJobPreflightResult,
)


def make_test_job(
    platform: str = "linkedin",
    platform_job_id: str = "test-job-123",
    title: str = "Backend Engineer",
    company: str = "Acme Corp",
    location: str = "Bengaluru",
    description: str = "We are seeking a Backend Engineer with Java and Spring Boot experience. Contact hr@acme.com or call +91-9876543210.",
    decision: DeterministicDecision = DeterministicDecision.ELIGIBLE,
    rejection_reasons: list = None,
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
            rejection_reasons=rejection_reasons or ([] if decision != DeterministicDecision.REJECTED else ["Experience exceeds hard limit"]),
            review_reasons=[],
            criteria_breakdown={"experience": decision.value},
        ),
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="preflight_test",
            evidence_notes="offline test listing",
            application_url=url,
        ),
        relevance=RelevanceScore(
            total_score=85,
            experience_level=ExperienceLevel.MID_LEVEL,
            eligibility=FitEligibility.HIGH_FIT if decision == DeterministicDecision.ELIGIBLE else FitEligibility.LOW_FIT,
            eligibility_reason="Deterministic alignment verified",
            confidence_level=ConfidenceLevel.HIGH,
            confidence_score=0.95,
            matched_skills=["java", "spring boot"],
        ),
    )


class TestModelConfigurationAndValidation:
    def test_default_model_is_gemini_35_flash_lite(self):
        service = GeminiAIService(api_key="test-key")
        assert service.model_name == "gemini-3.5-flash-lite"
        assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"

    def test_env_override_model_resolution(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-custom-pro"}):
            service = GeminiAIService(api_key="test-key")
            assert service.model_name == "gemini-custom-pro"

    def test_explicit_argument_overrides_env_var(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": "env-model"}):
            service = GeminiAIService(api_key="test-key", model_name="arg-model")
            assert service.model_name == "arg-model"

    def test_empty_model_name_raises_configuration_error(self):
        with pytest.raises(AIConfigurationError) as exc:
            GeminiAIService(api_key="test-key", model_name="")
        assert "empty or invalid" in str(exc.value)

    def test_whitespace_model_name_raises_configuration_error(self):
        with pytest.raises(AIConfigurationError) as exc:
            GeminiAIService(api_key="test-key", model_name="   ")
        assert "empty or invalid" in str(exc.value)

    def test_empty_env_var_model_raises_configuration_error(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": "   "}):
            with pytest.raises(AIConfigurationError) as exc:
                GeminiAIService(api_key="test-key")
            assert "empty or invalid" in str(exc.value)

    def test_strict_preservation_no_silent_fallback(self):
        service = GeminiAIService(api_key="test-key", model_name="specialized-evaluator")
        assert service.model_name == "specialized-evaluator"
        assert service.model_name != DEFAULT_GEMINI_MODEL

    def test_missing_api_key_raises_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            service = GeminiAIService(api_key=None, require_persistent_quota=False)
            assert not service.is_configured
            with pytest.raises(AIConfigurationError) as exc:
                service.generate_json("prompt", {})
            assert "GEMINI_API_KEY is not configured" in str(exc.value)


class TestRequestPayloadAndSecurity:
    @patch("src.ai_service.requests.post")
    def test_request_payload_format_and_x_goog_api_key_header(self, mock_post):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "match_score": 90,
                                    "fit_verdict": "HIGH_FIT",
                                    "matching_evidence": ["Java", "Spring Boot"],
                                    "missing_requirements": [],
                                    "experience_assessment": "Aligned",
                                    "reasoning": "Strong match",
                                    "confidence_score": 0.95,
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="my-secret-gemini-key", require_persistent_quota=False)
        candidate = DEFAULT_CANDIDATE_PROFILE

        eval_res = service.evaluate_job_match(
            profile=candidate,
            job_title="Java Developer",
            company="Enterprise Inc",
            location="Remote",
            description="Looking for Java 17 and Spring Boot microservices developer. Contact contact@enterprise.com.",
        )

        assert eval_res.match_score == 90
        assert eval_res.fit_verdict == "HIGH_FIT"

        # Verify HTTP post parameters
        call_args = mock_post.call_args
        url = call_args[0][0]
        headers = call_args[1]["headers"]
        body = call_args[1]["json"]

        # 1. URL must NOT contain api_key query param
        assert "key=" not in url
        assert url == f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent"

        # 2. Header authentication via x-goog-api-key
        assert headers["x-goog-api-key"] == "my-secret-gemini-key"
        assert headers["Content-Type"] == "application/json"

        # 3. Request payload matches Google v1beta schema
        assert "contents" in body
        assert "generationConfig" in body
        assert body["generationConfig"]["response_mime_type"] == "application/json"
        assert body["generationConfig"]["temperature"] == 0.1
        assert "response_schema" in body["generationConfig"]
        assert "systemInstruction" in body

        # 4. Privacy: candidate name, contact info, and job description PII redacted
        prompt_text = body["contents"][0]["parts"][0]["text"]
        assert "contact@enterprise.com" not in prompt_text
        assert "[REDACTED_EMAIL]" in prompt_text
        assert candidate.name not in prompt_text

    @patch("src.ai_service.requests.post")
    def test_api_key_never_appears_in_network_error_messages(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Failed to connect to host with key=my-super-secret-key-12345"
        )

        service = GeminiAIService(api_key="my-super-secret-key-12345", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.generate_json("test prompt", {})

        error_message = str(exc.value)
        assert "my-super-secret-key-12345" not in error_message
        assert "[REDACTED_API_KEY]" in error_message

    @patch("src.ai_service.requests.post")
    def test_api_key_never_appears_in_bad_request_error_messages(self, mock_post):
        mock_resp = MagicMock(status_code=400)
        mock_resp.text = "Bad Request: unauthorized key my-super-secret-key-99999"
        mock_post.return_value = mock_resp

        service = GeminiAIService(api_key="my-super-secret-key-99999", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.generate_json("test prompt", {})

        error_message = str(exc.value)
        assert "my-super-secret-key-99999" not in error_message
        assert "[REDACTED_API_KEY]" in error_message

    @patch("src.ai_service.requests.post")
    def test_http_404_unsupported_model_fails_clearly(self, mock_post):
        mock_resp = MagicMock(status_code=404)
        mock_resp.text = "Model not found"
        mock_post.return_value = mock_resp

        service = GeminiAIService(api_key="valid-key", model_name="nonexistent-model-xyz", require_persistent_quota=False)
        with pytest.raises(AIConfigurationError) as exc:
            service.generate_json("test prompt", {})
        assert "was not found or is unsupported by API (HTTP 404)" in str(exc.value)


class TestPrivacyAndEligibilityAudits:
    def test_candidate_payload_contains_no_candidate_pii(self):
        candidate = CandidateProfile(
            name="John Doe",
            total_experience_years=3.0,
            experience_confirmed=True,
            hard_limit_years=5.0,
            target_title="Fullstack Developer",
            languages=["Python", "TypeScript"],
            frameworks=["Django", "React"],
            databases=["PostgreSQL"],
            messaging_and_cache=["Redis"],
            devops_and_tools=["Docker"],
            inferred_capabilities=["REST API design"],
            target_cities=["New York"],
        )
        job = make_test_job()

        matcher = AIMatcher()
        payload = matcher.build_scrubbed_payload(job, candidate)

        # Technical attributes present
        cand_summary = payload["candidate_summary"]
        assert cand_summary["languages"] == ["Python", "TypeScript"]
        assert cand_summary["frameworks"] == ["Django", "React"]
        assert cand_summary["experience_confirmed"] is True

        # Strict PII exclusion
        assert "name" not in cand_summary
        assert "email" not in cand_summary
        assert "phone" not in cand_summary
        assert "target_cities" not in cand_summary
        assert "raw_resume" not in cand_summary
        assert "John Doe" not in json.dumps(cand_summary)

    def test_job_description_pii_scrubbed_before_payload(self):
        job = make_test_job(
            description="Contact hiring@startup.io or call +91-9123456780. PIN: 560001. Visit https://linkedin.com/in/recruiter-bob"
        )
        candidate = DEFAULT_CANDIDATE_PROFILE

        matcher = AIMatcher()
        payload = matcher.build_scrubbed_payload(job, candidate)

        preview = payload["description_preview"]
        assert "hiring@startup.io" not in preview
        assert "+91-9123456780" not in preview
        assert "recruiter-bob" not in preview
        assert payload["redactions_applied"]["emails"] >= 1
        assert payload["redactions_applied"]["phones"] >= 1
        assert payload["redactions_applied"]["pins"] >= 1
        assert payload["redactions_applied"]["links"] >= 1

    def test_cache_keys_incorporate_all_four_invalidation_dimensions(self):
        candidate = DEFAULT_CANDIDATE_PROFILE
        job = make_test_job()

        matcher_v1 = AIMatcher(model_version="gemini-3.5-flash-lite", prompt_version="v1")
        payload_v1 = matcher_v1.build_scrubbed_payload(job, candidate)

        matcher_v2 = AIMatcher(model_version="gemini-custom-model", prompt_version="v2")
        payload_v2 = matcher_v2.build_scrubbed_payload(job, candidate)

        # Profile & Job hashes are identical when profile and job are identical
        assert payload_v1["profile_hash"] == payload_v2["profile_hash"]
        assert payload_v1["job_hash"] == payload_v2["job_hash"]

        # Model and prompt versions differ
        assert payload_v1["model_version"] != payload_v2["model_version"]
        assert payload_v1["prompt_version"] != payload_v2["prompt_version"]

    def test_ai_first_candidate_selection_includes_rejected_jobs(self):
        eligible_job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        review_job = make_test_job(decision=DeterministicDecision.NEEDS_REVIEW)
        rejected_job = make_test_job(decision=DeterministicDecision.REJECTED)

        candidates, excluded = AIMatcher.select_ai_candidates([eligible_job, review_job, rejected_job])

        assert len(candidates) == 3
        assert len(excluded) == 0
        assert rejected_job in candidates


class TestMalformedProviderResponses:
    @patch("src.ai_service.requests.post")
    def test_missing_candidates_fails_safely(self, mock_post):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"candidates": []}
        mock_post.return_value = mock_resp

        service = GeminiAIService(api_key="valid-key", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.generate_json("prompt", {})
        assert "no candidates" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_invalid_json_part_fails_safely(self, mock_post):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "{malformed json..."}]}}]
        }
        mock_post.return_value = mock_resp

        service = GeminiAIService(api_key="valid-key", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.generate_json("prompt", {})
        assert "Failed to parse structured JSON" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_schema_mismatch_fails_safely_in_evaluate_job_match(self, mock_post):
        # Missing required fields like fit_verdict and matching_evidence
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"match_score": "not_an_int"})}]}}]
        }
        mock_post.return_value = mock_resp

        service = GeminiAIService(api_key="valid-key", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.evaluate_job_match(
                profile=DEFAULT_CANDIDATE_PROFILE,
                job_title="Software Engineer",
                company="Company",
                location="Bengaluru",
                description="desc",
            )
        assert "did not conform to required AIMatchEvaluation schema" in str(exc.value)


class TestSingleJobLivePreflightSafety:
    def test_live_preflight_strictly_disabled_by_default(self):
        job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        candidate = DEFAULT_CANDIDATE_PROFILE

        with pytest.raises(RuntimeError) as exc:
            execute_single_job_live_preflight(job, candidate)
        assert "strictly disabled by default" in str(exc.value)

    def test_live_preflight_refuses_deterministic_rejected_job(self):
        rejected_job = make_test_job(decision=DeterministicDecision.REJECTED)
        candidate = DEFAULT_CANDIDATE_PROFILE

        with pytest.raises(ValueError) as exc:
            execute_single_job_live_preflight(rejected_job, candidate, enable_live_test=True)
        assert "deterministically REJECTED" in str(exc.value)
        assert "Deterministic rejections are impossible to override" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_live_preflight_returns_cached_evaluation_without_http_call(self, mock_post):
        job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        candidate = DEFAULT_CANDIDATE_PROFILE

        mock_resume_service = MagicMock()
        mock_cached_eval = AIMatchEvaluation(
            match_score=92,
            fit_verdict="HIGH_FIT",
            matching_evidence=["Java 17", "PostgreSQL"],
            missing_requirements=[],
            experience_assessment="Match",
            reasoning="Precomputed cached evaluation",
            confidence_score=0.98,
        )
        mock_resume_service.get_cached_match.return_value = mock_cached_eval

        ai_svc = GeminiAIService(api_key="valid-key", require_persistent_quota=False)

        res = execute_single_job_live_preflight(
            job=job,
            candidate=candidate,
            enable_live_test=True,
            resume_service=mock_resume_service,
            ai_service=ai_svc,
        )

        assert res.from_cache is True
        assert res.quota_consumed is False
        assert res.evaluation.match_score == 92
        assert mock_post.call_count == 0

    @patch("src.ai_service.requests.post")
    def test_live_preflight_dispatches_single_job_and_persists_to_cache(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps({
                                        "match_score": 85,
                                        "fit_verdict": "HIGH_FIT",
                                        "matching_evidence": ["Spring Boot"],
                                        "missing_requirements": [],
                                        "experience_assessment": "Aligned",
                                        "reasoning": "Fits junior-to-mid profile",
                                        "confidence_score": 0.9,
                                    })
                                }
                            ]
                        }
                    }
                ]
            },
        )

        job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        candidate = DEFAULT_CANDIDATE_PROFILE

        mock_resume_service = MagicMock()
        mock_resume_service.get_cached_match.return_value = None  # Cache miss

        ai_svc = GeminiAIService(api_key="valid-key", require_persistent_quota=False)

        res = execute_single_job_live_preflight(
            job=job,
            candidate=candidate,
            enable_live_test=True,
            resume_service=mock_resume_service,
            ai_service=ai_svc,
        )

        assert res.from_cache is False
        assert res.quota_consumed is True
        assert res.evaluation.match_score == 85
        assert mock_post.call_count == 1
        mock_resume_service.save_cached_match.assert_called_once()


class TestSingleJobLiveCLIAndTransactionReadiness:
    """
    Phase 3D.2 Test Suite: Single-Job Live-Test CLI Readiness,
    transaction boundary isolation, error sanitization, and deterministic safety.
    """

    def test_cli_disabled_by_default(self, capsys):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        parser = build_parser()
        args = parser.parse_args([])  # No flags passed

        exit_code = run_preflight_cli(args)
        assert exit_code == 1

        captured = capsys.readouterr().out
        assert "DISABLED BY DEFAULT" in captured
        assert "--enable-live-ai-test" in captured

    @patch("src.ai_service.requests.post")
    def test_missing_opt_in_results_in_zero_provider_calls(self, mock_post, capsys):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        parser = build_parser()
        args = parser.parse_args([])

        exit_code = run_preflight_cli(args)
        assert exit_code == 1
        mock_post.assert_not_called()

    @patch("src.ai_service.requests.post")
    @patch("src.resume_service.ResumeService.has_active_profile", return_value=True)
    @patch("src.resume_service.ResumeService.get_active_profile", return_value=DEFAULT_CANDIDATE_PROFILE)
    @patch("scripts.single_job_live_test.load_single_job_for_preflight")
    def test_deterministic_rejection_results_in_zero_provider_calls(
        self, mock_load_job, mock_get_profile, mock_has_profile, mock_post, capsys
    ):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        rejected_job = make_test_job(decision=DeterministicDecision.REJECTED)
        mock_load_job.return_value = rejected_job

        parser = build_parser()
        args = parser.parse_args(["--enable-live-ai-test"])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            exit_code = run_preflight_cli(args)

        assert exit_code == 1
        mock_post.assert_not_called()
        captured = capsys.readouterr().out
        assert "deterministically REJECTED" in captured

    @patch("src.ai_service.requests.post")
    @patch("src.resume_service.ResumeService.has_active_profile", return_value=True)
    @patch("src.resume_service.ResumeService.get_active_profile", return_value=DEFAULT_CANDIDATE_PROFILE)
    @patch("src.resume_service.ResumeService.get_cached_match")
    @patch("scripts.single_job_live_test.load_single_job_for_preflight")
    def test_cached_results_skip_provider_calls_and_quota_reservation(
        self, mock_load_job, mock_get_cache, mock_get_profile, mock_has_profile, mock_post, capsys
    ):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        eligible_job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        mock_load_job.return_value = eligible_job

        cached_eval = AIMatchEvaluation(
            match_score=95,
            fit_verdict="HIGH_FIT",
            matching_evidence=["Java", "Spring Boot"],
            missing_requirements=[],
            experience_assessment="Match",
            reasoning="Pre-existing cache entry",
            confidence_score=0.99,
        )
        mock_get_cache.return_value = cached_eval

        parser = build_parser()
        args = parser.parse_args(["--enable-live-ai-test"])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            exit_code = run_preflight_cli(args)

        assert exit_code == 0
        mock_post.assert_not_called()
        captured = capsys.readouterr().out
        assert "CACHED (0 external calls, 0 quota used)" in captured
        assert "95/100" in captured

    @patch("src.ai_service.requests.post")
    @patch("src.resume_service.ResumeService.has_active_profile", return_value=True)
    @patch("src.resume_service.ResumeService.get_active_profile", return_value=DEFAULT_CANDIDATE_PROFILE)
    @patch("src.resume_service.ResumeService.get_cached_match", return_value=None)
    @patch("src.resume_service.ResumeService.save_cached_match")
    @patch("src.db_config.connect_database")
    @patch("scripts.single_job_live_test.load_single_job_for_preflight")
    def test_uncached_eligible_job_dispatches_at_most_one_mocked_request(
        self, mock_load_job, mock_connect_db, mock_save_cache, mock_get_cache,
        mock_get_profile, mock_has_profile, mock_post, capsys
    ):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        eligible_job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        mock_load_job.return_value = eligible_job

        # Mock DB connection for quota check
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)  # 1st call
        mock_connect_db.return_value = mock_conn

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps({
                                        "match_score": 88,
                                        "fit_verdict": "HIGH_FIT",
                                        "matching_evidence": ["Java", "Spring Boot"],
                                        "missing_requirements": [],
                                        "experience_assessment": "Aligned",
                                        "reasoning": "Live test passed",
                                        "confidence_score": 0.95,
                                    })
                                }
                            ]
                        }
                    }
                ]
            },
        )

        parser = build_parser()
        args = parser.parse_args(["--enable-live-ai-test"])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            exit_code = run_preflight_cli(args)

        assert exit_code == 0
        assert mock_post.call_count == 1  # Exactly ONE request dispatched
        mock_save_cache.assert_called_once()
        captured = capsys.readouterr().out
        assert "LIVE PROVIDER CALL (1 quota consumed)" in captured
        assert "88/100" in captured

    @patch("src.ai_service.requests.post")
    @patch("src.resume_service.ResumeService.has_active_profile", return_value=True)
    @patch("src.resume_service.ResumeService.get_active_profile", return_value=DEFAULT_CANDIDATE_PROFILE)
    @patch("src.resume_service.ResumeService.get_cached_match", return_value=None)
    @patch("src.db_config.connect_database")
    @patch("scripts.single_job_live_test.load_single_job_for_preflight")
    def test_quota_reservation_failure_prevents_dispatch(
        self, mock_load_job, mock_connect_db, mock_get_cache,
        mock_get_profile, mock_has_profile, mock_post, capsys
    ):
        from scripts.single_job_live_test import build_parser, run_preflight_cli

        eligible_job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        mock_load_job.return_value = eligible_job

        # Mock DB failure
        mock_connect_db.side_effect = Exception("Neon database network timeout")

        parser = build_parser()
        args = parser.parse_args(["--enable-live-ai-test"])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            exit_code = run_preflight_cli(args)

        assert exit_code == 1
        mock_post.assert_not_called()  # Fails closed! Zero HTTP calls!
        captured = capsys.readouterr().out
        assert "database unavailable" in captured.lower()

    def test_transaction_boundary_and_connection_cleanup(self):
        """Verifies that quota reservation commits immediately and connection is closed before HTTP call."""
        job = make_test_job(decision=DeterministicDecision.ELIGIBLE)
        candidate = DEFAULT_CANDIDATE_PROFILE

        mock_quota_conn = MagicMock()
        mock_quota_cur = MagicMock()
        mock_quota_conn.cursor.return_value.__enter__.return_value = mock_quota_cur
        mock_quota_cur.fetchone.return_value = (1,)

        mock_rs = MagicMock()
        mock_rs.get_cached_match.return_value = None

        mock_ai = MagicMock()
        mock_ai.is_configured = True
        mock_ai.model_name = "gemini-3.5-flash-lite"
        mock_ai.evaluate_job_match.return_value = AIMatchEvaluation(
            match_score=80,
            fit_verdict="HIGH_FIT",
            matching_evidence=["Java"],
            missing_requirements=[],
            reasoning="OK",
            confidence_score=0.9,
        )

        with patch("src.db_config.connect_database", return_value=mock_quota_conn):
            result = execute_single_job_live_preflight(
                job=job,
                candidate=candidate,
                enable_live_test=True,
                resume_service=mock_rs,
                ai_service=mock_ai,
            )

        # 1. Quota reservation executed on quota_conn
        mock_ai.reserve_persistent_quota.assert_called_once_with(mock_quota_conn)
        # 2. Dedicated quota connection was closed before evaluate_job_match
        mock_quota_conn.close.assert_called_once()
        # 3. Provider call was made with reserve_quota=False
        mock_ai.evaluate_job_match.assert_called_once_with(
            profile=candidate,
            job_title=job.title,
            company=job.company,
            location=job.location,
            description=job.description_snippet,
            db_conn=None,
            reserve_quota=False,
        )
        # 4. Result was saved to cache
        mock_rs.save_cached_match.assert_called_once()
        assert result.quota_consumed is True
