"""
Migration 005 Safety and Schema Conformance Tests (Offline).
Verifies that migration 005 is non-destructive, idempotent, and conforms to domain constraints.
"""
from pathlib import Path
import re
import pytest

MIGRATION_PATH = Path(__file__).resolve().parent.parent / "migrations" / "005_v2_ai_evaluation_and_provenance.sql"


def test_migration_005_file_exists():
    assert MIGRATION_PATH.exists(), f"Migration file not found at {MIGRATION_PATH}"


def test_migration_005_contents_and_safety():
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    assert len(content.strip()) > 0

    # Ensure transaction wrap
    assert "BEGIN;" in content
    assert "COMMIT;" in content

    # Ensure no destructive commands
    assert "DROP TABLE" not in content.upper()
    assert "TRUNCATE" not in content.upper()

    # Check for column additions on jobs
    assert "ADD COLUMN IF NOT EXISTS ai_score" in content
    assert "ADD COLUMN IF NOT EXISTS ai_verdict" in content
    assert "ADD COLUMN IF NOT EXISTS evaluation_source" in content
    assert "ADD COLUMN IF NOT EXISTS ai_evaluation" in content

    # Check for column addition on ai_match_cache
    assert "ADD COLUMN IF NOT EXISTS evaluation_data" in content

    # Check for check constraints
    assert "jobs_ai_score_check" in content
    assert "jobs_ai_verdict_check" in content
    assert "jobs_evaluation_source_check" in content

    # Check allowed evaluation_source values
    expected_sources = {"AI_LIVE", "AI_CACHED", "DETERMINISTIC_FALLBACK"}
    for src in expected_sources:
        assert src in content, f"Missing expected source '{src}' in check constraint"

    # Check allowed ai_verdict values
    expected_verdicts = {"STRONG_MATCH", "GOOD_MATCH", "STRETCH_MATCH", "MISMATCH"}
    for v in expected_verdicts:
        assert v in content, f"Missing expected verdict '{v}' in check constraint"

    # Check for safe index creation
    assert "CREATE INDEX IF NOT EXISTS idx_jobs_evaluation_source" in content
    assert "CREATE INDEX IF NOT EXISTS idx_jobs_ai_verdict" in content


def test_migration_005_preserves_v1_cache():
    """Verify that migration does not alter or delete existing cache rows."""
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DELETE FROM ai_match_cache" not in content
    assert "UPDATE ai_match_cache" not in content
    # The evaluation_data column is nullable so V1 rows naturally stay valid with NULL evaluation_data
    assert "evaluation_data JSONB" in content
