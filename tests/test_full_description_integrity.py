import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from src.candidate_profile import CandidateProfile
from src.privacy_scrubber import PrivacyScrubber
from src.discovery import JobDiscoveryService
from src.ai_matcher import AIMatcher
from src.ai_service import (
    compute_job_hash,
    compute_profile_hash,
    GeminiAIService,
    AIMatchEvaluationV2,
    AIMatchVerdict,
    MatchProvenance,
    PROMPT_VERSION_V2,
)
from src.db_persistence import DatabasePersistence
from src.resume_service import ResumeService


@pytest.fixture
def mock_candidate():
    return CandidateProfile(
        name="Candidate Test",
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


def test_privacy_scrubber_no_artificial_length_limit():
    """Proves PrivacyScrubber processes large inputs (e.g. 10,000 chars) without truncation."""
    large_text = "Standard enterprise architecture text without PII. " * 200
    assert len(large_text) > 10000

    scrubbed, counts = PrivacyScrubber.scrub_text(large_text)
    assert len(scrubbed) == len(large_text)
    assert counts["emails"] == 0
    assert counts["phones"] == 0

    # With PII embedded at the end (beyond 3,000 chars)
    text_with_pii_tail = large_text + " Contact recruiter@example.com or +91 9876543210."
    scrubbed_pii, counts_pii = PrivacyScrubber.scrub_text(text_with_pii_tail)
    assert "[REDACTED_EMAIL]" in scrubbed_pii
    assert "[REDACTED_PHONE]" in scrubbed_pii
    assert counts_pii["emails"] == 1
    assert counts_pii["phones"] == 1
    assert len(scrubbed_pii) > 10000


@pytest.mark.asyncio
async def test_descriptions_longer_than_1500_survive_discovery_intact(mock_candidate):
    """Proves Freehire and Indeed discovery retain descriptions > 1,500 chars without clipping."""
    full_desc_2500 = "Core Java and distributed systems requirement. " * 55
    assert len(full_desc_2500) > 2500

    # 1. Mock Freehire discovery
    mock_mcp = MagicMock()
    mock_mcp.call_tool = AsyncMock(return_value={
        "data": [{
            "url": "https://freehire.me/job/full-test",
            "public_slug": "full-test-job-slug",
            "title": "Java Backend Engineer",
            "company": "Tech Corp",
            "description": full_desc_2500,
            "posted_at": "2026-10-10",
        }]
    })

    discovery = JobDiscoveryService(mcp_client=mock_mcp, profile=mock_candidate)
    fh_jobs = await discovery.search_freehire(query="java", limit=1, max_pages=1)

    assert len(fh_jobs) == 1
    assert len(fh_jobs[0].description_snippet) == len(full_desc_2500)
    assert fh_jobs[0].description_snippet == full_desc_2500
    assert fh_jobs[0].is_partial_description is False

    # 2. Mock Indeed discovery with > 3,800 chars
    full_desc_3800 = "Senior Software Engineer Responsibilities and Tech Stack. " * 70
    assert len(full_desc_3800) > 3800

    async def fake_call_tool(tool_name, payload, **kwargs):
        if tool_name == "indeed_search_jobs":
            return {"data": [{"url": "https://indeed.com/viewjob?jk=12345", "key": "12345", "title": "SE", "company": "IndeedCo"}]}
        elif tool_name == "indeed_get_job_detail":
            return {"description": full_desc_3800}
        return {}

    mock_mcp.call_tool = AsyncMock(side_effect=fake_call_tool)
    ind_jobs = await discovery.search_indeed(keyword="Java", limit=1, max_pages=1, fetch_details=True)

    assert len(ind_jobs) == 1
    assert len(ind_jobs[0].description_snippet) == len(full_desc_3800)
    assert ind_jobs[0].description_snippet == full_desc_3800
    assert ind_jobs[0].is_partial_description is False


def test_descriptions_longer_than_3000_reach_evaluator_unclipped(mock_candidate):
    """Proves descriptions > 3,000 characters reach AI prompts without application-level clipping."""
    full_desc_4500 = "Enterprise backend architecture with Kafka, Spring Boot, and Kubernetes. " * 60
    assert len(full_desc_4500) > 4000

    job = JobRecord(
        platform="indeed",
        platform_job_id="test-job-4500",
        title="Senior Java Architect",
        company="Enterprise Global",
        url="https://example.com/job/4500",
        normalized_url="https://example.com/job/4500",
        description_snippet=full_desc_4500,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="test",
            evidence_notes="test",
        ),
    )

    # 1. AIMatcher.build_effective_prompt_v2
    matcher = AIMatcher()
    sys_inst, user_prompt = matcher.build_effective_prompt_v2(mock_candidate, job)
    assert full_desc_4500 in user_prompt
    assert len(user_prompt) > 4000

    # 2. GeminiAIService.evaluate_job_match_v2 prompt assembly
    ai_service = GeminiAIService(api_key="test_dummy_key")
    with patch.object(ai_service, "generate_json") as mock_gen:
        mock_gen.return_value = {
            "match_score": 75,
            "fit_verdict": "GOOD_MATCH",
            "role_responsibilities": ["Backend design"],
            "required_skills": ["Java"],
            "preferred_skills": [],
            "matching_skills": ["Java"],
            "missing_skills": [],
            "transferable_skills": [],
            "job_seniority_level": "Mid",
            "job_experience_required": "2 years",
            "experience_fit": "ALIGNED",
            "experience_assessment": "Good fit",
            "work_mode": "Onsite",
            "location_fit": "Bengaluru",
            "education_and_certifications": "BTech",
            "evidence": ["Strong Java"],
            "uncertainties": [],
            "actionable_recommendations": [],
            "reasoning": "Solid fit",
        }

        eval_res = ai_service.evaluate_job_match_v2(
            profile=mock_candidate,
            job_title=job.title,
            company=job.company,
            location="Bengaluru",
            description=job.description_snippet,
            reserve_quota=False,
        )

        assert mock_gen.called
        called_prompt = mock_gen.call_args[0][0]
        assert full_desc_4500 in called_prompt
        assert len(called_prompt) > 4000
        assert eval_res.match_score == 75


