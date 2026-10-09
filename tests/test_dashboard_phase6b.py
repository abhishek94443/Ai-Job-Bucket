"""
Tests for Phase 6B: Dashboard Fixes and Verification.
Verifies:
1. SQL selection of dedicated AI columns (ai_score, ai_verdict, evaluation_source, ai_evaluation).
2. AI score sorting (ai_score DESC NULLS LAST).
3. Minimum score filtering using COALESCE(ai_score, relevance_score, 0).
4. Unknown posting date handling (transparent inclusion/exclusion without false recent labeling).
5. Protection against accidental discovery pipeline execution.
6. Correct rendering of all V2 AI evaluation fields and complete job descriptions > 1,500 chars.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.dashboard_service import DashboardQueryService, format_freshness
from src.ai_service import AIMatchEvaluationV2, AIMatchVerdict, ExperienceSeniorityFit, MatchProvenance


@pytest.fixture
def mock_db_connection():
    conn = MagicMock()
    cursor = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn, cursor


# ---------------------------------------------------------------------------
# 1. SQL selection of dedicated AI columns
# ---------------------------------------------------------------------------

def test_sql_selects_dedicated_ai_columns(mock_db_connection):
    """search_jobs selects ai_score, ai_verdict, evaluation_source, and ai_evaluation directly."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 1}
    cursor.fetchall.return_value = [
        {
            "id": 4,
            "platform": "freehire",
            "platform_job_id": "capco-001",
            "title": "Java Full Stack Developer",
            "company": "Capco",
            "location": "Bengaluru",
            "description": "Full description text...",
            "posted_date": "2026-10-10",
            "url": "https://capco.example.com/job/4",
            "relevance_score": 56,
            "ai_score": 68,
            "ai_verdict": "STRETCH_MATCH",
            "evaluation_source": "AI_LIVE",
            "ai_evaluation": {"fit_verdict": "STRETCH_MATCH", "match_score": 68},
            "raw_metadata": {},
        }
    ]

    with patch.object(service, "get_connection", return_value=conn):
        rows, count = service.search_jobs()

        assert count == 1
        assert len(rows) == 1
        row = rows[0]
        assert row["ai_score"] == 68
        assert row["ai_verdict"] == "STRETCH_MATCH"
        assert row["evaluation_source"] == "AI_LIVE"
        assert row["ai_evaluation"]["match_score"] == 68

        # Verify SELECT query includes dedicated AI columns
        select_sql = cursor.execute.call_args_list[1][0][0]
        assert "ai_score" in select_sql
        assert "ai_verdict" in select_sql
        assert "evaluation_source" in select_sql
        assert "ai_evaluation" in select_sql


# ---------------------------------------------------------------------------
# 2. AI Score Sorting
# ---------------------------------------------------------------------------

def test_ai_score_sorting_sql(mock_db_connection):
    """search_jobs with sort_by='ai_score' orders by ai_score DESC NULLS LAST."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 0}
    cursor.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(sort_by="ai_score", sort_order="DESC")
        select_sql_desc = cursor.execute.call_args_list[1][0][0]
        assert "ORDER BY ai_score DESC NULLS LAST, relevance_score DESC NULLS LAST, id ASC" in select_sql_desc

    cursor.reset_mock()
    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(sort_by="ai_score", sort_order="ASC")
        select_sql_asc = cursor.execute.call_args_list[1][0][0]
        assert "ORDER BY ai_score ASC NULLS LAST, relevance_score ASC NULLS LAST, id ASC" in select_sql_asc


# ---------------------------------------------------------------------------
# 3. Minimum Score Filtering
# ---------------------------------------------------------------------------

def test_min_score_filtering_uses_coalesce(mock_db_connection):
    """min_score filter uses COALESCE(ai_score, relevance_score, 0) to avoid ignoring AI evaluations."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 1}
    cursor.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(min_score=60)

        count_sql, count_params = cursor.execute.call_args_list[0][0]
        select_sql, select_params = cursor.execute.call_args_list[1][0]

        assert "COALESCE(ai_score, relevance_score, 0) >= %s" in count_sql
        assert "COALESCE(ai_score, relevance_score, 0) >= %s" in select_sql
        assert 60 in count_params
        assert 60 in select_params


def test_score_filtering_logic_ai_vs_deterministic():
    """Verify that COALESCE prioritizes AI score over deterministic relevance_score."""
    def passes_filter(ai_score, relevance_score, min_score):
        effective = ai_score if ai_score is not None else (relevance_score if relevance_score is not None else 0)
        return effective >= min_score

    # Job 1: Capco - AI score 68, deterministic score 56
    # With min_score=60, it MUST pass because AI score is 68 (deterministic 56 does NOT exclude it)
    assert passes_filter(ai_score=68, relevance_score=56, min_score=60) is True

    # Job 2: State Street - AI score 45, deterministic score 56
    # With min_score=50, it MUST fail because AI score is 45 (deterministic 56 does NOT artificially rescue it)
    assert passes_filter(ai_score=45, relevance_score=56, min_score=50) is False

    # Job 3: Unevaluated job - AI score None, deterministic score 70
    # With min_score=60, it falls back to 70 and passes
    assert passes_filter(ai_score=None, relevance_score=70, min_score=60) is True

    # Job 4: Unevaluated low score - AI score None, deterministic score 40
    # With min_score=60, it fails
    assert passes_filter(ai_score=None, relevance_score=40, min_score=60) is False


