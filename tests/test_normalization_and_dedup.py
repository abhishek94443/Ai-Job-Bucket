import pytest
from src.normalizer import normalize_url, extract_platform_job_id
from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from src.deduplicator import Deduplicator


def test_normalize_url():
    # Test tracking query params stripped, case normalized, fragments removed
    url1 = "https://www.LinkedIn.com/jobs/view/1234567890/?refId=abc&trackingId=def&utm_source=feed#details"
    expected1 = "https://www.linkedin.com/jobs/view/1234567890"
    assert normalize_url(url1) == expected1

    # Test preserving non-tracking query parameters in deterministic order
    url2 = "https://in.indeed.com/viewjob?jk=abc123xyz&vjk=filter&utm_campaign=blast&q=java"
    # jk is preserved in query, vjk and utm_campaign stripped, q is preserved
    expected2 = "https://in.indeed.com/viewjob?jk=abc123xyz&q=java"
    assert normalize_url(url2) == expected2


def test_extract_platform_job_id():
    # LinkedIn URL
    li_url = "https://www.linkedin.com/jobs/view/4028394812?refId=xyz"
    job_id, platform = extract_platform_job_id("linkedin", li_url)
    assert job_id == "4028394812"
    assert platform == "linkedin"

    # Indeed URL
    indeed_url = "https://in.indeed.com/viewjob?jk=789abc456&from=search"
    job_id, platform = extract_platform_job_id("indeed", indeed_url)
    assert job_id == "789abc456"
    assert platform == "indeed"

    # Fallback ID
    job_id, platform = extract_platform_job_id("custom", "https://example.com/job", fallback_id="999")
    assert job_id == "999"
    assert platform == "custom"


def test_deduplicator():
    dedup = Deduplicator()

    job1 = JobRecord(
        platform="linkedin",
        platform_job_id="1001",
        title="Java Backend Engineer",
        company="Tech Corp",
        url="https://linkedin.com/jobs/view/1001?trk=1",
        normalized_url="https://linkedin.com/jobs/view/1001",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="search_api",
            evidence_notes="Discovered via LinkedIn search"
        )
    )

    # Duplicate by platform and job_id (different tracking query)
    job2 = JobRecord(
        platform="linkedin",
        platform_job_id="1001",
        title="Java Backend Engineer",
        company="Tech Corp",
        url="https://linkedin.com/jobs/view/1001?trk=2",
        normalized_url="https://linkedin.com/jobs/view/1001",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="search_api",
            evidence_notes="Discovered via LinkedIn search page 2"
        )
    )

    # Different job
    job3 = JobRecord(
        platform="indeed",
        platform_job_id="ind99",
        title="Senior Java Developer",
        company="Cloud Systems",
        url="https://indeed.com/viewjob?jk=ind99",
        normalized_url="https://indeed.com/viewjob?jk=ind99",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="search_api",
            evidence_notes="Discovered via Indeed search"
        )
    )

    assert dedup.add(job1) is True
    assert dedup.is_duplicate(job2) is True
    assert dedup.add(job2) is False
    assert dedup.add(job3) is True

    # Test batch deduplicate
    dedup_batch = Deduplicator()
    unique_list = dedup_batch.deduplicate([job1, job2, job3])
    assert len(unique_list) == 2
    assert unique_list[0].platform_job_id == "1001"
    assert unique_list[1].platform_job_id == "ind99"
