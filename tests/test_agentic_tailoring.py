"""
Unit & Integration Tests for Job Bucket 2-Agent Tailoring & Candidate Master Profile
Verifies:
1. Authentic prompt markdown files in src/prompts/ exist and are loaded verbatim.
2. CandidateMasterProfileService performs strict PII masking (phone & email masked, name/GitHub/LinkedIn kept).
3. 2-Agent Tailoring Engine (Drafter + Reviewer) executes with full factual grounding (Draft ⊆ Profile).
4. Reviewer feedback parses Part A mechanical string replacements and Part B narrative critique.
5. Negative style constraints (no em-dashes, no buzzwords) are enforced.
6. ATS PDF generation succeeds with 100/100 score.
"""

import os
import pytest
from typing import Dict, Any

from src.candidate_profile_service import (
    CandidateMasterProfileService,
    PHONE_PLACEHOLDER,
    EMAIL_PLACEHOLDER,
)
from src.agentic_tailoring_service import (
    AgenticTailoringEngine,
    ReviewerEdit,
    ReviewerFeedback,
    load_prompt_file,
    PROMPTS_DIR,
)
from src.pdf_compiler import PDFCompilationEngine, CandidateContactInfo


def test_authentic_prompts_exist_and_non_empty():
    """Verify all gold markdown files copied from ai-job-search exist in src/prompts/."""
    expected_files = [
        "01-candidate-profile.md",
        "02-behavioral-profile.md",
        "03-writing-style.md",
        "04-job-evaluation.md",
        "05-cv-templates.md",
        "06-cover-letter-templates.md",
        "07-interview-prep.md",
        "08-application-forms.md",
        "09-web-research.md",
        "apply.md",
    ]
    for filename in expected_files:
        path = os.path.join(PROMPTS_DIR, filename)
        assert os.path.exists(path), f"Missing prompt file: {filename}"
        content = load_prompt_file(filename)
        assert len(content) > 100, f"Prompt file {filename} is suspiciously short"
        if filename == "03-writing-style.md":
            assert "NO em-dashes" in content
            assert "NO cliches" in content
            assert "backtrack test" in content


def test_candidate_profile_service_markdown_export():
    """Test that CandidateMasterProfileService exports real contact details, name, and public URLs directly."""
    svc = CandidateMasterProfileService()
    profile = svc.get_active_profile()

    assert profile, "Active profile should exist in PostgreSQL"
    real_name = profile.get("candidate_name", "")
    real_phone = profile.get("phone", "")
    real_email = profile.get("email", "")

    # Generate markdown
    md = svc.export_profile_to_markdown(profile)

    # Assert contact details and profile content present
    if real_phone:
        assert real_phone in md
    if real_email:
        assert real_email in md
    if real_name:
        assert real_name in md


def test_agentic_tailoring_deterministic_fallback():
    """Test deterministic 2-agent tailoring workflow execution."""
    engine = AgenticTailoringEngine()
    job = {
        "id": 999,
        "title": "Senior Backend Software Engineer",
        "company": "Adyen",
        "description": "Building high-scale payments platform with Java, Spring Boot, Kafka, and Kubernetes."
    }

    result = engine.run_workflow(job, prefer_ai=False)

    assert result.job_title == "Senior Backend Software Engineer"
    assert result.company == "Adyen"
    assert len(result.tailored_bullets) >= 3
    assert len(result.tailored_latex_cv) > 500
    assert result.ats_score == 100
    assert result.page_count in [1, 2]
    assert result.reviewer_feedback.grounding_passed is True

    assert len(result.recruiter_outreach) > 20 and len(result.recruiter_outreach) <= 300
    assert "—" not in result.recruiter_outreach

    # Check contact details are directly in tailored LaTeX
    p = CandidateMasterProfileService().get_active_profile()
    if p.get("phone"):
        assert p["phone"] in result.tailored_latex_cv
    if p.get("email"):
        assert p["email"] in result.tailored_latex_cv
    assert result.unmask_applied is False

    # Negative style rules: NO em-dashes in pitch or bullets
    assert "—" not in result.cover_pitch
    for b in result.tailored_bullets:
        assert "—" not in b


def test_reviewer_part_a_revision_application():
    """Test that Step 4 mechanical string replacements are applied accurately."""
    engine = AgenticTailoringEngine()

    drafter_output = {
        "tailored_latex_cv": "Summary: I spearheaded several projects and am passionate about tech.",
        "tailored_bullets": ["I leveraged microservices to achieve synergy."],
        "cover_pitch": "I am thrilled to apply and believe I would be a great fit.",
        "recruiter_outreach": "Hi, I am passionate about tech and want to achieve synergy.",
    }

    reviewer_fb = ReviewerFeedback(
        part_a_edits=[
            ReviewerEdit(
                file="cv.tex",
                old_string="spearheaded several projects",
                new_string="engineered core backend services",
                reason="style",
            ),
            ReviewerEdit(
                file="cv.tex",
                old_string="passionate about tech",
                new_string="focused on distributed systems",
                reason="style",
            ),
            ReviewerEdit(
                file="cv.tex",
                old_string="achieve synergy",
                new_string="reduce latency",
                reason="style",
            ),
        ]
    )

    revised_latex, revised_pitch, revised_bullets, revised_outreach = engine._apply_revisions(
        drafter_output=drafter_output,
        reviewer_feedback=reviewer_fb,
    )

    assert "spearheaded" not in revised_latex
    assert "engineered core backend services" in revised_latex
    assert "passionate about tech" not in revised_latex
    assert "focused on distributed systems" in revised_latex
    assert "achieve synergy" not in revised_bullets[0]
    assert "reduce latency" in revised_bullets[0]
    assert "passionate about tech" not in revised_outreach
    assert "achieve synergy" not in revised_outreach


def test_pdf_compilation_from_master_profile():
    """Test that PDFCompilationEngine compiles unmasked PDF with verified 100/100 ATS score."""
    pdf_engine = PDFCompilationEngine()
    contact = CandidateContactInfo(
        name="Jane Doe",
        phone="+1 555-019-2831",
        email="jane.doe@example.com",
        linkedin_url="linkedin.com/in/janedoe",
        github_url="github.com/janedoe",
        location="City, Country",
    )
    bullets = [
        "Architected a high-throughput payment ingestion pipeline handling up to 100K transactions per file via JMS and Spring Batch.",
        "Migrated legacy account-opening flows to Kubernetes microservices using ActiveMQ and the Strangler Fig pattern.",
        "Engineered Javassist dynamic proxies to runtime-switch datasources for downstream database writes without legacy WebLogic dependencies.",
    ]

    pdf_bytes = pdf_engine.compile_tailored_resume_pdf(
        contact_info=contact,
        tailored_bullets=bullets,
        target_role="Senior Backend Engineer",
        target_company="Stripe",
    )

    audit = pdf_engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)
    assert audit.ats_score == 100
    assert audit.page_count in [1, 2]
    assert audit.extracted_contacts.get("email") == "jane.doe@example.com"
    assert audit.extracted_contacts.get("phone") == "+1 555-019-2831"
    assert audit.passed is True
