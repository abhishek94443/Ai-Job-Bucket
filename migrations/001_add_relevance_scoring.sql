-- Migration: Add relevance scoring and experience level columns to jobs table

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'experience_level_enum') THEN
        CREATE TYPE experience_level_enum AS ENUM ('ENTRY_LEVEL', 'JUNIOR', 'MID_LEVEL', 'SENIOR', 'UNKNOWN');
    END IF;
END $$;

ALTER TABLE jobs 
ADD COLUMN IF NOT EXISTS relevance_score INTEGER,
ADD COLUMN IF NOT EXISTS experience_level experience_level_enum DEFAULT 'UNKNOWN',
ADD COLUMN IF NOT EXISTS score_breakdown JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS score_reasons JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS matched_skills JSONB DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_jobs_relevance_score ON jobs (relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_experience_level ON jobs (experience_level);
