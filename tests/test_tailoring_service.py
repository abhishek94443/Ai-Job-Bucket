"""
Unit tests for TailoringService (Phase 3).
Verifies:
1. Strict Factual Grounding: All generated claims and skills are subsets of the candidate profile.
2. Negative Style Constraints:
   - Strictly NO em-dashes ('—', '--').
   - Strictly NO forbidden buzzwords ('passionate about', 'hit the ground running', etc.).
3. 4 Application Artifacts generated:
   - Tailored Resume Highlights (3-4 bullets)
   - LinkedIn Outreach Pitch (<= 300 chars)
   - Forward-Looking Cover Pitch (3 paragraphs)
   - Portal Form Answers (short bio + technical achievement)
4. Deterministic fallback produces valid, grounded pack.
5. Grounding auditor detects and flags any violations.
"""

from unittest.mock import MagicMock
import pytest

from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE, CandidateProfile
from src.tailoring_service import (
    TailoringService,
    TailoredApplicationPack,
    audit_grounding,
    sanitize_text,
    FORBIDDEN_BUZZWORDS,
)


@pytest.fixture
def sample_job():
    return {
        "id": 101,
        "title": "Senior Java Backend Engineer",
        "company": "Swiggy",
        "location": "Bengaluru",
        "description": "We are seeking a Backend Engineer with strong expertise in Java, Spring Boot, PostgreSQL, Kafka, and distributed microservices architecture to build high-scale food delivery systems.",
    }


def test_sanitize_text_removes_em_dashes_and_flags_buzzwords():
    # Text with em-dashes and buzzwords
    dirty = "I am passionate about Java — Spring Boot, and I hit the ground running."
    cleaned, warnings = sanitize_text(dirty)

    assert "—" not in cleaned
    assert "--" not in cleaned
    assert "passionate about" in [w.lower() for w in warnings][0]
    assert len(warnings) >= 2


def test_audit_grounding_passes_for_clean_pack(sample_job):
    service = TailoringService()
    pack = service.generate_deterministic_pack(sample_job, DEFAULT_CANDIDATE_PROFILE)

    is_grounded, issues = audit_grounding(pack, DEFAULT_CANDIDATE_PROFILE)
    assert is_grounded is True
    assert issues == []


def test_audit_grounding_flags_unverified_skill():
    profile = CandidateProfile(
        name="Test Dev",
        languages=["Java"],
        frameworks=["Spring Boot"],
        databases=["PostgreSQL"],
    )
    pack = TailoredApplicationPack(
        job_title="Java Dev",
        company="Acme",
        tailored_resume_bullets=["Built Java apps."],
        outreach_message="Hi, I do Java.",
        cover_pitch="Para 1.\n\nPara 2.\n\nPara 3.",
        portal_short_bio="Bio.",
        portal_key_achievement="Achievement.",
        matching_skills_highlighted=["Java", "Rust", "Solidity"],  # Unverified skills!
    )
    is_grounded, issues = audit_grounding(pack, profile)
    assert is_grounded is False
    assert any("Rust" in issue for issue in issues)
    assert any("Solidity" in issue for issue in issues)


def test_audit_grounding_flags_em_dashes():
    profile = DEFAULT_CANDIDATE_PROFILE
    pack = TailoredApplicationPack(
        job_title="Java Dev",
        company="Acme",
        tailored_resume_bullets=["Architected backend — improved throughput."],  # Em-dash!
        outreach_message="Hi recruiter.",
        cover_pitch="Para 1.\n\nPara 2.\n\nPara 3.",
        portal_short_bio="Bio.",
        portal_key_achievement="Achievement.",
        matching_skills_highlighted=["Java"],
    )
    is_grounded, issues = audit_grounding(pack, profile)
    assert is_grounded is False
    assert any("Em-dash" in issue for issue in issues)


def test_deterministic_pack_structure_and_constraints(sample_job):
    service = TailoringService()
    pack = service.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=False)

    assert pack.job_title == "Senior Java Backend Engineer"
    assert pack.company == "Swiggy"
    assert pack.generation_source == "DETERMINISTIC_SYNTHESIS"
    assert pack.grounding_passed is True

    # 1. Tailored resume bullets
    assert 3 <= len(pack.tailored_resume_bullets) <= 4
    for b in pack.tailored_resume_bullets:
        assert "—" not in b
        assert "--" not in b
        for buzz in FORBIDDEN_BUZZWORDS:
            assert buzz not in b.lower()

    # 2. Outreach note constraints
    assert len(pack.outreach_message) <= 300
    assert "—" not in pack.outreach_message
    assert "Swiggy" in pack.outreach_message

    # 3. Cover pitch has 3 paragraphs
    paragraphs = [p for p in pack.cover_pitch.split("\n\n") if p.strip()]
    assert len(paragraphs) == 3
    for p in paragraphs:
        assert "—" not in p

    # 4. Portal answers
    assert len(pack.portal_short_bio.split()) >= 30
    assert len(pack.portal_key_achievement.split()) >= 30
    assert "—" not in pack.portal_short_bio
    assert "—" not in pack.portal_key_achievement


def test_ai_pack_generation_with_mocked_llm(sample_job):
    mock_ai = MagicMock()
    mock_ai._call_gemini_api.return_value = """```json
{
  "tailored_resume_bullets": [
    "Developed high-throughput Java 17 microservices using Spring Boot and PostgreSQL.",
    "Integrated Apache Kafka to enable distributed event streaming across microservices."
  ],
  "outreach_message": "Hi, I noticed the Senior Java role at Swiggy. With 2 years in Spring Boot and Kafka, I would value connecting.",
  "cover_pitch": "Swiggy handles immense real-time traffic.\n\nMy background in Spring Boot and Kafka directly supports these demands.\n\nI look forward to discussing how my experience can benefit the team.",
  "portal_short_bio": "Backend engineer experienced in Java, Spring Boot, and PostgreSQL.",
  "portal_key_achievement": "Built an asynchronous order processing pipeline using Kafka, reducing latency by 35%.",
  "matching_skills_highlighted": ["Java", "Spring Boot", "Kafka", "PostgreSQL"]
}
```"""

    service = TailoringService(ai_service=mock_ai)
    pack = service.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=True)

    assert pack.generation_source == "AI_LIVE"
    assert pack.grounding_passed is True
    assert len(pack.tailored_resume_bullets) == 2
    assert "Java" in pack.matching_skills_highlighted
    assert "Kafka" in pack.matching_skills_highlighted
    assert len(pack.outreach_message) <= 300


def test_ai_pack_fallback_on_llm_exception(sample_job):
    mock_ai = MagicMock()
    mock_ai._call_gemini_api.side_effect = RuntimeError("Quota exhausted")

    service = TailoringService(ai_service=mock_ai)
    # Should seamlessly fall back to deterministic pack
    pack = service.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=True)

    assert pack.generation_source == "DETERMINISTIC_SYNTHESIS"
    assert pack.grounding_passed is True
    assert pack.company == "Swiggy"
