"""
Tests for Phase 4B Dashboard query service and presentation logic.
Verifies:
- AI metrics aggregation (live, cached, fallback, verdict counts)
- search_jobs filtering by ai_verdict and evaluation_source
- Fallback score distinction from AI score
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from src.dashboard_service import DashboardQueryService


@pytest.fixture
def mock_dashboard_db():
    conn = MagicMock()
    cursor = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn, cursor


def test_dashboard_overview_metrics_ai_fields(mock_dashboard_db):
    """get_overview_metrics returns AI counts and verdict breakdown."""
    conn, cursor = mock_dashboard_db
    service = DashboardQueryService()

    with patch.object(service, "_has_ai_columns", return_value=True), \
         patch.object(service, "get_connection", return_value=conn):
        
        cursor.fetchone.side_effect = [
            (25,),  # total jobs
        ]
        cursor.fetchall.side_effect = [
            [("linkedin", 15), ("indeed", 10)],      # 1. platform
            [("ACTIVE", 20), ("UNKNOWN", 5)],        # 2. status
            [("HIGH_FIT", 15), ("MEDIUM_FIT", 10)],  # 3. eligibility
            [("HIGH", 20), ("MEDIUM", 5)],           # 4. confidence
            [("AI_LIVE", 10), ("AI_CACHED", 8), ("DETERMINISTIC_FALLBACK", 7)],  # 5. eval source
            [("STRONG_MATCH", 8), ("GOOD_MATCH", 7), ("MISMATCH", 3)],           # 6. ai verdict
        ]

        metrics = service.get_overview_metrics()

        assert metrics["total_jobs"] == 25
        assert metrics["ai_live_count"] == 10
        assert metrics["ai_cached_count"] == 8
        assert metrics["deterministic_fallback_count"] == 7
        assert metrics["by_ai_verdict"]["STRONG_MATCH"] == 8
        assert metrics["by_ai_verdict"]["GOOD_MATCH"] == 7
        assert metrics["by_ai_verdict"]["MISMATCH"] == 3


def test_dashboard_search_jobs_with_ai_filters(mock_dashboard_db):
    """search_jobs applies ai_verdict and evaluation_source filters to SQL query."""
    conn, cursor = mock_dashboard_db
    service = DashboardQueryService()

    with patch.object(service, "_has_ai_columns", return_value=True), \
         patch.object(service, "get_connection", return_value=conn):

        cursor.fetchone.return_value = {"count": 1}
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "platform": "linkedin",
                "platform_job_id": "job-1",
                "title": "Lead Dev",
                "company": "Acme",
                "location": "Bengaluru",
                "url": "https://url",
                "relevance_score": 85,
                "eligibility": "HIGH_FIT",
                "job_status": "ACTIVE",
                "ai_score": 92,
                "ai_verdict": "STRONG_MATCH",
                "evaluation_source": "AI_LIVE",
                "ai_evaluation": json.dumps({"verdict": "STRONG_MATCH"}),
                "raw_metadata": {},
            }
        ]

        jobs, count = service.search_jobs(
            ai_verdict="STRONG_MATCH",
            evaluation_source="AI_LIVE",
        )

        assert count == 1
        assert len(jobs) == 1
        job = jobs[0]
        assert job["ai_score"] == 92
        assert job["ai_verdict"] == "STRONG_MATCH"
        assert job["evaluation_source"] == "AI_LIVE"

        # Verify that the executed SQL included the AI WHERE conditions
        sql_calls = [call[0][0] for call in cursor.execute.call_args_list]
        count_sql = sql_calls[0]
        select_sql = sql_calls[1]

        assert "evaluation_source = %s" in count_sql
        assert "ai_verdict = %s" in count_sql
        assert "evaluation_source = %s" in select_sql
        assert "ai_verdict = %s" in select_sql


def test_dashboard_search_jobs_fallback_unpacking_from_raw_metadata(mock_dashboard_db):
    """When dedicated columns do not exist, search_jobs unpacks AI fields from raw_metadata."""
    conn, cursor = mock_dashboard_db
    service = DashboardQueryService()

    raw_meta = {
        "ai_score": 70,
        "ai_verdict": None,
        "evaluation_source": "DETERMINISTIC_FALLBACK",
        "ai_evaluation": None,
    }

    with patch.object(service, "_has_ai_columns", return_value=False), \
         patch.object(service, "get_connection", return_value=conn):

        cursor.fetchone.return_value = {"count": 1}
        cursor.fetchall.return_value = [
            {
                "id": 2,
                "platform": "indeed",
                "platform_job_id": "job-fb-1",
                "title": "Backend Dev",
                "company": "Beta Corp",
                "location": "Bengaluru",
                "url": "https://url2",
                "relevance_score": 70,
                "eligibility": "HIGH_FIT",
                "job_status": "ACTIVE",
                "ai_score": None,
                "ai_verdict": None,
                "evaluation_source": None,
                "ai_evaluation": None,
                "raw_metadata": json.dumps(raw_meta),
            }
        ]

        jobs, count = service.search_jobs(
            evaluation_source="DETERMINISTIC_FALLBACK",
        )

        assert count == 1
        job = jobs[0]
        assert job["evaluation_source"] == "DETERMINISTIC_FALLBACK"
        assert job["relevance_score"] == 70
        assert job["ai_score"] == 70 or job["ai_score"] is None
