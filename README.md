# Job Bucket

Job Bucket is an open-source, automated job discovery and application tailoring platform. It continuously discovers software engineering postings across job boards and company applicant tracking systems (ATS), scores relevance using multi-tier deterministic and AI evaluation, and generates ATS-optimized resumes and cover letters using a 2-agent drafting and reviewing engine.

---

## Key Features

- **Multi-Source Job Discovery**: Ingests postings from LinkedIn, Indeed, freehire, and 41,000+ direct company ATS portals (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, etc.) via a vendored Model Context Protocol (MCP) server with precompiled binaries for Windows, Linux, and macOS.
- **Two-Tier Evaluation Pipeline**:
  - **Tier 1 (Deterministic)**: Fast normalization, MD5 canonical deduplication, and rule-based relevance scoring for instant dashboard previews.
  - **Tier 2 (AI Enrichment)**: Asynchronous semantic gap analysis and behavioral alignment via Google Gemini models with rate-limit waterfall routing.
- **2-Agent Tailoring Engine**:
  - **Agent 1 (Drafter)**: Analyzes job descriptions against candidate profiles to craft tailored LaTeX resumes and concise recruiter outreach notes.
  - **Agent 2 (Reviewer)**: Enforces strict grounding checks to prevent hallucinated experience and runs automated ATS compliance audits (100/100 score target).
- **Application Tracking (CRM)**: Tracks applications through lifecycle stages (`DISCOVERED`, `SHORTLISTED`, `APPLIED`, `INTERVIEWING`, `OFFER`, `REJECTED`).
- **Modern Next.js Workbench**: Responsive dashboard with real-time drawer preview, full job description viewer, and direct PDF downloads.

---

## Architecture Overview

```text
[ Job Discovery ] ──► [ Deduplicator ] ──► [ Browser Validator ] ──► [ Deterministic Ingestion ]
        │                                                                     │
        ▼                                                                     ▼
 [ Freehire / LI / Indeed ]                                           [ Relevance Scorer ]
                                                                               │
                                                                               ▼
[ Next.js UI Drawer ] ◄── [ FastAPI Server ] ◄── [ PostgreSQL DB ] ◄── [ Staged Pipeline ]
        │                                                                     │
        ▼                                                                     ▼
[ 2-Agent Tailoring ] ──► [ PDF Compiler ] ◄── [ AI Gateway ] ◄─────── [ Concurrent Workers ]
```

---

## Prerequisites

- **Python**: 3.10 or higher
- **Node.js**: 18.x or higher
- **PostgreSQL**: 15 or higher (Local instance or cloud-hosted such as Neon)
- **Google Gemini API Key**: For AI matching and tailoring features (free-tier keys supported)

---

## Quickstart Guide

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/job-bucket.git
cd job-bucket
```

### 2. Set Up the Python Backend

```bash
# Create and activate a virtual environment
python -m venv venv

# Windows:
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

Copy the example environment configuration:

```bash
cp .env.example .env
```

Edit `.env` and set your database connection string and Gemini API key:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/job_bucket
GEMINI_API_KEY=your_gemini_api_key_here
```

### 5. Initialize the Database

Run the database initialization script to create all tables, enums, constraints, indexes, and seed AI models in a single operation:

```bash
python scripts/init_db.py
```

### 6. Start the Application

In terminal 1 (Backend API):
```bash
uvicorn src.api_server:app --port 8000 --reload
```

In terminal 2 (Frontend Dashboard):
```bash
cd frontend
npm run dev
```

Open your browser to:
- **Dashboard UI**: `http://localhost:3000`
- **Backend API Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/api/health`

---

## Multi-Platform Discovery Engine

Job Bucket vendors the open-source `openings-mcp` service under `mcp_servers/openings-mcp/` and includes precompiled binaries for multiple platforms in `mcp_servers/openings-mcp/bin/`:

- `openings-mcp-windows-amd64.exe` (Windows x64)
- `openings-mcp-linux-amd64` (Linux x86_64)
- `openings-mcp-darwin-arm64` (macOS Apple Silicon)
- `openings-mcp-darwin-amd64` (macOS Intel)

The backend automatically detects the host operating system and executes the correct binary with zero external Go setup required.

---

## Running Tests

Execute the full pytest test suite:

```bash
pytest tests/
```

Build and validate the frontend production bundle:

```bash
cd frontend
npm run build
```

---

## License

This project is licensed under the MIT License. See [mcp_servers/openings-mcp/LICENSE](mcp_servers/openings-mcp/LICENSE) for the vendored discovery engine license.
