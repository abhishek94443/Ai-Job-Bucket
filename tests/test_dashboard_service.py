import pytest
from unittest.mock import MagicMock, patch

from src.dashboard_service import DashboardQueryService


@pytest.fixture
def mock_db_rows():
    return [
        {
            "id": 1,
            "platform": "linkedin",
            "platform_job_id": "li-001",
            "title": "Backend Developer (Java)",
            "company": "SAP",
            "location": "Bengaluru, Karnataka",
            "description": "Enterprise Java systems",
            "posted_date": "2026-10-06",
            "url": "https://linkedin.com/jobs/view/li-001",
            "normalized_url": "https://linkedin.com/jobs/view/li-001",
            "application_url": None,
            "job_status": "UNKNOWN",
            "application_type": "UNKNOWN",
            "experience_level": "MID_LEVEL",
            "relevance_score": 56,
            "confidence_level": "MEDIUM",
            "confidence_score": 0.6,
            "eligibility": "PROVISIONAL_NEEDS_REVIEW",
            "eligibility_reason": "Description missing/unretrieved",
            "is_provisional": True,
            "score_breakdown": {"title_match": 25},
            "score_reasons": ["Title matches"],
            "matched_skills": ["java"],
            "unverified_components": ["skills_description"],
            "available_fields": ["title", "company"],
            "evidence_source": "linkedin_search_jobs",
            "evidence_notes": "Card preview",
            "last_checked_at": "2026-10-10 10:00:00",
            "discovered_at": "2026-10-10 10:00:00"
        },
        {
            "id": 2,
            "platform": "indeed",
            "platform_job_id": "ind-002",
            "title": "Java Springboot Developer",
            "company": "Infosys",
            "location": "Bengaluru",
            "description": None,  # Test NULL description handling
            "posted_date": "2026-10-08",
            "url": "https://indeed.com/viewjob?jk=ind-002",
            "normalized_url": "https://indeed.com/viewjob?jk=ind-002",
            "application_url": "https://careers.infosys.com/job/2",
            "job_status": "UNKNOWN",
            "application_type": "EXTERNAL",
            "experience_level": "UNKNOWN",
            "relevance_score": 65,
            "confidence_level": "MEDIUM",
            "confidence_score": 0.6,
            "eligibility": "MEDIUM_FIT",
            "eligibility_reason": "Partial framework coverage",
            "is_provisional": False,
            "score_breakdown": None,  # Test NULL breakdown
            "score_reasons": None,
            "matched_skills": None,
            "unverified_components": None,
            "available_fields": None,
            "evidence_source": "indeed_search_jobs",
            "evidence_notes": "API detail response",
            "last_checked_at": "2026-10-10 10:05:00",
            "discovered_at": "2026-10-10 10:05:00"
        }
    ]


def test_dashboard_overview_metrics():
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock responses for COUNT queries
    mock_cur.fetchone.return_value = [12]  # Total
    mock_cur.fetchall.side_effect = [
        [("linkedin", 6), ("indeed", 6)],      # By platform
        [("UNKNOWN", 12)],                     # By status
        [("HIGH_FIT", 2), ("MEDIUM_FIT", 5)],  # By eligibility
        [("MEDIUM", 7), ("LOW", 5)]            # By confidence
    ]

    with patch.object(service, "get_connection", return_value=mock_conn):
        metrics = service.get_overview_metrics()

    assert metrics["total_jobs"] == 12
    assert metrics["by_platform"]["linkedin"] == 6
    assert metrics["by_status"]["UNKNOWN"] == 12
    assert metrics["by_eligibility"]["HIGH_FIT"] == 2
    assert metrics["by_confidence"]["MEDIUM"] == 7


def test_dashboard_search_and_filtering(mock_db_rows):
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Return total count = 1, then rows
    mock_cur.fetchone.return_value = {"count": 1}
    mock_cur.fetchall.return_value = [mock_db_rows[0]]

    with patch.object(service, "get_connection", return_value=mock_conn):
        rows, total = service.search_jobs(
            search_term="SAP",
            platform="linkedin",
            eligibility="PROVISIONAL_NEEDS_REVIEW",
            listing_status="UNKNOWN",
            confidence_level="MEDIUM",
            experience_level="MID_LEVEL",
            min_score=50,
            sort_by="score",
            sort_order="DESC",
            page=1,
            page_size=10
        )

    assert total == 1
    assert len(rows) == 1
    assert rows[0]["company"] == "SAP"

    # Verify query parameters were passed safely to avoid SQL injection
    assert mock_cur.execute.call_count == 2
    count_call_args = mock_cur.execute.call_args_list[0][0]
    select_call_args = mock_cur.execute.call_args_list[1][0]

    # Verify parameterized SQL placeholders used
    assert "%s" in count_call_args[0]
    assert "%s" in select_call_args[0]
    params = select_call_args[1]
    assert "%sap%" in params
    assert "linkedin" in params
    assert 50 in params
    assert 10 in params  # page_size
    assert 0 in params   # offset


