"""
Tests for AIDatabaseConnectivityError and AIQuotaExceededError decoupling.
Ensures that AIDatabaseConnectivityError cannot trigger deterministic quota fallback.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.ai_service import (
    AIServiceError,
    AIQuotaExceededError,
    AIDailyQuotaExhaustedError,
    AIDatabaseConnectivityError,
    AIMatchEvaluationV2,
    MatchProvenance,
    EvaluationProvenance,
)
from src.ai_matcher import AIMatcher
from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE
from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
)


def make_test_job(job_id: str) -> JobRecord:
    return JobRecord(
        platform="linkedin",
        platform_job_id=job_id,
        title="Java Developer",
        company="TechCorp",
        location="Bengaluru",
        url=f"https://linkedin.com/jobs/view/{job_id}",
        normalized_url=f"https://linkedin.com/jobs/view/{job_id}",
        description_snippet="Core Java Spring Boot",
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="test",
            evidence_notes="test",
        ),
    )


def test_exception_hierarchy_decoupling():
    """Verify that AIDatabaseConnectivityError does NOT inherit from AIQuotaExceededError."""
    # AIDatabaseConnectivityError is a subclass of AIServiceError
    assert issubclass(AIDatabaseConnectivityError, AIServiceError)
    # AIDatabaseConnectivityError is NOT a subclass of AIQuotaExceededError
    assert not issubclass(AIDatabaseConnectivityError, AIQuotaExceededError)
    assert not issubclass(AIDatabaseConnectivityError, AIDailyQuotaExhaustedError)

    # AIDailyQuotaExhaustedError IS a subclass of AIQuotaExceededError
    assert issubclass(AIDailyQuotaExhaustedError, AIQuotaExceededError)
    assert issubclass(AIDailyQuotaExhaustedError, AIServiceError)


def test_catch_quota_error_does_not_catch_database_error():
    """Verify that try-except AIQuotaExceededError lets AIDatabaseConnectivityError through."""
    with pytest.raises(AIDatabaseConnectivityError):
        try:
            raise AIDatabaseConnectivityError("PostgreSQL pool timeout")
        except AIQuotaExceededError:
            pytest.fail("AIQuotaExceededError should not catch AIDatabaseConnectivityError")


def test_evaluate_job_quota_exhausted_triggers_deterministic_fallback():
    """Only AIDailyQuotaExhaustedError should trigger deterministic fallback."""
    job = make_test_job("job-quota-01")

    mock_ai_service = MagicMock()
    mock_ai_service.model_name = "gemini-2.5-flash"
    mock_ai_service.require_persistent_quota = True
    # Mock reserve_persistent_quota to raise AIDailyQuotaExhaustedError
    mock_ai_service.reserve_persistent_quota.side_effect = AIDailyQuotaExhaustedError("50 calls/day reached")

    matcher = AIMatcher()
    result = matcher.evaluate_job_ai_first(
        job=job,
        candidate=DEFAULT_CANDIDATE_PROFILE,
        ai_service=mock_ai_service,
        resume_service=None,
        db_conn=MagicMock(),
    )

    assert result.match_source == MatchProvenance.DETERMINISTIC_FALLBACK
    assert "Deterministic fallback evaluation due to AI quota exhaustion" in result.reasoning


def test_evaluate_job_db_connectivity_fails_closed_without_fallback():
    """AIDatabaseConnectivityError must NOT trigger deterministic fallback; it must fail closed."""
    job = make_test_job("job-db-err-01")

    mock_ai_service = MagicMock()
    mock_ai_service.model_name = "gemini-2.5-flash"
    mock_ai_service.require_persistent_quota = True
    # Mock reserve_persistent_quota to raise AIDatabaseConnectivityError
    mock_ai_service.reserve_persistent_quota.side_effect = AIDatabaseConnectivityError("Neon connection refused")

    matcher = AIMatcher()
    with pytest.raises(AIDatabaseConnectivityError):
        matcher.evaluate_job_ai_first(
            job=job,
            candidate=DEFAULT_CANDIDATE_PROFILE,
            ai_service=mock_ai_service,
            resume_service=None,
            db_conn=MagicMock(),
        )
