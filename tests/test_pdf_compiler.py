"""
Unit tests for PDFCompilationEngine and ATS verification.
Verifies:
1. Contact extraction from raw resume text (Phone, Email, LinkedIn, GitHub).
2. Resume PDF compilation with tailored bullet points.
3. Cover letter compilation (strictly 1 page).
4. ATS parseability inspection and OCR compliance scoring.
"""

import io
import pytest
import pypdf

from src.pdf_compiler import PDFCompilationEngine, CandidateContactInfo, ATSAuditResult


SAMPLE_RESUME_TEXT = """
Jane Doe
+1 555-019-2831 | jane.doe@example.com | linkedin.com/in/janedoe | github.com/janedoe
Summary
Backend Software Engineer (2+ years) specializing in Java, Spring Boot, Kubernetes, and microservices.
"""


def test_contact_info_extraction():
    contact = CandidateContactInfo.from_raw_resume_text(SAMPLE_RESUME_TEXT)
    assert contact.name == "Jane Doe"
    assert contact.phone == "+1 555-019-2831"
    assert contact.email == "jane.doe@example.com"
    assert contact.linkedin_url == "linkedin.com/in/janedoe"
    assert contact.github_url == "github.com/janedoe"


def test_compile_tailored_resume_pdf():
    engine = PDFCompilationEngine()
    contact = CandidateContactInfo.from_raw_resume_text(SAMPLE_RESUME_TEXT)
    tailored_bullets = [
        "Architected high-throughput payment ingestion pipelines using Spring Batch and Artemis MQ.",
        "Engineered Javassist dynamic proxies for zero-downtime database routing on Kubernetes."
    ]

    pdf_bytes = engine.compile_tailored_resume_pdf(
        contact_info=contact,
        tailored_bullets=tailored_bullets,
        target_role="Lead Java Engineer",
        target_company="Acme Corp",
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 2000
    assert pdf_bytes.startswith(b"%PDF")

    # Run ATS compliance audit
    audit = engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)
    assert audit.passed is True
    assert audit.page_count <= 2
    assert audit.ats_score >= 80
    assert audit.extracted_contacts["email"] == "jane.doe@example.com"
    assert audit.extracted_contacts["phone"] == "+1 555-019-2831"


def test_compile_cover_letter_pdf():
    engine = PDFCompilationEngine()
    contact = CandidateContactInfo.from_raw_resume_text(SAMPLE_RESUME_TEXT)

    pitch = (
        "I am excited to apply for the Backend Software Engineer role at Google.\n\n"
        "At TCS BaNCS, I modernized critical financial microservices using Spring Boot and Kubernetes.\n\n"
        "I look forward to discussing how my experience can contribute to your team."
    )

    pdf_bytes = engine.compile_cover_letter_pdf(
        contact_info=contact,
        target_role="Backend Software Engineer",
        target_company="Google",
        pitch_paragraphs=pitch,
    )

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1  # Strictly 1 page

    audit = engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=1)
    assert audit.passed is True
    assert audit.page_count == 1
    assert audit.ats_score >= 90
