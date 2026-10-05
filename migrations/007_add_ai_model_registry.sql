-- Migration 007: Add AI Model Registry and Quota Tracking Table
-- Enables dynamic multi-model waterfall, multi-key rotation, and real-time RPM/RPD limits tracking.

BEGIN;

CREATE TABLE IF NOT EXISTS ai_model_registry (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(50) NOT NULL DEFAULT 'google',
    model_id VARCHAR(100) NOT NULL,
    api_key_alias VARCHAR(100) NOT NULL DEFAULT 'GEMINI_API_KEY',
    rpm_limit INT NOT NULL DEFAULT 15,
    tpm_limit INT NOT NULL DEFAULT 250000,
    rpd_limit INT NOT NULL DEFAULT 500,
    calls_today INT NOT NULL DEFAULT 0,
    tokens_today INT NOT NULL DEFAULT 0,
    last_reset_date DATE NOT NULL DEFAULT CURRENT_DATE,
    last_called_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    priority INT NOT NULL DEFAULT 1,
    cooldown_until TIMESTAMPTZ,
    status_note VARCHAR(255) DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_provider_model_key UNIQUE (provider, model_id, api_key_alias)
);

CREATE INDEX IF NOT EXISTS idx_ai_model_priority ON ai_model_registry (priority ASC);
CREATE INDEX IF NOT EXISTS idx_ai_model_active ON ai_model_registry (is_active);
CREATE INDEX IF NOT EXISTS idx_ai_model_provider ON ai_model_registry (provider);

-- Seed initial models from verified limits table
INSERT INTO ai_model_registry (provider, model_id, api_key_alias, rpm_limit, tpm_limit, rpd_limit, priority, is_active)
VALUES
    ('google', 'gemini-3.5-flash-lite', 'GEMINI_API_KEY', 15, 250000, 500, 1, TRUE),
    ('google', 'gemini-3.1-flash-lite', 'GEMINI_API_KEY', 15, 250000, 500, 2, TRUE),
    ('google', 'gemini-3.7-flash',      'GEMINI_API_KEY', 5,  250000, 20,  3, TRUE),
    ('google', 'gemini-3.8-flash',      'GEMINI_API_KEY', 5,  250000, 20,  4, TRUE),
    ('google', 'gemini-3.6-flash',      'GEMINI_API_KEY', 5,  250000, 20,  5, TRUE)
ON CONFLICT (provider, model_id, api_key_alias) DO UPDATE
SET rpm_limit = EXCLUDED.rpm_limit,
    tpm_limit = EXCLUDED.tpm_limit,
    rpd_limit = EXCLUDED.rpd_limit,
    priority = EXCLUDED.priority;

COMMIT;
