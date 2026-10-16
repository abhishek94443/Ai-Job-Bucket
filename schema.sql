-- PostgreSQL Schema for Job Bucket Discovery and AI Tailoring System
-- Synchronized with development database: idempotent, clean, zero sensitive data

BEGIN;

-- 1. Enum types for strict data integrity matching domain models
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_status_enum') THEN
        CREATE TYPE job_status_enum AS ENUM ('ACTIVE', 'EXPIRED', 'UNKNOWN');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'application_type_enum') THEN
        CREATE TYPE application_type_enum AS ENUM ('EASY_APPLY', 'EXTERNAL', 'COMPANY_DIRECT', 'UNKNOWN');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'experience_level_enum') THEN
        CREATE TYPE experience_level_enum AS ENUM ('ENTRY_LEVEL', 'JUNIOR', 'MID_LEVEL', 'SENIOR', 'UNKNOWN');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'confidence_level_enum') THEN
        CREATE TYPE confidence_level_enum AS ENUM ('HIGH', 'MEDIUM', 'LOW');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'fit_eligibility_enum') THEN
        CREATE TYPE fit_eligibility_enum AS ENUM ('HIGH_FIT', 'MEDIUM_FIT', 'PROVISIONAL_NEEDS_REVIEW', 'LOW_FIT');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_application_status_enum') THEN
        CREATE TYPE user_application_status_enum AS ENUM (
            'DISCOVERED', 'SHORTLISTED', 'APPLIED', 'INTERVIEWING',
            'OFFER', 'REJECTED', 'WITHDRAWN', 'STALE'
        );
    END IF;
END $$;

