"""
Tests for Phase 4B V2 persistence and cache separation.
Verifies:
- V2 cache serialization and retrieval
- Preservation of V1 cache rows without forcing V2 structure
- Cache anti-poisoning: refusal to cache DETERMINISTIC_FALLBACK or AI_ERROR
- JobRecord AI fields persistence (dedicated columns & fallback)
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from src.ai_service import (
    AIMatchEvaluation,
    AIMatchEvaluationV2,
    AIMatchVerdict,
    ExperienceSeniorityFit,
    MatchProvenance,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
)
from src.resume_service import ResumeService
from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
)
from src.db_persistence import DatabasePersistence


@pytest.fixture
def mock_db():
    conn = MagicMock()
    cursor = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchone.return_value = [101]
    return conn, cursor


def make_test_job(job_id: str, ai_score=None, ai_verdict=None, evaluation_source=None, ai_evaluation=None) -> JobRecord:
    return JobRecord(
        platform="linkedin",
        platform_job_id=job_id,
        title="Senior Java Architect",
        company="Global Fin",
        location="Bengaluru",
        url=f"https://linkedin.com/jobs/view/{job_id}",
        normalized_url=f"https://linkedin.com/jobs/view/{job_id}",
        description_snippet="Design and maintain Java Spring microservices.",
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="linkedin",
            evidence_notes="Direct posting",
        ),
        ai_score=ai_score,
        ai_verdict=ai_verdict,
        evaluation_source=evaluation_source,
        ai_evaluation=ai_evaluation,
    )


def test_cache_anti_poisoning_rejects_fallback(mock_db):
    """ai_match_cache MUST NEVER cache deterministic fallback results."""
    conn, cursor = mock_db
    service = ResumeService()
    service._get_connection = MagicMock(return_value=conn)

    fallback_eval = AIMatchEvaluationV2(
        match_score=70,
        fit_verdict=AIMatchVerdict.GOOD_MATCH,
        confidence_score=0.7,
        role_responsibilities=[],
        required_skills=[],
        preferred_skills=[],
        matching_skills=["Java"],
        missing_skills=[],
        transferable_skills=[],
        job_seniority_level="MID_LEVEL",
        job_experience_required="3 years",
        experience_fit=ExperienceSeniorityFit.ALIGNED,
        experience_assessment="Fallback assessment",
        work_mode="Remote",
        location_fit="Bengaluru",
        education_and_certifications="None",
        evidence=["Skills matched"],
        uncertainties=[],
        actionable_recommendations=[],
        reasoning="Fallback due to quota exhaustion",
        match_source=MatchProvenance.DETERMINISTIC_FALLBACK,
    )

    service.save_cached_match(
        profile_hash="hash-p1",
        platform="linkedin",
        platform_job_id="job-101",
        job_hash="hash-j1",
        model_version="gemini-2.5-flash",
        evaluation=fallback_eval,
        prompt_version=PROMPT_VERSION_V2,
    )

    cursor.execute.assert_not_called()


def test_cache_anti_poisoning_rejects_ai_error(mock_db):
    """ai_match_cache MUST NEVER cache AI errors."""
    conn, cursor = mock_db
    service = ResumeService()
    service._get_connection = MagicMock(return_value=conn)

    error_eval = AIMatchEvaluationV2(
        match_score=0,
        fit_verdict=AIMatchVerdict.MISMATCH,
        confidence_score=0.0,
        role_responsibilities=[],
        required_skills=[],
        preferred_skills=[],
        matching_skills=[],
        missing_skills=[],
        transferable_skills=[],
        job_seniority_level="UNKNOWN",
        job_experience_required="UNKNOWN",
        experience_fit=ExperienceSeniorityFit.UNCERTAIN_CANDIDATE_DATA,
        experience_assessment="Error",
        work_mode="Unknown",
        location_fit="Unknown",
        education_and_certifications="None",
        evidence=[],
        uncertainties=[],
        actionable_recommendations=[],
        reasoning="Provider error",
        match_source=MatchProvenance.AI_ERROR,
    )

    service.save_cached_match(
        profile_hash="hash-p1",
        platform="linkedin",
        platform_job_id="job-102",
        job_hash="hash-j2",
        model_version="gemini-2.5-flash",
        evaluation=error_eval,
        prompt_version=PROMPT_VERSION_V2,
    )

    cursor.execute.assert_not_called()


def test_cache_v2_saves_evaluation_data_json(mock_db):
    """Genuine AI evaluations in V2 format must serialize full evaluation_data JSON."""
    conn, cursor = mock_db
    service = ResumeService()
    service._get_connection = MagicMock(return_value=conn)

    v2_eval = AIMatchEvaluationV2(
        match_score=92,
        fit_verdict=AIMatchVerdict.STRONG_MATCH,
        confidence_score=0.9,
        role_responsibilities=["Backend design", "API development"],
        required_skills=["Java", "Spring Boot"],
        preferred_skills=["Docker"],
        matching_skills=["Java", "Spring Boot"],
        missing_skills=[],
        transferable_skills=["AWS ECS"],
        job_seniority_level="SENIOR",
        job_experience_required="3-5 years",
        experience_fit=ExperienceSeniorityFit.ALIGNED,
        experience_assessment="Strong alignment",
        work_mode="Hybrid",
        location_fit="Bengaluru",
        education_and_certifications="B.Tech",
        evidence=["Java", "Spring Boot matched"],
        uncertainties=[],
        actionable_recommendations=["Highlight Spring Boot in interview"],
        reasoning="Exceptional match on all core technologies",
        match_source=MatchProvenance.AI_LIVE,
    )

    service.save_cached_match(
        profile_hash="hash-p2",
        platform="linkedin",
        platform_job_id="job-201",
        job_hash="hash-j2",
        model_version="gemini-2.5-flash",
        evaluation=v2_eval,
        prompt_version=PROMPT_VERSION_V2,
    )

    assert cursor.execute.called
    query_str = cursor.execute.call_args[0][0]
    params = cursor.execute.call_args[0][1]

    assert "evaluation_data" in query_str
    assert params[0] == "hash-p2"
    assert params[1] == "linkedin"
    assert params[2] == "job-201"
    assert params[3] == "hash-j2"
    assert params[4] == "gemini-2.5-flash"
    assert params[5] == PROMPT_VERSION_V2
    assert params[6] == 92
    assert params[7] == "STRONG_MATCH"
    
    # Check evaluation_data JSON param
    eval_json = json.loads(params[13])
    assert eval_json["fit_verdict"] == "STRONG_MATCH"
    assert eval_json["match_score"] == 92
    assert eval_json["transferable_skills"] == ["AWS ECS"]
    assert eval_json["match_source"] == "AI_LIVE"


def test_get_cached_match_v2_deserialization(mock_db):
    """get_cached_match with prompt_version='v2' deserializes evaluation_data into V2 AIMatchEvaluationV2."""
    conn, cursor = mock_db
    service = ResumeService()
    service._get_connection = MagicMock(return_value=conn)

    v2_payload = {
        "match_score": 88,
        "fit_verdict": "STRONG_MATCH",
        "confidence_score": 0.85,
        "match_source": "AI_LIVE",
        "role_responsibilities": ["Backend services"],
        "required_skills": ["Java"],
        "preferred_skills": ["Docker"],
        "matching_skills": ["Java"],
        "missing_skills": ["Kafka"],
        "transferable_skills": ["RabbitMQ"],
        "job_seniority_level": "MID_LEVEL",
        "job_experience_required": "2-4 years",
        "experience_fit": "ALIGNED",
        "experience_assessment": "Good fit",
        "work_mode": "Remote",
        "location_fit": "Bengaluru",
        "education_and_certifications": "B.Tech",
        "evidence": ["Java confirmed"],
        "uncertainties": [],
        "actionable_recommendations": [],
        "reasoning": "Solid background",
        "prompt_version": PROMPT_VERSION_V2,
    }

    # RealDictCursor return dict
    cursor.fetchone.return_value = {
        "overall_score": 88,
        "match_verdict": "STRONG_MATCH",
        "matching_evidence": ["Java confirmed"],
        "missing_requirements": ["Kafka"],
        "experience_assessment": "Good fit",
        "explanation": "Solid background",
        "confidence_score": 0.85,
        "prompt_version": PROMPT_VERSION_V2,
        "evaluation_data": json.dumps(v2_payload),
    }

    cached_eval = service.get_cached_match(
        profile_hash="hash-p3",
        platform="linkedin",
        platform_job_id="job-301",
        job_hash="hash-j3",
        model_version="gemini-2.5-flash",
        prompt_version=PROMPT_VERSION_V2,
    )

    assert cached_eval is not None
    assert isinstance(cached_eval, AIMatchEvaluationV2)
    assert cached_eval.fit_verdict == AIMatchVerdict.STRONG_MATCH
    assert cached_eval.match_score == 88
    assert cached_eval.match_source == MatchProvenance.AI_CACHED
    assert cached_eval.matching_skills == ["Java"]
    assert cached_eval.missing_skills == ["Kafka"]
    assert cached_eval.transferable_skills == ["RabbitMQ"]


def test_get_cached_match_v1_preservation(mock_db):
    """get_cached_match with prompt_version='v1' returns V1 fields and does not fail when evaluation_data is NULL."""
    conn, cursor = mock_db
    service = ResumeService()
    service._get_connection = MagicMock(return_value=conn)

    # V1 row in Neon has NULL evaluation_data
    cursor.fetchone.return_value = {
        "overall_score": 85,
        "match_verdict": "HIGH_FIT",
        "matching_evidence": ["Java", "Spring Boot"],
        "missing_requirements": [],
        "experience_assessment": "Experienced",
        "explanation": "Great fit",
        "confidence_score": 0.8,
        "prompt_version": PROMPT_VERSION_V1,
        "evaluation_data": None,
    }

    cached_eval = service.get_cached_match(
        profile_hash="hash-pv1",
        platform="linkedin",
        platform_job_id="job-v1-01",
        job_hash="hash-jv1",
        model_version="gemini-2.5-flash",
        prompt_version=PROMPT_VERSION_V1,
    )

    assert cached_eval is not None
    assert isinstance(cached_eval, AIMatchEvaluation)
    assert cached_eval.fit_verdict == "HIGH_FIT" or getattr(cached_eval.fit_verdict, "value", None) == "HIGH_FIT"
    assert cached_eval.match_score == 85
    assert cached_eval.confidence_score == 0.8
    assert cached_eval.match_source == MatchProvenance.AI_CACHED


def test_save_job_ai_columns_persistence(mock_db):
    """save_job persists ai_score, ai_verdict, evaluation_source, and ai_evaluation."""
    conn, cursor = mock_db

    job = make_test_job(
        job_id="999",
        ai_score=95,
        ai_verdict="STRONG_MATCH",
        evaluation_source="AI_LIVE",
        ai_evaluation={"fit_verdict": "STRONG_MATCH", "match_score": 95, "transferable_skills": ["GCP"]},
    )

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=conn):
        job_id = db.save_job(job)

    assert job_id == 101
    assert cursor.execute.called

    # First call is jobs upsert
    first_call_sql = cursor.execute.call_args_list[0][0][0]
    first_call_params = cursor.execute.call_args_list[0][0][1]

    assert "ai_score" in first_call_sql
    assert "ai_verdict" in first_call_sql
    assert "evaluation_source" in first_call_sql
    assert "ai_evaluation" in first_call_sql

    assert first_call_params[-4] == 95
    assert first_call_params[-3] == "STRONG_MATCH"
    assert first_call_params[-2] == "AI_LIVE"


def test_save_job_graceful_fallback_to_raw_metadata(mock_db):
    """If dedicated AI columns do not exist yet in schema, save_job falls back to UPSERT_JOB_SQL."""
    conn, cursor = mock_db

    import psycopg2
    # Simulate undefined_column error on first query
    first_call = True
    def mock_execute(sql, params):
        nonlocal first_call
        if first_call:
            first_call = False
            raise psycopg2.ProgrammingError('column "ai_score" of relation "jobs" does not exist')
        return None

    cursor.execute.side_effect = mock_execute

    job = make_test_job(
        job_id="999",
        ai_score=95,
        ai_verdict="STRONG_MATCH",
        evaluation_source="AI_LIVE",
        ai_evaluation={"fit_verdict": "STRONG_MATCH", "match_score": 95},
    )

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=conn):
        job_id = db.save_job(job)

    assert job_id == 101
    # Rollback called, and then fallback upsert called
    assert conn.rollback.called
    assert cursor.execute.call_count == 3  # failed upsert + fallback upsert + validation history

    # Check the fallback upsert parameters: raw_metadata is param 26
    fallback_params = cursor.execute.call_args_list[1][0][1]
    raw_meta = fallback_params[26].adapted
    assert raw_meta["ai_score"] == 95
    assert raw_meta["ai_verdict"] == "STRONG_MATCH"
    assert raw_meta["evaluation_source"] == "AI_LIVE"
    assert raw_meta["ai_evaluation"]["match_score"] == 95


def test_save_jobs_batch_fallback_preserves_earlier_jobs(mock_db):
    """Verifies that in a batch of jobs on an unmigrated schema, fallback does not wipe earlier jobs."""
    conn, cursor = mock_db

    import psycopg2
    first_call = True
    def mock_execute(sql, params=None):
        nonlocal first_call
        if first_call and "ai_score" in sql:
            first_call = False
            raise psycopg2.ProgrammingError('column "ai_score" of relation "jobs" does not exist')
        return None

    cursor.execute.side_effect = mock_execute
    cursor.fetchone.side_effect = [[101], [102], [103]]

    jobs = [
        make_test_job("batch-1", ai_score=85, ai_verdict="GOOD_MATCH", evaluation_source="AI_LIVE"),
        make_test_job("batch-2", ai_score=90, ai_verdict="STRONG_MATCH", evaluation_source="AI_LIVE"),
        make_test_job("batch-3", ai_score=75, ai_verdict="GOOD_MATCH", evaluation_source="AI_LIVE"),
    ]

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=conn):
        saved_ids = db.save_jobs(jobs)

    assert saved_ids == [101, 102, 103]
    # Rollback must be called AT MOST once (for the very first job before any jobs were written),
    # never repeatedly inside the loop which would wipe earlier jobs!
    assert conn.rollback.call_count == 1
    assert conn.commit.call_count == 1