# ---------------------------------------------------------------------------
# 4. Unknown Posting Date Handling
# ---------------------------------------------------------------------------

def test_unfiltered_retains_unknown_posting_dates(mock_db_connection):
    """When posted_within is 'All time' or None, no date filter is applied."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 2}
    cursor.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(posted_within="All time")
        count_sql = cursor.execute.call_args_list[0][0][0]
        assert "posted_date" not in count_sql

    cursor.reset_mock()
    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(posted_within=None)
        count_sql = cursor.execute.call_args_list[0][0][0]
        assert "posted_date" not in count_sql


def test_freshness_filter_strictly_excludes_unknown_by_default(mock_db_connection):
    """When freshness filter is active and include_unknown_dates=False, unknown dates are excluded."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 1}
    cursor.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(posted_within="Last 3 days", include_unknown_dates=False)
        count_sql, count_params = cursor.execute.call_args_list[0][0]

        assert "posted_date IS NOT NULL" in count_sql
        assert "posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'" in count_sql
        assert 3 in count_params
        assert "OR (posted_date IS NULL" not in count_sql


def test_freshness_filter_explicitly_includes_unknown_when_requested(mock_db_connection):
    """When freshness filter is active and include_unknown_dates=True, unknown dates are included via OR."""
    conn, cursor = mock_db_connection
    service = DashboardQueryService()

    cursor.fetchone.return_value = {"count": 2}
    cursor.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=conn):
        service.search_jobs(posted_within="Last 7 days", include_unknown_dates=True)
        count_sql, count_params = cursor.execute.call_args_list[0][0]

        assert "OR (posted_date IS NULL OR NOT (posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'))" in count_sql
        assert 7 in count_params


def test_format_freshness_never_falsely_labels_unknown_dates():
    """format_freshness explicitly returns 'Posting date unknown' and ('N/A') for missing or unparseable dates."""
    assert format_freshness(None) == ("N/A", "Posting date unknown")
    assert format_freshness("") == ("N/A", "Posting date unknown")
    assert format_freshness("   ") == ("N/A", "Posting date unknown")
    assert format_freshness("Not specified") == ("Not specified", "Posting date unknown")
    assert format_freshness("2026-99-99") == ("2026-99-99", "Posting date unknown")


# ---------------------------------------------------------------------------
# 5. Protection against accidental discovery pipeline execution
# ---------------------------------------------------------------------------

def test_pipeline_execution_requires_explicit_confirmation():
    """Verify that run_pipeline is gated by operator confirmation and never executed by default."""
    with open("src/dashboard_app.py", "r", encoding="utf-8") as f:
        code = f.read()

    # Verify confirmation checkbox exists and is disabled by default
    assert 'confirm_run = st.checkbox' in code or 'confirm_run = ' in code
    assert 'disabled=not confirm_run' in code
    # Verify execution block requires BOTH run_now and confirm_run
    assert 'if run_now and confirm_run:' in code


# ---------------------------------------------------------------------------
# 6. Complete description handling > 1,500 characters
# ---------------------------------------------------------------------------

def test_complete_description_not_truncated():
    """Verify that the dashboard does not apply desc[:1500] truncation."""
    with open("src/dashboard_app.py", "r", encoding="utf-8") as f:
        code = f.read()

    # The hardcoded [:1500] must be completely removed
    assert "desc[:1500]" not in code
    # The complete description is placed inside an expander
    assert "Inspect Complete Description" in code
    assert "st.text(desc)" in code


# ---------------------------------------------------------------------------
# 7. V2 Structured AI evaluation fields coverage
# ---------------------------------------------------------------------------

def test_v2_ai_evaluation_fields_coverage():
    """Verify that dashboard_app extracts all AIMatchEvaluationV2 fields."""
    with open("src/dashboard_app.py", "r", encoding="utf-8") as f:
        code = f.read()

    # Required fields from AIMatchEvaluationV2 schema:
    required_schema_keys = [
        "fit_verdict",
        "match_score",
        "confidence_score",
        "matching_skills",
        "missing_skills",
        "transferable_skills",
        "evidence",
        "actionable_recommendations",
        "job_seniority_level",
        "experience_fit",
        "job_experience_required",
        "experience_assessment",
        "work_mode",
        "role_responsibilities",
        "education_and_certifications",
        "uncertainties",
        "reasoning",
    ]

    for key in required_schema_keys:
        assert f"ai_eval.get(\"{key}\")" in code or f"ai_eval.get('{key}')" in code, (
            f"Expected ai_eval extraction for key '{key}' in dashboard_app.py"
        )
