import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import json

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
    FreshnessStatus,
)
from src.db_persistence import DatabasePersistence
from src.dashboard_service import DashboardQueryService


def make_full_job(
    platform: str = "freehire",
    platform_job_id: str = "fh-2001",
    title: str = "Java Backend Engineer",
    company: str = "Tech Innovations",
    location: str = "Bengaluru, Karnataka",
    url: str = "https://example.com/job/2001",
    description_snippet: str = "Building high-performance Spring microservices. 2 years experience.",
    source_experience_years_min: int = 2,
    work_mode: str = "hybrid",
) -> JobRecord:
    det_elig = DeterministicEligibility(
        decision=DeterministicDecision.ELIGIBLE,
        is_eligible=True,
        reasons=["Experience compatible: job requires 2 YOE", "Location matches target hub"],
        rejection_reasons=[],
        review_reasons=[],
        criteria_breakdown={
            "experience": "ELIGIBLE",
            "location_and_remote": "ELIGIBLE",
            "role_domain": "ELIGIBLE",
        },
    )

    relevance = RelevanceScore(
        total_score=85,
        experience_level=ExperienceLevel.JUNIOR,
        confidence_level=ConfidenceLevel.HIGH,
        confidence_score=0.9,
        eligibility=FitEligibility.HIGH_FIT,
        eligibility_reason="Strong match: verified description with Java, Spring Boot, REST API.",
        is_provisional=False,
        matched_skills=["java", "spring boot", "rest api"],
        score_breakdown={"title_match": 25, "skills_match": 35, "experience_fit": 15, "location_fit": 10},
        reasons=["Title matches Java Backend", "Skills match core stack"],
        unverified_components=[],
        available_fields=["title", "description", "experience_requirements"],
    )

    return JobRecord(
        platform=platform,
        platform_job_id=platform_job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        normalized_url=url,
        posted_date="2026-10-11",
        freshness_status=FreshnessStatus.CONFIRMED_FRESH,
        work_mode=work_mode,
        description_snippet=description_snippet,
        source_experience_years_min=source_experience_years_min,
        structured_requirements=[{"text": "2 years Java experience", "priority": "required"}],
        deterministic_eligibility=det_elig,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="freehire_search_jobs",
            evidence_notes="Live job card retrieved",
            application_url=url,
            last_checked_at=datetime.utcnow(),
        ),
        relevance=relevance,
        discovered_at=datetime.utcnow(),
    )