-- 2. Jobs Table: Discovered postings, validation, and AI enrichment
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    platform VARCHAR(50) NOT NULL,
    platform_job_id VARCHAR(100) NOT NULL,
    title VARCHAR(255) NOT NULL,
    company VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    description TEXT,
    posted_date VARCHAR(50),
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    application_url TEXT,
    job_status job_status_enum NOT NULL DEFAULT 'UNKNOWN',
    application_type application_type_enum NOT NULL DEFAULT 'UNKNOWN',
    experience_level experience_level_enum NOT NULL DEFAULT 'UNKNOWN',
    relevance_score INTEGER,
    score_breakdown JSONB DEFAULT '{}'::jsonb,
    score_reasons JSONB DEFAULT '[]'::jsonb,
    matched_skills JSONB DEFAULT '[]'::jsonb,
    confidence_level confidence_level_enum DEFAULT 'LOW',
    confidence_score NUMERIC DEFAULT 0.0,
    eligibility fit_eligibility_enum DEFAULT 'LOW_FIT',
    eligibility_reason TEXT DEFAULT '',
    is_provisional BOOLEAN DEFAULT FALSE,
    unverified_components JSONB DEFAULT '[]'::jsonb,
    available_fields JSONB DEFAULT '[]'::jsonb,
    evidence_source VARCHAR(100) NOT NULL,
    evidence_notes TEXT,
    raw_metadata JSONB DEFAULT '{}'::jsonb,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ai_score INTEGER DEFAULT NULL,
    ai_verdict VARCHAR(32) DEFAULT NULL,
    evaluation_source VARCHAR(32) DEFAULT NULL,
    ai_evaluation JSONB DEFAULT NULL,
    user_status user_application_status_enum NOT NULL DEFAULT 'DISCOVERED',
    applied_at TIMESTAMPTZ DEFAULT NULL,
    user_notes TEXT DEFAULT '',
    contact_person VARCHAR(255) DEFAULT '',
    last_status_change TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    enrichment_status VARCHAR(32) DEFAULT 'STAGED_DETERMINISTIC',
    char_count INTEGER DEFAULT 0,
    estimated_tokens INTEGER DEFAULT 0,
    canonical_hash VARCHAR(64) DEFAULT NULL,
    CONSTRAINT uq_platform_job UNIQUE (platform, platform_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_ai_score ON jobs (ai_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_ai_verdict ON jobs (ai_verdict);
CREATE INDEX IF NOT EXISTS idx_jobs_application_type ON jobs (application_type);
CREATE INDEX IF NOT EXISTS idx_jobs_applied_at ON jobs (applied_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_hash ON jobs (canonical_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_confidence_level ON jobs (confidence_level);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs (discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_eligibility ON jobs (eligibility);
CREATE INDEX IF NOT EXISTS idx_jobs_enrichment_status ON jobs (enrichment_status);
CREATE INDEX IF NOT EXISTS idx_jobs_evaluation_source ON jobs (evaluation_source);
CREATE INDEX IF NOT EXISTS idx_jobs_experience_level ON jobs (experience_level);
CREATE INDEX IF NOT EXISTS idx_jobs_is_provisional ON jobs (is_provisional);
CREATE INDEX IF NOT EXISTS idx_jobs_last_status_change ON jobs (last_status_change DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs (normalized_url);
CREATE INDEX IF NOT EXISTS idx_jobs_relevance_score ON jobs (relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (job_status);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs (user_status);

-- 3. Validation History Table: Historical audit log of every validation check performed
CREATE TABLE IF NOT EXISTS job_validation_history (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    job_status job_status_enum NOT NULL,
    application_type application_type_enum NOT NULL,
    evidence_source VARCHAR(100) NOT NULL,
    evidence_notes TEXT,
    application_url TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_val_history_checked_at ON job_validation_history (checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_val_history_job_id ON job_validation_history (job_id);

-- 4. Resume Versions Table: Uploaded file metadata and binary storage
CREATE TABLE IF NOT EXISTS resume_versions (
    id SERIAL PRIMARY KEY,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    file_content BYTEA NOT NULL,
    extracted_text TEXT,
    extraction_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resume_versions_content_hash ON resume_versions (content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_resume_versions_single_active ON resume_versions (is_active) WHERE is_active = TRUE;

-- 5. Candidate Profiles Table: Structured candidate facts from parsed resume
CREATE TABLE IF NOT EXISTS candidate_profiles (
    id SERIAL PRIMARY KEY,
    resume_version_id INTEGER NOT NULL REFERENCES resume_versions(id) ON DELETE CASCADE,
    profile_version VARCHAR(64) NOT NULL,
    candidate_name VARCHAR(255),
    total_experience_years NUMERIC,
    experience_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    languages JSONB DEFAULT '[]'::jsonb,
    frameworks JSONB DEFAULT '[]'::jsonb,
    databases JSONB DEFAULT '[]'::jsonb,
    messaging_cache JSONB DEFAULT '[]'::jsonb,
    devops_cloud JSONB DEFAULT '[]'::jsonb,
    extracted_skills JSONB DEFAULT '[]'::jsonb,
    inferred_capabilities JSONB DEFAULT '[]'::jsonb,
    target_title VARCHAR(255),
    experience_range_min NUMERIC,
    experience_range_max NUMERIC,
    hard_limit_years NUMERIC,
    target_locations JSONB DEFAULT '[]'::jsonb,
    remote_preference VARCHAR(50) DEFAULT 'flexible',
    user_preferences JSONB DEFAULT '{}'::jsonb,
    raw_profile_json JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_candidate_profiles_resume_id ON candidate_profiles (resume_version_id);
CREATE INDEX IF NOT EXISTS idx_candidate_profiles_version ON candidate_profiles (profile_version);
CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_profiles_single_active ON candidate_profiles (is_active) WHERE is_active = TRUE;

-- 6. Candidate Master Profile Table: Single source of truth for 2-agent tailoring
CREATE TABLE IF NOT EXISTS candidate_master_profile (
    id SERIAL PRIMARY KEY,
    candidate_name VARCHAR(255) NOT NULL DEFAULT '',
    location VARCHAR(255) DEFAULT '',
    phone VARCHAR(50) DEFAULT '',
    email VARCHAR(255) DEFAULT '',
    linkedin_url VARCHAR(255) DEFAULT '',
    github_url VARCHAR(255) DEFAULT '',
    status VARCHAR(100) DEFAULT 'Actively Looking',
    constraints TEXT DEFAULT '',
    languages JSONB DEFAULT '[]'::jsonb,
    education JSONB DEFAULT '[]'::jsonb,
    work_history JSONB DEFAULT '[]'::jsonb,
    projects JSONB DEFAULT '[]'::jsonb,
    technical_skills JSONB DEFAULT '{"languages": ["Java", "SQL", "Python"], "frameworks": ["Spring Boot", "Next.js"], "databases": ["PostgreSQL", "Redis"], "devops_cloud": ["Docker", "Kubernetes", "AWS"]}'::jsonb,
    behavioral_profile_md TEXT DEFAULT '## Behavioral & Work Style
- **Collaborative Problem Solver:** Values clear system boundaries, high code quality, and peer review.
- **Engineering Autonomy:** Strong ownership of backend services from design to production observability.
- **Direct & Transparent:** Prefers concise, factual communication over buzzwords and corporate jargon.',
    master_latex_source TEXT DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_master_profile_active ON candidate_master_profile (is_active) WHERE is_active = TRUE;

-- 7. AI Model Registry Table: Multi-model waterfall and rate-limit tracking
CREATE TABLE IF NOT EXISTS ai_model_registry (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL DEFAULT 'google',
    model_id VARCHAR(100) NOT NULL,
    api_key_alias VARCHAR(100) NOT NULL DEFAULT 'GEMINI_API_KEY',
    rpm_limit INTEGER NOT NULL DEFAULT 15,
    tpm_limit INTEGER NOT NULL DEFAULT 250000,
    rpd_limit INTEGER NOT NULL DEFAULT 500,
    calls_today INTEGER NOT NULL DEFAULT 0,
    tokens_today INTEGER NOT NULL DEFAULT 0,
    last_reset_date DATE NOT NULL DEFAULT CURRENT_DATE,
    last_called_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 1,
    cooldown_until TIMESTAMPTZ,
    status_note VARCHAR(255) DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_provider_model_key UNIQUE (provider, model_id, api_key_alias)
);

CREATE INDEX IF NOT EXISTS idx_ai_model_active ON ai_model_registry (is_active);
CREATE INDEX IF NOT EXISTS idx_ai_model_priority ON ai_model_registry (priority);
CREATE INDEX IF NOT EXISTS idx_ai_model_provider ON ai_model_registry (provider);

-- 8. AI Daily Usage Table: Daily atomic quota tracking
CREATE TABLE IF NOT EXISTS ai_daily_usage (
    usage_date DATE PRIMARY KEY DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::DATE,
    call_count INTEGER NOT NULL DEFAULT 0,
    last_called_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. AI Match Cache Table: Fast cache for candidate-to-job evaluations
CREATE TABLE IF NOT EXISTS ai_match_cache (
    id SERIAL PRIMARY KEY,
    profile_hash VARCHAR(64) NOT NULL,
    platform VARCHAR(50) NOT NULL,
    platform_job_id VARCHAR(100) NOT NULL,
    job_hash VARCHAR(64) NOT NULL,
    model_version VARCHAR(100) NOT NULL,
    prompt_version VARCHAR(50) NOT NULL DEFAULT 'v1',
    overall_score INTEGER NOT NULL,
    match_verdict VARCHAR(50) NOT NULL,
    matching_evidence JSONB DEFAULT '[]'::jsonb,
    missing_requirements JSONB DEFAULT '[]'::jsonb,
    experience_assessment VARCHAR(1000),
    explanation VARCHAR(2000),
    confidence_score NUMERIC DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evaluation_data JSONB DEFAULT NULL,
    CONSTRAINT uq_ai_match_cache_entry UNIQUE (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_ai_match_cache_lookup ON ai_match_cache (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version);

-- 10. AI Matching Results Table: Rich analytical matching result cache
CREATE TABLE IF NOT EXISTS ai_matching_results (
    id SERIAL PRIMARY KEY,
    profile_hash VARCHAR(64) NOT NULL,
    platform VARCHAR(50) NOT NULL,
    platform_job_id VARCHAR(100) NOT NULL,
    job_hash VARCHAR(64) NOT NULL,
    model_version VARCHAR(50) NOT NULL,
    prompt_version VARCHAR(50) NOT NULL,
    match_score INTEGER NOT NULL,
    technical_alignment JSONB DEFAULT '{}'::jsonb,
    key_strengths JSONB DEFAULT '[]'::jsonb,
    skill_gaps JSONB DEFAULT '[]'::jsonb,
    experience_fit JSONB DEFAULT '{}'::jsonb,
    compensation_fit JSONB DEFAULT '{}'::jsonb,
    growth_potential JSONB DEFAULT '{}'::jsonb,
    recommendation VARCHAR(50) NOT NULL DEFAULT 'REVIEW',
    concise_summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ai_match_cache UNIQUE (profile_hash, platform, platform_job_id, job_hash, model_version, prompt_version)
);

-- 11. Seed Default AI Models in Registry (Full Google Gemini Endpoint Registry)
INSERT INTO ai_model_registry (provider, model_id, api_key_alias, rpm_limit, tpm_limit, rpd_limit, priority, is_active)
VALUES
    ('google', 'gemini-3.5-flash-lite', 'GEMINI_API_KEY', 15, 250000, 500, 1, TRUE),
    ('google', 'gemini-3.1-flash-lite', 'GEMINI_API_KEY', 15, 250000, 500, 2, TRUE),
    ('google', 'gemini-2.0-flash',      'GEMINI_API_KEY', 15, 1000000, 1500, 3, TRUE),
    ('google', 'gemini-1.5-pro',        'GEMINI_API_KEY', 2,  32000,  50,   4, TRUE),
    ('google', 'gemini-1.5-flash',      'GEMINI_API_KEY', 15, 1000000, 1500, 4, TRUE),
    ('google', 'gemini-3.7-flash',      'GEMINI_API_KEY', 5,  250000, 20,   5, TRUE),
    ('google', 'gemini-3.8-flash',      'GEMINI_API_KEY', 5,  250000, 20,   6, TRUE),
    ('google', 'gemini-3.6-flash',      'GEMINI_API_KEY', 5,  250000, 20,   7, TRUE)
ON CONFLICT (provider, model_id, api_key_alias) DO UPDATE
SET
    rpm_limit = EXCLUDED.rpm_limit,
    tpm_limit = EXCLUDED.tpm_limit,
    rpd_limit = EXCLUDED.rpd_limit,
    priority = EXCLUDED.priority,
    is_active = EXCLUDED.is_active;

-- 12. Seed Default Active Master Profile (Ensures instant out-of-the-box tailoring)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM candidate_master_profile WHERE is_active = TRUE) THEN
        INSERT INTO candidate_master_profile (
            candidate_name,
            location,
            phone,
            email,
            linkedin_url,
            github_url,
            status,
            constraints,
            languages,
            education,
            work_history,
            projects,
            technical_skills,
            behavioral_profile_md,
            master_latex_source,
            is_active
        ) VALUES (
            'Software Engineer',
            'Remote / Flexible',
            '+1-555-0100',
            'candidate@example.com',
            'https://linkedin.com',
            'https://github.com',
            'Actively Looking',
            'Open to Remote',
            '[{"language": "English", "level": "Fluent", "notes": "Professional working proficiency"}]'::jsonb,
            '[{"degree": "Bachelor of Technology in Computer Science", "period": "2020 - 2024", "institution": "University", "key_topics": "Distributed Systems, Algorithms"}]'::jsonb,
            '[{"company": "Technology Solutions", "role": "Software Engineer", "period": "2023 - Present", "location": "Remote", "bullets": ["Architected distributed event pipelines handling up to 100K events per batch with zero data loss.", "Migrated legacy monolith components to containerized Kubernetes microservices using event-driven architectures.", "Engineered dynamic proxy connection pools for downstream database writes without legacy container dependencies."]}]'::jsonb,
            '[{"name": "Distributed Pipeline Platform", "tech_stack": "Java, Spring Boot, Kafka, PostgreSQL, Docker", "bullets": ["Developed scalable microservices with PostgreSQL row-level security.", "Engineered high-throughput event processing layers with automated telemetry."]}]'::jsonb,
            '{"languages": ["Java", "SQL", "Python", "TypeScript"], "frameworks": ["Spring Boot", "FastAPI", "Next.js"], "databases": ["PostgreSQL", "Redis"], "devops_cloud": ["Docker", "Kubernetes", "AWS"]}'::jsonb,
            '## Behavioral & Work Style
- **Collaborative Problem Solver:** Values clear system boundaries, high code quality, and peer review.
- **Engineering Autonomy:** Strong ownership of backend services from design to production observability.
- **Direct & Transparent:** Prefers concise, factual communication over buzzwords and corporate jargon.',
            E'\\documentclass[11pt,a4paper,sans]{moderncv}\n\\moderncvstyle{banking}\n\\moderncvcolor{blue}\n\n\\renewcommand*{\\namefont}{\\fontsize{32}{34}\\bfseries\\upshape}\n\\colorlet{firstnamecolor}{color1}\n\\colorlet{lastnamecolor}{color1}\n\\colorlet{namecolor}{color1}\n\\renewcommand*{\\sectionstyle}[1]{{\\sectionfont\\color{color1}#1}}\n\n\\usepackage[utf8]{inputenc}\n\\usepackage[scale=0.78]{geometry}\n\\usepackage{import}\n\n\\name{Candidate}{Profile}\n\\address{Location, Country}{}{}\n\\phone[mobile]{+1-555-0100}\n\\email{candidate@example.com}\n\\extrainfo{\\href{https://linkedin.com}{LinkedIn} \\textbar\\ \\href{https://github.com}{GitHub}}\n\n\\begin{document}\n\\makecvtitle\n\n\\section{Summary}\nBackend Software Engineer specializing in scalable APIs, distributed message pipelines, and containerized microservices.\n\n\\section{Technical Skills}\n\\cvitem{Languages}{Java, SQL, Python, TypeScript}\n\\cvitem{Frameworks}{Spring Boot, FastAPI, Next.js}\n\\cvitem{Databases}{PostgreSQL, MySQL, Redis}\n\\cvitem{DevOps \\& Cloud}{Docker, Kubernetes, AWS, CI/CD, Git}\n\\cvitem{APIs \\& Messaging}{REST, Kafka, ActiveMQ, Microservices}\n\n\\section{Professional Experience}\n\\cventry{2023 -- Present}{Software Engineer}{Technology Solutions}{Remote}{}{\n\\begin{itemize}\n\\item Architected distributed event pipelines handling up to 100K events per batch with zero data loss.\n\\item Migrated legacy monolith components to containerized Kubernetes microservices using event-driven architectures.\n\\item Engineered dynamic proxy connection pools for downstream database writes without legacy container dependencies.\n\\end{itemize}}\n\n\\section{Projects}\n\\cventry{2024}{Distributed Data Platform}{Open Source Project}{}{}{\n\\begin{itemize}\n\\item Developed scalable microservices with PostgreSQL row-level security and automated schema validation.\n\\item Integrated message queues for asynchronous processing and telemetry instrumentation.\n\\end{itemize}}\n\n\\section{Education}\n\\cventry{2020 -- 2024}{Bachelor of Technology in Computer Science}{University}{City}{}{}\n\n\\end{document}',
            TRUE
        );
    END IF;
END $$;

COMMIT;
