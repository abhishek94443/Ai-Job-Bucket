-- Migration 009: Pipeline Staging Queue, Character Budgeting, and Canonicalization Schema
-- Idempotent and backward-compatible

BEGIN;

-- 1. Add staging queue and budgeting columns to jobs table
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS enrichment_status VARCHAR(32) DEFAULT 'STAGED_DETERMINISTIC',
    ADD COLUMN IF NOT EXISTS char_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS estimated_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS canonical_hash VARCHAR(64) DEFAULT NULL;

-- 2. Add named check constraint for enrichment_status safely
ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_enrichment_status_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_enrichment_status_check
    CHECK (enrichment_status IN (
        'STAGED_DETERMINISTIC',
        'AI_QUEUED',
        'AI_IN_FLIGHT',
        'AI_ENRICHED',
        'AI_QUOTA_HELD'
    ));

-- 3. Create performance indexes
CREATE INDEX IF NOT EXISTS idx_jobs_enrichment_status ON jobs (enrichment_status);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_hash ON jobs (canonical_hash);

-- 4. Backfill existing jobs
-- Mark jobs with completed AI evaluations as AI_ENRICHED
UPDATE jobs
SET enrichment_status = 'AI_ENRICHED'
WHERE (ai_score IS NOT NULL OR evaluation_source IN ('AI_LIVE', 'AI_CACHED'))
  AND (enrichment_status IS NULL OR enrichment_status = 'STAGED_DETERMINISTIC');

-- Backfill character and estimated token counts for all jobs
UPDATE jobs
SET char_count = LENGTH(COALESCE(description, '')),
    estimated_tokens = GREATEST(1, CEIL(LENGTH(COALESCE(description, '')) / 4.0))
WHERE char_count = 0 OR char_count IS NULL;

-- Backfill canonical hash using MD5 of normalized company and title
UPDATE jobs
SET canonical_hash = MD5(LOWER(TRIM(COALESCE(company, ''))) || ':' || LOWER(TRIM(COALESCE(title, ''))))
WHERE canonical_hash IS NULL;

COMMIT;
