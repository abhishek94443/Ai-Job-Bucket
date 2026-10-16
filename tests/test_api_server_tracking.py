import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.api_server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_track_job_saved_status_maps_to_shortlisted(client):
    """
    Verifies that calling POST /api/jobs/{id}/track with status 'Saved' (frontend bookmark)
    correctly calls db_p.update_job_user_status with user_status='SHORTLISTED' and user_notes.
    """
    with patch("src.db_persistence.DatabasePersistence.update_job_user_status") as mock_update:
        mock_update.return_value = True

        payload = {"status": "Saved", "notes": "Candidate bookmarked from discovery"}
        res = client.post("/api/jobs/56/track", json=payload)

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["job_id"] == 56
        assert data["new_status"] == "SHORTLISTED"

        mock_update.assert_called_once_with(
            job_id=56,
            user_status="SHORTLISTED",
            user_notes="Candidate bookmarked from discovery",
        )


def test_track_job_applied_status(client):
    """
    Verifies that calling POST /api/jobs/{id}/track with status 'Applied'
    normalizes to 'APPLIED' and calls db_p.update_job_user_status.
    """
    with patch("src.db_persistence.DatabasePersistence.update_job_user_status") as mock_update:
        mock_update.return_value = True

        payload = {"status": "Applied", "notes": None}
        res = client.post("/api/jobs/288/track", json=payload)

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["job_id"] == 288
        assert data["new_status"] == "APPLIED"

        mock_update.assert_called_once_with(
            job_id=288,
            user_status="APPLIED",
            user_notes=None,
        )


def test_track_job_db_failure_returns_400(client):
    """
    Verifies that if db_p.update_job_user_status returns False,
    the endpoint returns HTTP 400 with expected error detail.
    """
    with patch("src.db_persistence.DatabasePersistence.update_job_user_status") as mock_update:
        mock_update.return_value = False

        payload = {"status": "Offer"}
        res = client.post("/api/jobs/99/track", json=payload)

        assert res.status_code == 400
        assert "Failed to update job tracking status" in res.json()["detail"]


def test_get_jobs_normalizes_saved_filter_to_shortlisted(client):
    """
    Verifies that calling GET /api/jobs?status=Saved translates
    status filter to 'SHORTLISTED' before calling query_svc.search_jobs.
    """
    with patch("src.api_server.query_svc.search_jobs") as mock_search:
        mock_search.return_value = ([], 0)

        res = client.get("/api/jobs?status=Saved")
        assert res.status_code == 200

        mock_search.assert_called_once()
        _, kwargs = mock_search.call_args
        assert kwargs["user_status"] == "SHORTLISTED"


def test_get_jobs_preserves_standard_status_filter(client):
    """
    Verifies that standard status filters like 'APPLIED' are preserved.
    """
    with patch("src.api_server.query_svc.search_jobs") as mock_search:
        mock_search.return_value = ([], 0)

        res = client.get("/api/jobs?status=Interviewing")
        assert res.status_code == 200

        mock_search.assert_called_once()
        _, kwargs = mock_search.call_args
        assert kwargs["user_status"] == "INTERVIEWING"
