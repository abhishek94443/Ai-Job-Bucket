# Job Bucket Architecture Documentation

This document describes the architectural design, component interactions, database models, and security principles of Job Bucket.

---

## 1. System Topology Overview

Job Bucket is architected as an asynchronous, decoupled full-stack platform consisting of a high-performance Python FastAPI backend, a responsive React and Next.js frontend, an internal Model Context Protocol (MCP) discovery engine, and a PostgreSQL database layer.

```text
[ Job Boards & Company ATS Portals ]
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│             Job Bucket Discovery Engine (MCP)               │
│     Native binary client managing protocol execution        │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                         │
│  ├── [ Normalizer & Canonical Deduplicator ]                │
│  ├── [ Browser Listing Validator ]                          │
│  ├── [ Deterministic Relevance Scorer ]                     │
│  ├── [ AI Enrichment Worker (Bounded Concurrency) ]         │
│  ├── [ Multi-Provider AI Gateway & Quota Manager ]          │
│  ├── [ 2-Agent Tailoring Engine (Drafter + Reviewer) ]      │
│  └── [ ATS LaTeX Compilation Engine ]                       │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────────────┐ ┌────────────────────────────┐
│      Neon PostgreSQL DB      │ │      Next.js Frontend      │
│  ├── jobs                    │ │  ├── Discovery Feed        │
│  ├── candidate_master_profile│ │  ├── Application Tracker   │
│  ├── ai_model_registry       │ │  ├── 2-Agent Tailor Drawer │
│  └── job_validation_history  │ │  └── Model Quota Registry  │
└──────────────────────────────┘ └────────────────────────────┘
```

---

## 2. Component Architecture

### A. FastAPI Headless Backend Server
* Located in [src/api_server.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/api_server.py).
* Exposes asynchronous REST endpoints for job search, application lifecycle updates, AI batch enrichment, 2-agent resume tailoring, master profile management, and model quota settings.
* Mounts the compiled Next.js frontend static assets in production, allowing the entire application to deploy as a unified single-container monolith while remaining completely decoupled during local development.

### B. Job Bucket Unified Discovery Engine (MCP Layer)
* Located in [src/mcp_client.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/mcp_client.py) and [src/discovery.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/discovery.py).
* Communicates directly with native, precompiled engine binaries via the Model Context Protocol (MCP) using standard input and output JSON-RPC streams.
* Discovers listings across public job portals and company applicant tracking systems (Workday, Greenhouse, Lever, Ashby, SmartRecruiters) without requiring external browser dependencies or browser automation overhead.
* Normalizes disparate upstream fields into uniform schema contracts.

### C. Ingestion, Normalization, and Deduplication Pipeline
* Located in [src/normalizer.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/normalizer.py) and [src/deduplicator.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/deduplicator.py).
* Strips tracking parameters (`utm_*`, `refId`, `trackingId`) and constructs canonical platform URLs.
* Computes an MD5 canonical fingerprint from `(platform, canonical_job_id, company, normalized_title)` to prevent duplicate entries across overlapping search sweeps.

### D. Deterministic Relevance Scorer
* Located in [src/scorer.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/scorer.py) and [src/eligibility.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/eligibility.py).
* Tier-1 evaluation that runs synchronously on ingestion.
* Assigns an initial relevance score (0 to 100) based on title match, core technical stack keywords (e.g. Java, Spring Boot, Microservices), location eligibility, and experience boundaries.
* Sets initial listing state to `STAGED_FOR_ENRICHMENT` for subsequent deep AI analysis.

### E. AI Enrichment Worker (Bounded Concurrency & Batch Packing)
* Located in [src/ai_enrichment_worker.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/ai_enrichment_worker.py) and [src/batch_window_optimizer.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/batch_window_optimizer.py).
* Pulls staged opportunities using transactional PostgreSQL row-level locks (`FOR UPDATE SKIP LOCKED`).
* Packs jobs into multi-opportunity prompt blocks (2 jobs per API call) to double token throughput and reduce external network roundtrips.
* Enforces bounded semaphore concurrency (default 2 parallel workers, maximum 4) to stay strictly within API provider rate limits.
* Evaluates semantic tech stack fit, flags hidden requirements, and assigns an AI relevance score.

