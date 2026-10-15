"""
Tests for Candidate Profile Onboarding, Project Vault Handpicking, and Resume Upload
Verifies:
1. ResumeParser extracts structured profile facts (contacts, experience, projects, skills).
2. AgenticTailoringEngine.rank_and_select_projects intelligently reranks vault projects
   to handpick the top 1 or 2 best-fit projects matching specific job descriptions.
3. CandidateMasterProfileService gracefully handles empty tables by inserting defaults.
4. POST /api/profile/upload-resume processes uploads and saves structured profiles.
"""

import io
import pytest
from fastapi.testclient import TestClient

from src.resume_parser import ResumeParser
from src.agentic_tailoring_service import AgenticTailoringEngine
from src.candidate_profile_service import CandidateMasterProfileService
from src.api_server import app


SAMPLE_RESUME_TEXT = """Alex Morgan
alex.morgan@techcorp.io
+1 (555) 234-5678
https://linkedin.com/in/alexmorgan
https://github.com/alexmorgan
San Francisco, CA

PROFESSIONAL SUMMARY
Senior backend engineer specializing in high-scale distributed systems and cloud native architectures.

TECHNICAL SKILLS
Languages: Python, Go, Java, TypeScript
Frameworks: FastAPI, Spring Boot, React, Node.js
Databases: PostgreSQL, Redis, DynamoDB
DevOps Cloud: Docker, Kubernetes, AWS, Terraform
Tools: Git, Linux, Prometheus

WORK EXPERIENCE
TechCorp Systems | Staff Software Engineer | Jan 2021 - Present
San Francisco, CA
- Architected distributed data ingestion pipeline processing 50M events daily with sub-second latency.
- Led migration of 14 monolithic microservices to Amazon EKS using Helm and ArgoCD.
- Reduced database read latency by 45% through Redis caching clusters and connection pooling.

ScaleFlow Networks | Software Engineer | Jun 2018 - Dec 2020
Austin, TX
- Developed asynchronous REST and gRPC endpoints in Go handling 10K requests per second.
- Implemented automated database migration workflows with zero application downtime.

PROJECTS
Distributed Event Broker (Go, Kafka, Docker)
https://github.com/alexmorgan/event-broker
- Built high-throughput pub-sub message broker with Raft consensus and WAL persistence.
- Benchmarked at 150K msgs/sec throughput with p99 latency under 4ms.

Next.js Enterprise Dashboard (TypeScript, React, Tailwind)
https://github.com/alexmorgan/enterprise-dash
- Developed modern analytics console featuring real-time telemetry and dark mode UI.
- Integrated WebSocket streaming updates and dynamic widget dashboard.

EDUCATION
Bachelor of Science in Computer Science
2014 - 2018
University of California, Berkeley
Key Topics: Distributed Systems, Operating Systems, Algorithms
"""


def test_resume_parser_structured_extraction():
    """Verify deterministic extraction of candidate profile coordinates and sections."""
    profile = ResumeParser.extract_structured_profile(SAMPLE_RESUME_TEXT)

    # Coordinates
    assert profile["candidate_name"] == "Alex Morgan"
    assert profile["email"] == "alex.morgan@techcorp.io"
    assert "555" in profile["phone"]
    assert "linkedin.com/in/alexmorgan" in profile["linkedin_url"]
    assert "github.com/alexmorgan" in profile["github_url"]

    # Projects
    assert len(profile["projects"]) >= 2
    proj_names = [p["name"] for p in profile["projects"]]
    assert any("Distributed Event Broker" in name for name in proj_names)

    # Work history
    assert len(profile["work_history"]) >= 2
    comp_names = [w["company"] for w in profile["work_history"]]
    assert any("TechCorp Systems" in comp for comp in comp_names)

    # Skills
    skills = profile["technical_skills"]
    assert any("Python" in s or "Go" in s for s in skills.get("languages", []))
    assert any("FastAPI" in s or "Spring Boot" in s for s in skills.get("frameworks", []))
    assert any("PostgreSQL" in s or "Redis" in s for s in skills.get("databases", []))


