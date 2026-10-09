"""
Unit tests for Phase8Pipeline logic in isolated mock mode.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.phase8_pipeline import Phase8Pipeline, Phase8Config, Phase8RunResult
from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    FreshnessStatus,
)
from src.candidate_profile import CandidateProfile
from src.ai_service import AIMatchEvaluationV2, AIMatchVerdict, MatchProvenance


@pytest.fixture
def mock_profile():
    return CandidateProfile(
        name="Abhishek Dwivedi",
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


def make_job(platform: str, job_id: str, title: str, company: str, desc: str = "A" * 600) -> JobRecord:
    is_partial = len(desc.strip()) < 250
    return JobRecord(
        platform=platform,
        platform_job_id=job_id,
        title=title,
        company=company,
        location="Bengaluru",
        url=f"https://{platform}.example.com/{job_id}",
        normalized_url=f"https://{platform}.example.com/{job_id}",
        description_snippet=desc,
        is_partial_description=is_partial,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source=platform,
            evidence_notes="test",
        ),
    )


@pytest.mark.asyncio
async def test_phase8_pipeline_mock_run(mock_profile):
    mock_discovery = MagicMock()
    # Freehire returns 3 jobs (2 complete, 1 partial)
    mock_discovery.search_freehire = AsyncMock(return_value=[
        make_job("freehire", "fh-1", "Java Dev 1", "Company 1", "Full description " * 30),
        make_job("freehire", "fh-2", "Java Dev 2", "Company 2", "Full description " * 30),
        make_job("freehire", "fh-3", "Java Dev 3", "Company 3", "Short"),  # Partial
    ])
    # LinkedIn returns 2 jobs (1 duplicate of fh-1, 1 distinct)
    mock_discovery.search_linkedin = AsyncMock(return_value=[
        make_job("linkedin", "li-1", "Java Dev 1", "Company 1", "Full description " * 30),  # Duplicate
        make_job("linkedin", "li-2", "Java Dev 4", "Company 4", "Full description " * 30),
    ])
    # Indeed returns 1 job
    mock_discovery.search_indeed = AsyncMock(return_value=[
        make_job("indeed", "ind-1", "Java Dev 5", "Company 5", "Full description " * 30),
    ])

    mock_resume_service = MagicMock()
    mock_resume_service.get_active_profile.return_value = mock_profile
    # Cache hit for fh-1, miss for others
    cached_eval = AIMatchEvaluationV2(
        match_score=85,
        fit_verdict=AIMatchVerdict.GOOD_MATCH,
        confidence_score=0.9,
        match_source=MatchProvenance.AI_CACHED,
    )
    mock_resume_service.get_cached_match.side_effect = lambda profile_hash, platform, platform_job_id, **kwargs: (
        cached_eval if platform_job_id == "fh-1" else None
    )

    mock_ai_service = MagicMock()
    mock_ai_service.model_name = "gemini-3.5-flash-lite"
    def mock_eval_batch(profile, jobs, **kwargs):
        return [
            {
                "job_id": j["job_id"],
                "match_score": 88,
                "fit_verdict": "STRONG_MATCH",
                "confidence_score": 0.92,
                "matching_skills": ["Java"],
                "missing_skills": [],
                "reasoning": "Strong match",
            }
            for j in jobs
        ]
    mock_ai_service.evaluate_jobs_batch.side_effect = mock_eval_batch
    mock_ai_service.evaluate_job_match_v2.return_value = AIMatchEvaluationV2(
        match_score=88,
        fit_verdict=AIMatchVerdict.STRONG_MATCH,
        confidence_score=0.92,
        match_source=MatchProvenance.AI_LIVE,
    )

    mock_db = MagicMock()
    mock_db.save_jobs.return_value = [10, 11, 12, 13, 14]

    config = Phase8Config(
        sources=["freehire", "linkedin", "indeed"],
        max_per_source=15,
        max_total_before_dedup=45,
        batch_size=15,
        daily_quota_limit=50,
    )

    pipeline = Phase8Pipeline(
        config=config,
        discovery_service=mock_discovery,
        resume_service=mock_resume_service,
        ai_service=mock_ai_service,
        db_persistence=mock_db,
    )

    with patch.object(pipeline, "_get_current_quota_usage", return_value=10), \
         patch("src.phase8_pipeline.DashboardQueryService.search_jobs", return_value=([
             {"platform": "freehire", "platform_job_id": "java-full-stack-developer-assistant-vice-president-state-street-k3ksw4nb"},
             {"platform": "freehire", "platform_job_id": "java-developer-turing-rhqasw35"},
             {"platform": "freehire", "platform_job_id": "java-full-stack-developer-capco-d62vm7ns"},
             {"platform": "indeed", "platform_job_id": "79d75184060cbdbf"},
             {"platform": "indeed", "platform_job_id": "81ff3f25ad111116"},
         ], 5)):

        res = await pipeline.execute_run()

    assert res.total_discovered_raw == 6
    assert res.total_duplicates_skipped == 1  # li-1 was duplicate
    assert res.total_unique_accepted == 5
    assert res.partial_descriptions_count == 1  # fh-3
    assert res.complete_descriptions_count == 4
    assert res.evaluations_cached == 1  # fh-1
    assert res.evaluations_live == 3    # fh-2, li-2, ind-1
    assert res.evaluations_skipped == 1 # fh-3
    assert res.existing_jobs_intact is True
    mock_db.save_jobs.assert_called_once()
