"""
Isolated Unit and Integration Tests for Job Bucket Phase 7B Multi-Source AI Preview Mode.

Tests all 10 required preview safety boundaries:
1. All three sources configured and invoked in preview mode.
2. Per-source (max 2) and total job limits (max 6) enforced.
3. Cross-source deduplication preserves distinct vacancies and discards duplicates.
4. Partial descriptions marked and skipped for AI evaluation.
5. Cache hits do not consume Gemini calls or quota.
6. Shared 4-RPM rate limiter respected under concurrent requests.
7. Daily quota guardrail remains active.
8. Provider errors fail closed (never silently fallback to deterministic).
9. Preview mode persistence boundary (zero DB writes, zero cache writes, zero file exports).
10. Source failures do not trigger unbounded retries or query expansion.
"""

import asyncio
import os
import tempfile
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    DeterministicDecision,
    DeterministicEligibility,
    RelevanceScore,
    FreshnessStatus,
)
from src.candidate_profile import CandidateProfile
from src.rate_limiter import SharedRollingRateLimiter
from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluationV2,
    AIMatchVerdict,
    ExperienceSeniorityFit,
    MatchProvenance,
    AIDailyQuotaExhaustedError,
    AIServiceError,
    CURRENT_PROMPT_VERSION,
)
from src.preview_service import (
    MultiSourcePreviewService,
    PreviewConfig,
    PreviewJobItem,
    PreviewReport,
    DeduplicationOutcome,
    EvaluationStatus,
    extract_practical_warnings,
)


@pytest.fixture
def mock_candidate():
    return CandidateProfile(
        name="Abhishek Preview",
        total_experience_years=2.0,
        experience_confirmed=True,
        target_title="Java Developer",
        hard_limit_years=4.0,
        languages=["Java", "SQL"],
        frameworks=["Spring Boot"],
        databases=["PostgreSQL"],
        messaging_and_cache=["Kafka"],
        devops_and_tools=["Docker"],
        inferred_capabilities=["REST API design"],
        target_cities=["Bengaluru"],
    )


def make_sample_job(
    platform: str,
    job_id: str,
    title: str = "Java Software Engineer",
    company: str = "TechCorp",
    location: str = "Bengaluru",
    description: str = "A" * 600,
    posted_date: str = "2026-10-12",
) -> JobRecord:
    """Helper to build sample JobRecord with valid metadata."""
    is_partial = len(description.strip()) < 250
    return JobRecord(
        platform=platform,
        platform_job_id=job_id,
        title=title,
        company=company,
        location=location,
        url=f"https://{platform}.example.com/jobs/{job_id}",
        normalized_url=f"https://{platform}.example.com/jobs/{job_id}",
        posted_date=posted_date,
        freshness_status=FreshnessStatus.CONFIRMED_FRESH,
        description_snippet=description,
        is_partial_description=is_partial,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source=f"{platform}_preview",
            evidence_notes="Verified in isolated preview mock",
        ),
    )


