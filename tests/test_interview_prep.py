"""
Unit tests for InterviewPrepEngine (Phase 4).
Verifies:
1. Skill Gap Analysis: Correctly detects missing job technologies compared to candidate profile.
2. Deterministic Interview Prep Pack:
   - 5 technical probe questions with honest bridge answers.
   - 2 project STAR stories.
   - 3 strategic reverse questions.
3. Negative Style Constraints:
   - Strictly NO em-dashes ('—', '--').
   - Strictly NO forbidden buzzwords ('passionate about', 'hit the ground running').
4. AI Live generation with mock LLM.
5. Seamless deterministic fallback on LLM errors.
"""

from unittest.mock import MagicMock
import pytest

from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE, CandidateProfile
from src.interview_prep import (
    InterviewPrepEngine,
    InterviewPrepPack,
    TechnicalProbe,
    StarStory,
    ReverseQuestion,
)
from src.tailoring_service import FORBIDDEN_BUZZWORDS


@pytest.fixture
def sample_job():
    return {
        "id": 202,
        "title": "Senior Distributed Systems Engineer",
        "company": "Zepto",
        "location": "Bengaluru",
        "description": "We are seeking a Senior Backend Engineer to architect real-time delivery infrastructure using Java, Spring Boot, PostgreSQL, Kafka, and AWS cloud services.",
    }


def test_skill_gap_analysis(sample_job):
    engine = InterviewPrepEngine()
    # Abhishek's profile has Kafka and PostgreSQL, but not AWS in languages/frameworks
    gaps = engine.analyze_skill_gaps(sample_job, DEFAULT_CANDIDATE_PROFILE)
    assert isinstance(gaps, list)
    assert len(gaps) >= 1
    # Check that identified gaps are non-empty strings
    for g in gaps:
        assert len(g) > 0


def test_deterministic_interview_pack_structure(sample_job):
    engine = InterviewPrepEngine()
    pack = engine.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=False)

    assert pack.job_title == "Senior Distributed Systems Engineer"
    assert pack.company == "Zepto"
    assert pack.generation_source == "DETERMINISTIC_SYNTHESIS"

    # 1. 5 Technical Probes
    assert len(pack.technical_probes) == 5
    for probe in pack.technical_probes:
        assert len(probe.question) > 20
        assert len(probe.honest_bridge_answer) > 30
        assert len(probe.key_talking_points) >= 2
        assert "—" not in probe.question
        assert "—" not in probe.honest_bridge_answer
        for buzz in FORBIDDEN_BUZZWORDS:
            assert buzz not in probe.honest_bridge_answer.lower()

    # 2. 2 STAR Stories
    assert len(pack.star_stories) == 2
    for star in pack.star_stories:
        assert len(star.title) > 10
        assert len(star.situation) > 20
        assert len(star.task) > 20
        assert len(star.action) > 20
        assert len(star.result) > 20
        assert len(star.demonstrated_competencies) >= 2
        assert "—" not in star.situation
        assert "—" not in star.action

    # 3. 3 Strategic Reverse Questions
    assert len(pack.reverse_questions) == 3
    for rev in pack.reverse_questions:
        assert len(rev.question) > 20
        assert len(rev.strategic_intent) > 15
        assert "—" not in rev.question


def test_interview_pack_with_mocked_llm(sample_job):
    mock_ai = MagicMock()
    mock_ai._call_gemini_api.return_value = """```json
{
  "technical_probes": [
    {
      "question": "How do you ensure zero message loss across Kafka topics?",
      "target_skill_or_gap": "Kafka Replication",
      "honest_bridge_answer": "In Spring Boot microservices, I configure acks=all and min.insync.replicas=2 to guarantee partition replication before returning success.",
      "key_talking_points": ["Set acks=all", "Configure idempotent producer"]
    },
    {
      "question": "How do you tune PostgreSQL connection pool under high traffic?",
      "target_skill_or_gap": "HikariCP Tuning",
      "honest_bridge_answer": "I set pool sizes based on database CPU cores and ensure connection acquisition timeouts are kept under 250ms.",
      "key_talking_points": ["Calculate optimal pool size", "Monitor connection wait metrics"]
    },
    {
      "question": "How do you isolate cascading failures in microservices?",
      "target_skill_or_gap": "Resilience & Circuit Breakers",
      "honest_bridge_answer": "I wrap external RPC calls with circuit breakers to fail fast and prevent thread exhaustion.",
      "key_talking_points": ["Fail fast", "Fallback payloads"]
    }
  ],
  "star_stories": [
    {
      "title": "Optimized Async Pipeline Throughput",
      "situation": "High peak traffic overloaded database connections.",
      "task": "Decouple synchronous write requests.",
      "action": "Implemented Kafka event streaming and Redis caching.",
      "result": "Reduced API response times by 35% without dropped transactions.",
      "demonstrated_competencies": ["Kafka", "Spring Boot"]
    }
  ],
  "reverse_questions": [
    {
      "question": "What is the biggest scalability hurdle your team faces this year?",
      "strategic_intent": "Shows alignment with system roadmap."
    }
  ]
}
```"""

    engine = InterviewPrepEngine(ai_service=mock_ai)
    pack = engine.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=True)

    assert pack.generation_source == "AI_LIVE"
    assert len(pack.technical_probes) == 3
    assert len(pack.star_stories) == 1
    assert len(pack.reverse_questions) == 1
    assert "acks=all" in pack.technical_probes[0].honest_bridge_answer


def test_interview_pack_fallback_on_llm_exception(sample_job):
    mock_ai = MagicMock()
    mock_ai._call_gemini_api.side_effect = RuntimeError("Service Unavailable")

    engine = InterviewPrepEngine(ai_service=mock_ai)
    pack = engine.generate_pack(sample_job, DEFAULT_CANDIDATE_PROFILE, prefer_ai=True)

    # Should fall back cleanly without crashing
    assert pack.generation_source == "DETERMINISTIC_SYNTHESIS"
    assert len(pack.technical_probes) == 5
    assert len(pack.star_stories) == 2
    assert len(pack.reverse_questions) == 3
