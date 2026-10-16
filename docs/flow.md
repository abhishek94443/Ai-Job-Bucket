# Job Bucket Request and Data Flow Documentation

This document describes the end-to-end request lifecycle and execution sequences across all primary operations in Job Bucket.

---

## Scenario 1: Job Discovery and Staging Pipeline Flow

This flow executes when discovery is triggered manually from the workbench or automatically in the background.

```text
[ Next.js UI / User ]
         │
         │  1. POST /api/jobs/discover or /api/pipeline/discover
         ▼
[ FastAPI (src/api_server.py) ]
         │
         │  2. Launches background task: run_pipeline()
         ▼
[ Discovery Orchestrator (src/pipeline.py) ]
         │
         │  3. Spawns Job Bucket Discovery Engine (src/mcp_client.py)
         ▼
[ Native Engine Binary via MCP Protocol ]
         │
         │  4. Returns raw job listings from boards and company ATS portals
         ▼
[ Normalizer (src/normalizer.py) ]
         │
         │  5. Cleans URLs, standardizes platform names, strips tracking parameters
         ▼
[ Deduplicator (src/deduplicator.py) ]
         │
         │  6. Generates MD5 canonical fingerprint (platform + id + company + title)
         │     Discards duplicates against local batch and database
         ▼
[ Browser Validator (src/browser_validator.py) ]
         │
         │  7. Probes listing URLs for active status and redirects
         ▼
[ Deterministic Scorer (src/scorer.py) ]
         │
         │  8. Computes keyword match, experience band, and target location score
         ▼
[ Database Persistence (src/db_persistence.py) ]
         │
         │  9. INSERT / ON CONFLICT UPDATE into 'jobs' table
         │     Sets user_status = 'DISCOVERED'
         │     Sets enrichment_status = 'STAGED_FOR_ENRICHMENT'
         ▼
[ PostgreSQL Storage ]
```

---

## Scenario 2: Concurrent AI Batch Enrichment Flow

This flow processes staged opportunities under rate-limit boundaries.

```text
[ Scheduler / UI / Auto-chain ]
         │
         │  1. POST /api/pipeline/enrich-batch { jobs_per_call: 2, concurrency: 2 }
         ▼
[ AI Enrichment Worker (src/ai_enrichment_worker.py) ]
         │
         │  2. SELECT ... FOR UPDATE SKIP LOCKED
         │     Claims batches of staged opportunities
         │     Sets enrichment_status = 'ENRICHMENT_IN_FLIGHT'
         ▼
[ Multi-Job Prompt Packer (src/ai_enrichment_worker.py) ]
         │
         │  3. Combines 2 jobs into a single structured evaluation prompt
         ▼
[ AI Gateway (src/ai_gateway.py) ]
         │
         │  4. Checks RPM/TPM/RPD quota for active model in registry
         │     If limit exceeded, fails over to next priority model
         ▼
[ Target LLM Provider (Gemini / OpenAI / Groq / Local) ]
         │
         │  5. Generates structured JSON evaluation
         │     (Tech stack fit, missing skills, hidden requirements, AI score)
         ▼
[ Database Persistence (src/db_persistence.py) ]
         │
         │  6. Persists ai_score and fit_analysis JSONB to 'jobs' table
         │     Sets enrichment_status = 'AI_ENRICHED'
         │     Releases row-level locks
         ▼
[ PostgreSQL Storage ]
```

---

## Scenario 3: Real-Time Job Browsing and Filtered Search Flow

This flow provides responsive listing browsing and deep multi-facet filtering.

```text
[ Next.js UI (JobDiscoveryView.tsx) ]
         │
         │  1. GET /api/jobs?page=1&page_size=20&min_score=75&portal=linkedin&sort_by=ai_score
         ▼
[ FastAPI (src/api_server.py) ]
         │
         │  2. Normalizes status filter (e.g. 'Saved' -> 'SHORTLISTED')
         │     Validates pagination bounds (page >= 1, page_size <= 500)
         ▼
[ Dashboard Query Service (src/dashboard_service.py) ]
         │
         │  3. Builds parameterized SQL query on 'jobs' table
         │     Applies text search, platform array filter, score threshold, and date windows
         │     Executes: SELECT ..., COUNT(*) OVER() FROM jobs WHERE ... ORDER BY ... LIMIT ... OFFSET ...
         ▼
[ PostgreSQL Storage ]
         │
         │  4. Returns rows and total match count
         ▼
[ Next.js UI ]
         │
         │  5. Renders paginated opportunity cards with match indicators
```

---

## Scenario 4: Application Lifecycle CRM Tracking Flow

This flow manages candidate interactions (saving opportunities and moving stages in the Kanban board).

```text
[ Next.js UI (TrackerView.tsx / JobDiscoveryView.tsx) ]
         │
         │  1. User clicks Bookmark or moves Kanban stage to 'Interviewing'
         │     POST /api/jobs/{job_id}/track { status: 'Interviewing', notes: 'Call scheduled' }
         ▼
[ FastAPI (src/api_server.py) ]
         │
         │  2. Normalizes status string:
         │     'Saved' -> 'SHORTLISTED'
         │     'Interviewing' -> 'INTERVIEWING'
         ▼
[ Database Persistence (src/db_persistence.py) ]
         │
         │  3. Executes UPDATE jobs:
         │     SET user_status = 'INTERVIEWING'::user_application_status_enum,
         │         user_notes = COALESCE(%s, user_notes),
         │         last_status_change = NOW(),
         │         applied_at = CASE WHEN status = 'APPLIED' AND applied_at IS NULL THEN NOW() ELSE applied_at END
         │     WHERE id = {job_id}
         ▼
[ PostgreSQL Storage ]
         │
         │  4. Returns { status: 'success', job_id: 56, new_status: 'INTERVIEWING' }
         ▼
[ Next.js UI ]
         │
         │  5. Updates local stage state immediately without full page reload
```

