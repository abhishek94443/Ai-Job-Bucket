# Job Bucket - Pipeline Staging and Concurrent AI Enrichment Guide

This document provides a comprehensive technical reference for the queue-based staging, token budgeting, dynamic window optimization, and quota-aware concurrent AI enrichment architecture implemented across Steps 1, 2, and 3.

---

## 1. Architectural Overview

The Job Bucket ingestion and evaluation system uses a two-tier architecture:

1. **Tier 1 (Deterministic Ingestion & Staging)**:
   - Scraped jobs are normalized, canonicalized, and hashed.
   - Character counts and estimated tokens are computed.
   - Jobs are persisted immediately to PostgreSQL as `STAGED_DETERMINISTIC`.
   - Jobs are instantly visible on the dashboard with a `[Deterministic Preview]` badge.

2. **Tier 2 (Quota-Aware Concurrent AI Enrichment)**:
   - Staged jobs are grouped into balanced batch windows via bin-packing.
   - Bounded concurrent workers (`asyncio.Semaphore`) evaluate jobs against candidate technical and behavioral profiles.
   - Responses pass through a 4-stage resilient JSON parser.
   - Enriched evaluations are persisted to PostgreSQL as `AI_ENRICHED`.
   - Cards dynamically transition to `[AI Verified]`.

---

## 2. Component Specifications

### 2.1 Database Schema (Migration 009)
File: `migrations/009_pipeline_staging_and_canonicalization.sql`

Added columns to `jobs` table:
- `enrichment_status`: `VARCHAR(32)` constrained to `('STAGED_DETERMINISTIC', 'AI_QUEUED', 'AI_IN_FLIGHT', 'AI_ENRICHED', 'AI_QUOTA_HELD')`.
- `char_count`: `INTEGER DEFAULT 0` (character length of full job description).
- `estimated_tokens`: `INTEGER DEFAULT 0` (token budget approximation: `ceil(chars / 4)`).
- `canonical_hash`: `VARCHAR(64)` (MD5 hash of canonical company + role key).

Indexes created:
- `idx_jobs_enrichment_status ON jobs (enrichment_status)`
- `idx_jobs_canonical_hash ON jobs (canonical_hash)`

### 2.2 Ingestion Canonicalization & Staging
File: `src/pipeline.py`

- During job discovery, each posting computes:
  - Canonical key via `compute_canonical_job_key(title, company)`.
  - Canonical MD5 hash.
  - Character count from `description_full` or `description`.
  - Estimated tokens.
- Default ingestion status: `STAGED_DETERMINISTIC`.
- Idempotent upsert in `src/db_persistence.py`: If a job is already `AI_ENRICHED`, a re-scrape will never downgrade it back to staged.

### 2.3 Dynamic Batch Window Optimizer
File: `src/batch_window_optimizer.py`

- **Purpose**: Eliminates latency variance and prevents sudden token-per-minute (TPM) spikes.
- **Fixed Context Overhead**: System instructions + Candidate profile + Behavioral framework (~12,000 characters).
- **Max Window Ceiling**: `MAX_WINDOW_CHARS = 35000` (~8,500 tokens).
- **Balanced Bin-Packing**:
  - Pairs longer job descriptions with shorter job descriptions so concurrent workers finish at similar times.
- **Adaptive Window Shrinking**:
  - If a single job description is massive (e.g., 15,000 to 20,000 characters), the window automatically shrinks the batch size down to whatever number safely fits under the ceiling.

### 2.4 Quota-Aware Concurrent AI Worker
File: `src/ai_enrichment_worker.py`

- **Concurrency Control**: Non-blocking `asyncio.Semaphore(concurrency)` with user-configurable parallel worker count (1 to 4).
- **Inter-Request Delay**: 1,000ms to 1,500ms delay between dispatches to eliminate burst rate throttling on cloud gateways.
- **Profile Grounding**: Injects verified candidate technical facts from `01-candidate-profile.md` and behavioral preferences from `02-behavioral-profile.md`.
- **4-Stage Resilient JSON Parser**:
  1. Markdown stripping (backticks and code fences).
  2. Strict `json.loads`.
  3. AST & bracket repair (balances unclosed braces, escapes raw backslashes, strips trailing commas).
  4. Regex key-value salvage fallback.
- **HTTP 429 Circuit Breaker**:
  - Catches `ProviderRateLimitError` or HTTP 429.
  - Immediately pauses remaining tasks.
  - Reverts uncompleted in-flight jobs to `AI_QUOTA_HELD`.
  - Successfully enriched jobs remain committed in PostgreSQL (zero data loss).

### 2.5 Database Persistence Layer
File: `src/db_persistence.py`

Key methods:
- `claim_staged_jobs_for_enrichment(limit, job_ids)`: Atomically updates matching jobs to `AI_IN_FLIGHT` using `FOR UPDATE SKIP LOCKED`.
- `save_job_ai_enrichment(job_id, ai_score, fit_analysis)`: Persists score, `score_reasons`, `matched_skills`, and structured JSON, marking status `AI_ENRICHED`.
- `mark_jobs_quota_held(job_ids, reason)`: Reverts in-flight jobs to `AI_QUOTA_HELD`.
- `get_enrichment_metrics()`: Returns live queue counters.

### 2.6 API Telemetry & Trigger Endpoints
File: `src/api_server.py`

- `GET /api/pipeline/status`: Returns live queue counters, active AI model, and window capacity metrics.
- `POST /api/pipeline/enrich-batch`: Accepts `{ "batch_size": int, "concurrency": int }` and triggers concurrent evaluation.

### 2.7 Frontend UI Dashboard
File: `frontend/src/components/JobDiscoveryView.tsx`

- **Batch Size Selector**: Dropdown options: 1, 3, 5, 10, 15 jobs.
- **Parallel Workers Selector**: Dropdown options: 1 Worker, 2 Workers, 3 Workers, 4 Workers.
- **Enrich Staged Button**: One-click async trigger with in-flight loading animation.
- **Telemetry Pill**: Displays live verified count, staged count, in-flight count, and window character metrics.
- **Dual Status Badges**: Clean distinction between `[AI Verified]` and `[Deterministic Preview]`.
- Zero version jargon (no "V1" or "V2" labels).

---

## 3. Verification & Test Coverage

All 329 tests across the repository pass cleanly:
- `tests/test_batch_window_optimizer.py` (5/5 passing)
- `tests/test_ai_enrichment_worker.py` (7/7 passing)
- `tests/test_pipeline_staging_api.py` (2/2 passing)
- `tests/test_persistence_v2.py` (8/8 passing)
- `tests/test_application_tracking.py` (3/3 passing)
- Full regression suite: `python -m pytest -q` -> 329 passed in 71.04s.
- Frontend build: `npm run build` -> Turbopack compiled successfully with 0 errors.
