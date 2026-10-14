import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.models import (
    JobRecord, ValidationEvidence, JobStatus, ApplicationType,
    ExperienceLevel, ExperienceEligibility, FreshnessStatus, FitEligibility
)
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.experience_classifier import ExperienceClassifier
from src.classifier import FreshnessClassifier
from src.scorer import JobRelevanceScorer
from src.discovery import JobDiscoveryService
from src.deduplicator import Deduplicator


def test_candidate_profile_skills():
    """Validates candidate profile contains only verified resume skills."""
    profile = DEFAULT_CANDIDATE_PROFILE
    assert "Java" in profile.languages
    assert "Spring Boot" in profile.frameworks
    assert "PostgreSQL" in profile.databases
    assert "Kafka" in profile.messaging_and_cache
    assert "Docker" in profile.devops_and_tools
    assert profile.target_experience_years == 2.0
    assert profile.hard_limit_years == 4.0


def test_india_target_cities_handling():
    """Tests India tech hubs prioritization: Bengaluru, Delhi, Gurgaon, Noida, Hyderabad, Pune, Mumbai, Remote."""
    scorer = JobRelevanceScorer(target_location="bengaluru")
    
    cities = [
        ("Bengaluru, Karnataka", 15),
        ("Bangalore", 15),
        ("Hyderabad, Telangana", 13),
        ("Pune, Maharashtra", 13),
        ("Noida, Uttar Pradesh", 13),
        ("Gurgaon, Haryana", 13),
        ("Gurugram", 13),
        ("Mumbai, Maharashtra", 13),
        ("Remote, India", 12),
        ("Tokyo, Japan", 0)  # Outside target hubs
    ]

    for loc, expected_score in cities:
        score = scorer.score_job(
            title="Java Backend Engineer",
            company="Tech Corp",
            location=loc,
            description="Java 17, Spring Boot, REST APIs with 2 years of experience."
        )
        assert score.score_breakdown["location_fit"] == expected_score