def test_dashboard_pagination_offset(mock_db_rows):
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchone.return_value = {"count": 25}
    mock_cur.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=mock_conn):
        # Request Page 3 with page size 10 -> offset should be 20
        _, _ = service.search_jobs(page=3, page_size=10)

    select_sql, params = mock_cur.execute.call_args_list[1][0]
    assert "LIMIT %s OFFSET %s" in select_sql
    assert params[-2] == 10  # page_size
    assert params[-1] == 20  # offset


def test_dashboard_handles_null_and_missing_fields(mock_db_rows):
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchone.return_value = {"count": 2}
    mock_cur.fetchall.return_value = mock_db_rows

    with patch.object(service, "get_connection", return_value=mock_conn):
        rows, total = service.search_jobs()

    assert total == 2
    # Verify row with NULL fields is parsed safely without throwing
    row2 = rows[1]
    assert row2["description"] is None
    assert row2["score_breakdown"] is None
    assert row2["relevance_score"] == 65


def test_dashboard_gracefully_handles_database_unreachable():
    service = DashboardQueryService()
    with patch.object(service, "get_connection", side_effect=Exception("Connection refused")):
        healthy, msg = service.check_health()
        assert healthy is False
        assert "Connection refused" in msg


def test_format_freshness_labels():
    from datetime import datetime
    from src.dashboard_service import format_freshness

    ref = datetime(2026, 10, 10, 12, 0, 0)

    # Today
    assert format_freshness("2026-10-10", ref) == ("2026-10-10", "Today")
    # 1 day ago
    assert format_freshness("2026-10-09", ref) == ("2026-10-09", "1 day ago")
    # Multiple days ago
    assert format_freshness("2026-10-03", ref) == ("2026-10-03", "7 days ago")
    assert format_freshness("2026-09-10", ref) == ("2026-09-10", "30 days ago")
    
    # Missing / None / Empty / Whitespace
    assert format_freshness(None, ref) == ("N/A", "Posting date unknown")
    assert format_freshness("", ref) == ("N/A", "Posting date unknown")
    assert format_freshness("   ", ref) == ("N/A", "Posting date unknown")
    
    # Unparseable strings
    assert format_freshness("invalid-date", ref) == ("invalid-date", "Posting date unknown")
    assert format_freshness("2026-99-99", ref) == ("2026-99-99", "Posting date unknown")


def test_dashboard_search_posted_within_filter(mock_db_rows):
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchone.return_value = {"count": 1}
    mock_cur.fetchall.return_value = [mock_db_rows[0]]

    with patch.object(service, "get_connection", return_value=mock_conn):
        rows, total = service.search_jobs(posted_within="Last 7 days")

    assert total == 1
    # Verify execute was called with safe parameterized query
    select_sql, params = mock_cur.execute.call_args_list[1][0]
    assert "posted_date IS NOT NULL" in select_sql
    assert "CURRENT_DATE - (%s * INTERVAL '1 day')" in select_sql
    # Parameter for 7 days
    assert 7 in params


def test_dashboard_search_all_time_does_not_filter_posted_date():
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchone.return_value = {"count": 2}
    mock_cur.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=mock_conn):
        rows, total = service.search_jobs(posted_within="All time")

    select_sql, params = mock_cur.execute.call_args_list[1][0]
    assert "INTERVAL '1 day'" not in select_sql


def test_dashboard_search_sort_by_newest():
    service = DashboardQueryService()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_cur.fetchone.return_value = {"count": 2}
    mock_cur.fetchall.return_value = []

    with patch.object(service, "get_connection", return_value=mock_conn):
        rows, total = service.search_jobs(sort_by="newest", sort_order="DESC")

    select_sql, params = mock_cur.execute.call_args_list[1][0]
    assert "ORDER BY (CASE WHEN posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'" in select_sql
    assert "DESC NULLS LAST, id ASC" in select_sql

