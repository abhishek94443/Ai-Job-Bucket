import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.api_server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_pipeline_status_endpoint(client):
    res = client.get("/api/pipeline/status")
    assert res.status_code == 200
    data = res.json()
    assert "total_jobs" in data
    assert "ai_enriched_count" in data
    assert "staged_count" in data
    assert "in_flight_count" in data
    assert "quota_held_count" in data
    assert "window_metrics" in data
    assert "recommended_batch_size" in data["window_metrics"]


def test_enrich_batch_endpoint_success(client):
    with patch("src.ai_enrichment_worker.AIEnrichmentWorker.run_enrichment_batch") as mock_run:
        mock_run.return_value = {
            "claimed_count": 2,
            "enriched_count": 2,
            "held_count": 0,
            "failed_count": 0,
            "details": [{"job_id": 1, "status": "AI_ENRICHED", "score": 90}],
            "message": "Enriched 2 jobs.",
        }

        payload = {"batch_size": 2, "concurrency": 1}
        res = client.post("/api/pipeline/enrich-batch", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["claimed_count"] == 2
        assert data["enriched_count"] == 2
        assert data["held_count"] == 0


def test_enrich_single_job_endpoint(client):
    with patch("src.api_server.query_svc.get_job_by_id") as mock_get, \
         patch("src.ai_enrichment_worker.AIEnrichmentWorker.run_enrichment_batch") as mock_run:
        mock_get.return_value = {
            "id": 999,
            "title": "Principal Architect",
            "company": "Acme",
            "enrichment_status": "AI_ENRICHED",
            "ai_score": 95,
        }
        mock_run.return_value = {
            "claimed_count": 1,
            "enriched_count": 1,
            "held_count": 0,
            "failed_count": 0,
            "details": [{"job_id": 999, "status": "AI_ENRICHED", "score": 95}],
            "message": "Enriched 1 jobs.",
        }

        res = client.post("/api/jobs/999/enrich")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["job"]["id"] == 999
        assert data["job"]["ai_score"] == 95