### F. Multi-Provider AI Gateway & Quota Manager
* Located in [src/ai_gateway.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/ai_gateway.py) and [src/env_manager.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/env_manager.py).
* Decouples the application from any single AI vendor.
* Supports Google Gemini, OpenAI, Groq, Anthropic, and local Ollama instances through unified request routing.
* Tracks requests-per-minute (RPM), tokens-per-minute (TPM), and requests-per-day (RPD) in PostgreSQL.
* Implements automatic waterfall failover: if the primary model hits a rate limit or daily quota, requests transparently cascade to the next prioritized active model in the registry.

### G. 2-Agent Tailoring Engine
* Located in [src/agentic_tailoring_service.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/agentic_tailoring_service.py).
* **Agent 1 (Drafter):** Ingests the candidate master profile facts and target job description to synthesize job-specific LaTeX resume bullets, executive summary, and cover letter text.
* **Agent 2 (Reviewer & Grounding Auditor):** Audits every claim in the draft against the unredacted candidate facts. Detects and flags any fabricated metrics, unverified employers, or exaggerated skills. Enforces an automated ATS compliance score before finalization.

### H. ATS LaTeX Compilation Engine
* Located in [src/pdf_compiler.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/pdf_compiler.py).
* Compiles LaTeX source into binary PDFs with strict compliance for applicant tracking systems.
* Uses standard PDF Type 1 fonts (Helvetica and Helvetica-Bold) to ensure 100% OCR parseability across Workday, Taleo, Greenhouse, and Ashby parsers.
* Verifies page count constraints and guarantees clean single-page or two-page fits without orphan headers.

---

## 3. Database Schema & Data Models

Job Bucket uses PostgreSQL (compatible with Neon serverless and local PostgreSQL 15+). The schema is defined in [schema.sql](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/schema.sql).

### Key Entities:

1. **`jobs` Table:**
   * Stores canonical job listings, deterministic scores, AI enrichment results, and candidate CRM lifecycle states.
   * `user_status` enum: `DISCOVERED`, `SHORTLISTED`, `APPLIED`, `INTERVIEWING`, `OFFER`, `REJECTED`, `WITHDRAWN`, `STALE`.
   * `enrichment_status` enum: `STAGED_FOR_ENRICHMENT`, `ENRICHMENT_IN_FLIGHT`, `AI_ENRICHED`, `QUOTA_HELD`, `ENRICHMENT_FAILED`.

2. **`candidate_master_profile` Table:**
   * Single source of truth for candidate background facts.
   * Stores structured JSON arrays for work history, projects, technical skills, education, and master LaTeX CV template source.

3. **`ai_model_registry` Table:**
   * Central registry for active LLMs.
   * Stores provider, model ID, priority rank, RPM limit, TPM limit, RPD limit, and active status flag.

4. **`job_validation_history` Table:**
   * Audit log tracking listing verification probes and HTTP response codes over time.

---

## 4. Privacy & Security Principles

1. **Strict PII Separation:**
   * Personally Identifiable Information (candidate phone number, private email, home address) is never transmitted to external AI endpoints during enrichment or drafting workflows.
   * The AI models operate exclusively on technical skills, project scopes, and engineering achievements.
   * Local candidate contact headers are restored client-side or during final local PDF compilation.

2. **Zero In-Memory Secret Leakage:**
   * API keys are managed through [src/env_manager.py](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/src/env_manager.py).
   * Sensitive credentials are masked in logs and API status responses (e.g., displaying only the first and last four characters).

---

## 5. Deployment Topology

The entire platform is packaged in a multi-stage [Dockerfile](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/Dockerfile) designed for cloud platforms like Render:

* **Stage 1 (Node.js Builder):** Builds Next.js frontend pages and bundles optimized production assets into `.next/`.
* **Stage 2 (Python Runtime):** Installs Python dependencies, sets up runtime system packages, verifies MCP discovery binaries, and executes [scripts/start-render.sh](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/scripts/start-render.sh).
* **Process Model:** Starts Uvicorn running FastAPI on internal port 8000, confirms healthcheck readiness, and starts Next.js running on public port 3000 to serve web traffic.