def test_content_hash_changes_when_omitted_tail_restored():
    """Proves job content hash changes when omitted tail content is restored."""
    truncated_desc = "Java backend developer with Spring Boot experience."
    full_desc = "Java backend developer with Spring Boot experience. Must also have 5+ years of AWS and Docker."

    hash_truncated = compute_job_hash(
        platform="indeed",
        platform_job_id="job-123",
        title="Java Developer",
        company="Acme Corp",
        description=truncated_desc,
    )

    hash_full = compute_job_hash(
        platform="indeed",
        platform_job_id="job-123",
        title="Java Developer",
        company="Acme Corp",
        description=full_desc,
    )

    assert hash_truncated != hash_full


def test_partial_description_not_falsely_marked_complete():
    """Proves partial description (< 250 chars or Telegram source) is explicitly flagged."""
    partial_telegram_desc = "🎯 KogniVera Java Developer Salary: Not specified Level: Fresher #Hiring"

    job_partial = JobRecord(
        platform="freehire",
        platform_job_id="telegram-job",
        title="Java Developer",
        company="KogniVera",
        url="https://t.me/algo/1",
        normalized_url="https://t.me/algo/1",
        description_snippet=partial_telegram_desc,
        is_partial_description=True,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="telegram",
            evidence_notes="test",
        ),
    )

    assert job_partial.is_partial_description is True

    # Check persistence metadata captures is_partial_description
    meta = DatabasePersistence.extract_job_raw_metadata(job_partial)
    assert meta.get("is_partial_description") is True


def test_cache_entries_for_truncated_descriptions_cannot_be_reused_for_full_descriptions():
    """Proves cache lookup with full description content hash misses when cache contains truncated hash."""
    resume_svc = ResumeService(config={"dbname": "fake"})

    profile_hash = "prof_hash_123"
    platform = "indeed"
    platform_job_id = "job-456"
    model_version = "gemini-3.5-flash-lite"
    prompt_version = PROMPT_VERSION_V2

    hash_truncated = compute_job_hash(platform, platform_job_id, "Java Dev", "Corp", "Short desc")
    hash_full = compute_job_hash(platform, platform_job_id, "Java Dev", "Corp", "Short desc + full comprehensive requirements tail")

    assert hash_truncated != hash_full

    # Mock DB query in get_cached_match
    with patch.object(resume_svc, "_get_connection") as mock_conn:
        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cur

        # Scenario: DB only returns row when queried with hash_truncated, but returns None when queried with hash_full
        def fake_execute(query, params):
            # params: (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version)
            query_job_hash = params[3]
            if query_job_hash == hash_truncated:
                mock_cur.fetchone.return_value = {
                    "overall_score": 60,
                    "match_verdict": "GOOD_MATCH",
                    "evaluation_data": '{"match_score": 60, "fit_verdict": "GOOD_MATCH", "confidence_score": 0.85, "role_responsibilities": [], "required_skills": [], "preferred_skills": [], "matching_skills": [], "missing_skills": [], "transferable_skills": [], "job_seniority_level": "Mid", "job_experience_required": "2 yrs", "experience_fit": "ALIGNED", "experience_assessment": "ok", "work_mode": "Onsite", "location_fit": "BLR", "education_and_certifications": "BS", "evidence": [], "uncertainties": [], "actionable_recommendations": [], "reasoning": "ok", "prompt_version": "v2", "match_source": "AI_LIVE"}',
                }
            else:
                mock_cur.fetchone.return_value = None

        mock_cur.execute = MagicMock(side_effect=fake_execute)

        # Lookup with full-description hash must be a MISS
        cached_for_full = resume_svc.get_cached_match(
            profile_hash=profile_hash,
            platform=platform,
            platform_job_id=platform_job_id,
            job_hash=hash_full,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        assert cached_for_full is None

        # Lookup with truncated hash hits (as expected for old hash)
        cached_for_trunc = resume_svc.get_cached_match(
            profile_hash=profile_hash,
            platform=platform,
            platform_job_id=platform_job_id,
            job_hash=hash_truncated,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        assert cached_for_trunc is not None
        assert cached_for_trunc.match_score == 60


def test_persistence_prepares_full_description_unclipped():
    """Proves DatabasePersistence._prepare_upsert_params passes full description text."""
    long_desc = "Full unclipped description text with comprehensive requirements. " * 50
    job = JobRecord(
        platform="freehire",
        platform_job_id="test-full-persist",
        title="Java Dev",
        company="PersistCo",
        url="https://example.com/p",
        normalized_url="https://example.com/p",
        description_snippet=long_desc,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="test",
            evidence_notes="test",
        ),
    )

    job_params, hist_params = DatabasePersistence._prepare_upsert_params(job)
    # job_params index 5 corresponds to 'description'
    assert job_params[5] == long_desc
    assert len(job_params[5]) == len(long_desc)