def test_experience_ranges_and_eligibility():
    """
    Tests:
    - 0-3 years -> MATCH
    - exactly 4 years -> STRETCH
    - >4 years -> OVER_LIMIT
    - Missing -> UNKNOWN
    """
    # 0-3 years -> MATCH
    level, reason = ExperienceClassifier.classify("Java Developer", "Requires 1-3 years of experience in Java.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "Requires 1-3 years of experience in Java.")
    assert elig == ExperienceEligibility.MATCH

    # Up to 3 years -> MATCH
    level, reason = ExperienceClassifier.classify("Java Developer", "Experience up to 3 years.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "Experience up to 3 years.")
    assert elig == ExperienceEligibility.MATCH

    # Exactly 4 years -> STRETCH
    level, reason = ExperienceClassifier.classify("Java Developer", "Requires 4 years of experience.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "Requires 4 years of experience.")
    assert elig == ExperienceEligibility.STRETCH

    # 2 to 5 years (range crossing 4) -> STRETCH
    level, reason = ExperienceClassifier.classify("Java Developer", "2 to 5 years of experience.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "2 to 5 years of experience.")
    assert elig == ExperienceEligibility.STRETCH

    # Explicit > 4 years (e.g. 5+ years) -> OVER_LIMIT
    level, reason = ExperienceClassifier.classify("Java Developer", "Requires minimum 5 years of experience.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "Requires minimum 5 years of experience.")
    assert elig == ExperienceEligibility.OVER_LIMIT

    # Missing experience in title and description -> UNKNOWN
    level, reason = ExperienceClassifier.classify("Java Developer", "Seeking skilled backend developer.")
    elig, note = ExperienceClassifier.evaluate_eligibility(level, reason, "Java Developer", "Seeking skilled backend developer.")
    assert elig == ExperienceEligibility.UNKNOWN


def test_hard_limit_excludes_over_limit_from_eligible_recommendations():
    """Requirements above 4 years must be excluded from eligible recommendations (LOW_FIT)."""
    scorer = JobRelevanceScorer(target_location="bengaluru")
    # A job with perfect skill match, in Bengaluru, but requiring 5+ years
    score = scorer.score_job(
        title="Java Backend Developer",
        company="Global FinTech",
        location="Bengaluru",
        description="""
        Expert in Java 17, Spring Boot, Spring MVC, REST APIs, Microservices,
        Hibernate, Kafka, PostgreSQL, Docker, Maven, CI/CD.
        Requires 5+ years of experience.
        """
    )
    # Must be OVER_LIMIT and classified as LOW_FIT despite rich skill presence
    assert score.experience_eligibility == ExperienceEligibility.OVER_LIMIT
    assert score.eligibility == FitEligibility.LOW_FIT
    assert "exceeds 4-year limit" in score.eligibility_reason


def test_freshness_classification_and_contradictions():
    """
    Tests:
    - Posted in preceding 24h -> CONFIRMED_FRESH
    - Updated recently but originally posted long ago -> STALE
    - Contradictory metadata (fake_freshness or stale class vs recent date) -> CONTRADICTORY
    - Missing / unparseable date -> UNKNOWN_FRESHNESS
    """
    ref_time = datetime(2026, 10, 11, 12, 0, 0)

    # 1. Posted today
    status, note = FreshnessClassifier.evaluate("2026-10-11", reference_time=ref_time)
    assert status == FreshnessStatus.CONFIRMED_FRESH

    # 2. Posted yesterday (within 24-48h window)
    status, note = FreshnessClassifier.evaluate("2026-10-10", reference_time=ref_time)
    assert status == FreshnessStatus.CONFIRMED_FRESH

    # 3. Updated today, but originally posted 60 days ago
    status, note = FreshnessClassifier.evaluate(
        posted_date_str="2026-08-10",
        updated_date_str="2026-10-11",
        reference_time=ref_time
    )
    assert status == FreshnessStatus.STALE
    assert "Updated recently" in note

    # 4. Contradictory signals: metadata class='stale' while posted date says today
    status, note = FreshnessClassifier.evaluate(
        posted_date_str="2026-10-11",
        reality_meta={"class": "stale", "fake_freshness": True},
        reference_time=ref_time
    )
    assert status == FreshnessStatus.CONTRADICTORY
    assert "Contradictory freshness signals" in note

    # 5. Missing date
    status, note = FreshnessClassifier.evaluate(None, reference_time=ref_time)
    assert status == FreshnessStatus.UNKNOWN_FRESHNESS

    # 6. Malformed date
    status, note = FreshnessClassifier.evaluate("invalid-date", reference_time=ref_time)
    assert status == FreshnessStatus.UNKNOWN_FRESHNESS


def test_cross_source_deduplication():
    """Verifies jobs discovered across Freehire, LinkedIn, and Indeed are deduplicated by canonical URL and platform job ID."""
    deduplicator = Deduplicator()
    job1 = JobRecord(
        platform="freehire",
        platform_job_id="job-101",
        title="Java Developer",
        company="TCS",
        url="https://company.com/careers/101?utm_source=freehire.me",
        normalized_url="https://company.com/careers/101",
        validation=ValidationEvidence(evidence_source="freehire_search_jobs", evidence_notes="")
    )
    job2 = JobRecord(
        platform="indeed",
        platform_job_id="job-101",
        title="Java Developer",
        company="TCS",
        url="https://company.com/careers/101?utm_source=indeed&ref=abc",
        normalized_url="https://company.com/careers/101",
        validation=ValidationEvidence(evidence_source="indeed_search_jobs", evidence_notes="")
    )
    unique = deduplicator.deduplicate([job1, job2])
    assert len(unique) == 1


@pytest.mark.asyncio
async def test_freehire_discovery_service_integration():
    """Tests Freehire discovery service parsing, field mapping, and error resilience with mocked MCP client."""
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {
        "data": [
            {
                "public_slug": "java-dev-123",
                "external_id": "ats-12345",
                "title": "Java Spring Boot Developer",
                "company": "Infosys",
                "cities": ["Bengaluru"],
                "url": "https://infosys.com/job/123",
                "posted_at": datetime.now().strftime("%Y-%m-%d"),
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
                "description": "Building microservices with Java, Spring Boot, and PostgreSQL.",
                "work_mode": "hybrid",
                "reality": {"class": "fresh", "fake_freshness": False}
            }
        ]
    }

    discovery = JobDiscoveryService(mcp_client=mock_mcp)
    jobs = await discovery.search_freehire(query="Java Spring Boot", country_code="in", posted_within_days=1, limit=5)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.platform == "freehire"
    assert job.title == "Java Spring Boot Developer"
    assert job.company == "Infosys"
    assert job.location == "Bengaluru"
    assert job.freshness_status == FreshnessStatus.CONFIRMED_FRESH
    assert job.work_mode == "hybrid"
    assert job.description_snippet is not None
    assert job.validation.job_status == JobStatus.UNKNOWN  # Conservative classification intact


@pytest.mark.asyncio
async def test_discovery_service_empty_and_error_handling():
    """Verifies discovery handles empty or failing tool responses gracefully without crashing."""
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.side_effect = Exception("MCP connection dropped")

    discovery = JobDiscoveryService(mcp_client=mock_mcp)
    jobs = await discovery.search_freehire(query="Java", country_code="in", limit=5)
    assert jobs == []
