-- Migration 006: Add Application Lifecycle CRM Tracking to Jobs
-- Enables tracking stages: DISCOVERED -> SHORTLISTED -> APPLIED -> INTERVIEWING -> OFFER / REJECTED / WITHDRAWN / STALE

BEGIN;

-- 1. Create enum type for application lifecycle status if not exists
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_application_status_enum') THEN
        CREATE TYPE user_application_status_enum AS ENUM (
            'DISCOVERED',
            'SHORTLISTED',
            'APPLIED',
            'INTERVIEWING',
            'OFFER',
            'REJECTED',
            'WITHDRAWN',
            'STALE'
        );
    END IF;
END $$;

-- 2. Add application lifecycle tracking columns to jobs table
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS user_status user_application_status_enum NOT NULL DEFAULT 'DISCOVERED',
    ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS user_notes TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS contact_person VARCHAR(255) DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_status_change TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 3. Create indexes for quick filtering in the CRM dashboard
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs (user_status);
CREATE INDEX IF NOT EXISTS idx_jobs_applied_at ON jobs (applied_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_last_status_change ON jobs (last_status_change DESC);

COMMIT;
