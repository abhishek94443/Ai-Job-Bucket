-- Migration 005: Additive V2 AI Evaluation, Provenance and Verdict Schema
-- DO NOT APPLY TO NEON WITHOUT EXPLICIT USER APPROVAL

BEGIN;

-- 1. Expand ai_match_cache verdict check constraint to allow V2 verdicts alongside V1
ALTER TABLE ai_match_cache
    DROP CONSTRAINT IF EXISTS ai_match_cache_match_verdict_check;

ALTER TABLE ai_match_cache
    ADD CONSTRAINT ai_match_cache_match_verdict_check
    CHECK (match_verdict IN (
        -- Legacy V1 Verdicts
        'HIGH_FIT', 'MEDIUM_FIT', 'LOW_FIT', 'OVER_LIMIT',
        -- Phase 4 V2 Verdicts
        'STRONG_MATCH', 'GOOD_MATCH', 'STRETCH_MATCH', 'MISMATCH'
    ));

-- 2. Add JSONB column to ai_match_cache for full structured V2 payload retention
ALTER TABLE ai_match_cache
    ADD COLUMN IF NOT EXISTS evaluation_data JSONB DEFAULT NULL;

-- 3. Enhance jobs table to store active AI evaluation, scores, and provenance
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS ai_score INTEGER DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS ai_verdict VARCHAR(32) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS evaluation_source VARCHAR(32) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS ai_evaluation JSONB DEFAULT NULL;

-- 4. Apply clean named check constraints on jobs columns safely
ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_ai_score_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_ai_score_check
    CHECK (ai_score IS NULL OR (ai_score >= 0 AND ai_score <= 100));

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_ai_verdict_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_ai_verdict_check
    CHECK (ai_verdict IS NULL OR ai_verdict IN ('STRONG_MATCH', 'GOOD_MATCH', 'STRETCH_MATCH', 'MISMATCH'));

ALTER TABLE jobs
    DROP CONSTRAINT IF EXISTS jobs_evaluation_source_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_evaluation_source_check
    CHECK (evaluation_source IS NULL OR evaluation_source IN ('AI_LIVE', 'AI_CACHED', 'DETERMINISTIC_FALLBACK', 'AI_ERROR'));

-- 5. Create performance indexes for dashboard sorting and filtering
CREATE INDEX IF NOT EXISTS idx_jobs_ai_score ON jobs (ai_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_ai_verdict ON jobs (ai_verdict);
CREATE INDEX IF NOT EXISTS idx_jobs_evaluation_source ON jobs (evaluation_source);

COMMIT;
