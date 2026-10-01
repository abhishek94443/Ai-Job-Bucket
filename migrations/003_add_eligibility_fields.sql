-- Migration: Add eligibility enum and columns to jobs table

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'fit_eligibility_enum') THEN
        CREATE TYPE fit_eligibility_enum AS ENUM ('HIGH_FIT', 'MEDIUM_FIT', 'PROVISIONAL_NEEDS_REVIEW', 'LOW_FIT');
    END IF;
END $$;

ALTER TABLE jobs 
ADD COLUMN IF NOT EXISTS eligibility fit_eligibility_enum DEFAULT 'LOW_FIT',
ADD COLUMN IF NOT EXISTS eligibility_reason TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_jobs_eligibility ON jobs (eligibility);