# ==============================================================================
# TEST 1: All three sources configured and invoked in preview mode
# ==============================================================================
@pytest.mark.asyncio
async def test_all_three_sources_configured_and_invoked(mock_candidate):
    """Verifies LinkedIn, Indeed, and Freehire are all configured and queried with 24h limits."""
    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[
        make_sample_job("freehire", "fh-1", "Java Developer", "Freehire Co")
    ])
    mock_discovery.search_linkedin = AsyncMock(return_value=[
        make_sample_job("linkedin", "li-1", "Java Backend Engineer", "LinkedIn Co")
    ])
    mock_discovery.search_indeed = AsyncMock(return_value=[
        make_sample_job("indeed", "ind-1", "Senior Java Engineer", "Indeed Co")
    ])

    config = PreviewConfig(
        sources=["linkedin", "indeed", "freehire"],
        location="Bengaluru",
        max_per_source=2,
        max_total=6,
        enable_ai_evaluation=False,  # Isolated discovery check
    )

    service = MultiSourcePreviewService(
        config=config,
        discovery_service=mock_discovery,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    assert set(report.sources_configured) == {"linkedin", "indeed", "freehire"}
    assert set(report.sources_attempted) == {"linkedin", "indeed", "freehire"}
    assert set(report.sources_succeeded) == {"linkedin", "indeed", "freehire"}
    assert len(report.sources_failed) == 0

    mock_discovery.search_linkedin.assert_awaited_once()
    mock_discovery.search_indeed.assert_awaited_once()
    mock_discovery.search_freehire.assert_awaited_once()

    # Verify LinkedIn and Indeed detail fetching was requested
    _, li_kwargs = mock_discovery.search_linkedin.call_args
    assert li_kwargs.get("fetch_details") is True
    assert li_kwargs.get("posted_within") == "Past day"

    _, ind_kwargs = mock_discovery.search_indeed.call_args
    assert ind_kwargs.get("fetch_details") is True
    assert ind_kwargs.get("hours_old") == 24

    _, fh_kwargs = mock_discovery.search_freehire.call_args
    assert fh_kwargs.get("posted_within_days") == 1


# ==============================================================================
# TEST 2: Per-source and total job limits are enforced
# ==============================================================================
@pytest.mark.asyncio
async def test_per_source_and_total_job_limits_enforced(mock_candidate):
    """Verifies max 2 unique jobs per source and max 6 total across all sources."""
    mock_discovery = MagicMock()
    # Each source returns 5 distinct jobs
    mock_discovery.search_freehire = AsyncMock(return_value=[
        make_sample_job("freehire", f"fh-{i}", f"Java Dev {i}", f"Company FH {i}") for i in range(5)
    ])
    mock_discovery.search_linkedin = AsyncMock(return_value=[
        make_sample_job("linkedin", f"li-{i}", f"Java Dev {i}", f"Company LI {i}") for i in range(5)
    ])
    mock_discovery.search_indeed = AsyncMock(return_value=[
        make_sample_job("indeed", f"ind-{i}", f"Java Dev {i}", f"Company IND {i}") for i in range(5)
    ])

    config = PreviewConfig(
        sources=["freehire", "linkedin", "indeed"],
        max_per_source=2,
        max_total=6,
        enable_ai_evaluation=False,
    )

    service = MultiSourcePreviewService(
        config=config,
        discovery_service=mock_discovery,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    # Exactly 2 accepted per source
    fh_accepted = [j for j in report.jobs if j.source == "freehire" and j.deduplication_outcome == "UNIQUE_ACCEPTED"]
    li_accepted = [j for j in report.jobs if j.source == "linkedin" and j.deduplication_outcome == "UNIQUE_ACCEPTED"]
    ind_accepted = [j for j in report.jobs if j.source == "indeed" and j.deduplication_outcome == "UNIQUE_ACCEPTED"]

    assert len(fh_accepted) == 2
    assert len(li_accepted) == 2
    assert len(ind_accepted) == 2
    assert report.total_unique_accepted == 6

    # Test early stopping when total limit is smaller (e.g. max_total=3)
    config_small = PreviewConfig(
        sources=["freehire", "linkedin", "indeed"],
        max_per_source=2,
        max_total=3,
        enable_ai_evaluation=False,
    )
    service_small = MultiSourcePreviewService(
        config=config_small,
        discovery_service=mock_discovery,
        candidate_profile=mock_candidate,
    )
    mock_discovery.search_indeed.reset_mock()

    report_small = await service_small.run_preview()
    assert report_small.total_unique_accepted == 3
    # Indeed should NOT have been queried because freehire(2) + linkedin(1) reached 3
    mock_discovery.search_indeed.assert_not_awaited()


# ==============================================================================
# TEST 3: Cross-source deduplication preserves distinct vacancies
# ==============================================================================
@pytest.mark.asyncio
async def test_cross_source_deduplication_preserves_distinct_vacancies(mock_candidate):
    """Verifies identical title+company across sources is deduplicated while distinct vacancies are kept."""
    mock_discovery = MagicMock()
    # Freehire returns Job A
    mock_discovery.search_freehire = AsyncMock(return_value=[
        make_sample_job("freehire", "fh-1", "Backend Developer", "Alpha Corp"),
        make_sample_job("freehire", "fh-2", "Java Architect", "Beta Corp"),
    ])
    # LinkedIn returns duplicate of Job A, plus distinct Job C
    mock_discovery.search_linkedin = AsyncMock(return_value=[
        make_sample_job("linkedin", "li-1", "Backend Developer", "Alpha Corp"),  # Duplicate!
        make_sample_job("linkedin", "li-2", "Fullstack Java", "Gamma Corp"),    # Distinct!
    ])
    # Indeed returns distinct Job D
    mock_discovery.search_indeed = AsyncMock(return_value=[
        make_sample_job("indeed", "ind-1", "Backend Developer", "Delta Corp"),  # Distinct company!
    ])

    config = PreviewConfig(
        sources=["freehire", "linkedin", "indeed"],
        max_per_source=2,
        max_total=6,
        enable_ai_evaluation=False,
    )

    service = MultiSourcePreviewService(
        config=config,
        discovery_service=mock_discovery,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    accepted = [j for j in report.jobs if j.deduplication_outcome == "UNIQUE_ACCEPTED"]
    cross_dup = [j for j in report.jobs if j.deduplication_outcome == "DUPLICATE_CROSS_SOURCE_SKIPPED"]

    # Exactly 1 cross-source duplicate detected
    assert len(cross_dup) == 1
    assert cross_dup[0].source == "linkedin"
    assert cross_dup[0].title == "Backend Developer"
    assert cross_dup[0].company == "Alpha Corp"

    # Distinct vacancies are all preserved
    accepted_keys = [(j.title, j.company) for j in accepted]
    assert ("Backend Developer", "Alpha Corp") in accepted_keys
    assert ("Java Architect", "Beta Corp") in accepted_keys
    assert ("Fullstack Java", "Gamma Corp") in accepted_keys
    assert ("Backend Developer", "Delta Corp") in accepted_keys
    assert len(accepted) == 4


# ==============================================================================
# TEST 4: Partial descriptions are marked and handled safely
# ==============================================================================
@pytest.mark.asyncio
async def test_partial_descriptions_marked_and_handled_safely(mock_candidate):
    """Verifies job with < 250 chars description is marked partial and AI evaluation is skipped."""
    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[
        make_sample_job("freehire", "fh-1", "Java Dev", "Snippet Corp", description="Short snippet only."),
        make_sample_job("freehire", "fh-2", "Java Engineer", "Full Corp", description="Complete description " * 30),
    ])
    mock_discovery.search_linkedin = AsyncMock(return_value=[])
    mock_discovery.search_indeed = AsyncMock(return_value=[])

    mock_ai = MagicMock(spec=GeminiAIService)
    mock_ai.model_name = "gemini-3.5-flash-lite"
    mock_ai.rate_limiter = None

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire"], enable_ai_evaluation=True),
        discovery_service=mock_discovery,
        ai_service=mock_ai,
        candidate_profile=mock_candidate,
    )

    # Patch evaluate_job_match_v2 for the complete job
    with patch.object(mock_ai, "evaluate_job_match_v2") as mock_eval:
        mock_eval.return_value = AIMatchEvaluationV2(
            match_score=88,
            fit_verdict=AIMatchVerdict.STRONG_MATCH,
            confidence_score=0.9,
            match_source=MatchProvenance.AI_LIVE,
        )

        report = await service.run_preview()

    assert report.total_partial_descriptions == 1

    partial_item = next(j for j in report.jobs if j.company == "Snippet Corp")
    assert partial_item.is_partial_description is True
    assert partial_item.evaluation_status == "SKIPPED_PARTIAL_DESCRIPTION"
    assert any("Incomplete job description" in w for w in partial_item.practical_warnings)

    complete_item = next(j for j in report.jobs if j.company == "Full Corp")
    assert complete_item.is_partial_description is False
    assert complete_item.evaluation_status == "LIVE"
    assert complete_item.ai_score == 88

    # Ensure AI evaluation was called strictly ONCE (for the complete job only)
    assert mock_eval.call_count == 1


# ==============================================================================
# TEST 5: Cache hits do not consume Gemini calls or quota
# ==============================================================================
@pytest.mark.asyncio
async def test_cache_hits_do_not_consume_gemini_calls(mock_candidate):
    """Verifies cached evaluation returns AI_CACHED with 0 Gemini calls and 0 quota reservations."""
    job = make_sample_job("freehire", "fh-1", "Java Lead", "Cache Corp", description="Full description " * 30)

    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[job])
    mock_discovery.search_linkedin = AsyncMock(return_value=[])
    mock_discovery.search_indeed = AsyncMock(return_value=[])

    cached_eval = AIMatchEvaluationV2(
        match_score=92,
        fit_verdict=AIMatchVerdict.STRONG_MATCH,
        confidence_score=0.95,
        match_source=MatchProvenance.AI_CACHED,
        matching_skills=["Java", "Spring Boot"],
        missing_skills=[],
        reasoning="Cached evaluation hit",
    )

    mock_resume_service = MagicMock()
    mock_resume_service.get_cached_match.return_value = cached_eval

    mock_ai = MagicMock(spec=GeminiAIService)
    mock_ai.model_name = "gemini-3.5-flash-lite"
    mock_ai.rate_limiter = None

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire"], enable_ai_evaluation=True),
        discovery_service=mock_discovery,
        resume_service=mock_resume_service,
        ai_service=mock_ai,
        candidate_profile=mock_candidate,
    )

    with patch.object(mock_ai, "evaluate_job_match_v2") as mock_eval:
        report = await service.run_preview()

    assert report.total_evaluated_cached == 1
    assert report.gemini_requests_made == 0
    mock_eval.assert_not_called()

    item = report.jobs[0]
    assert item.evaluation_status == "CACHED"
    assert item.evaluation_provenance == "AI_CACHED"
    assert item.ai_score == 92