def test_project_vault_reranking_frontend_role():
    """Verify rank_and_select_projects handpicks React/TypeScript project for frontend roles."""
    projects = [
        {
            "name": "Distributed Event Broker",
            "tech_stack": "Go, Kafka, Kubernetes, Raft",
            "bullets": ["High-throughput message pipeline", "Distributed consensus algorithm"],
        },
        {
            "name": "Next.js Enterprise Portal",
            "tech_stack": "TypeScript, React, Next.js, Tailwind CSS",
            "bullets": ["Interactive component library", "Micro-frontend dashboard rendering"],
        },
        {
            "name": "AI Web Scraping Pipeline",
            "tech_stack": "Python, BeautifulSoup, PostgreSQL",
            "bullets": ["Extracted 100K job postings", "Cleaned deduplication tables"],
        },
    ]

    frontend_jd = "We are seeking a Senior Frontend Engineer to build interactive web apps using React, Next.js, and TypeScript."
    top_projects = AgenticTailoringEngine.rank_and_select_projects(projects, frontend_jd, top_k=2)

    assert len(top_projects) == 2
    # The React/Next.js project must be ranked #1
    assert top_projects[0]["name"] == "Next.js Enterprise Portal"


def test_project_vault_reranking_distributed_backend_role():
    """Verify rank_and_select_projects handpicks distributed Kafka/K8s project for backend roles."""
    projects = [
        {
            "name": "React Component Explorer",
            "tech_stack": "React, Tailwind, Storybook",
            "bullets": ["UI component design system"],
        },
        {
            "name": "High-Throughput Payments Engine",
            "tech_stack": "Java, Spring Boot, Kafka, Kubernetes, Docker",
            "bullets": ["Processed PAIN001 transactions with sub-second SLA"],
        },
        {
            "name": "Mobile iOS Health Tracker",
            "tech_stack": "Swift, SwiftUI, CoreData",
            "bullets": ["HealthKit biometric tracking"],
        },
    ]

    backend_jd = "Looking for a Distributed Systems Engineer to scale our Kafka streaming platform on Kubernetes with Java and Spring Boot."
    top_projects = AgenticTailoringEngine.rank_and_select_projects(projects, backend_jd, top_k=1)

    assert len(top_projects) == 1
    assert top_projects[0]["name"] == "High-Throughput Payments Engine"


def test_project_vault_handles_empty_and_small_lists():
    """Verify rank_and_select_projects gracefully handles empty or single-item project lists."""
    assert AgenticTailoringEngine.rank_and_select_projects([], "Job description") == []

    single = [{"name": "Solo Project", "tech_stack": "Python", "bullets": []}]
    res = AgenticTailoringEngine.rank_and_select_projects(single, "Any job", top_k=2)
    assert len(res) == 1
    assert res[0]["name"] == "Solo Project"


def test_candidate_profile_service_default_template():
    """Verify CandidateMasterProfileService provides complete default structure on empty query."""
    svc = CandidateMasterProfileService()
    default_p = svc.get_default_empty_profile()

    assert default_p["status"] == "Actively Looking"
    assert isinstance(default_p["projects"], list)
    assert isinstance(default_p["work_history"], list)
    assert isinstance(default_p["technical_skills"], dict)
    assert "languages" in default_p["technical_skills"]


def test_upload_resume_api_endpoint(monkeypatch):
    """Verify POST /api/profile/upload-resume handles text file upload and extracts profile."""
    monkeypatch.setattr("src.api_server.profile_svc.update_profile", lambda data: True)
    client = TestClient(app)

    file_bytes = SAMPLE_RESUME_TEXT.encode("utf-8")
    files = {"file": ("sample_resume.txt", io.BytesIO(file_bytes), "text/plain")}

    resp = client.post("/api/profile/upload-resume", files=files)
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "success"
    assert "profile" in data
    assert data["profile"]["candidate_name"] == "Alex Morgan"
    assert data["profile"]["email"] == "alex.morgan@techcorp.io"
    assert len(data["profile"]["projects"]) >= 1


def test_upload_resume_api_empty_file_rejected():
    """Verify POST /api/profile/upload-resume rejects 0-byte files with 400."""
    client = TestClient(app)
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}

    resp = client.post("/api/profile/upload-resume", files=files)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_resume_api_unsupported_ext_rejected():
    """Verify POST /api/profile/upload-resume rejects unsupported formats with 400."""
    client = TestClient(app)
    files = {"file": ("malware.exe", io.BytesIO(b"badcontent"), "application/octet-stream")}

    resp = client.post("/api/profile/upload-resume", files=files)
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"].lower()
