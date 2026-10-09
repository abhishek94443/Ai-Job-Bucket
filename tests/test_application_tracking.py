import pytest
from datetime import datetime, timedelta
from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    UserApplicationStatus,
)
from src.db_persistence import DatabasePersistence
from src.dashboard_service import DashboardQueryService


def test_user_application_status_enum():
    """Validates UserApplicationStatus enum contains all expected lifecycle stages."""
    assert UserApplicationStatus.DISCOVERED == "DISCOVERED"
    assert UserApplicationStatus.SHORTLISTED == "SHORTLISTED"
    assert UserApplicationStatus.APPLIED == "APPLIED"
    assert UserApplicationStatus.INTERVIEWING == "INTERVIEWING"
    assert UserApplicationStatus.OFFER == "OFFER"
    assert UserApplicationStatus.REJECTED == "REJECTED"
    assert UserApplicationStatus.WITHDRAWN == "WITHDRAWN"
    assert UserApplicationStatus.STALE == "STALE"


def test_job_record_crm_defaults():
    """Validates JobRecord CRM fields have safe, non-breaking defaults."""
    job = JobRecord(
        platform="linkedin",
        platform_job_id="crm-test-1",
        title="Java Developer",
        company="Acme Corp",
        url="https://linkedin.com/jobs/view/crm-test-1",
        normalized_url="https://linkedin.com/jobs/view/crm-test-1",
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="test",
            evidence_notes="test",
        ),
    )
    assert job.user_status == UserApplicationStatus.DISCOVERED
    assert job.applied_at is None
    assert job.user_notes is None
    assert job.contact_person is None
    assert job.last_status_change is None


@pytest.mark.asyncio
async def test_db_persistence_crm_lifecycle():
    """Integration test verifying CRM tracking methods with Neon PostgreSQL."""
    db = DatabasePersistence()
    health, _ = db.check_health()
    if not health:
        pytest.skip("Neon PostgreSQL not reachable")

    # 1. Create a fresh test job
    test_id = f"crm-{int(datetime.now().timestamp())}"
    job = JobRecord(
        platform="freehire",
        platform_job_id=test_id,
        title="Senior Java Architect",
        company="Acme Global Inc",
        location="Bengaluru",
        url=f"https://freehire.me/jobs/{test_id}",
        normalized_url=f"https://freehire.me/jobs/{test_id}",
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="test_crm",
            evidence_notes="test_crm",
        ),
    )

    saved_ids = db.save_jobs([job])
    assert len(saved_ids) == 1
    job_db_id = saved_ids[0]

    # Verify initial state is DISCOVERED
    job_row = db.get_job_by_platform_id("freehire", test_id)
    assert job_row is not None
    assert job_row["user_status"] == "DISCOVERED"

    # 2. Update to SHORTLISTED with notes
    ok = db.update_job_user_status(
        job_id=job_db_id,
        user_status="SHORTLISTED",
        user_notes="High match for microservices; planning to apply.",
        contact_person="Sneha Sharma",
    )
    assert ok is True

    row_shortlisted = db.get_job_by_platform_id("freehire", test_id)
    assert row_shortlisted["user_status"] == "SHORTLISTED"
    assert row_shortlisted["user_notes"] == "High match for microservices; planning to apply."
    assert row_shortlisted["contact_person"] == "Sneha Sharma"

    # 3. Update to APPLIED (applied_at automatically populated)
    ok = db.update_job_user_status(
        job_id=job_db_id,
        user_status="APPLIED",
    )
    assert ok is True

    row_applied = db.get_job_by_platform_id("freehire", test_id)
    assert row_applied["user_status"] == "APPLIED"
    assert row_applied["applied_at"] is not None

    # 4. Verify Idempotent Preservation:
    # A subsequent save_jobs (simulating a re-discovery run) must NOT overwrite user_status back to DISCOVERED!
    db.save_jobs([job])  # job has default user_status=DISCOVERED
    row_after_rediscovery = db.get_job_by_platform_id("freehire", test_id)
    assert row_after_rediscovery["user_status"] == "APPLIED"  # Status preserved!

    # 5. Verify query service helpers
    service = DashboardQueryService()
    tracked = service.get_tracked_applications(status_filter=["APPLIED"])
    found_job = next((t for t in tracked if t["id"] == job_db_id), None)
    assert found_job is not None
    assert found_job["user_status"] == "APPLIED"

    # 6. Verify invalid status raises ValueError
    with pytest.raises(ValueError):
        db.update_job_user_status(job_id=job_db_id, user_status="INVALID_STAGE")
