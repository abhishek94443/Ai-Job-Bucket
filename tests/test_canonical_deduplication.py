import pytest
from src.deduplicator import (
    clean_company_name,
    clean_job_title,
    clean_location_name,
    compute_canonical_job_key,
    compute_description_content_hash,
    Deduplicator,
)
from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType


def test_clean_company_name():
    # Indian corporate suffixes
    assert clean_company_name("Infosys Technologies Pvt Ltd") == "infosys"
    assert clean_company_name("Wipro Private Limited") == "wipro"
    assert clean_company_name("Tata Consultancy Services Ltd.") == "tata"

    # Global corporate suffixes & noise
    assert clean_company_name("Google LLC (USA)") == "google"
    assert clean_company_name("Amazon.com, Inc. [HQ]") == "amazon-com"
    assert clean_company_name("SAP Software Solutions GmbH") == "sap"
    assert clean_company_name("Maersk A/S") == "maersk"

    # Empty / whitespace fallback
    assert clean_company_name("") == "unknown-company"
    assert clean_company_name("   ") == "unknown-company"


def test_clean_job_title():
    # Abbreviations expanded
    assert clean_job_title("Sr. Java Dev") == "senior-java-developer"
    assert clean_job_title("Jr. SWE") == "junior-software-engineer"
    assert clean_job_title("SDE 2 - Backend") == "software-development-engineer-ii-backend"

    # Requisition numbers and hype stripped
    assert clean_job_title("Java Backend Developer [REQ-102938]") == "java-backend-developer"
    assert clean_job_title("Immediate Joiner: Java Spring Boot Engineer (Job ID 456)") == "java-spring-boot-engineer"


def test_clean_location_name():
    # City aliases
    assert clean_location_name("Bangalore, Karnataka, India") == "bengaluru"
    assert clean_location_name("Bengaluru Urban") == "bengaluru"
    assert clean_location_name("Bombay") == "mumbai"
    assert clean_location_name("Gurgaon") == "gurugram"

    # Remote detection
    assert clean_location_name("Remote - Pan India") == "remote"
    assert clean_location_name("Work from Anywhere") == "remote"

    # Fallback
    assert clean_location_name(None) == "flexible"


def test_compute_canonical_job_key_length_cap():
    # Normal key
    key1 = compute_canonical_job_key("Infosys Pvt Ltd", "Java Developer", "Bangalore")
    assert key1 == "infosys|java-developer|bengaluru"

    # Extremely long title length-capped with hash slice
    very_long_title = "Lead Principal Staff Distinguished Cloud Distributed Microservices Solutions Architect Specialist Consultant"
    long_key = compute_canonical_job_key("Enterprise Corp", very_long_title, "Bengaluru")
    assert len(long_key) <= 80
    assert "-" in long_key  # Hash slice separator


def test_cross_platform_deduplication():
    """Verifies that the same job posted on LinkedIn and Freehire is detected as duplicate."""
    dedup = Deduplicator(cross_platform=True)

    job_linkedin = JobRecord(
        platform="linkedin",
        platform_job_id="li-12345",
        title="Java Backend Developer",
        company="Infosys Technologies Pvt Ltd",
        location="Bangalore, Karnataka",
        url="https://linkedin.com/jobs/view/li-12345",
        normalized_url="https://linkedin.com/jobs/view/li-12345",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="linkedin_search",
            evidence_notes="LinkedIn listing",
        ),
    )

    job_freehire = JobRecord(
        platform="freehire",
        platform_job_id="fh-98765",
        title="Java Backend Dev",  # abbreviated title
        company="Infosys Limited",  # different legal suffix
        location="Bengaluru",
        url="https://freehire.me/jobs/fh-98765",
        normalized_url="https://freehire.me/jobs/fh-98765",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="freehire_search",
            evidence_notes="Freehire listing",
        ),
    )

    assert dedup.add(job_linkedin) is True
    # Should catch as duplicate cross-platform!
    assert dedup.is_duplicate(job_freehire) is True
    assert dedup.add(job_freehire) is False


def test_mass_posting_detection():
    """Verifies detection of mass-posted identical descriptions across different locations."""
    dedup = Deduplicator(detect_mass_postings=True)

    desc = "We are seeking a senior Java engineer to build event-driven microservices with Spring Boot, Kafka, and PostgreSQL. Must have 5+ years experience."

    job_bengaluru = JobRecord(
        platform="linkedin",
        platform_job_id="job-blr-1",
        title="Java Microservices Engineer",
        company="Global Tech Systems Pvt Ltd",
        location="Bengaluru",
        description_snippet=desc,
        url="https://linkedin.com/jobs/view/1",
        normalized_url="https://linkedin.com/jobs/view/1",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="linkedin",
            evidence_notes="Bangalore listing",
        ),
    )

    job_pune = JobRecord(
        platform="linkedin",
        platform_job_id="job-pune-2",
        title="Java Microservices Engineer",
        company="Global Tech Systems Inc",
        location="Pune",
        description_snippet=desc,  # Identical description!
        url="https://linkedin.com/jobs/view/2",
        normalized_url="https://linkedin.com/jobs/view/2",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.UNKNOWN,
            evidence_source="linkedin",
            evidence_notes="Pune listing",
        ),
    )

    assert dedup.add(job_bengaluru) is True
    is_mass, original = dedup.is_mass_posting(job_pune)
    assert is_mass is True
    assert original.location == "Bengaluru"
