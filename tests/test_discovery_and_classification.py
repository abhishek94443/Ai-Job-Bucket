import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from src.normalizer import normalize_url, extract_platform_job_id
from src.deduplicator import Deduplicator
from src.classifier import ConservativeClassifier
from src.discovery import JobDiscoveryService
from src.exporter import export_jobs_to_json


def test_missing_or_malformed_fields_normalization():
    # Null or empty url
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""
    
    # URL without scheme
    norm = normalize_url("linkedin.com/jobs/view/12345?refId=99")
    assert norm == "https://linkedin.com/jobs/view/12345"

    # URL without platform ID match fallback
    job_id, platform = extract_platform_job_id("linkedin", "https://linkedin.com/invalid/path", fallback_id="fallback_99")
    assert job_id == "fallback_99"
    assert platform == "linkedin"


def test_conservative_status_and_application_classification():
    # Rule 6: Detail presence does NOT make job ACTIVE
    detail_payload = {
        "title": "Backend Dev",
        "description": "Some description",
        "url": "https://example.com/job/1"
    }
    evidence = ConservativeClassifier.classify(
        platform="indeed",
        job_url="https://example.com/job/1",
        detail_payload=detail_payload
    )
    assert evidence.job_status == JobStatus.UNKNOWN
    assert evidence.application_type == ApplicationType.UNKNOWN

    # Rule 7: Search filter alone does not declare EASY_APPLY
    evidence_search = ConservativeClassifier.classify(
        platform="indeed",
        job_url="https://example.com/job/1",
        detail_payload=None,
        initial_source="indeed_search_jobs_easy_apply_filter"
    )
    assert evidence_search.application_type == ApplicationType.UNKNOWN


def test_application_url_vs_listing_url():
    # When apply_url is an external ATS (Greenhouse)
    detail_payload = {
        "apply_url": "https://grnh.se/xyz123?utm_source=indeed",
        "description": "Apply at Greenhouse"
    }
    listing_url = "https://in.indeed.com/viewjob?jk=abc12345"
    evidence = ConservativeClassifier.classify(
        platform="indeed",
        job_url=listing_url,
        detail_payload=detail_payload
    )
    # Status remains UNKNOWN (requires active verification), but application_type is identified as EXTERNAL
    assert evidence.job_status == JobStatus.UNKNOWN
    assert evidence.application_type == ApplicationType.EXTERNAL
    assert evidence.application_url == "https://grnh.se/xyz123"
    assert "External ATS detected" in evidence.evidence_notes

    # When apply_url is identical to listing url
    detail_same = {
        "apply_url": listing_url,
        "description": "Native"
    }
    evidence_same = ConservativeClassifier.classify(
        platform="indeed",
        job_url=listing_url,
        detail_payload=detail_same
    )
    assert evidence_same.application_type == ApplicationType.UNKNOWN
    assert "Apply URL matches listing URL" in evidence_same.evidence_notes


def test_json_serialization_and_export(tmp_path: Path):
    job = JobRecord(
        platform="linkedin",
        platform_job_id="998877",
        title="Java Developer",
        company="Global Corp",
        url="https://linkedin.com/jobs/view/998877",
        normalized_url="https://linkedin.com/jobs/view/998877",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="linkedin_search_jobs",
            evidence_notes="Initial discovery"
        )
    )

    out_file = export_jobs_to_json([job], output_dir=tmp_path)
    assert out_file.exists()

    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["total_jobs"] == 1
    assert data["jobs"][0]["platform_job_id"] == "998877"
    assert data["jobs"][0]["validation"]["job_status"] == "UNKNOWN"
    assert data["jobs"][0]["validation"]["application_type"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_mcp_errors_and_empty_results():
    mock_mcp = AsyncMock()
    # Mock empty or error response
    mock_mcp.call_tool.return_value = None

    service = JobDiscoveryService(mcp_client=mock_mcp)
    li_jobs = await service.search_linkedin(limit=5)
    assert li_jobs == []

    # Mock tool exception
    mock_mcp.call_tool.side_effect = TimeoutError("Timeout")
    ind_jobs = await service.search_indeed(limit=5)
    assert ind_jobs == []
