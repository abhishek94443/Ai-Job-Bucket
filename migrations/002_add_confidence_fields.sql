-- Migration: Add evidence confidence and provisional indicators to jobs table

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'confidence_level_enum') THEN
        CREATE TYPE confidence_level_enum AS ENUM ('HIGH', 'MEDIUM', 'LOW');
    END IF;
END $$;

ALTER TABLE jobs 
ADD COLUMN IF NOT EXISTS confidence_level confidence_level_enum DEFAULT 'LOW',
ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(3, 2) DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS unverified_components JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS available_fields JSONB DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_jobs_confidence_level ON jobs (confidence_level);
CREATE INDEX IF NOT EXISTS idx_jobs_is_provisional ON jobs (is_provisional);