# ==============================================================================
# TEST 6: Shared 4-RPM limit is respected under concurrent requests
# ==============================================================================
def test_shared_4_rpm_rate_limiter_under_concurrent_requests():
    """Verifies SharedRollingRateLimiter enforces <= 4 requests in rolling window under concurrency."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "shared_limiter_test.json")
        # 4 requests per 1.5 seconds window for test speed
        limiter = SharedRollingRateLimiter(
            max_requests=4,
            window_seconds=1.5,
            state_file=state_file,
        )

        results = []
        timestamps = []

        def worker(wid: int):
            t0 = time.time()
            limiter.acquire(timeout=5.0)
            t1 = time.time()
            results.append((wid, t1 - t0))
            timestamps.append(t1)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]

        t_start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        # The first 4 should acquire almost immediately (< 0.5s), 5th must wait for window to clear (> 1.0s)
        wait_times = [r[1] for r in results]
        delayed = [w for w in wait_times if w >= 1.0]
        assert len(delayed) >= 1, f"Expected at least 1 thread to wait for rolling window, got wait times: {wait_times}"


# ==============================================================================
# TEST 7: Daily quota guardrail remains active
# ==============================================================================
@pytest.mark.asyncio
async def test_daily_quota_guardrail_remains_active(mock_candidate):
    """Verifies AIDailyQuotaExhaustedError triggers deterministic fallback with DETERMINISTIC_FALLBACK provenance."""
    job = make_sample_job("freehire", "fh-1", "Java Dev", "Quota Corp", description="Full description " * 30)

    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[job])
    mock_discovery.search_linkedin = AsyncMock(return_value=[])
    mock_discovery.search_indeed = AsyncMock(return_value=[])

    mock_ai = MagicMock(spec=GeminiAIService)
    mock_ai.model_name = "gemini-3.5-flash-lite"
    mock_ai.rate_limiter = None
    mock_ai.require_persistent_quota = True

    # Quota check raises daily quota exhaustion
    mock_ai.reserve_persistent_quota.side_effect = AIDailyQuotaExhaustedError(
        "Persistent daily quota exhausted: 50/50 calls reached for UTC date."
    )

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire"], enable_ai_evaluation=True),
        discovery_service=mock_discovery,
        ai_service=mock_ai,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    assert report.total_unique_accepted == 1
    item = report.jobs[0]
    assert item.evaluation_status == "FALLBACK_QUOTA_EXHAUSTED"
    assert item.evaluation_provenance == "DETERMINISTIC_FALLBACK"
    assert item.ai_score is not None  # Deterministic score populated
    assert "quota exhausted" in (item.failure_reason or "").lower() or "quota exhausted" in (item.reasoning or "").lower()


# ==============================================================================
# TEST 8: Provider errors fail closed
# ==============================================================================
@pytest.mark.asyncio
async def test_provider_errors_fail_closed(mock_candidate):
    """Verifies provider/network errors fail closed without fallback to deterministic scoring."""
    job = make_sample_job("freehire", "fh-1", "Java Dev", "Error Corp", description="Full description " * 30)

    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[job])
    mock_discovery.search_linkedin = AsyncMock(return_value=[])
    mock_discovery.search_indeed = AsyncMock(return_value=[])

    mock_ai = MagicMock(spec=GeminiAIService)
    mock_ai.model_name = "gemini-3.5-flash-lite"
    mock_ai.rate_limiter = None
    mock_ai.require_persistent_quota = False
    mock_ai.evaluate_job_match_v2.side_effect = AIServiceError("Gemini 503 Service Unavailable")

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire"], enable_ai_evaluation=True),
        discovery_service=mock_discovery,
        ai_service=mock_ai,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    assert report.total_evaluations_failed == 1
    item = report.jobs[0]
    assert item.evaluation_status == "FAILED"
    assert item.evaluation_provenance is None  # Never marked as valid or fallback AI
    assert "Gemini 503" in item.failure_reason


# ==============================================================================
# TEST 9: Preview mode does not persist jobs, modify cache, or export files
# ==============================================================================
@pytest.mark.asyncio
async def test_preview_mode_persistence_boundary(mock_candidate):
    """Verifies zero DB writes, zero cache writes, zero file exports in preview mode."""
    job = make_sample_job("freehire", "fh-1", "Java Dev", "Clean Corp", description="Full description " * 30)

    mock_discovery = MagicMock()
    mock_discovery.search_freehire = AsyncMock(return_value=[job])
    mock_discovery.search_linkedin = AsyncMock(return_value=[])
    mock_discovery.search_indeed = AsyncMock(return_value=[])

    mock_resume_service = MagicMock()
    mock_resume_service.get_cached_match.return_value = None  # Cache miss

    mock_ai = MagicMock(spec=GeminiAIService)
    mock_ai.model_name = "gemini-3.5-flash-lite"
    mock_ai.rate_limiter = None
    mock_ai.require_persistent_quota = False
    mock_ai.evaluate_job_match_v2.return_value = AIMatchEvaluationV2(
        match_score=85,
        fit_verdict=AIMatchVerdict.GOOD_MATCH,
        confidence_score=0.88,
        match_source=MatchProvenance.AI_LIVE,
    )

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire"], enable_ai_evaluation=True),
        discovery_service=mock_discovery,
        resume_service=mock_resume_service,
        ai_service=mock_ai,
        candidate_profile=mock_candidate,
    )

    with patch("src.exporter.export_jobs_to_json") as mock_export, \
         patch("src.db_persistence.DatabasePersistence") as mock_db:

        report = await service.run_preview()

        # 1. Zero cache writes
        mock_resume_service.save_cached_match.assert_not_called()
        # 2. Zero file exports
        mock_export.assert_not_called()
        # 3. Zero database writes
        mock_db.assert_not_called()

    assert report.is_preview is True
    assert report.total_evaluated_live == 1


# ==============================================================================
# TEST 10: Failures do not trigger unbounded retries or query expansion
# ==============================================================================
@pytest.mark.asyncio
async def test_preview_failures_do_not_trigger_unbounded_retries(mock_candidate):
    """Verifies individual source failure is recorded and does not cause infinite retries or query expansion."""
    mock_discovery = MagicMock()
    # Freehire fails with exception
    mock_discovery.search_freehire = AsyncMock(side_effect=RuntimeError("Freehire API endpoint unreachable"))
    # LinkedIn and Indeed succeed normally
    mock_discovery.search_linkedin = AsyncMock(return_value=[
        make_sample_job("linkedin", "li-1", "Java Engineer", "LinkedIn Safe")
    ])
    mock_discovery.search_indeed = AsyncMock(return_value=[
        make_sample_job("indeed", "ind-1", "Java Developer", "Indeed Safe")
    ])

    service = MultiSourcePreviewService(
        config=PreviewConfig(sources=["freehire", "linkedin", "indeed"], enable_ai_evaluation=False),
        discovery_service=mock_discovery,
        candidate_profile=mock_candidate,
    )

    report = await service.run_preview()

    # Freehire failed safely
    assert "freehire" in report.sources_failed
    assert "Freehire API endpoint unreachable" in report.sources_failed["freehire"]
    # Freehire search was called exactly ONCE (no unbounded retries)
    assert mock_discovery.search_freehire.await_count == 1

    # Remaining sources succeeded
    assert "linkedin" in report.sources_succeeded
    assert "indeed" in report.sources_succeeded
    assert report.total_unique_accepted == 2


# ==============================================================================
# Practical warnings test
# ==============================================================================
def test_practical_warnings_extraction():
    """Verifies bond and experience warnings are generated correctly from posting text."""
    job = make_sample_job(
        "freehire", "fh-w",
        title="Java Dev",
        company="BondCorp",
        description="Must sign a service agreement of 2 years bond upon joining."
    )
    eval_v2 = AIMatchEvaluationV2(
        match_score=60,
        fit_verdict=AIMatchVerdict.STRETCH_MATCH,
        confidence_score=0.7,
        experience_fit=ExperienceSeniorityFit.POTENTIAL_STRETCH,
        job_experience_required="5+ years",
    )
    warnings = extract_practical_warnings(job, eval_v2)
    assert any("bond" in w.lower() for w in warnings)
    assert any("experience stretch" in w.lower() for w in warnings)