---

## Scenario 5: 2-Agent Resume Tailoring Flow

This flow produces verified, ATS-compliant LaTeX resumes and custom outreach letters.

```text
[ Next.js Tailoring Drawer (TailoringDrawer.tsx) ]
         │
         │  1. POST /api/jobs/{job_id}/tailor { prefer_ai: true, sync: false }
         ▼
[ FastAPI (src/api_server.py) ]
         │
         │  2. Stores in-progress marker in database
         │     Dispatches asynchronous background execution task
         │     Returns immediate HTTP 200 { status: 'in_progress', job_id: {job_id} }
         ▼
[ Agentic Tailoring Engine (src/agentic_tailoring_service.py) ]
         │
         │  3. Retrieves Candidate Ground Truth Facts from CandidateMasterProfileService
         │     Retrieves Job Description from Database
         ▼
[ Agent 1: The Drafter ]
         │
         │  4. Ingests candidate facts + job requirements
         │     Drafts targeted bullet points adhering to active verb standards
         │     Generates customized executive summary and tailored cover letter
         ▼
[ Agent 2: The Reviewer & Grounding Auditor ]
         │
         │  5. Performs sentence-by-sentence verification against master profile facts
         │     Detects and scrubs any hallucinated claims or unverified achievements
         │     Calculates ATS compliance score and formatting verification
         ▼
[ Dashboard Query Service (src/dashboard_service.py) ]
         │
         │  6. Persists completed pack to database:
         │     { status: 'completed', tailored_latex_cv: '...', cover_letter: '...', ats_score: 98 }
         ▼
[ Next.js Polling Worker ]
         │
         │  7. Periodically calls GET /api/jobs/{job_id}/tailor
         │     Receives completed pack and displays split-pane LaTeX editor and diff review
```

---

## Scenario 6: Candidate Master Profile and Resume Parsing Flow

This flow ingests raw candidate resumes and maintains verified ground truth profile facts.

```text
[ Next.js UI (ProfileFactsView.tsx) ]
         │
         │  1. User uploads resume file (PDF, DOCX, or TXT)
         │     POST /api/profile/upload-resume (multipart/form-data)
         ▼
[ FastAPI (src/api_server.py) ]
         │
         │  2. Validates file extension, byte length, and readable encoding
         ▼
[ Resume Parser (src/resume_parser.py) ]
         │
         │  3. Extracts raw text via PyPDF or python-docx
         │     Executes structured profile extraction (AI + heuristic fallback)
         │     Extracts contact info, technical skills, projects, and work history
         ▼
[ Candidate Master Profile Service (src/candidate_profile_service.py) ]
         │
         │  4. Executes UPSERT into 'candidate_master_profile'
         │     Sets is_active = TRUE
         │     Preserves user custom master LaTeX template if already configured
         ▼
[ PostgreSQL Storage ]
         │
         │  5. Returns structured profile facts to UI
         ▼
[ Next.js UI ]
         │
         │  6. Renders editable skill chips, project cards, and markdown export preview
```

---

## Scenario 7: ATS LaTeX PDF Compilation Flow

This flow compiles LaTeX source code into standardized, ATS-grade binary PDFs.

```text
[ Client / API Request ]
         │
         │  1. POST /api/compile-pdf or POST /api/download-pdf { latex_code: '...' }
         ▼
[ PDF Compilation Engine (src/pdf_compiler.py) ]
         │
         │  2. Normalizes LaTeX macros into ReportLab ATS flowables
         │     Applies Helvetica / Helvetica-Bold standard PDF Type 1 typography
         │     Generates PDF byte buffer
         ▼
[ Automated ATS Compliance Auditor (src/pdf_compiler.py) ]
         │
         │  3. Extracts generated text using PyPDF
         │     Verifies OCR readability, contact header integrity, and page count budgets
         │     Calculates ATS score (target >= 95)
         ▼
[ FastAPI Response ]
         │
         │  4. Returns Base64 PDF payload and audit breakdown,
         │     or streams raw application/pdf binary download
```

---

## Scenario 8: AI Gateway Model Failover and Quota Governance Flow

This flow protects the system against third-party AI rate limits and service interruptions.

```text
[ Calling Service (Enrichment / Tailoring / Extraction) ]
         │
         │  1. Requests model invocation via AI Gateway
         ▼
[ AI Gateway (src/ai_gateway.py) ]
         │
         │  2. Queries ai_model_registry table ordered by priority ASC
         │     Inspects requests-per-minute (RPM) and requests-per-day (RPD)
         ▼
[ Quota Check Evaluation ]
         │
        ├── Quota Available:
        │     Dispatches prompt to current model endpoint
        │
        └── Rate Limit (HTTP 429) or Daily Limit Reached:
              Logs warning and marks current model held
              Selects next available active model from registry
              Retries prompt transparently without user error
         ▼
[ Caller receives successful result from active healthy model ]
```
