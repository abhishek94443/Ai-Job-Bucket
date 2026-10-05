import datetime
import json
from unittest.mock import MagicMock, patch
import pytest
import requests

from src.ai_service import (
    GeminiAIService,
    DEFAULT_GEMINI_MODEL,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    CURRENT_PROMPT_VERSION,
    QUOTA_TIMEZONE,
    AIConfigurationError,
    AIQuotaExceededError,
    AIDatabaseConnectivityError,
    AIDailyQuotaExhaustedError,
    AIServiceError,
    AIMatchEvaluation,
    compute_profile_hash,
    compute_job_hash,
)
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE


class TestGeminiAIService:
    def test_default_model_is_verified_gemini_35_flash_lite(self):
        service = GeminiAIService(api_key="test-key")
        assert service.model_name == "gemini-3.5-flash-lite"
        assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
        assert QUOTA_TIMEZONE == "UTC"

    def test_strict_model_configuration_no_silent_fallback(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "custom-model-id"}):
            service = GeminiAIService(api_key="test-key")
            assert service.model_name == "custom-model-id"

    def test_missing_api_key_raises_configuration_error(self):
        service = GeminiAIService(api_key=None, require_persistent_quota=False)
        with patch.dict("os.environ", {}, clear=True):
            service.api_key = None
            assert not service.is_configured
            with pytest.raises(AIConfigurationError) as exc:
                service.generate_json("prompt", {})
            assert "GEMINI_API_KEY is not configured" in str(exc.value)

    def test_fail_closed_when_database_connection_missing(self):
        """Proves that when persistent quota cannot be verified, AI calls are skipped."""
        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=True)
        with patch("src.ai_service.connect_database", side_effect=Exception("Neon unreachable")):
            with pytest.raises(AIDatabaseConnectivityError) as exc:
                service.generate_json("prompt", {}, db_conn=None)
            assert "Persistent quota tracking cannot be confirmed" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_fail_closed_when_database_unreachable(self, mock_post):
        """Proves that a database error during quota check stops the request before sending HTTP."""
        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=True)
        broken_conn = MagicMock()
        broken_conn.cursor.side_effect = Exception("Neon connection timed out")

        with pytest.raises(AIDatabaseConnectivityError) as exc:
            service.generate_json("prompt", {}, db_conn=broken_conn)
        assert "Failing closed: skipping AI calls" in str(exc.value)
        # Verify HTTP post was NEVER attempted
        mock_post.assert_not_called()

    def test_utc_timezone_boundary_in_quota_query(self):
        """Verifies that the atomic SQL query specifies UTC explicitly."""
        service = GeminiAIService(api_key="test-fake-key", daily_limit=10)
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        service.reserve_persistent_quota(mock_conn)
        executed_params = mock_cur.execute.call_args[0][1]
        assert executed_params[0] == datetime.datetime.now(datetime.timezone.utc).date()
        assert service.get_current_utc_date() == datetime.datetime.now(datetime.timezone.utc).date()

    def test_midnight_utc_rollover_quota_accounting(self):
        """Proves that quota reservation around midnight UTC partitions calls into distinct UTC dates."""
        service = GeminiAIService(api_key="test-fake-key", daily_limit=10)
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        # 1 second before midnight UTC
        before_midnight = datetime.date(2026, 10, 11)
        service.reserve_persistent_quota(mock_conn, utc_date=before_midnight)
        params_before = mock_cur.execute.call_args[0][1]
        assert params_before[0] == datetime.date(2026, 10, 11)

        # 1 second after midnight UTC (new day boundary)
        after_midnight = datetime.date(2026, 10, 12)
        service.reserve_persistent_quota(mock_conn, utc_date=after_midnight)
        params_after = mock_cur.execute.call_args[0][1]
        assert params_after[0] == datetime.date(2026, 10, 12)
        assert params_before[0] != params_after[0]

    @patch("src.ai_service.requests.post")
    def test_atomic_quota_reservation_precedes_http_call(self, mock_post):
        """Verifies quota is reserved BEFORE the HTTP request is dispatched."""
        service = GeminiAIService(api_key="test-fake-key", daily_limit=10)
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps({"fit": "ok"})}]}}]},
        )

        service.generate_json("prompt", {}, db_conn=mock_conn)
        # Both quota reservation and HTTP dispatch were executed
        mock_cur.execute.assert_called_once()
        mock_post.assert_called_once()

    def test_persistent_quota_fails_closed_when_cap_reached(self):
        """Verifies concurrent/subsequent calls fail closed when limit is hit."""
        service = GeminiAIService(api_key="test-fake-key", daily_limit=5)
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        # When cap reached in DB, atomic update updates 0 rows and returns None
        mock_cur.fetchone.return_value = None

        with pytest.raises(AIQuotaExceededError) as exc:
            service.reserve_persistent_quota(mock_conn)
        assert "persistent daily AI call limit of 5 calls/day reached for UTC date" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_http_429_quota_exceeded(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=False)
        with pytest.raises(AIQuotaExceededError) as exc:
            service.generate_json("prompt", {})
        assert "quota exceeded (HTTP 429)" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_http_403_invalid_key(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="invalid-key", require_persistent_quota=False)
        with pytest.raises(AIConfigurationError) as exc:
            service.generate_json("prompt", {})
        assert "API key is invalid" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_timeout_handling(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=False)
        with pytest.raises(AIServiceError) as exc:
            service.generate_json("prompt", {})
        assert "timed out" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_successful_profile_extraction_with_pii_scrubbing(self, mock_post):
        mock_candidate_payload = {
            "target_title": "Java Backend Engineer",
            "total_experience_years": 2.0,
            "experience_range_min": 0.0,
            "experience_range_max": 3.0,
            "hard_limit_years": 4.0,
            "languages": ["Java", "SQL"],
            "frameworks": ["Spring Boot", "Hibernate"],
            "apis_and_auth": ["REST", "Microservices"],
            "messaging_and_cache": ["Kafka", "Redis"],
            "databases": ["PostgreSQL"],
            "devops_and_tools": ["Docker", "Git"],
            "target_cities": ["bengaluru", "pune"],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(mock_candidate_payload)}]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=False)
        raw_resume = (
            "Abhishek Dwivedi\nEmail: secret@example.com\nPhone: +91-9999988888\n"
            "Java Backend Engineer with 2 years experience in Spring Boot, Kafka, PostgreSQL."
        )

        profile = service.extract_candidate_profile(raw_resume, candidate_name="Abhishek Dwivedi")

        assert profile.target_experience_years == 2.0
        assert "Java" in profile.languages
        assert "Spring Boot" in profile.frameworks
        assert "Kafka" in profile.messaging_and_cache

        call_args = mock_post.call_args
        request_body = call_args[1]["json"]
        prompt_text = request_body["contents"][0]["parts"][0]["text"]
        assert "secret@example.com" not in prompt_text
        assert "9999988888" not in prompt_text
        assert "[REDACTED_EMAIL]" in prompt_text
        assert "[REDACTED_PHONE]" in prompt_text

    @patch("src.ai_service.requests.post")
    def test_missing_experience_in_resume_is_not_fabricated(self, mock_post):
        """Proves that missing experience information leaves total_experience_years as None and never fabricates 2.0."""
        mock_payload = {
            "target_title": None,
            "total_experience_years": None,
            "languages": ["Python", "SQL"],
            "frameworks": ["FastAPI"],
            "databases": ["PostgreSQL"],
        }
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(mock_payload)}]}}]
        }
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="test-key", require_persistent_quota=False)
        profile = service.extract_candidate_profile("Resume text with no clear YOE")

        assert profile.total_experience_years is None
        assert profile.is_experience_known is False
        assert profile.experience_confirmed is False
        assert profile.languages == ["Python", "SQL"]
        assert profile.preferences_source == "editable_default"
        assert profile.experience_range_min == 0.0
        assert profile.experience_range_max == 3.0
        assert profile.hard_limit_years == 4.0

    @patch("src.ai_service.requests.post")
    def test_evaluate_job_match_with_unknown_experience(self, mock_post):
        """Verifies that when experience is unknown, the prompt explicitly flags uncertainty to the model."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "match_score": 75,
                "fit_verdict": "MEDIUM_FIT",
                "matching_evidence": ["Python"],
                "missing_requirements": ["3+ years required"],
                "experience_assessment": "Experience uncertain in candidate profile",
                "reasoning": "Skills match but YOE requires human review",
                "confidence_score": 0.8,
            })}]}}]},
        )

        service = GeminiAIService(api_key="test-key", require_persistent_quota=False)
        unknown_exp_profile = CandidateProfile(
            name="New Grad",
            total_experience_years=None,
            languages=["Python"],
            frameworks=["Django"],
        )

        service.evaluate_job_match(
            profile=unknown_exp_profile,
            job_title="Python Developer",
            company="Startup",
            location="Remote",
            description="Looking for Python dev",
        )

        call_args = mock_post.call_args
        prompt_text = call_args[1]["json"]["contents"][0]["parts"][0]["text"]
        assert "UNKNOWN / UNCONFIRMED" in prompt_text


    @patch("src.ai_service.requests.post")
    def test_evaluate_job_match(self, mock_post):
        match_payload = {
            "match_score": 88,
            "fit_verdict": "HIGH_FIT",
            "matching_evidence": ["Strong Java and Spring Boot experience", "Kafka message queues"],
            "missing_requirements": ["AWS Lambda not explicitly detailed"],
            "experience_assessment": "Job requires 2 years, candidate has 2 years (ideal fit)",
            "reasoning": "Excellent match across core technical requirements.",
            "confidence_score": 0.95,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(match_payload)}]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="test-fake-key", require_persistent_quota=False)
        eval_result = service.evaluate_job_match(
            profile=DEFAULT_CANDIDATE_PROFILE,
            job_title="Java Software Engineer",
            company="Acme Corp",
            location="Bengaluru",
            description="Looking for Java, Spring Boot, and Kafka backend engineer with 1-3 years experience.",
        )

        assert eval_result.match_score == 88
        assert eval_result.fit_verdict == "HIGH_FIT"
        assert len(eval_result.matching_evidence) == 2
        assert eval_result.confidence_score == 0.95
        assert eval_result.prompt_version == PROMPT_VERSION_V1


class TestHashingAndCacheUniqueness:
    def test_profile_hash_consistency_and_sensitivity(self):
        profile_a = DEFAULT_CANDIDATE_PROFILE
        hash_a1 = compute_profile_hash(profile_a)
        hash_a2 = compute_profile_hash(profile_a)
        assert hash_a1 == hash_a2
        assert len(hash_a1) == 64

        profile_b = profile_a.model_copy(update={"languages": ["Java", "Rust"]})
        hash_b = compute_profile_hash(profile_b)
        assert hash_a1 != hash_b

    def test_job_hash_consistency_and_sensitivity(self):
        hash1 = compute_job_hash(
            platform="linkedin",
            platform_job_id="12345",
            title="Java Backend Engineer",
            company="Tech Corp",
            description="Develop microservices in Spring Boot",
        )
        hash2 = compute_job_hash(
            platform="linkedin",
            platform_job_id="12345",
            title="Java Backend Engineer",
            company="Tech Corp",
            description="Develop microservices in Spring Boot",
        )
        assert hash1 == hash2

        hash_different_desc = compute_job_hash(
            platform="linkedin",
            platform_job_id="12345",
            title="Java Backend Engineer",
            company="Tech Corp",
            description="Develop microservices in Spring Boot and Kafka",
        )
        assert hash1 != hash_different_desc

        hash_different_platform = compute_job_hash(
            platform="indeed",
            platform_job_id="12345",
            title="Java Backend Engineer",
            company="Tech Corp",
            description="Develop microservices in Spring Boot",
        )
        assert hash1 != hash_different_platform
