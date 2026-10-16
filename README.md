# Job Bucket

Job Bucket is an automated job discovery, multi-tier evaluation, and application tailoring platform designed for software engineers. It unifies opportunity discovery across major job boards and tens of thousands of direct company applicant tracking systems (ATS), scores opportunities using two-tier deterministic and semantic evaluation, and produces verified, ATS-compliant resumes using a 2-agent drafting and reviewing engine.

---

## Documentation Links

* [System Architecture Guide](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/architecture.md): In-depth breakdown of backend services, database schema, AI Gateway quota governance, and container topology.
* [Request and Data Flow Guide](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/flow.md): Step-by-step sequence diagrams covering discovery, batch enrichment, CRM tracking, resume tailoring, and model failovers.

---

## Key System Capabilities

### 1. Unified Multi-Platform Job Discovery
* Ingests listings across public career platforms and direct company ATS portals (Workday, Greenhouse, Lever, Ashby, SmartRecruiters) using an internal Model Context Protocol (MCP) discovery engine.
* Native precompiled binaries for Linux, macOS, and Windows handle upstream discovery without browser automation overhead.
* Normalizes disparate fields into uniform, type-safe data structures.

### 2. Canonical Normalization and Deduplication
* Strips marketing parameters (`utm_*`, `refId`, session IDs) to generate canonical listing URLs.
* Calculates an MD5 fingerprint from `(platform, canonical_job_id, company, title)` to ensure zero duplicate entries across repetitive search cycles.

### 3. Two-Tier Scoring Pipeline
* **Tier 1 (Deterministic Evaluation):** Fast keyword matching, technology stack filtering, target location matching, and experience boundary checks run immediately on ingestion.
* **Tier 2 (AI Enrichment Worker):** Asynchronous workers pull staged opportunities using PostgreSQL row-level locks (`FOR UPDATE SKIP LOCKED`), pack multiple opportunities per call, and evaluate tech stack fit, skill gaps, and hidden requirements.

### 4. 2-Agent Resume Tailoring Engine
* **Agent 1 (The Drafter):** Analyzes job requirements against candidate master profile facts to draft targeted LaTeX resume bullets, an executive summary, and custom recruiter outreach messages.
* **Agent 2 (The Reviewer & Grounding Auditor):** Checks every generated bullet point against verified candidate profile facts to prevent hallucinated accomplishments, enforcing strict factual grounding and calculating an automated ATS compliance score.

### 5. Multi-Provider AI Gateway & Quota Governance
* Decoupled architecture supporting Google Gemini, OpenAI, Groq, Anthropic, and local Ollama models.
* Dynamic priority ordering, automated rate-limit detection, and transparent waterfall failover when a model reaches RPM, TPM, or daily quota ceilings.

### 6. Application Lifecycle CRM Tracker
* Kanban-style pipeline tracking across stages: `DISCOVERED`, `SHORTLISTED`, `APPLIED`, `INTERVIEWING`, `OFFER`, `REJECTED`, `WITHDRAWN`, and `STALE`.
* Tracks status transition timestamps, application submission dates, recruiter contacts, and personal interview notes.

### 7. Modern Decoupled Workbench
* Responsive Next.js frontend with split-pane drawer navigation, real-time filtering, full job description preview, candidate master facts manager, and model quota controls.

---

## High-Level Architecture

```text
[ Job Boards & Company Portals ]
               │
               ▼
[ Job Bucket Discovery Engine (MCP) ]
               │
               ▼
[ Normalizer & Deduplicator ] ──► [ Deterministic Scorer ] ──► [ Staged Queue ]
                                                                      │
                                                                      ▼
[ Next.js Workbench ] ◄── [ FastAPI Backend ] ◄── [ PostgreSQL ] ◄── [ AI Worker ]
         │                                                            │
         ▼                                                            ▼
[ 2-Agent Tailoring Drawer ] ──► [ LaTeX PDF Compiler ] ◄── [ AI Gateway ]
```

For full architectural details, see [architecture.md](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/architecture.md).

---

## Prerequisites

* **Python:** 3.10 or higher
* **Node.js:** 18.x or higher (Node 20 recommended)
* **PostgreSQL:** 15 or higher (Local installation or cloud-hosted database such as Neon)
* **LLM API Key:** Google Gemini, OpenAI, or Groq API key for AI enrichment and tailoring features

---

## Quickstart Guide

### 1. Clone the Repository

```bash
git clone https://github.com/abhishek94443/Ai-Job-Bucket.git
cd job-bucket
```

### 2. Set Up the Python Backend

```bash
# Create and activate a virtual environment
python -m venv venv

# Windows (PowerShell):
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Set Up the Next.js Frontend

```bash
cd frontend
npm install
cd ..
```

### 4. Configure Environment Variables

Create a `.env` file in the repository root:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/job_bucket
GEMINI_API_KEY=your_gemini_api_key_here
```

### 5. Initialize Database Schema

Execute the initialization script to create tables, indexes, custom enums, and register default AI models:

```bash
python scripts/init_db.py
```

### 6. Start Development Servers

In terminal 1 (FastAPI Backend):
```bash
uvicorn src.api_server:app --port 8000 --reload
```

In terminal 2 (Next.js Frontend):
```bash
cd frontend
npm run dev
```

* Frontend Workbench: `http://localhost:3000`
* Backend API Documentation: `http://localhost:8000/docs`
* Health Check Endpoint: `http://localhost:8000/api/health`

---

## Test Suite Execution

The repository contains an automated test suite verifying all active ingestion, scoring, AI gateway, CRM tracking, and tailoring modules:

```bash
# Run the full test suite
pytest tests/

# Run a specific module test suite
pytest tests/test_api_server_tracking.py -v
```

Validate frontend production compilation:

```bash
cd frontend
npm run build
```

---

## Deployment Topology

Job Bucket is packaged as a production-grade multi-stage [Dockerfile](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/Dockerfile) ready for single-container cloud hosting (such as Render):

* **Stage 1 (Frontend Builder):** Compiles the Next.js application into production artifacts.
* **Stage 2 (Runtime Container):** Sets up Python dependencies, verifies the native discovery engine binary, boots the FastAPI backend on internal port 8000, and starts the Next.js web server on public port 3000 via [scripts/start-render.sh](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/scripts/start-render.sh).

---

## License

This project is open-source software licensed under the MIT License. See [LICENSE](file:///c:/Users/Lenovo/Desktop/abhishe/AI/job-bucket/LICENSE) for details.