class TestSafePersistentStorage:
    """
    Phase 3C Test Suite: Safe persistent job storage for Job Bucket.
    Verifies that discovered jobs, structured requirements, and deterministic eligibility
    results are safely and idempotently persisted to PostgreSQL without touching candidate profile tables.
    """

    @pytest.fixture
    def db(self) -> DatabasePersistence:
        return DatabasePersistence(config={"dbname": "mock_db"})

    def test_successful_persistence_of_normalized_job_and_eligibility(self, db):
        """Verifies full persistence of job, relevance score, and deterministic eligibility metadata."""
        job = make_full_job()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = [101]

        with patch.object(db, "get_connection", return_value=mock_conn):
            job_id = db.save_job(job)

        assert job_id == 101
        assert mock_cur.execute.call_count == 2
        mock_conn.commit.assert_called_once()

        # Inspect parameters passed to jobs upsert
        job_sql, job_params = mock_cur.execute.call_args_list[0][0]
        assert "INSERT INTO jobs" in job_sql
        assert job_params[0] == "freehire"
        assert job_params[1] == "fh-2001"
        assert job_params[2] == "Java Backend Engineer"
        assert job_params[3] == "Tech Innovations"
        assert job_params[4] == "Bengaluru, Karnataka"
        assert job_params[5] == job.description_snippet
        assert job_params[12] == "JUNIOR"  # experience_level
        assert job_params[13] == 85  # relevance_score
        assert job_params[17] == "HIGH"  # confidence_level
        assert job_params[19] == "HIGH_FIT"  # eligibility

        # Inspect raw_metadata JSON payload
        raw_meta = job_params[26].adapted  # psycopg2 Json object wraps dict
        assert raw_meta["source_experience_years_min"] == 2
        assert raw_meta["work_mode"] == "hybrid"
        assert raw_meta["freshness_status"] == "CONFIRMED_FRESH"
        assert raw_meta["deterministic_eligibility"]["decision"] == "ELIGIBLE"
        assert raw_meta["deterministic_eligibility"]["is_eligible"] is True
        assert raw_meta["deterministic_eligibility"]["criteria_breakdown"]["experience"] == "ELIGIBLE"

        # Inspect validation history insert
        hist_sql, hist_params = mock_cur.execute.call_args_list[1][0]
        assert "INSERT INTO job_validation_history" in hist_sql
        assert hist_params[0] == 101  # job_id

    def test_idempotent_repeated_upserts(self, db):
        """Verifies repeated calls with the same job return the same ID and record history idempotently."""
        job = make_full_job()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = [101]

        with patch.object(db, "get_connection", return_value=mock_conn):
            id1 = db.save_job(job)
            id2 = db.save_job(job)

        assert id1 == 101
        assert id2 == 101
        assert mock_cur.execute.call_count == 4
        assert mock_conn.commit.call_count == 2

    def test_preservation_of_nonempty_metadata_in_upsert_sql(self):
        """Verifies UPSERT_JOB_SQL explicitly protects descriptions and metadata from being wiped."""
        sql = DatabasePersistence.UPSERT_JOB_SQL

        # 1. Nonempty description preservation
        assert "description = COALESCE(NULLIF(TRIM(EXCLUDED.description), ''), jobs.description)" in sql

        # 2. Nonempty title, company, location preservation
        assert "title = COALESCE(NULLIF(TRIM(EXCLUDED.title), ''), jobs.title)" in sql
        assert "company = COALESCE(NULLIF(TRIM(EXCLUDED.company), ''), jobs.company)" in sql
        assert "location = COALESCE(NULLIF(TRIM(EXCLUDED.location), ''), jobs.location)" in sql

        # 3. JSONB raw_metadata shallow merge preservation
        assert "raw_metadata = COALESCE(jobs.raw_metadata, '{}'::jsonb) || COALESCE(EXCLUDED.raw_metadata, '{}'::jsonb)" in sql

        # 4. Experience level downgrade protection (UNKNOWN does not wipe JUNIOR/MID/SENIOR)
        assert "WHEN EXCLUDED.experience_level::text != 'UNKNOWN'" in sql

        # 5. Score and breakdown protection
        assert "relevance_score = COALESCE(EXCLUDED.relevance_score, jobs.relevance_score)" in sql
        assert "eligibility = COALESCE(EXCLUDED.eligibility, jobs.eligibility)" in sql

    def test_different_source_ids_and_platforms_isolated(self, db):
        """Jobs with identical titles but different platforms/IDs must never be merged."""
        job_fh = make_full_job(platform="freehire", platform_job_id="fh-1", url="https://freehire.com/1")
        job_li = make_full_job(platform="linkedin", platform_job_id="li-1", url="https://linkedin.com/1")
        job_ind = make_full_job(platform="indeed", platform_job_id="ind-1", url="https://indeed.com/1")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.side_effect = [[201], [202], [203]]

        with patch.object(db, "get_connection", return_value=mock_conn):
            saved_ids = db.save_jobs([job_fh, job_li, job_ind])

        assert saved_ids == [201, 202, 203]
        # Verify 3 distinct job upserts and 3 history records
        assert mock_cur.execute.call_count == 6
        platforms = [mock_cur.execute.call_args_list[i][0][1][0] for i in (0, 2, 4)]
        assert platforms == ["freehire", "linkedin", "indeed"]

    def test_database_error_triggers_transaction_rollback(self, db):
        """If any operation fails during batch saving, the transaction must roll back without committing."""
        job1 = make_full_job(platform="freehire", platform_job_id="fh-1")
        job2 = make_full_job(platform="freehire", platform_job_id="fh-2")

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # First job succeeds, second job fails on DB constraint error
        mock_cur.fetchone.side_effect = [[301], RuntimeError("Unique constraint violation or connection reset")]

        with patch.object(db, "get_connection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="Unique constraint violation"):
                db.save_jobs([job1, job2])

        # Atomicity guarantee: commit was NEVER called
        mock_conn.commit.assert_not_called()

    def test_existing_dashboard_query_compatibility(self):
        """Verifies DashboardQueryService queries successfully read persisted columns."""
        dashboard = DashboardQueryService(config={"dbname": "mock_db"})

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # 1. Test get_overview_metrics
        mock_cur.fetchone.return_value = [5]  # total_jobs
        mock_cur.fetchall.side_effect = [
            [("freehire", 3), ("linkedin", 2)],  # by_platform
            [("ACTIVE", 5)],  # by_status
            [("HIGH_FIT", 4), ("LOW_FIT", 1)],  # by_eligibility
            [("HIGH", 4), ("MEDIUM", 1)],  # by_confidence
        ]

        with patch.object(dashboard, "get_connection", return_value=mock_conn):
            metrics = dashboard.get_overview_metrics()

        assert metrics["total_jobs"] == 5
        assert metrics["by_platform"] == {"freehire": 3, "linkedin": 2}
        assert metrics["by_eligibility"] == {"HIGH_FIT": 4, "LOW_FIT": 1}

        # 2. Test search_jobs
        mock_cur.fetchone.return_value = {"count": 1}
        mock_cur.fetchall.side_effect = [[
            {
                "id": 101,
                "platform": "freehire",
                "platform_job_id": "fh-2001",
                "title": "Java Backend Engineer",
                "company": "Tech Innovations",
                "location": "Bengaluru",
                "eligibility": "HIGH_FIT",
                "relevance_score": 85,
                "confidence_level": "HIGH",
            }
        ]]

        with patch.object(dashboard, "get_connection", return_value=mock_conn):
            rows, total = dashboard.search_jobs(eligibility="HIGH_FIT", min_score=60)

        assert total == 1
        assert len(rows) == 1
        assert rows[0]["eligibility"] == "HIGH_FIT"
        assert rows[0]["relevance_score"] == 85

    def test_persistence_never_modifies_resume_or_candidate_profile_tables(self):
        """Verifies DatabasePersistence queries touch only 'jobs' and 'job_validation_history'."""
        sql_upsert = DatabasePersistence.UPSERT_JOB_SQL
        sql_history = DatabasePersistence.INSERT_HISTORY_SQL

        # Must not mention resume or candidate tables
        forbidden_tables = ["candidate_profiles", "resume_versions", "ai_match_cache", "ai_daily_usage"]
        for table in forbidden_tables:
            assert table not in sql_upsert
            assert table not in sql_history
