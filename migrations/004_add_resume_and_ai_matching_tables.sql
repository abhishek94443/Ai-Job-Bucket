-- Migration 004: Add Resume Versions, Candidate Profiles, AI Match Cache, and Daily Quota Tracking
-- Status: APPLIED TO NEON POSTGRESQL
-- Enforces:
-- 1. At most one active resume version via partial unique index.
-- 2. At most one active candidate profile via partial unique index.
-- 3. Default FALSE for is_active on both resume_versions and candidate_profiles.
-- 4. Mandatory retention of original file bytes (file_content BYTEA NOT NULL).
-- 5. Strict integrity: candidate profiles MUST reference a valid resume version (resume_version_id INTEGER NOT NULL REFERENCES resume_versions(id) ON DELETE CASCADE).
-- 6. Check constraint: active candidate profile must always have a non-null resume version.
-- 7. Zero fabricated facts: total_experience_years has NO default (NULL indicates unknown/unconfirmed).
-- 8. Explicit distinction: resume facts (skills, experience), inferred capabilities, and editable user preferences (titles, locations, cutoffs).
-- 9. Collision-resistant AI match caching keyed on (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version).
-- 10. Validated, bounded AI cache fields with domain check constraints and zero raw response retention.
-- 11. Persistent, concurrency-safe AI daily quota tracking table (ai_daily_usage) locked strictly to UTC date boundary.

-- 1. Resume Versions Table: Stores uploaded file metadata, SHA-256 hash, extracted text, and raw binary
CREATE TABLE IF NOT EXISTS resume_versions (
    id SERIAL PRIMARY KEY,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,                 -- 'pdf' or 'docx'
    file_size_bytes INTEGER NOT NULL,
    content_hash VARCHAR(64) NOT NULL,              -- SHA-256 hex digest of raw file bytes
    file_content BYTEA NOT NULL,                     -- Original document bytes (mandatory retention)
    extracted_text TEXT,                             -- Sanitized extracted text
    extraction_status VARCHAR(50) NOT NULL DEFAULT 'PENDING', -- PENDING, EXTRACTED, FAILED
    error_message TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,        -- Default FALSE: activated only inside explicit transaction
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resume_versions_content_hash ON resume_versions (content_hash);
-- Partial unique index: strictly enforces at most one active resume version in the database
CREATE UNIQUE INDEX IF NOT EXISTS uq_resume_versions_single_active ON resume_versions (is_active) WHERE is_active = TRUE;

-- 2. Candidate Profiles Table: Separates resume facts, inferred capabilities, and user search preferences
CREATE TABLE IF NOT EXISTS candidate_profiles (
    id SERIAL PRIMARY KEY,
    resume_version_id INTEGER NOT NULL REFERENCES resume_versions(id) ON DELETE CASCADE, -- Deleting a resume version cascades to remove its profile; must be protected with UI confirmation
    profile_version VARCHAR(64) NOT NULL,           -- SHA-256 hash of structured profile contents
    candidate_name VARCHAR(255),
    
    -- A. Resume-Derived Facts (NULL when not explicitly determined from resume; NEVER silently invented)
    total_experience_years NUMERIC(4, 1),            -- NULL if unknown/unconfirmed in resume
    experience_confirmed BOOLEAN NOT NULL DEFAULT FALSE, -- True only when user verifies extracted experience
    languages JSONB DEFAULT '[]'::jsonb,
    frameworks JSONB DEFAULT '[]'::jsonb,
    databases JSONB DEFAULT '[]'::jsonb,
    messaging_cache JSONB DEFAULT '[]'::jsonb,
    devops_cloud JSONB DEFAULT '[]'::jsonb,
    extracted_skills JSONB DEFAULT '[]'::jsonb,
    
    -- B. Inferred Capabilities (demonstrated engineering competencies inferred from projects)
    inferred_capabilities JSONB DEFAULT '[]'::jsonb,
    
    -- C. User Search Preferences (explicit user-configured criteria; NOT resume-derived facts)
    target_title VARCHAR(255),                      -- User-editable target role title (no silent default)
    experience_range_min NUMERIC(4, 1),             -- User-editable lower experience boundary
    experience_range_max NUMERIC(4, 1),             -- User-editable upper experience boundary
    hard_limit_years NUMERIC(4, 1),                 -- User-editable hard ceiling cutoff
    target_locations JSONB DEFAULT '[]'::jsonb,     -- User-selected target cities (no silent default)
    remote_preference VARCHAR(50) DEFAULT 'flexible', -- 'remote_only', 'hybrid', 'onsite', 'flexible'
    user_preferences JSONB DEFAULT '{}'::jsonb,     -- Complete user preferences payload (preferences_source, boundaries)
    raw_profile_json JSONB DEFAULT '{}'::jsonb,     -- Validated CandidateProfile.model_dump() (apis_and_auth, etc.); NEVER raw resume text, PII, or raw LLM output
    
    is_active BOOLEAN NOT NULL DEFAULT FALSE,       -- Default FALSE: activated only inside explicit transaction
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_candidate_profiles_active_has_resume CHECK (NOT is_active OR resume_version_id IS NOT NULL)
);

-- Partial unique index: strictly enforces at most one active candidate profile in the database
CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_profiles_single_active ON candidate_profiles (is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_candidate_profiles_resume_id ON candidate_profiles (resume_version_id);
CREATE INDEX IF NOT EXISTS idx_candidate_profiles_version ON candidate_profiles (profile_version);

-- 3. AI Match Cache Table: Collision-resistant result caching with validated fields and zero raw response retention
CREATE TABLE IF NOT EXISTS ai_match_cache (
    id SERIAL PRIMARY KEY,
    profile_hash VARCHAR(64) NOT NULL,              -- SHA-256 of candidate profile
    platform VARCHAR(50) NOT NULL,                  -- Source portal (linkedin, indeed, freehire)
    platform_job_id VARCHAR(100) NOT NULL,          -- Portal job ID
    job_hash VARCHAR(64) NOT NULL,                  -- SHA-256 of canonical job content
    model_version VARCHAR(100) NOT NULL,            -- e.g. 'gemini-3.5-flash-lite'
    prompt_version VARCHAR(50) NOT NULL DEFAULT 'v1', -- Schema/prompt template version
    overall_score INTEGER NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
    match_verdict VARCHAR(50) NOT NULL CHECK (match_verdict IN ('HIGH_FIT', 'MEDIUM_FIT', 'LOW_FIT', 'OVER_LIMIT')),
    matching_evidence JSONB DEFAULT '[]'::jsonb,
    missing_requirements JSONB DEFAULT '[]'::jsonb,
    experience_assessment VARCHAR(1000),
    explanation VARCHAR(2000),
    confidence_score NUMERIC(3, 2) DEFAULT 0.0 CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ai_match_cache_entry UNIQUE (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_ai_match_cache_lookup 
    ON ai_match_cache (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version);

-- 4. AI Daily Usage Table: Concurrency-safe, persistent quota tracking locked strictly to UTC date boundary
CREATE TABLE IF NOT EXISTS ai_daily_usage (
    usage_date DATE PRIMARY KEY DEFAULT ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date),
    call_count INTEGER NOT NULL DEFAULT 0,
    last_called_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
