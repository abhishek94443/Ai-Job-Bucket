import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from src.db_persistence import DatabasePersistence


@pytest.fixture
def sample_job():
    return JobRecord(
        platform="linkedin",
        platform_job_id="990011",
        title="Senior Java Developer",
        company="Enterprise Tech",
        location="Bengaluru, Karnataka",
        url="https://linkedin.com/jobs/view/990011",
        normalized_url="https://linkedin.com/jobs/view/990011",
        posted_date="2026-10-01",
        description_snippet="Design and maintain Java Spring microservices.",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="linkedin_search_jobs",
            evidence_notes="External link found",
            application_url="https://careers.enterprise.com/jobs/990011"
        )
    )


def test_save_job_insert_and_history(sample_job):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    # Return simulated serial ID 42 from RETURNING id
    mock_cur.fetchone.return_value = [42]

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=mock_conn):
        job_id = db.save_job(sample_job)

    assert job_id == 42
    # Verify execute was called twice: once for jobs upsert, once for validation history
    assert mock_cur.execute.call_count == 2
    
    # First query should be the jobs upsert
    first_call_sql = mock_cur.execute.call_args_list[0][0][0]
    assert "INSERT INTO jobs" in first_call_sql
    assert "ON CONFLICT (platform, platform_job_id)" in first_call_sql
    
    # Second query should be the validation history insert
    second_call_sql = mock_cur.execute.call_args_list[1][0][0]
    assert "INSERT INTO job_validation_history" in second_call_sql
    second_call_params = mock_cur.execute.call_args_list[1][0][1]
    assert second_call_params[0] == 42  # foreign key job_id


def test_duplicate_upsert_preserves_history(sample_job):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = [42]

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=mock_conn):
        # First save
        id1 = db.save_job(sample_job)
        # Update validation status and save again (simulating subsequent probe)
        sample_job.validation.job_status = JobStatus.EXPIRED
        sample_job.validation.evidence_notes = "DOM shows listing closed"
        id2 = db.save_job(sample_job)

    assert id1 == 42
    assert id2 == 42
    # Both executions must record history entries (total 4 execute calls)
    assert mock_cur.execute.call_count == 4
    hist_call_2_params = mock_cur.execute.call_args_list[3][0][1]
    assert hist_call_2_params[1] == "EXPIRED"
    assert "DOM shows listing closed" in hist_call_2_params[4]


def test_transaction_rollback_on_error(sample_job):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    # Simulate DB error during history insertion
    mock_cur.fetchone.return_value = [42]
    mock_cur.execute.side_effect = [None, RuntimeError("DB constraint failure")]

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=mock_conn):
        with pytest.raises(RuntimeError, match="DB constraint failure"):
            db.save_job(sample_job)

    # When an exception is raised inside the context manager, commit is never reached
    mock_conn.commit.assert_not_called()


def test_batch_save_jobs(sample_job):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [[101], [102]]

    db = DatabasePersistence(config={"dbname": "mock_db"})
    with patch.object(db, "get_connection", return_value=mock_conn):
        ids = db.save_jobs([sample_job, sample_job])

    assert ids == [101, 102]
    assert mock_conn.commit.call_count == 1
